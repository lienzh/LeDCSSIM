# -*- coding: utf-8 -*-
"""
MVP 基础功能块

- CON           常数源(无输入,输出固定值)
- DirectThrough 直通(输入即输出,可选一阶滞后防 DCS 误判抖动)
- FirstOrder    一阶惯性 G(s) = K/(Ts+1)
"""
from typing import Dict, Any, Optional
from .base import Block


class CON(Block):
    """
    常数块:0 输入 1 输出,输出参数 value

    params:
        value: float - 输出常数值
    """
    INPUTS = []
    OUTPUTS = ["out"]
    STATEFUL = False

    def step(self, inputs: Dict[str, float], dt: float) -> Dict[str, float]:
        return {"out": float(self.params.get("value", 0.0))}


class DirectThrough(Block):
    """
    直通块:输入 → (可选一阶滞后) → 输出

    用于阀门指令回写场景:DCS 出指令 → 读到 → 直接当反馈回写。
    可选一阶滞后(T > 0)防止瞬时回写导致 DCS 逻辑误判/抖动。

    params:
        T: float = 0  - 一阶滞后时间常数(秒);0 = 立即直通
    """
    INPUTS = ["in"]
    OUTPUTS = ["out"]

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        super().__init__(name, params)
        self._y = 0.0
        # 有滞后才算有状态
        self.STATEFUL = float(self.params.get("T", 0.0)) > 0

    def step(self, inputs: Dict[str, float], dt: float) -> Dict[str, float]:
        x = float(inputs.get("in", 0.0))
        T = float(self.params.get("T", 0.0))
        if T <= 0:
            self._y = x
        else:
            # 一阶差分: y(k) = y(k-1) + dt/(T+dt) * (x - y(k-1))
            alpha = dt / (T + dt)
            self._y = self._y + alpha * (x - self._y)
        return {"out": self._y}

    def reset(self, state: Optional[Any] = None) -> None:
        self._y = float(state) if state is not None else 0.0


class FirstOrder(Block):
    """
    一阶惯性环节  G(s) = K / (T*s + 1)

    用于流量模拟、设备响应延迟等场景。

    params:
        K: float = 1  - 增益(稳态放大倍数)
        T: float      - 时间常数(秒);T<=0 退化为纯比例 y = K*x
    """
    INPUTS = ["in"]
    OUTPUTS = ["out"]
    STATEFUL = True

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        super().__init__(name, params)
        self._y = 0.0

    def step(self, inputs: Dict[str, float], dt: float) -> Dict[str, float]:
        x = float(inputs.get("in", 0.0))
        K = float(self.params.get("K", 1.0))
        T = float(self.params.get("T", 0.0))
        if T <= 0:
            self._y = K * x
        else:
            alpha = dt / (T + dt)
            self._y = self._y + alpha * (K * x - self._y)
        return {"out": self._y}

    def reset(self, state: Optional[Any] = None) -> None:
        self._y = float(state) if state is not None else 0.0


# 块类型注册表 - YAML 里的 type 字段就是这里的 key
BLOCK_REGISTRY = {
    "CON": CON,
    "DirectThrough": DirectThrough,
    "FirstOrder": FirstOrder,
}
