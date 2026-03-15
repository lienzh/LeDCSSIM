# -*- coding: utf-8 -*-
"""
仿真引擎命令行入口

用法:
    py -3.12 -m src.sim_engine                       # 离线演示
    py -3.12 -m src.sim_engine online                # 在线闭环
    py -3.12 -m src.sim_engine online --duration 60  # 指定运行时长
    py -3.12 -m src.sim_engine offline --duration 30 # 离线运行
"""
import asyncio
import argparse
import json
import logging
from pathlib import Path

from .engine import SimEngine
from .graph_runner import GraphRunner


def main():
    parser = argparse.ArgumentParser(description="DCS 协调控制仿真引擎")
    parser.add_argument("mode", nargs="?", default="offline",
                        choices=["offline", "online"],
                        help="运行模式: offline(离线) / online(连接OPC)")
    parser.add_argument("--duration", "-d", type=float, default=60.0,
                        help="运行时长, 秒 (默认 60)")
    parser.add_argument("--step", "-s", type=float, default=0.2,
                        help="仿真步长, 秒 (默认 0.2)")
    parser.add_argument("--model", "-m", type=str, default="CCS_model",
                        help="IB 层模型名称 (默认 CCS_model)")
    parser.add_argument("--config", "-c", type=str,
                        default="config/opc_mapping.yaml",
                        help="OPC 映射配置文件路径")
    parser.add_argument("--output", "-o", type=str, default="",
                        help="数据导出路径 (默认 data/{mode}_run.csv)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    output_path = args.output or f"data/{args.mode}_run.csv"

    # 加载画布组态
    model_path = Path("config/models") / f"{args.model}.json"
    if not model_path.exists():
        logger.error(f"模型文件不存在: {model_path}")
        logger.info("请先在 Web 界面 IB 层保存组态，或使用 --model 指定模型名称")
        return

    with open(model_path, "r", encoding="utf-8") as f:
        ib_json = json.load(f)

    runner = GraphRunner()
    runner.load(ib_json)

    engine = SimEngine(runner, step_size=args.step)

    if args.mode == "online":
        from ..opc_client import OPCClient, SignalMapping
        opc_client = OPCClient("opc.tcp://localhost:9440")
        mapping = SignalMapping.from_yaml(args.config)
        asyncio.run(engine.start(duration=args.duration,
                                 opc_client=opc_client,
                                 mapping=mapping))
    else:
        asyncio.run(engine.run_offline(duration=args.duration))

    # 导出数据
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    engine.export_data(output_path)
    logger.info(f"数据已保存: {output_path}")


if __name__ == "__main__":
    main()
