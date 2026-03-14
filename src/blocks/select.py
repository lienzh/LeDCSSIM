# -*- coding: utf-8 -*-
"""
选择器功能块
高选、低选、开关切换
"""
from .base import Block


class HighSelect(Block):
    """
    高选器

    输出多个输入中的最大值。

    示例:
        hs = HighSelect(name="压力高选")
        result = hs.calc_multi([pressure1, pressure2, pressure3], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        """单输入直接透传"""
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入取最大值"""
        self._output = max(inputs)
        return self._output


class LowSelect(Block):
    """
    低选器

    输出多个输入中的最小值。

    示例:
        ls = LowSelect(name="阀位低选")
        result = ls.calc_multi([valve1, valve2], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入取最小值"""
        self._output = min(inputs)
        return self._output


class Switch(Block):
    """
    二选一开关

    根据条件选择输出 A 或 B。

    参数:
        threshold: 切换阈值。condition > threshold 时选 A，否则选 B

    示例:
        sw = Switch(name="自动/手动切换")
        output = sw.calc_switch(auto_value, manual_value, is_auto, dt=0.2)
    """

    def __init__(self, threshold: float = 0.5, name: str = ""):
        super().__init__(name)
        self.threshold = threshold

    def calc(self, x: float, dt: float) -> float:
        self._output = x
        return self._output

    def calc_switch(self, a: float, b: float, condition: float,
                    dt: float) -> float:
        """
        条件切换

        Args:
            a: condition > threshold 时的输出
            b: condition <= threshold 时的输出
            condition: 条件值
            dt: 步长
        """
        self._output = a if condition > self.threshold else b
        return self._output
