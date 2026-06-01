# -*- coding: utf-8 -*-
"""
OPC UA 适配层实现

包装现有 src.opc_client.OPCClient,加上 tagmap 翻译:
    上层只见 tag → 本层内部转 OPC node_id → 调 OPCClient 批量读写
"""
import logging
from typing import Dict, List, Any

from src.opc_client.client import OPCClient

logger = logging.getLogger(__name__)


class OPCUAAdapter:
    """
    OPC UA 适配器

    职责:
    - 管理 OPCClient 生命周期(connect/disconnect)
    - 把模型层的 tag 翻译成 OPC node_id
    - AI 通道写入走 HR/LR 双写方案(由 OPCClient.write_ai_channel 封装)
    - PV 读取容忍 UncertainInitialValue 状态(由 OPCClient.read_value 封装)
    """

    def __init__(self, url: str, tagmap):
        """
        Args:
            url: OPC UA Server 地址, 如 opc.tcp://localhost:9440
            tagmap: TagMap 实例(src.engine.tagmap.TagMap),用于 tag ↔ node 翻译
        """
        self.url = url
        self.tagmap = tagmap
        self._client = OPCClient(url)

    async def connect(self) -> None:
        await self._client.connect()  # 默认 10 次重试,间隔 3s

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def read_batch(self, tags: List[str]) -> Dict[str, Any]:
        """
        批量读 tag(物理量层面 — 已做工程量→物理量换算)
        """
        if not tags:
            return {}
        node_ids = [self.tagmap.tag_to_node(t) for t in tags]
        raw_values = await self._client.read_values(node_ids)
        out: Dict[str, Any] = {}
        for tag, raw in zip(tags, raw_values):
            if raw is None:
                out[tag] = None
                continue
            # 工程量 → 物理量换算(MVP 阶段就是直读,future 在 tagmap 里做线性变换)
            out[tag] = self.tagmap.engineering_to_physical(tag, raw)
        return out

    async def write_batch(self, values: Dict[str, Any]) -> None:
        """
        批量写 tag(物理量层面 → 内部做换算 + 走 AI HR/LR 方案)
        """
        if not values:
            return
        # 分两组:AI 通道走 HR/LR 双写;其它(如功能块输出)用普通 write
        ai_writes: Dict[str, float] = {}      # {channel_base: target_value}
        normal_writes: Dict[str, Any] = {}    # {node_id: value}

        for tag, val in values.items():
            if val is None:
                continue
            entry = self.tagmap.get(tag)
            phys_val = self.tagmap.physical_to_engineering(tag, val)
            if entry.get("write_mode") == "ai_hr_lr":
                # AI 通道,走 HR/LR 双写;channel_base 是去掉 .PV 后缀的路径
                ai_writes[entry["channel_base"]] = float(phys_val)
            else:
                normal_writes[entry["opc_node"]] = phys_val

        if ai_writes:
            await self._client.write_ai_channels(ai_writes)
        if normal_writes:
            await self._client.write_values(normal_writes)
