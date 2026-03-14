# -*- coding: utf-8 -*-
"""
定时器功能块
通延时、断延时、脉冲定时器、计数器
用于仿真DCS中的时序逻辑。
"""
from .base import Block


class TimerOn(Block):
    """
    通延时定时器（TON）

    输入持续为 1 达到延时时间后，输出变为 1。
    输入变为 0 时立即复位，输出变为 0。
    常用于确认信号稳定（如压力持续低于阈值N秒才报警）。

    参数:
        delay_time: 延时时间, 秒

    示例:
        ton = TimerOn(delay_time=3.0, name="低压确认延时")
        confirmed = ton.calc(low_pressure_flag, dt=0.2)
    """

    def __init__(self, delay_time: float = 1.0, name: str = ""):
        super().__init__(name)
        self.delay_time = delay_time
        self._elapsed = 0.0  # 已计时时间

    def calc(self, x: float, dt: float) -> float:
        if x > 0.5:
            self._elapsed += dt
            if self._elapsed >= self.delay_time:
                self._output = 1.0
        else:
            # 输入为 0，立即复位
            self._elapsed = 0.0
            self._output = 0.0
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._elapsed = 0.0


class TimerOff(Block):
    """
    断延时定时器（TOFF）

    输入为 1 时输出立即为 1。
    输入变为 0 后，输出保持 1，延时到达后输出变为 0。
    若延时期间输入再次为 1，则复位计时。
    常用于防止信号抖动（如泵停止后延时关冷却水）。

    参数:
        delay_time: 延时时间, 秒

    示例:
        toff = TimerOff(delay_time=5.0, name="冷却水延时关")
        cooling = toff.calc(pump_running, dt=0.2)
    """

    def __init__(self, delay_time: float = 1.0, name: str = ""):
        super().__init__(name)
        self.delay_time = delay_time
        self._elapsed = 0.0
        self._timing = False  # 是否正在计时

    def calc(self, x: float, dt: float) -> float:
        if x > 0.5:
            # 输入有效，立即输出 1，复位计时
            self._output = 1.0
            self._elapsed = 0.0
            self._timing = False
        else:
            if not self._timing and self._output > 0.5:
                # 输入刚变为 0，开始计时
                self._timing = True
                self._elapsed = 0.0

            if self._timing:
                self._elapsed += dt
                if self._elapsed >= self.delay_time:
                    self._output = 0.0
                    self._timing = False
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._elapsed = 0.0
        self._timing = False


class TimerPulse(Block):
    """
    脉冲定时器（TP）

    检测到上升沿后，输出 1 持续指定时间，期间输入变化不影响输出。
    脉冲结束后才能响应下一个上升沿。
    常用于生成固定宽度的触发脉冲。

    参数:
        pulse_time: 脉冲持续时间, 秒

    示例:
        tp = TimerPulse(pulse_time=2.0, name="吹灰脉冲")
        trigger = tp.calc(start_signal, dt=0.2)
    """

    def __init__(self, pulse_time: float = 1.0, name: str = ""):
        super().__init__(name)
        self.pulse_time = pulse_time
        self._elapsed = 0.0
        self._pulsing = False  # 是否正在输出脉冲
        self._x_prev = 0.0    # 上一步输入，用于边沿检测

    def calc(self, x: float, dt: float) -> float:
        # 上升沿检测: 上一步 <= 0.5 且当前 > 0.5
        rising_edge = (x > 0.5) and (self._x_prev <= 0.5)

        if self._pulsing:
            # 脉冲进行中
            self._elapsed += dt
            if self._elapsed >= self.pulse_time:
                self._output = 0.0
                self._pulsing = False
        elif rising_edge:
            # 检测到上升沿，开始脉冲
            self._output = 1.0
            self._pulsing = True
            self._elapsed = 0.0

        self._x_prev = x
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._elapsed = 0.0
        self._pulsing = False
        self._x_prev = 0.0


class Counter(Block):
    """
    计数器（上升沿计数）

    对输入信号的上升沿进行计数，输出 = 计数值 / 预设值（归一化到 0~1）。
    达到预设值后输出保持 1.0，可通过 reset() 清零。
    direction 控制加计数或减计数。

    参数:
        preset:    预设值（目标计数）
        direction: 计数方向，1=加计数，-1=减计数

    示例:
        cnt = Counter(preset=10, direction=1, name="启动步序计数")
        progress = cnt.calc(step_pulse, dt=0.2)
    """

    def __init__(self, preset: int = 10, direction: int = 1, name: str = ""):
        super().__init__(name)
        self.preset = max(1, preset)  # 预设值至少为 1
        self.direction = 1 if direction >= 0 else -1
        self._count = 0       # 当前计数值
        self._x_prev = 0.0    # 上一步输入

    def calc(self, x: float, dt: float) -> float:
        """
        计数一步

        Args:
            x: 输入信号（检测上升沿）
            dt: 步长, 秒
        Returns:
            归一化输出 count/preset (0.0 ~ 1.0)
        """
        # 上升沿检测
        rising_edge = (x > 0.5) and (self._x_prev <= 0.5)

        if rising_edge:
            self._count += self.direction
            # 限制范围 [0, preset]
            self._count = max(0, min(self.preset, self._count))

        self._output = self._count / self.preset
        self._x_prev = x
        return self._output

    def reset(self, value: float = 0.0):
        """复位计数器"""
        super().reset(value)
        self._count = 0
        self._x_prev = 0.0
