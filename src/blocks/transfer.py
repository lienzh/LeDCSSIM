# -*- coding: utf-8 -*-
"""
传递函数功能块
超前滞后、二阶环节
"""
from .base import Block


class LeadLag(Block):
    """
    超前滞后环节  G(s) = (T1·s + 1) / (T2·s + 1)

    T1 > T2 时为超前补偿，T1 < T2 时为滞后补偿。
    常用于控制器前馈补偿。

    参数:
        T1: 超前时间常数, 秒
        T2: 滞后时间常数, 秒
        K:  增益

    示例:
        comp = LeadLag(T1=10, T2=30, K=1.0, name="燃料前馈补偿")
        compensated = comp.calc(fuel_demand, dt=0.2)
    """

    def __init__(self, T1: float = 1.0, T2: float = 1.0, K: float = 1.0,
                 name: str = ""):
        super().__init__(name)
        self.T1 = T1  # 超前时间常数
        self.T2 = T2  # 滞后时间常数
        self.K = K
        self._x_prev = 0.0  # 上一步输入

    def calc(self, x: float, dt: float) -> float:
        if self.T2 <= 0:
            self._output = self.K * x
        else:
            alpha = dt / (self.T2 + dt)
            # 超前项: T1 * dx/dt
            dx = (x - self._x_prev) / dt if dt > 0 else 0.0
            target = self.K * (x + self.T1 * dx)
            self._output = self._output + alpha * (target - self._output)

        self._x_prev = x
        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._x_prev = 0.0


class SecondOrder(Block):
    """
    二阶惯性环节  G(s) = K / (T1·T2·s² + (T1+T2)·s + 1)

    用于模拟具有振荡特性的对象（如过热汽温）。

    参数:
        K:  增益
        T1: 第一时间常数, 秒
        T2: 第二时间常数, 秒

    示例:
        sh_temp = SecondOrder(K=1.0, T1=60, T2=30, name="过热器温度响应")
        temp = sh_temp.calc(spray_flow, dt=0.2)
    """

    def __init__(self, K: float = 1.0, T1: float = 1.0, T2: float = 1.0,
                 name: str = ""):
        super().__init__(name)
        self.K = K
        self.T1 = T1
        self.T2 = T2
        # 内部状态: 两个串联的一阶惯性
        self._mid = 0.0  # 中间状态

    def calc(self, x: float, dt: float) -> float:
        # 串联两个一阶惯性: 第一级 T1, 第二级 T2
        if self.T1 > 0:
            alpha1 = dt / (self.T1 + dt)
            self._mid = self._mid + alpha1 * (self.K * x - self._mid)
        else:
            self._mid = self.K * x

        if self.T2 > 0:
            alpha2 = dt / (self.T2 + dt)
            self._output = self._output + alpha2 * (self._mid - self._output)
        else:
            self._output = self._mid

        return self._output

    def reset(self, value: float = 0.0):
        super().reset(value)
        self._mid = value
