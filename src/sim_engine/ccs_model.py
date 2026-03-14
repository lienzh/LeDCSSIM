# -*- coding: utf-8 -*-
"""
协调控制系统 (CCS) 被控对象模型

参考 modelref/CCS.png 框图实现。
模拟锅炉-汽机系统的动态特性：煤量/阀位 → 主汽压力/发电机功率。

物理原理：
    - 给煤量 → 燃烧放热 → 锅炉蓄热 → 主汽压力变化
    - 蒸汽流量 = 调门开度 × 主汽压力 × 折算系数（非线性）
    - 能量平衡: 热量输入 - 蒸汽带走热量 = 蓄热变化 → 压力变化
    - 发电功率 ∝ 蒸汽流量

框图信号流：
    μb → [LAG] → ×K1 ──(+)──→ ×K3 → [积分] → ×K4 → Pst
                        │(-)                          ↑
    μt ──→ [×Pst] → ×K2 ──→ [LAG] → Ne              │
              ↑                                        │
              └────────────────────────────────────────┘

参数说明（默认值基于 660MW 超临界机组）：
    K1 = 2.4    煤量热值系数: MW/(t/h)，250t/h 对应 600MW 热输入
    K2 = 51.3   蒸汽流量折算系数: MW/(阀位×MPa)
    K3 = 0.00015 锅炉响应速度: (MPa/s)/MW，决定压力响应快慢
    K4 = 1.0    汽压修正系数（直接输出 MPa）
    A1 = 30.0   压力饱和高限, MPa
    T_coal = 60  给煤惯性, s（制粉+燃烧延迟）
    T_power = 15 功率惯性, s（汽机响应）

稳态校验（额定工况）：
    μb=250 t/h, μt=0.7, Pst=16.7 MPa, Ne=600 MW
    热输入 = K1×250 = 2.4×250 = 600 MW
    蒸汽出力 = K2×0.7×16.7 = 51.3×11.69 ≈ 600 MW → 平衡 ✓
"""
from .model import SimModel
from ..blocks import Inertia, Integrator, Limiter


class CCSPlantModel(SimModel):
    """
    CCS 被控对象模型（2 输入 2 输出）

    输入（从 DCS 读取或固定值）:
        coal_flow       — 给煤量（BTU修正后）, t/h
        valve_position  — 汽机调门综合开度, 0~1

    输出（写入 DCS AI 通道）:
        main_steam_pressure — 主蒸汽压力, MPa
        unit_power          — 发电机功率, MW
    """

    # ── 可调参数（默认值对应 660MW 超临界机组）──
    K1 = 2.4          # 煤量热值系数, MW/(t/h)
    K2 = 51.3         # 蒸汽流量折算系数, MW/(阀位·MPa)
    K3 = 0.00015      # 锅炉响应速度 (积分增益), (MPa/s)/MW
    K4 = 1.0          # 汽压修正系数
    A1 = 30.0         # 压力饱和高限, MPa
    T_COAL = 60.0     # 给煤惯性时间常数, s
    T_POWER = 15.0    # 功率响应惯性时间常数, s

    # ── 额定工况（用于 reset 初始化）──
    RATED_COAL = 250.0      # 额定煤量, t/h
    RATED_VALVE = 0.7       # 额定调门开度
    RATED_PRESSURE = 16.7   # 额定主汽压力, MPa
    RATED_POWER = 600.0     # 额定功率, MW

    def setup(self):
        # ── 输入信号 ──
        self.add_input("coal_flow", "给煤量(BTU修正后)", "t/h",
                        default=self.RATED_COAL)
        self.add_input("valve_position", "汽机调门综合开度", "",
                        default=self.RATED_VALVE)

        # ── 输出信号 ──
        self.add_output("main_steam_pressure", "主蒸汽压力", "MPa",
                         default=self.RATED_PRESSURE)
        self.add_output("unit_power", "发电机功率", "MW",
                         default=self.RATED_POWER)

        # ── 功能块 ──

        # 给煤惯性（制粉系统 + 燃烧延迟）
        self.coal_lag = Inertia(K=1.0, T=self.T_COAL, name="给煤惯性")

        # 锅炉蓄热积分（能量平衡 → 压力）
        # 输入: 热量不平衡(MW), 输出: 主汽压力(MPa)
        self.pressure_integ = Integrator(
            K=self.K3, low=0.0, high=self.A1 / self.K4,
            name="锅炉蓄热积分")

        # 功率响应惯性（汽机侧延迟）
        self.power_lag = Inertia(K=1.0, T=self.T_POWER, name="功率响应惯性")

        # 压力限幅
        self.pressure_limiter = Limiter(
            low=0.0, high=self.A1, name="压力限幅")

    def step(self, inputs, dt):
        coal = inputs["coal_flow"]           # 给煤量, t/h
        valve = inputs["valve_position"]     # 调门开度, 0~1

        # 当前压力（上一步输出，用于非线性计算）
        current_pressure = self.pressure_integ.output * self.K4
        if current_pressure <= 0:
            current_pressure = self.RATED_PRESSURE  # 防止初始为0

        # 1. 热量输入 = K1 × LAG(μb)
        coal_lagged = self.coal_lag.calc(coal, dt)
        heat_in = self.K1 * coal_lagged  # MW

        # 2. 蒸汽流量 = K2 × μt × Pst（非线性交叉项）
        steam_flow = self.K2 * valve * current_pressure  # MW

        # 3. 能量平衡 → 压力积分
        energy_imbalance = heat_in - steam_flow  # MW
        pressure_raw = self.pressure_integ.calc(energy_imbalance, dt)

        # 4. 压力输出
        pressure = self.pressure_limiter.calc(pressure_raw * self.K4, dt)

        # 5. 功率输出 = LAG(蒸汽流量)
        power = self.power_lag.calc(steam_flow, dt)

        return {
            "main_steam_pressure": pressure,
            "unit_power": power,
        }

    def reset(self, initial_values=None):
        """重置到稳态工况"""
        coal = self.RATED_COAL
        valve = self.RATED_VALVE
        pressure = self.RATED_PRESSURE
        power = self.RATED_POWER

        if initial_values:
            coal = initial_values.get("coal_flow", coal)
            valve = initial_values.get("valve_position", valve)
            pressure = initial_values.get("main_steam_pressure", pressure)
            power = initial_values.get("unit_power", power)

        # 各功能块初始化到稳态
        self.coal_lag.reset(coal)                         # LAG 输出 = 稳态煤量
        self.pressure_integ.reset(pressure / self.K4)     # 积分器状态 = 压力/K4
        self.power_lag.reset(power)                       # 功率 LAG 输出 = 稳态功率
        self.pressure_limiter.reset(pressure)
