# -*- coding: utf-8 -*-
"""
信号映射管理
管理模型变量名与 OPC UA 节点路径的映射关系。
支持从 YAML 配置文件和 Excel 文件加载。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SignalInfo:
    """单个信号的映射信息"""
    name: str            # 模型变量名, 如 "main_steam_pressure"
    channel: str         # DCS 通道号, 如 "AI010605"
    channel_type: str    # 通道类型: "AI", "DI", "block"
    description: str     # 中文描述, 如 "主汽压力"
    unit: str = ""       # 工程单位, 如 "MPa"
    range_low: float = 0.0    # 量程下限（AI 通道）
    range_high: float = 100.0 # 量程上限（AI 通道）
    direction: str = "output" # "input" (OPC→模型) 或 "output" (模型→OPC)
    node_base: str = ""  # OPC 节点基础路径（自动生成或手动指定）

    def __post_init__(self):
        if not self.node_base:
            if self.channel:
                # 硬件通道: ns=0;s=DPU3013.HW.{channel}
                self.node_base = f"ns=0;s=DPU3013.HW.{self.channel}"

    @property
    def pv_node(self) -> str:
        """PV 节点路径"""
        # block 类型信号的 node 已经是完整路径（如 SH0021.PROMW.IN），不需要追加 .PV
        # AI/DI 硬件通道的 node_base 是通道基础路径（如 HW.AI010605），需要追加 .PV
        if self.channel_type.upper() == "BLOCK":
            return self.node_base
        return f"{self.node_base}.PV"

    @property
    def hr_node(self) -> str:
        """HR 节点路径（AI 通道量程上限）"""
        return f"{self.node_base}.HR"

    @property
    def lr_node(self) -> str:
        """LR 节点路径（AI 通道量程下限）"""
        return f"{self.node_base}.LR"


class SignalMapping:
    """
    信号映射管理器

    用法:
        mapping = SignalMapping.from_yaml("config/opc_mapping.yaml")

        # 按变量名获取信号信息
        sig = mapping.get("main_steam_pressure")
        print(sig.pv_node)  # "ns=0;s=DPU3013.HW.AI010605.PV"

        # 获取所有 AI 信号
        ai_signals = mapping.get_by_type("AI")
    """

    def __init__(self):
        self._signals: Dict[str, SignalInfo] = {}  # name -> SignalInfo
        # 冗余通道: {模型变量名: [额外的 AI 通道 node_base, ...]}
        self._redundancy: Dict[str, List[str]] = {}

    def add(self, signal: SignalInfo):
        """添加信号映射"""
        self._signals[signal.name] = signal

    def get(self, name: str) -> Optional[SignalInfo]:
        """按变量名获取信号"""
        return self._signals.get(name)

    def get_by_type(self, channel_type: str) -> List[SignalInfo]:
        """按通道类型获取信号列表"""
        return [s for s in self._signals.values()
                if s.channel_type.upper() == channel_type.upper()]

    def get_all(self) -> List[SignalInfo]:
        """获取全部信号"""
        return list(self._signals.values())

    def get_inputs(self) -> List[SignalInfo]:
        """获取所有输入信号（OPC→模型，如DCS控制指令）"""
        return [s for s in self._signals.values() if s.direction == "input"]

    def get_outputs(self) -> List[SignalInfo]:
        """获取所有输出信号（模型→OPC，如工艺参数）"""
        return [s for s in self._signals.values() if s.direction == "output"]

    def get_pv_nodes(self) -> Dict[str, str]:
        """获取所有信号的 PV 节点路径 {变量名: PV节点路径}"""
        return {name: sig.pv_node for name, sig in self._signals.items()}

    def get_redundant_channels(self, name: str) -> List[str]:
        """获取信号的冗余 AI 通道 node_base 列表"""
        return self._redundancy.get(name, [])

    @classmethod
    def from_yaml(cls, filepath: str) -> "SignalMapping":
        """
        从 YAML 文件加载信号映射

        YAML 格式:
            server: "opc.tcp://localhost:9440"
            dpu: "DPU3013"
            signals:
              - name: main_steam_pressure
                channel: AI010605
                type: AI
                description: 主汽压力
                unit: MPa
                range_low: 0
                range_high: 990
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"映射配置文件不存在: {filepath}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        mapping = cls()
        dpu = data.get("dpu", "DPU3013")

        for item in data.get("signals", []):
            channel = item.get("channel", "")
            channel_type = item.get("type", "AI" if channel.startswith("AI") else "DI")

            # 支持两种节点路径指定方式:
            # 1. channel 字段 → 自动生成 HW 路径 (用于 AI/DI 硬件通道)
            # 2. node 字段 → 直接指定任意 OPC 节点路径 (用于组态块输出)
            if "node" in item:
                node_path = item["node"]
                node_base = node_path if node_path.startswith("ns=") else f"ns=0;s={node_path}"
            elif channel:
                node_base = f"ns=0;s={dpu}.HW.{channel}"
            else:
                logger.warning(f"信号 {item['name']} 未指定 channel 或 node，跳过")
                continue

            sig = SignalInfo(
                name=item["name"],
                channel=channel,
                channel_type=channel_type,
                description=item.get("description", ""),
                unit=item.get("unit", ""),
                range_low=float(item.get("range_low", 0)),
                range_high=float(item.get("range_high", 100)),
                direction=item.get("direction", "output"),
                node_base=node_base,
            )
            mapping.add(sig)

        # 加载冗余通道配置
        for name, channels in data.get("redundancy", {}).items():
            node_bases = []
            for ch in channels:
                if isinstance(ch, dict):
                    channel_id = ch["channel"]
                elif isinstance(ch, str):
                    channel_id = ch
                else:
                    continue
                node_bases.append(f"ns=0;s={dpu}.HW.{channel_id}")
            mapping._redundancy[name] = node_bases

        total_redundant = sum(len(v) for v in mapping._redundancy.values())
        logger.info(f"加载信号映射: {len(mapping._signals)} 个信号, "
                    f"{total_redundant} 个冗余通道 (from {filepath})")
        return mapping

    def to_yaml(self, filepath: str, server_url: str = "opc.tcp://localhost:9440"):
        """导出信号映射到 YAML 文件"""
        data = {
            "server": server_url,
            "dpu": "DPU3013",
            "signals": []
        }
        for sig in self._signals.values():
            entry = {
                "name": sig.name,
                "direction": sig.direction,
                "type": sig.channel_type,
                "description": sig.description,
                "unit": sig.unit,
            }
            if sig.channel:
                entry["channel"] = sig.channel
                entry["range_low"] = sig.range_low
                entry["range_high"] = sig.range_high
            else:
                entry["node"] = sig.node_base
            data["signals"].append(entry)

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"信号映射已导出: {filepath}")
