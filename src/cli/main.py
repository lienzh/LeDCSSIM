# -*- coding: utf-8 -*-
"""
CLI 入口

用法:
    py -3.12 -m src.cli run --duration 30
    py -3.12 -m src.cli run --online --duration 30
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import yaml

from src.engine import GraphRunner, AlgebraicLoopError, TagMap, DataRecorder


def _load_sim_settings(path: str) -> dict:
    """加载 sim_settings.yaml — 只读 dt 和 output_csv.

    OPC URL 不在这里读, 统一从 opc_endpoints.yaml 拿 (跟 viewer 共享端点).
    """
    defaults = {"dt": 0.2, "output_csv": "data/run.csv"}
    p = Path(path)
    if not p.exists():
        return defaults
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    out = dict(defaults)
    if "dt" in raw:
        out["dt"] = raw["dt"]
    elif "engine" in raw and "step_size" in raw["engine"]:
        out["dt"] = raw["engine"]["step_size"]
    if "output_csv" in raw:
        out["output_csv"] = raw["output_csv"]
    elif "recorder" in raw and "output_dir" in raw["recorder"]:
        out["output_csv"] = str(Path(raw["recorder"]["output_dir"]) / "run.csv")
    return out


def _resolve_opc_url(cli_url: Optional[str], log: logging.Logger) -> str:
    """OPC URL 解析: --opc-url > opc_endpoints.yaml.

    opc_endpoints.yaml 是唯一真相源 — 跟 viewer 顶栏 [本地]/[VM] 共享, 切换即生效.
    """
    if cli_url:
        log.info(f"OPC URL: {cli_url}  (--opc-url 覆盖)")
        return cli_url
    from src.viewer.runtime import get_endpoint_config
    cfg = get_endpoint_config()
    mode_label = "VM" if cfg["mode"] == "vm" else "本地"
    log.info(f"OPC URL: {cfg['url']}  (opc_endpoints.yaml, mode={mode_label})")
    return cfg["url"]


def _cmd_run(args: argparse.Namespace) -> int:
    # Windows PowerShell 默认 GBK 解码 stdout,中文日志显示乱码;强制 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("cli.run")

    settings = _load_sim_settings(args.settings)
    dt: float = float(settings.get("dt", 0.2))
    output_csv: str = settings.get("output_csv", "data/run.csv")
    opc_url: str = _resolve_opc_url(getattr(args, "opc_url", None), log)

    recorder = DataRecorder()

    try:
        runner = GraphRunner.from_yaml(
            args.models, args.connections, dt=dt, recorder=recorder
        )
    except AlgebraicLoopError as e:
        log.error(str(e))
        return 2
    except Exception as e:
        log.error(f"加载失败: {e}")
        return 1

    log.info(f"块: {list(runner.blocks.keys())}")
    log.info(f"执行顺序: {runner.order}")

    n_steps = int(args.duration / dt)
    log.info(f"将执行 {n_steps} 步 (dt={dt}s, duration={args.duration}s, "
             f"模式={'online' if args.online else 'offline'})")

    if args.online:
        log.warning("在线模式骨架已就位,但 OPC 端到端尚未在 MVP 阶段验证。"
                    "需要 NTVDPU 运行,且 tagmap.yaml 必须配置正确的 OPC 节点。")
        return asyncio.run(_run_online(runner, n_steps, opc_url, args.tagmap,
                                       output_csv, log))
    else:
        _run_offline(runner, n_steps, output_csv, log)
        return 0


def _run_offline(runner: GraphRunner, n_steps: int, output_csv: str,
                 log: logging.Logger) -> None:
    log.info("离线模式开始 - 跳过 OPC,纯本地步进")
    for i in range(n_steps):
        runner.step_once()
        if (i + 1) % max(1, n_steps // 5) == 0:
            log.info(f"  进度: {i+1}/{n_steps} 步, t={runner.t:.1f}s")
    log.info(runner.recorder.summary() if runner.recorder else "(无 recorder)")
    if runner.recorder is not None:
        runner.recorder.to_csv(output_csv)


async def _run_online(runner: GraphRunner, n_steps: int, opc_url: str,
                      tagmap_path: str, output_csv: str,
                      log: logging.Logger) -> int:
    from src.adapter import OPCUAAdapter
    tagmap = TagMap.from_yaml(tagmap_path)
    if not tagmap.all_tags():
        log.error("tagmap 为空 — 在线模式必须配置 tagmap.yaml")
        return 3

    adapter = OPCUAAdapter(opc_url, tagmap)
    try:
        await adapter.connect()
    except Exception as e:
        log.error(f"OPC 连接失败: {e}")
        return 4

    try:
        in_tags = tagmap.tags_by_direction("in")
        out_tags = tagmap.tags_by_direction("out")
        for i in range(n_steps):
            # 读
            if in_tags:
                vals = await adapter.read_batch(in_tags)
                for tag, v in vals.items():
                    if v is None:
                        continue
                    # tag 命名约定: 'block.port' 直接映射到 GraphRunner 的端口
                    if "." in tag:
                        bname, port = tag.split(".", 1)
                        # 把 OPC 读到的值放进 _outputs[(bname, port)]
                        # 让下游块在 step 时取到这个值
                        runner._outputs[(bname, port)] = float(v)
            # 算
            runner.step_once()
            # 写
            if out_tags:
                write_payload = {}
                for tag in out_tags:
                    if "." in tag:
                        bname, port = tag.split(".", 1)
                        write_payload[tag] = runner.get_output(bname, port)
                await adapter.write_batch(write_payload)

            if (i + 1) % max(1, n_steps // 5) == 0:
                log.info(f"  进度: {i+1}/{n_steps} 步, t={runner.t:.1f}s")
    finally:
        await adapter.disconnect()

    log.info(runner.recorder.summary() if runner.recorder else "(无 recorder)")
    if runner.recorder is not None:
        runner.recorder.to_csv(output_csv)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="src.cli", description="DCS 仿真平台 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="运行仿真")
    run.add_argument("--models", default="config/models.yaml")
    run.add_argument("--connections", default="config/connections.yaml")
    run.add_argument("--tagmap", default="config/tagmap.yaml")
    run.add_argument("--settings", default="config/sim_settings.yaml")
    run.add_argument("--duration", type=float, default=30.0,
                     help="仿真时长(秒),默认 30s")
    run.add_argument("--online", action="store_true",
                     help="在线模式连接 NTVDPU OPC UA Server")
    run.add_argument("--opc-url", dest="opc_url", default=None,
                     help="显式覆盖 OPC UA Server URL "
                          "(优先级最高, 不指定则读 config/opc_endpoints.yaml)")
    run.set_defaults(func=_cmd_run)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
