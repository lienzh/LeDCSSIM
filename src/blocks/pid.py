# -*- coding: utf-8 -*-
"""
PID 控制器功能块
PI控制器、PID控制器、PD控制器
用于仿真DCS中的标准PID控制算法。
"""
from .base import Block


class PIController(Block):
    """
    标准PI控制器

    采用位置式算法，带抗积分饱和（clamping）和手自动无扰切换。
    控制器输出 = Kp * (error + 1/Ti * ∫error·dt)

    参数:
        Kp:          比例增益
        Ti:          积分时间, 秒（Ti=0 时无积分作用）
        output_low:  输出下限
        output_high: 输出上限

    示例:
        pi = PIController(Kp=2.0, Ti=60, output_low=0, output_high=100, name="汽压PI")
        pi.set_sp(16.0)  # 设定值 16 MPa
        valve = pi.calc(current_pressure, dt=0.2)
    """

    def __init__(self, Kp: float = 1.0, Ti: float = 10.0,
                 output_low: float = 0.0, output_high: float = 100.0,
                 name: str = ""):
        super().__init__(name)
        self.Kp = Kp           # 比例增益
        self.Ti = Ti           # 积分时间, 秒
        self.output_low = output_low    # 输出下限
        self.output_high = output_high  # 输出上限
        self._sp = 0.0         # 设定值
        self._integral = 0.0   # 积分累积量
        self._auto = True      # 自动模式标志

    def set_sp(self, sp: float):
        """设置设定值"""
        self._sp = sp

    def set_auto(self, auto: bool):
        """
        切换手自动模式

        切换到自动时，积分项反算以实现无扰切换。
        """
        if auto and not self._auto:
            # 手动→自动: 反算积分项，使输出不跳变
            error = self._sp - self._output  # 使用上次 calc 时的 PV 不可得，用当前输出反算
            self._integral = self._output / self.Kp - error if self.Kp != 0 else 0.0
        self._auto = auto

    def set_manual_output(self, value: float):
        """手动模式下直接设置输出"""
        if not self._auto:
            self._output = max(self.output_low, min(self.output_high, value))
            # 同步积分项，为切自动做准备
            error = self._sp - 0.0  # 手动时 PV 未知，保守处理
            self._integral = self._output / self.Kp if self.Kp != 0 else 0.0

    def calc(self, pv: float, dt: float) -> float:
        """
        计算一步

        Args:
            pv: 过程变量（被控量测量值）
            dt: 步长, 秒
        Returns:
            控制器输出
        """
        if not self._auto:
            return self._output

        error = self._sp - pv

        # 比例项
        p_term = error

        # 积分项（梯形积分）
        if self.Ti > 0 and dt > 0:
            integral_increment = error * dt / self.Ti
            # 抗积分饱和: 输出已饱和且积分方向一致时，停止积分
            trial_output = self.Kp * (p_term + self._integral + integral_increment)
            if trial_output > self.output_high and integral_increment > 0:
                pass  # 不累加积分
            elif trial_output < self.output_low and integral_increment < 0:
                pass  # 不累加积分
            else:
                self._integral += integral_increment

        self._output = self.Kp * (p_term + self._integral)

        # 输出限幅
        self._output = max(self.output_low, min(self.output_high, self._output))
        return self._output

    def reset(self, value: float = 0.0):
        """复位控制器"""
        super().reset(value)
        self._integral = 0.0
        self._sp = 0.0


