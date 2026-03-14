# -*- coding: utf-8 -*-
"""
信号处理功能块
采样保持、斜坡发生器、变化率、量程转换、偏置增益、
偏差、绝对值、除法、开方、最大值、最小值
用于仿真DCS中的信号调理和运算功能。
"""
import math
from .base import Block


class SampleHold(Block):
    """
    采样保持

    当触发信号上升沿到来时，采样输入值并保持输出，直到下一个触发。

    示例:
        sh = SampleHold(name="负荷采样")
        result = sh.calc_multi([load_signal, trigger], dt=0.2)
    """

    def __init__(self, name: str = ""):
        super().__init__(name)
        self._trigger_prev = 0.0  # 上一步触发信号

    def calc(self, x: float, dt: float) -> float:
        """单输入直接透传"""
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """
        采样保持

        Args:
            inputs: [input_value, trigger]
        """
        if len(inputs) < 2:
            return self._output

        value = inputs[0]
        trigger = inputs[1]

        # 上升沿检测
        if trigger > 0.5 and self._trigger_prev <= 0.5:
            self._output = value

        self._trigger_prev = trigger
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._trigger_prev = 0.0


class RampGenerator(Block):
    """
    斜坡信号发生器

    以指定速率向目标值渐变。输入 x 可动态覆盖目标值。
    常用于负荷升降指令的平滑过渡。

    参数:
        rate:   变化速率, 单位/秒
        target: 初始目标值

    示例:
        ramp = RampGenerator(rate=5.0, target=600, name="负荷升降")
        demand = ramp.calc(new_target, dt=0.2)
    """

    def __init__(self, rate: float = 1.0, target: float = 0.0, name: str = ""):
        super().__init__(name)
        self.rate = abs(rate)
        self.target = target

    def calc(self, x: float, dt: float) -> float:
        """
        计算一步

        Args:
            x: 目标值（动态覆盖 self.target）
            dt: 步长, 秒
        """
        self.target = x
        delta = self.target - self._output
        max_change = self.rate * dt

        if abs(delta) <= max_change:
            self._output = self.target
        elif delta > 0:
            self._output += max_change
        else:
            self._output -= max_change

        return self._output


class Gradient(Block):
    """
    变化率计算（微分）

    输出 = (当前值 - 上一步值) / dt
    常用于监测参数变化速率（如汽压变化率、温度变化率）。

    示例:
        grad = Gradient(name="汽压变化率")
        dp_dt = grad.calc(main_steam_pressure, dt=0.2)
    """

    def __init__(self, name: str = ""):
        super().__init__(name)
        self._x_prev = 0.0
        self._initialized = False

    def calc(self, x: float, dt: float) -> float:
        if not self._initialized:
            self._x_prev = x
            self._initialized = True
            self._output = 0.0
        elif dt > 0:
            self._output = (x - self._x_prev) / dt
        else:
            self._output = 0.0

        self._x_prev = x
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._x_prev = 0.0
        self._initialized = False


class ScaleConvert(Block):
    """
    量程转换（线性标定）

    将输入从 [in_low, in_high] 线性映射到 [out_low, out_high]。
    clamp=True 时输出限制在 [out_low, out_high] 范围内。

    参数:
        in_low:   输入量程下限
        in_high:  输入量程上限
        out_low:  输出量程下限
        out_high: 输出量程上限
        clamp:    是否限幅

    示例:
        sc = ScaleConvert(in_low=4, in_high=20, out_low=0, out_high=100, name="mA→%")
        percent = sc.calc(current_ma, dt=0.2)
    """

    def __init__(self, in_low: float = 0.0, in_high: float = 100.0,
                 out_low: float = 0.0, out_high: float = 100.0,
                 clamp: bool = True, name: str = ""):
        super().__init__(name)
        self.in_low = in_low
        self.in_high = in_high
        self.out_low = out_low
        self.out_high = out_high
        self.clamp = clamp

    def calc(self, x: float, dt: float) -> float:
        in_range = self.in_high - self.in_low
        if abs(in_range) < 1e-12:
            self._output = self.out_low
            return self._output

        ratio = (x - self.in_low) / in_range
        self._output = self.out_low + ratio * (self.out_high - self.out_low)

        if self.clamp:
            lo = min(self.out_low, self.out_high)
            hi = max(self.out_low, self.out_high)
            self._output = max(lo, min(hi, self._output))

        return self._output


class BiasGain(Block):
    """
    偏置增益  y = K * x + B

    参数:
        K: 增益（乘法系数）
        B: 偏置（加法常数）

    示例:
        bg = BiasGain(K=1.1, B=-5.0, name="温度修正")
        corrected = bg.calc(raw_temp, dt=0.2)
    """

    def __init__(self, K: float = 1.0, B: float = 0.0, name: str = ""):
        super().__init__(name)
        self.K = K  # 增益
        self.B = B  # 偏置

    def calc(self, x: float, dt: float) -> float:
        self._output = self.K * x + self.B
        return self._output


class Deviation(Block):
    """
    偏差计算  y = x - setpoint

    参数:
        setpoint: 设定值（参考值）

    示例:
        dev = Deviation(setpoint=540.0, name="汽温偏差")
        error = dev.calc(steam_temp, dt=0.2)
    """

    def __init__(self, setpoint: float = 0.0, name: str = ""):
        super().__init__(name)
        self.setpoint = setpoint

    def calc(self, x: float, dt: float) -> float:
        self._output = x - self.setpoint
        return self._output


class AbsValue(Block):
    """
    绝对值  y = |x|

    示例:
        av = AbsValue(name="偏差绝对值")
        abs_err = av.calc(error, dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = abs(x)
        return self._output


class Divider(Block):
    """
    除法器（带零保护）

    输出 = 分子 / 分母。分母接近零时输出 0，防止除零异常。

    示例:
        div = Divider(name="效率计算")
        efficiency = div.calc_multi([output_power, input_power], dt=0.2)
    """

    def __init__(self, zero_threshold: float = 1e-10, name: str = ""):
        super().__init__(name)
        self.zero_threshold = abs(zero_threshold)

    def calc(self, x: float, dt: float) -> float:
        """单输入直接透传"""
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """
        除法运算

        Args:
            inputs: [numerator, denominator]
        """
        if len(inputs) < 2:
            return self._output

        numerator = inputs[0]
        denominator = inputs[1]

        if abs(denominator) < self.zero_threshold:
            self._output = 0.0
        else:
            self._output = numerator / denominator

        return self._output


class SquareRoot(Block):
    """
    开方运算  y = sqrt(max(0, x))

    负值输入保护，取 0 后开方。
    常用于差压流量计算（流量 ∝ √ΔP）。

    示例:
        sq = SquareRoot(name="差压开方")
        flow = sq.calc(delta_p, dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = math.sqrt(max(0.0, x))
        return self._output


class MaxValue(Block):
    """
    最大值选择（多输入）

    输出所有输入中的最大值。

    示例:
        mv = MaxValue(name="最高温度")
        max_temp = mv.calc_multi([t1, t2, t3, t4], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入取最大值"""
        self._output = max(inputs)
        return self._output


class MinValue(Block):
    """
    最小值选择（多输入）

    输出所有输入中的最小值。

    示例:
        mv = MinValue(name="最低温度")
        min_temp = mv.calc_multi([t1, t2, t3, t4], dt=0.2)
    """

    def calc(self, x: float, dt: float) -> float:
        self._output = x
        return self._output

    def calc_multi(self, inputs: list, dt: float) -> float:
        """多输入取最小值"""
        self._output = min(inputs)
        return self._output
