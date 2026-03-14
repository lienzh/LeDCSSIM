# -*- coding: utf-8 -*-
"""
基本功能块
惯性环节、积分器、死区、速率限制、限幅器
"""
from .base import Block


class Inertia(Block):
    """
    一阶惯性环节  G(s) = K / (Ts + 1)

    用于模拟锅炉、汽机等设备的响应延迟。

    参数:
        K: 增益（稳态放大倍数）
        T: 时间常数, 秒

    示例:
        boiler = Inertia(K=1.2, T=30, name="锅炉蓄热")
        pressure = boiler.calc(fuel_flow, dt=0.2)
    """

    def __init__(self, K: float = 1.0, T: float = 1.0, name: str = ""):
        super().__init__(name)
        self.K = K  # 增益
        self.T = T  # 时间常数, 秒

    def calc(self, x: float, dt: float) -> float:
        if self.T <= 0:
            self._output = self.K * x
        else:
            # 一阶差分: y(k) = y(k-1) + dt/T * (K*x - y(k-1))
            alpha = dt / (self.T + dt)
            self._output = self._output + alpha * (self.K * x - self._output)
        return self._output


class Integrator(Block):
    """
    积分环节  G(s) = K / s

    输出是输入的时间积分。带上下限防止积分饱和。

    参数:
        K:    积分增益
        low:  输出下限
        high: 输出上限

    示例:
        level = Integrator(K=0.1, low=0, high=100, name="汽包水位积分")
        water_level = level.calc(flow_diff, dt=0.2)
    """

    def __init__(self, K: float = 1.0, low: float = -1e6, high: float = 1e6,
                 name: str = ""):
        super().__init__(name)
        self.K = K
        self.low = low
        self.high = high

    def calc(self, x: float, dt: float) -> float:
        self._output += self.K * x * dt
        # 限幅，防止积分饱和
        self._output = max(self.low, min(self.high, self._output))
        return self._output


class DeadZone(Block):
    """
    死区环节

    输入在 [-zone, +zone] 范围内时输出为 0，超出部分按原值输出。

    参数:
        zone: 死区半宽

    示例:
        dz = DeadZone(zone=0.5, name="压力偏差死区")
        error_out = dz.calc(pressure_error, dt=0.2)
    """

    def __init__(self, zone: float = 0.0, name: str = ""):
        super().__init__(name)
        self.zone = abs(zone)

    def calc(self, x: float, dt: float) -> float:
        if abs(x) <= self.zone:
            self._output = 0.0
        elif x > 0:
            self._output = x - self.zone
        else:
            self._output = x + self.zone
        return self._output


class RateLimiter(Block):
    """
    速率限制器

    限制输出的变化速率，不超过指定的上升/下降速率。

    参数:
        rate_up:   最大上升速率, 单位/秒
        rate_down: 最大下降速率, 单位/秒（正值）

    示例:
        rl = RateLimiter(rate_up=5.0, rate_down=3.0, name="负荷变化率限制")
        limited = rl.calc(load_demand, dt=0.2)
    """

    def __init__(self, rate_up: float = 1e6, rate_down: float = 1e6,
                 name: str = ""):
        super().__init__(name)
        self.rate_up = abs(rate_up)
        self.rate_down = abs(rate_down)
        self._initialized = False

    def calc(self, x: float, dt: float) -> float:
        if not self._initialized:
            self._output = x
            self._initialized = True
            return self._output

        delta = x - self._output
        max_up = self.rate_up * dt
        max_down = self.rate_down * dt

        if delta > max_up:
            self._output += max_up
        elif delta < -max_down:
            self._output -= max_down
        else:
            self._output = x
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._initialized = False


class Limiter(Block):
    """
    限幅器

    将输入限制在 [low, high] 范围内。

    参数:
        low:  下限
        high: 上限

    示例:
        lim = Limiter(low=0, high=100, name="阀位限幅")
        valve_pos = lim.calc(raw_output, dt=0.2)
    """

    def __init__(self, low: float = 0.0, high: float = 100.0, name: str = ""):
        super().__init__(name)
        self.low = low
        self.high = high

    def calc(self, x: float, dt: float) -> float:
        self._output = max(self.low, min(self.high, x))
        return self._output