class PIDController(Block):
    """
    标准PID控制器

    采用位置式算法，微分作用于PV（非偏差），带抗积分饱和和微分滤波。
    控制器输出 = Kp * (error + 1/Ti * ∫error·dt + Td * d(PV_filtered)/dt)

    微分对PV而非偏差求导，避免设定值阶跃引起微分冲击。

    参数:
        Kp:          比例增益
        Ti:          积分时间, 秒（Ti=0 时无积分作用）
        Td:          微分时间, 秒
        Tf:          微分滤波时间常数, 秒（通常 Tf = Td/5 ~ Td/10）
        output_low:  输出下限
        output_high: 输出上限

    示例:
        pid = PIDController(Kp=1.5, Ti=30, Td=5, Tf=1, name="汽温PID")
        pid.set_sp(540.0)  # 设定值 540°C
        spray = pid.calc(current_temp, dt=0.2)
    """

    def __init__(self, Kp: float = 1.0, Ti: float = 10.0,
                 Td: float = 0.0, Tf: float = 0.1,
                 output_low: float = 0.0, output_high: float = 100.0,
                 name: str = ""):
        super().__init__(name)
        self.Kp = Kp           # 比例增益
        self.Ti = Ti           # 积分时间, 秒
        self.Td = Td           # 微分时间, 秒
        self.Tf = Tf           # 微分滤波时间常数, 秒
        self.output_low = output_low
        self.output_high = output_high
        self._sp = 0.0         # 设定值
        self._integral = 0.0   # 积分累积量
        self._pv_prev = 0.0    # 上一步PV（微分用）
        self._d_filtered = 0.0  # 滤波后的微分项
        self._auto = True
        self._initialized = False

    def set_sp(self, sp: float):
        """设置设定值"""
        self._sp = sp

    def set_auto(self, auto: bool):
        """切换手自动模式，自动时无扰切换"""
        if auto and not self._auto:
            error = self._sp - self._pv_prev
            if self.Kp != 0:
                self._integral = self._output / self.Kp - error
            else:
                self._integral = 0.0
            self._d_filtered = 0.0
        self._auto = auto

    def set_manual_output(self, value: float):
        """手动模式下设置输出"""
        if not self._auto:
            self._output = max(self.output_low, min(self.output_high, value))
            if self.Kp != 0:
                self._integral = self._output / self.Kp
            else:
                self._integral = 0.0

    def calc(self, pv: float, dt: float) -> float:
        """
        计算一步

        Args:
            pv: 过程变量
            dt: 步长, 秒
        Returns:
            控制器输出
        """
        if not self._initialized:
            self._pv_prev = pv
            self._initialized = True

        if not self._auto:
            self._pv_prev = pv
            return self._output

        error = self._sp - pv

        # 比例项
        p_term = error

        # 积分项
        if self.Ti > 0 and dt > 0:
            integral_increment = error * dt / self.Ti
            trial_output = self.Kp * (p_term + self._integral + integral_increment)
            if trial_output > self.output_high and integral_increment > 0:
                pass
            elif trial_output < self.output_low and integral_increment < 0:
                pass
            else:
                self._integral += integral_increment

        # 微分项（对PV求导，带一阶滤波）
        if self.Td > 0 and dt > 0:
            dpv = -(pv - self._pv_prev) / dt  # 负号: 微分对PV，取反使方向与偏差一致
            if self.Tf > 0:
                alpha = dt / (self.Tf + dt)
                self._d_filtered += alpha * (self.Td * dpv - self._d_filtered)
            else:
                self._d_filtered = self.Td * dpv
            d_term = self._d_filtered
        else:
            d_term = 0.0

        self._output = self.Kp * (p_term + self._integral + d_term)

        # 输出限幅
        self._output = max(self.output_low, min(self.output_high, self._output))

        self._pv_prev = pv
        return self._output

    def reset(self, value: float = 0.0):
        """复位控制器"""
        super().reset(value)
        self._integral = 0.0
        self._pv_prev = 0.0
        self._d_filtered = 0.0
        self._sp = 0.0
        self._initialized = False


class PDController(Block):
    """
    PD控制器

    无积分作用的比例微分控制器，微分作用于PV，带滤波。
    常用于快速响应场合或串级控制的内环。

    参数:
        Kp: 比例增益
        Td: 微分时间, 秒
        Tf: 微分滤波时间常数, 秒

    示例:
        pd = PDController(Kp=3.0, Td=2.0, Tf=0.4, name="减温水PD")
        output = pd.calc(steam_temp, dt=0.2)
    """

    def __init__(self, Kp: float = 1.0, Td: float = 1.0, Tf: float = 0.1,
                 name: str = ""):
        super().__init__(name)
        self.Kp = Kp
        self.Td = Td           # 微分时间, 秒
        self.Tf = Tf           # 微分滤波时间常数, 秒
        self._sp = 0.0
        self._pv_prev = 0.0
        self._d_filtered = 0.0
        self._initialized = False

    def set_sp(self, sp: float):
        """设置设定值"""
        self._sp = sp

    def calc(self, pv: float, dt: float) -> float:
        """
        计算一步

        Args:
            pv: 过程变量
            dt: 步长, 秒
        Returns:
            控制器输出
        """
        if not self._initialized:
            self._pv_prev = pv
            self._initialized = True

        error = self._sp - pv

        # 微分项（对PV求导，带滤波）
        if self.Td > 0 and dt > 0:
            dpv = -(pv - self._pv_prev) / dt
            if self.Tf > 0:
                alpha = dt / (self.Tf + dt)
                self._d_filtered += alpha * (self.Td * dpv - self._d_filtered)
            else:
                self._d_filtered = self.Td * dpv
            d_term = self._d_filtered
        else:
            d_term = 0.0

        self._output = self.Kp * (error + d_term)

        self._pv_prev = pv
        return self._output

    def reset(self, value: float = 0.0):
        """复位控制器"""
        super().reset(value)
        self._pv_prev = 0.0
        self._d_filtered = 0.0
        self._sp = 0.0
        self._initialized = False
