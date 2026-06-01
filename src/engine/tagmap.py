# -*- coding: utf-8 -*-
"""
TagMap - 测点映射

职责:
- 加载 config/tagmap.yaml
- tag(模型层内部名) ↔ OPC node_id 双向翻译
- 物理量 ↔ 工程量换算(MVP:线性变换 range_low/high)

tagmap.yaml schema:
    tags:
      - tag: valve_cmd            # 模型内部名(必填)
        opc_node: "ns=0;s=DPU3013.SH0015.VLV01_CMD.PV"  # OPC 节点(必填)
        direction: in             # in/out
        dtype: Float              # Float / Bool / Int
        range_low: 0              # 工程量下限(可选)
        range_high: 100           # 工程量上限(可选)
        physical_unit: "%"        # 物理量纲(注释用,可选)
        write_mode: ai_hr_lr      # AI 通道走 HR/LR 双写(可选)
        channel_base: "ns=0;s=DPU3013.HW.AI010605"  # ai_hr_lr 模式必填
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

import yaml

logger = logging.getLogger(__name__)


class TagMap:
    """测点映射表"""

    def __init__(self, entries: Optional[List[Dict[str, Any]]] = None):
        # _by_tag: {tag: entry_dict}
        self._by_tag: Dict[str, Dict[str, Any]] = {}
        if entries:
            for e in entries:
                self.add(e)

    @classmethod
    def from_yaml(cls, path: str) -> "TagMap":
        p = Path(path)
        if not p.exists():
            logger.warning(f"tagmap 文件不存在,使用空表: {path}")
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        entries = doc.get("tags", [])
        return cls(entries)

    def add(self, entry: Dict[str, Any]) -> None:
        tag = entry.get("tag")
        if not tag:
            raise ValueError(f"tagmap 条目缺少 tag 字段: {entry}")
        if "opc_node" not in entry:
            raise ValueError(f"tagmap 条目 {tag} 缺少 opc_node 字段")
        self._by_tag[tag] = dict(entry)

    def get(self, tag: str) -> Dict[str, Any]:
        if tag not in self._by_tag:
            raise KeyError(f"tag 未在 tagmap 中定义: {tag}")
        return self._by_tag[tag]

    def has(self, tag: str) -> bool:
        return tag in self._by_tag

    def tag_to_node(self, tag: str) -> str:
        return self.get(tag)["opc_node"]

    def all_tags(self) -> List[str]:
        return list(self._by_tag.keys())

    def tags_by_direction(self, direction: str) -> List[str]:
        return [t for t, e in self._by_tag.items()
                if e.get("direction") == direction]

    # ----- 量纲换算 -----
    # MVP 阶段:如果 entry 里没有量程信息,就是直通;有量程才做线性映射。
    # 当前简化:tagmap 里的物理量和工程量相同(都是工程师写的工程量),
    # 等真有需要时再扩展(比如 0-20mA → 0-100%)。

    def engineering_to_physical(self, tag: str, eng_val: Any) -> Any:
        """读 OPC 后:工程量 → 物理量(MVP 直通)"""
        return eng_val

    def physical_to_engineering(self, tag: str, phys_val: Any) -> Any:
        """写 OPC 前:物理量 → 工程量(MVP 直通)"""
        return phys_val
