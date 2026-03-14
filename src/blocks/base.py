# -*- coding: utf-8 -*-
"""
功能块基类
所有仿真功能块的抽象基类，定义统一接口。
"""
from abc import ABC, abstractmethod


class Block(ABC):
    """
    功能块基类

    所有功能块遵循统一接口:
        output = block.calc(input, dt)

    其中:
        input: 输入值（float 或多输入 tuple）
        dt:    仿真步长, 秒
        output: 输出值 float
    """

    def __init__(self, name: str = ""):
        self.name = name
        self._output = 0.0  # 当前输出值

    @property
    def output(self) -> float:
        """当前输出值"""
        return self._output

    @abstractmethod
    def calc(self, x: float, dt: float) -> float:
        """
        计算一步

        Args:
            x: 输入值
            dt: 步长, 秒
        Returns:
            输出值
        """
        pass

    def reset(self, value: float = 0.0):
        """复位输出到指定值"""
        self._output = value
