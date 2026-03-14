# -*- coding: utf-8 -*-
"""
仿真引擎命令行入口

用法:
    py -3.12 -m src.sim_engine                       # 离线演示 (CCS 模型)
    py -3.12 -m src.sim_engine online                # 在线闭环
    py -3.12 -m src.sim_engine online --duration 60  # 指定运行时长
    py -3.12 -m src.sim_engine offline --duration 30  # 离线调试
"""
import asyncio
import argparse
import logging
import sys

from .engine import SimEngine
from .ccs_model import CCSPlantModel
from ..opc_client import SignalMapping


def main():
    parser = argparse.ArgumentParser(description="DCS 协调控制仿真引擎")
    parser.add_argument("mode", nargs="?", default="offline",
                        choices=["offline", "online"],
                        help="运行模式: offline(离线调试) / online(连接OPC)")
    parser.add_argument("--duration", "-d", type=float, default=60.0,
                        help="运行时长, 秒 (默认 60)")
    parser.add_argument("--step", "-s", type=float, default=0.2,
                        help="仿真步长, 秒 (默认 0.2)")
    parser.add_argument("--config", "-c", type=str,
                        default="config/opc_mapping.yaml",
                        help="OPC 映射配置文件路径")
    parser.add_argument("--output", "-o", type=str, default="",
                        help="数据导出路径 (默认 data/{mode}_run.csv)")
    args = parser.parse_args()

    # 日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    # 输出路径
    output_path = args.output or f"data/{args.mode}_run.csv"

    # 创建 CCS 模型
    model = CCSPlantModel("CCS被控对象模型")

    # 加载信号映射
    mapping = SignalMapping.from_yaml(args.config)

    # 创建引擎
    engine = SimEngine(model, mapping, step_size=args.step)

    if args.mode == "online":
        # 在线闭环
        initial = {
            "main_steam_pressure": CCSPlantModel.RATED_PRESSURE,
            "unit_power": CCSPlantModel.RATED_POWER,
        }
        asyncio.run(engine.start(duration=args.duration,
                                 initial_values=initial))
    else:
        # 离线调试：模拟煤量阶跃
        def coal_step_input(t):
            """模拟煤量阶跃: 0~10s 额定250t/h, 之后增加10%到275t/h"""
            coal = 250.0 if t < 10.0 else 275.0
            return {
                "coal_flow": coal,
                "valve_position": 0.7,  # 调门固定
            }

        asyncio.run(engine.run_offline(duration=args.duration,
                                       input_func=coal_step_input))

    # 导出数据
    engine.export_data(output_path)
    logger.info(f"数据已保存: {output_path}")


if __name__ == "__main__":
    main()
