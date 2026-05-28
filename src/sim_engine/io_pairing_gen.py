# -*- coding: utf-8 -*-
"""
IO 配对生成器核心（P1：模拟调节门）

从科远 NT6000 点表 CSV（GBK 编码）筛选现场硬件 IO，
按 KKS 设备码将 AQ 指令与 AI 反馈配对。

KKS 约定（已验证）：设备码 = 单元2位+系统3字母+序2位+设备类2字母，
其后信号位 1010=模拟指令，1019=位置反馈。
"""
import csv
import glob
import re
from pathlib import Path
from typing import Dict, List

# 点名: HW.<2字母><数字>.PV
PT = re.compile(r"^HW\.([A-Z]{2})(\d+)\.PV")
# 现场设备 KKS: 30HAG21AA + 信号位
DEV = re.compile(r"^(\d{2}[A-Z]{3}\d{2}[A-Z]{2})(\d+)$")
# 软点/通讯/备用/设定 关键词（描述层面剔除）
SOFT_KW = ["备用", "模件第", "TEST", "TO CCS", "来自DPU", "至PECCS",
           "需确认", "描述文本", "心跳", "SOC", "设定"]


def load_points(fn: str) -> List[dict]:
    """加载点表 CSV（GBK），返回 HW.*.PV 点列表"""
    with open(fn, encoding="gbk", errors="replace") as f:
        lines = f.read().splitlines()
    rows = [l for l in lines if l and not l.startswith("#")]
    reader = csv.reader(rows)
    next(reader)  # 跳过 ~ 表头
    pts = []
    for r in reader:
        if len(r) < 5:
            continue
        m = PT.match(r[1].strip())
        if not m:
            continue
        pts.append({"name": r[1].strip(), "code": m.group(1),
                    "desc": r[2].strip(), "kks": r[3].strip()})
    return pts


def is_soft(p: dict) -> bool:
    """是否软点/通讯/备用（按描述关键词）"""
    return any(k in p["desc"] for k in SOFT_KW)


def pair_analog(pts: List[dict], dpu: str) -> List[dict]:
    """同一 KKS 设备码下 AQ 指令 ↔ AI 反馈配对"""
    by_root: Dict[str, list] = {}
    for p in pts:
        m = DEV.match(p["kks"])
        if not m or is_soft(p):
            continue
        by_root.setdefault(m.group(1), []).append(p)
    pairs = []
    for root, grp in by_root.items():
        aq = [p for p in grp if p["code"] == "AQ"]
        ai = [p for p in grp if p["code"] == "AI"]
        if aq and ai:
            for c in aq:
                pairs.append({
                    "dpu": dpu, "device": root,
                    "cmd": c["name"], "fb": ai[0]["name"],
                    "template": "analog",
                    "transform": {"type": "inertia", "T": 2.0},
                    "online_writable": True,
                    "desc": c["desc"],
                })
    return pairs


def generate(src_dir: str) -> dict:
    """扫描目录下所有 *.csv，聚合 analog 配对"""
    analog = []
    for fn in sorted(glob.glob(str(Path(src_dir) / "*.csv"))):
        dpu = Path(fn).stem
        analog.extend(pair_analog(load_points(fn), dpu))
    return {"analog": analog}
