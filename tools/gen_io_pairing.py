# -*- coding: utf-8 -*-
"""
IO 配对生成器 CLI

用法:
    py -3.12 tools/gen_io_pairing.py [点表目录] [输出文件]
    默认: YQ3SIM-IO -> config/io_pairing.generated.yaml

产物需人工 diff 后合并到 config/io_pairing.yaml（避免覆盖人工微调）。
"""
import sys
from pathlib import Path

import yaml

# 允许从仓库根运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.sim_engine.io_pairing_gen import generate


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "YQ3SIM-IO"
    out = sys.argv[2] if len(sys.argv) > 2 else "config/io_pairing.generated.yaml"
    data = generate(src)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)
    print(f"analog 配对 {len(data['analog'])} 组 -> {out}")


if __name__ == "__main__":
    main()
