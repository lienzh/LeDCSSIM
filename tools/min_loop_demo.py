# -*- coding: utf-8 -*-
"""
最小闭环示例：煤量开环扰动 → CCS模型 → OPC写入功率 → OPC读回功率

流程：
  1. 煤量=200 t/h, 调门=0.7, 稳态运行 120s
  2. 煤量阶跃到 240 t/h, 运行 180s, 观察功率变化
  3. 煤量回到 200 t/h, 运行 180s, 观察功率恢复

用法:
  py -3.12 tools/min_loop_demo.py
"""
import asyncio
import logging
import sys
import os
import time

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sim_engine.ccs_model import CCSPlantModel
from src.opc_client.client import OPCClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 参数 ──
OPC_URL = "opc.tcp://localhost:9440"
AI_CHANNEL = "ns=0;s=DPU3013.HW.AI010605"  # 发电机功率 AI 通道
PV_NODE = f"{AI_CHANNEL}.PV"

DT = 0.2          # 仿真步长, s
VALVE = 0.7        # 调门固定

# 煤量调度：(起始时间s, 煤量t/h)
COAL_SCHEDULE = [
    (0,   200.0),   # 初始 200 t/h
    (120, 240.0),   # 120s 时阶跃到 240
    (300, 200.0),   # 300s 时回到 200
]
TOTAL_TIME = 480.0  # 总仿真时间, s


def get_coal(t: float) -> float:
    """根据时间返回当前煤量"""
    coal = COAL_SCHEDULE[0][1]
    for ts, val in COAL_SCHEDULE:
        if t >= ts:
            coal = val
    return coal


async def main():
    # ── 初始化模型 ──
    model = CCSPlantModel(name="CCS被控对象")

    # 稳态初始化: coal=200, valve=0.7
    # heat = K1*200 = 480 MW, pressure = 480/(K2*0.7) ≈ 13.37 MPa, power = 480 MW
    init_pressure = (model.K1 * 200.0) / (model.K2 * VALVE)
    init_power = model.K1 * 200.0
    model.reset({
        "coal_flow": 200.0,
        "valve_position": VALVE,
        "main_steam_pressure": init_pressure,
        "unit_power": init_power,
    })
    logger.info(f"模型初始化: 压力={init_pressure:.2f} MPa, 功率={init_power:.1f} MW")

    # ── 连接 OPC ──
    client = OPCClient(OPC_URL)
    await client.connect(retry_count=5, retry_interval=2.0)
    logger.info("OPC UA 连接成功")

    # 先写一次初始值，等待 PV 生效
    await client.write_ai_channel(AI_CHANNEL, float(init_power))
    await asyncio.sleep(1.5)  # AI 通道 HR/LR 写入后约 1s 生效

    # ── 仿真循环 ──
    t = 0.0
    step_count = 0
    prev_coal = 200.0

    logger.info("=" * 60)
    logger.info("开始仿真循环")
    logger.info(f"{'时间':>6s}  {'煤量':>8s}  {'模型功率':>10s}  {'模型压力':>10s}  {'OPC读回功率':>12s}")
    logger.info("-" * 60)

    try:
        while t <= TOTAL_TIME:
            loop_start = time.perf_counter()

            coal = get_coal(t)
            if coal != prev_coal:
                logger.info(f">>> 煤量阶跃: {prev_coal:.0f} → {coal:.0f} t/h <<<")
                prev_coal = coal

            # 模型计算
            outputs = model.step({"coal_flow": coal, "valve_position": VALVE}, DT)
            power = outputs["unit_power"]
            pressure = outputs["main_steam_pressure"]

            # 写入 OPC AI 通道
            await client.write_ai_channel(AI_CHANNEL, float(power))

            # 读回 PV（验证闭环）
            pv_readback = await client.read_value(PV_NODE)

            # 每 5s 打印一次
            if step_count % 25 == 0:
                logger.info(
                    f"{t:6.1f}s  {coal:8.1f}  {power:10.2f} MW  {pressure:10.2f} MPa  {pv_readback:12.2f} MW"
                )

            t += DT
            step_count += 1

            # 实时节拍控制
            elapsed = time.perf_counter() - loop_start
            sleep_time = DT - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        await client.disconnect()
        logger.info("仿真结束")


if __name__ == "__main__":
    asyncio.run(main())
