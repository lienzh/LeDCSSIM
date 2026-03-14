# -*- coding: utf-8 -*-
"""
函数功能块
折线插值、多项式
"""
from typing import List, Tuple
from .base import Block


class LinearInterp(Block):
    """
    折线插值（函数表/Function Generator）

    根据一组 (x, y) 点对做线性插值，超出范围按端点值输出。
    常用于非线性特性建模（如阀门流量特性、风量-氧量关系）。

    参数:
        points: [(x1,y1), (x2,y2), ...] 必须按 x 升序

    示例:
        # 阀门流量特性: 开度(%) → 流量(t/h)
        valve_curve = LinearInterp(
            points=[(0, 0), (20, 50), (50, 200), (80, 380), (100, 450)],
            name="调节阀流量特性"
        )
        flow = valve_curve.calc(valve_position, dt=0.2)
    """

    def __init__(self, points: List[Tuple[float, float]], name: str = ""):
        super().__init__(name)
        if len(points) < 2:
            raise ValueError("折线插值至少需要2个点")
        # 按 x 排序
        self.points = sorted(points, key=lambda p: p[0])

    def calc(self, x: float, dt: float) -> float:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]

        # 超出左端
        if x <= xs[0]:
            self._output = ys[0]
            return self._output

        # 超出右端
        if x >= xs[-1]:
            self._output = ys[-1]
            return self._output

        # 查找区间并插值
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                ratio = (x - xs[i]) / (xs[i + 1] - xs[i])
                self._output = ys[i] + ratio * (ys[i + 1] - ys[i])
                return self._output

        self._output = ys[-1]
        return self._output


class Polynomial(Block):
    """
    多项式函数  y = a0 + a1*x + a2*x² + ...

    参数:
        coeffs: 系数列表 [a0, a1, a2, ...]

    示例:
        # 烟气含氧量与过量空气系数关系
        o2_curve = Polynomial(coeffs=[0.0, 0.21, -0.005], name="氧量特性")
        o2 = o2_curve.calc(excess_air, dt=0.2)
    """

    def __init__(self, coeffs: List[float], name: str = ""):
        super().__init__(name)
        if not coeffs:
            raise ValueError("系数列表不能为空")
        self.coeffs = coeffs

    def calc(self, x: float, dt: float) -> float:
        self._output = sum(c * x**i for i, c in enumerate(self.coeffs))
        return self._output
