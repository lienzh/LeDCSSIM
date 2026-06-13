# -*- coding: utf-8 -*-
"""柜间通讯段 — 可插拔接口.

来源是用户的柜间线清册 (Excel). 本轮只消费一张归一化 csv:
    projects/<工程>/gateway.csv   表头: 目标信号,来源,描述
    每行 → "目标(描述) = 来源"  (来源是短码则直通, 是数字则写常数)
Excel → 此 csv 的转换是后续的事 (避免引入 Excel 解析 + 各家格式差异).
csv 不在 → gateway_lines_from_csv 返 [], 主引擎退回关键词扫描的注释占位.
"""
import csv as _csv
from pathlib import Path
from typing import List


def gateway_lines_from_csv(csv_path) -> List[str]:
    p = Path(csv_path)
    if not p.exists():
        return []
    out: List[str] = []
    rows = list(_csv.reader(p.read_text(encoding="utf-8").splitlines()))
    for r in rows[1:]:                       # 跳表头
        if len(r) < 2:
            continue
        target = r[0].strip()
        source = r[1].strip()
        desc = r[2].strip() if len(r) > 2 else ""
        if not target or not source:
            continue
        lhs = f"{target}({desc})" if desc else target
        out.append(f"{lhs} = {source}")
    return out
