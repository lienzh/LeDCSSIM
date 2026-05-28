# -*- coding: utf-8 -*-
"""
声明式 IL 运行器

读取 io_pairing.yaml，按设备模板逐步计算反馈值。
与 GraphRunner（画布）并行，承担"简单赋值"类 IO 配对。

P1 仅实现 analog 模板（模拟调节门：指令→阀位）。
transform: direct（直接赋值） | inertia（一阶惯性，模拟行程时间） | scale（折算 gain/bias）
"""
import logging
from pathlib import Path
from typing import Dict, List

import yaml

from ..blocks import Inertia

logger = logging.getLogger(__name__)


class PairingRunner:
    def __init__(self):
        self._analog: List[dict] = []   # [{cmd, fb, transform, _inertia}]
        self._cmd_tags: List[str] = []
        self._fb_tags: List[str] = []

    # ── 加载 ──────────────────────────────────────────────

    def load(self, pairing_yaml_path):
        """从 YAML 文件加载配对表"""
        path = Path(pairing_yaml_path)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.load_dict(data)

    def load_dict(self, data: dict):
        """从已解析的 dict 加载（便于测试）"""
        self._analog = []
        for item in data.get("analog", []):
            transform = item.get("transform", {"type": "direct"})
            entry = {
                "cmd": item["cmd"],
                "fb": item["fb"],
                "transform": transform,
                "_inertia": None,
            }
            if transform.get("type") == "inertia":
                entry["_inertia"] = Inertia(K=1.0, T=float(transform.get("T", 1.0)))
            self._analog.append(entry)
        self._cmd_tags = [e["cmd"] for e in self._analog]
        self._fb_tags = [e["fb"] for e in self._analog]
        logger.info(f"加载 IO 配对: analog {len(self._analog)} 组")

    # ── 查询 ──────────────────────────────────────────────

    def get_command_tags(self) -> List[str]:
        """需从 DPU 读取的指令点（模型输入）"""
        return list(self._cmd_tags)

    def get_feedback_tags(self) -> List[str]:
        """需写回 DPU 的反馈点（模型输出）"""
        return list(self._fb_tags)

    # ── 执行 ──────────────────────────────────────────────

    def step(self, commands: Dict[str, float], dt: float) -> Dict[str, float]:
        """一步：读指令 → 套 transform → 产出反馈 {fb_tag: 值}"""
        out: Dict[str, float] = {}
        for e in self._analog:
            cmd_val = float(commands.get(e["cmd"], 0.0))
            t = e["transform"]
            ttype = t.get("type", "direct")
            if ttype == "inertia":
                fb = e["_inertia"].calc(cmd_val, dt)
            elif ttype == "scale":
                fb = cmd_val * float(t.get("gain", 1.0)) + float(t.get("bias", 0.0))
            else:  # direct（含未知类型兜底）
                fb = cmd_val
            out[e["fb"]] = fb
        return out

    def reset(self):
        """复位所有有状态 transform"""
        for e in self._analog:
            if e["_inertia"] is not None:
                e["_inertia"].reset(0.0)
