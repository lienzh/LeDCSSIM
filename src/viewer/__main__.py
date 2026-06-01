# -*- coding: utf-8 -*-
"""使 `py -3.12 -m src.viewer` 可执行 — 启动 Web 仪表板"""
import argparse
import sys

from .app import configure, run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="src.viewer", description="DCS 仿真组态查看器(只读)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5002)
    p.add_argument("--models", default=None,
                   help="默认 config/models.generated.yaml")
    p.add_argument("--connections", default=None)
    p.add_argument("--tagmap", default=None)
    p.add_argument("--csv", default=None, help="默认 data/run.csv")
    args = p.parse_args(argv)
    configure(args.models, args.connections, args.tagmap, args.csv)
    run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
