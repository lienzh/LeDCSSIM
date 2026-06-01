# -*- coding: utf-8 -*-
"""
适配层抽象基类

对上层(engine/models)只暴露批量读写接口,协议细节(asyncua/ns=…;s=…)
全部封在子类内。换协议时只替换子类实现。
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any


class Adapter(ABC):
    """协议无关的批量读写接口"""

    @abstractmethod
    async def connect(self) -> None:
        """建立连接;失败时重试,达到上限抛错"""

    @abstractmethod
    async def disconnect(self) -> None:
        """优雅断开 - 必须释放对端 session,避免下次连接残留"""

    @abstractmethod
    async def read_batch(self, tags: List[str]) -> Dict[str, Any]:
        """
        批量读取
        Args:
            tags: 测点 tag 列表(模型层内部名,不是 OPC 节点 ID)
        Returns:
            {tag: value} 字典,读取失败的 tag 值为 None
        """

    @abstractmethod
    async def write_batch(self, values: Dict[str, Any]) -> None:
        """
        批量写入
        Args:
            values: {tag: value} 字典(模型层内部名)
        """
