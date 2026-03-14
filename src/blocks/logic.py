# -*- coding: utf-8 -*-
"""
逻辑功能块
与门、或门、非门、异或门、SR/RS触发器、比较器
用于仿真DCS中的联锁逻辑和条件判断。
"""
from .base import Block


class ANDGate(Block):
    """
    与门（多输入）

    所有输入均 > 0.5 时输出 1.0，否则输出 0.0。

    示例:
        gate = ANDGate(name="联锁条件与门")
        result = gate.calc_multi([cond1, cond2, cond3], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        """单输入: > 0.5 输出 1.0"""
        self._output = 1.0 if x > 0.5 else 0.0
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入与逻辑"""
        self._output = 1.0 if all(v > 0.5 for v in inputs) else 0.0
        return self._output


class ORGate(Block):
    """
    或门（多输入）

    任一输入 > 0.5 时输出 1.0，全部 <= 0.5 时输出 0.0。

    示例:
        gate = ORGate(name="报警或门")
        result = gate.calc_multi([alarm1, alarm2], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = 1.0 if x > 0.5 else 0.0
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入或逻辑"""
        self._output = 1.0 if any(v > 0.5 for v in inputs) else 0.0
        return self._output


class NOTGate(Block):
    """
    非门

    输入 <= 0.5 输出 1.0，输入 > 0.5 输出 0.0。

    示例:
        inv = NOTGate(name="信号取反")
        result = inv.calc(switch_state, dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = 1.0 if x <= 0.5 else 0.0
        return self._output


class XORGate(Block):
    """
    异或门（2输入）

    两个输入不同时输出 1.0，相同时输出 0.0。

    示例:
        xor = XORGate(name="差异检测")
        result = xor.calc_multi([signal_a, signal_b], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = 1.0 if x > 0.5 else 0.0
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """两输入异或逻辑"""
        if len(inputs) < 2:
            self._output = 1.0 if inputs and inputs[0] > 0.5 else 0.0
        else:
            a = inputs[0] > 0.5
            b = inputs[1] > 0.5
            self._output = 1.0 if a != b else 0.0
        return self._output


class FlipFlopSR(Block):
    """
    SR触发器（置位优先）

    Set 和 Reset 同时有效时，输出为 1（置位优先）。
    常用于 DCS 中的联锁保持逻辑。

    示例:
        sr = FlipFlopSR(name="MFT锁存")
        result = sr.calc_multi([set_signal, reset_signal], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = 1.0 if x > 0.5 else 0.0
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """
        SR触发器

        Args:
            inputs: [set, reset]
        """
        if len(inputs) < 2:
            return self._output

        s = inputs[0] > 0.5  # 置位信号
        r = inputs[1] > 0.5  # 复位信号

        if s:
            self._output = 1.0  # 置位优先
        elif r:
            self._output = 0.0
        # 都无效时保持

        return self._output


class FlipFlopRS(Block):
    """
    RS触发器（复位优先）

    Set 和 Reset 同时有效时，输出为 0（复位优先）。
    常用于安全联锁，确保复位信号能可靠清除锁存。

    示例:
        rs = FlipFlopRS(name="安全联锁")
        result = rs.calc_multi([set_signal, reset_signal], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = 1.0 if x > 0.5 else 0.0
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """
        RS触发器

        Args:
            inputs: [set, reset]
        """
        if len(inputs) < 2:
            return self._output

        s = inputs[0] > 0.5  # 置位信号
        r = inputs[1] > 0.5  # 复位信号

        if r:
            self._output = 0.0  # 复位优先
        elif s:
            self._output = 1.0
        # 都无效时保持

        return self._output


class Comparator(Block):
    """
    模拟量比较器（带迟滞）

    输入超过阈值+迟滞时输出 1.0，低于阈值-迟滞时输出 0.0。
    迟滞防止在阈值附近频繁翻转。

    参数:
        threshold:  比较阈值
        hysteresis: 迟滞量（单侧）

    示例:
        comp = Comparator(threshold=16.0, hysteresis=0.2, name="汽压高报警")
        alarm = comp.calc(main_steam_pressure, dt=0.2)
    """

    def __init__(self, threshold: float = 0.0, hysteresis: float = 0.0,
                 name: str = ""):
        super().__init__(name)
        self.threshold = threshold
        self.hysteresis = abs(hysteresis)

    def calc(self, x: float, dt: float) -> float:
        if self._output < 0.5:
            # 当前为关: 超过上门限才翻转
            if x > self.threshold + self.hysteresis:
                self._output = 1.0
        else:
            # 当前为开: 低于下门限才翻转
            if x < self.threshold - self.hysteresis:
                self._output = 0.0
        return self._output
