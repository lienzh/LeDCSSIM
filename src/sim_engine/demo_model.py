# -*- coding: utf-8 -*-
"""
演示模型 — 主汽压力单回路

用于验证仿真引擎流程的简单模型。
模拟：调节阀开度 → 燃料量变化 → 锅炉蓄热 → 主汽压力变化

闭环逻辑：
    DCS 侧: PID 控制器根据压力偏差调节阀位
    模型侧: 阀位 → 燃料量 → 蒸汽产量 → 压力（本模型计算此部分）
"""
from .model import SimModel
from ..blocks import Inertia, Integrator, Limiter, RateLimiter


class PressureLoopDemo(SimModel):
    """
    主汽压力单回路演示模型

    输入（从 DCS 读取）:
        valve_position — 调节阀开度, %

    输出（写入 DCS）:
        unit_power — 机组功率, MW

    简化物理模型:
        阀位 → [阀门响应] → 燃料量 → [锅炉蓄热惯性] → 热量 → [汽压积分] → 压力
    """

    def setup(self):
        # ── 输入信号（来自 DCS 控制器的输出）──
        self.add_input("valve_position", "调节阀开度", "%", default=50.0)

        # ── 输出信号（模型计算结果，写入 DCS）──
        self.add_output("unit_power", "机组功率", "MW", default=500.0)

        # ── 功能块 ──
        # 阀门动作响应（阀门从收到指令到实际动作的延迟）
        self.valve_response = Inertia(K=1.0, T=3.0, name="阀门响应")

        # 燃料量变化率限制（给煤机不能瞬间改变出力）
        self.fuel_rate_limit = RateLimiter(
            rate_up=5.0, rate_down=5.0, name="燃料变化率限制")  # 5%/s

        # 锅炉蓄热惯性（燃料变化 → 蒸汽产量变化的大惯性）
        self.boiler_inertia = Inertia(K=10.0, T=120.0, name="锅炉蓄热惯性")
        # K=10: 阀位每变化1%，功率变化10MW; T=120s: 锅炉惯性2分钟

        # 功率限幅（不能超出机组能力范围）
        self.power_limiter = Limiter(low=0.0, high=990.0, name="功率限幅")

    def step(self, inputs, dt):
        valve = inputs["valve_position"]

        # 阀门响应
        valve_actual = self.valve_response.calc(valve, dt)

        # 燃料变化率限制
        fuel = self.fuel_rate_limit.calc(valve_actual, dt)

        # 锅炉蓄热惯性 → 功率
        power = self.boiler_inertia.calc(fuel, dt)

        # 限幅
        power = self.power_limiter.calc(power, dt)

        return {"unit_power": power}

    def reset(self, initial_values=None):
        """重置到稳态工况"""
        # 默认 50% 负荷稳态
        init_valve = 50.0
        init_power = 500.0

        if initial_values:
            init_valve = initial_values.get("valve_position", init_valve)
            init_power = initial_values.get("unit_power", init_power)

        # 各功能块复位到稳态值
        self.valve_response.reset(init_valve)
        self.fuel_rate_limit.reset(init_valve)
        self.boiler_inertia.reset(init_power)
        self.power_limiter.reset(init_power)
