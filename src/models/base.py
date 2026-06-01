# -*- coding: utf-8 -*-
"""
Block 抽象基类

所有原子模型遵循同一接口,引擎只认这个接口:
    outputs = block.step(inputs, dt)
    block.reset(state=None)

输入/输出都是 {port_name: value} 字典,支持多输入多输出。
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class Block(ABC):
    """功能块基类"""

    # 子类覆写:输入端口名列表
    INPUTS: List[str] = []
    # 子类覆写:输出端口名列表
    OUTPUTS: List[str] = []
    # 子类覆写:是否有状态(用于代数环检测 - 有状态块可打破环路)
    STATEFUL: bool = False

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.params = params or {}

    @property
    def inputs(self) -> List[str]:
        return list(self.INPUTS)

    @property
    def outputs(self) -> List[str]:
        return list(self.OUTPUTS)

    @abstractmethod
    def step(self, inputs: Dict[str, float], dt: float) -> Dict[str, float]:
        """
        执行一步仿真
        Args:
            inputs: {port: value} - 输入端口的当前值
            dt:     步长, 秒
        Returns:
            {port: value} - 输出端口的新值
        """

    def reset(self, state: Optional[Any] = None) -> None:
        """复位内部状态;state=None 走默认冷态"""
        # 默认无状态,子类按需覆写
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} params={self.params}>"
