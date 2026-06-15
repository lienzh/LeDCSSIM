# -*- coding: utf-8 -*-
"""工程创建向导 CLI。

示例:
    py -3.12 -m tools.create_project --name lh3 --template usc_ccs_1000mw --activate --force
"""
from __future__ import annotations

import argparse
import json
import sys

from src.project_wizard import create_project_from_template, list_templates


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tools.create_project", description="按模板创建 LeDCSSIM 工程")
    p.add_argument("--list", action="store_true", help="列出可用模板")
    p.add_argument("--name", help="工程名, 如 lh3")
    p.add_argument("--template", default="usc_ccs_1000mw", help="模板名")
    p.add_argument("--display", help="显示名")
    p.add_argument("--capacity-mw", type=int, help="机组容量 MW")
    p.add_argument("--model-factory", help="DSL 协调模型工厂, 如 CCS_1000")
    p.add_argument("--local-url", help="本机 OPC URL")
    p.add_argument("--vm-url", help="VM OPC URL")
    p.add_argument("--mode", choices=("local", "vm"), default="local")
    p.add_argument("--force", action="store_true",
                   help="工程已存在时更新工程元数据和模板驱动; 默认保留已有脚本和 OPC 端点")
    p.add_argument("--overwrite-script", action="store_true",
                   help="配合 --force 使用: 覆盖已有 script.txt")
    p.add_argument("--overwrite-endpoints", action="store_true",
                   help="配合 --force 使用: 覆盖已有 opc_endpoints.yaml")
    p.add_argument("--activate", action="store_true", help="创建后切为当前 active 工程")
    args = p.parse_args(argv)

    if args.list:
        print(json.dumps(list_templates(), ensure_ascii=False, indent=2))
        return 0
    if not args.name:
        p.error("--name 必填, 或使用 --list")

    result = create_project_from_template(
        name=args.name,
        template=args.template,
        display=args.display,
        capacity_mw=args.capacity_mw,
        model_factory=args.model_factory,
        local_url=args.local_url,
        vm_url=args.vm_url,
        mode=args.mode,
        overwrite=args.force,
        overwrite_script=args.overwrite_script,
        overwrite_endpoints=args.overwrite_endpoints,
        activate=args.activate,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
