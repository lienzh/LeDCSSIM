# -*- coding: utf-8 -*-
"""
批量勾选点表 OPC 通讯列

输入:  YQ3SIM-IO/*.csv (GBK 编码, 39 列, 第 22 列是 OPC 通讯)
输出:
  - YQ3SIM-IO/*.csv      就地修改(被勾的行 col[22] N→Y)
  - YQ3SIM-IO/.原始备份/  保留改前的原始文件(可回滚)
  - YQ3SIM-IO/_OPC_勾选清单.md   人类可读的变更摘要

规则: 仅勾选 io_pairing_gen.pair_analog 产出的 AQ+AI 两端点
"""
import csv
import io
import shutil
import sys
from pathlib import Path
from collections import defaultdict

from src.sim_engine.io_pairing_gen import load_points, pair_analog, pair_digital

SRC_DIR = Path("YQ3SIM-IO")
BACKUP_DIR = SRC_DIR / ".原始备份"
SUMMARY_MD = SRC_DIR / "_OPC_勾选清单.md"

OPC_COL_INDEX = 22  # 由 GBK 表头分析确定: 第 22 列 = "OPC通讯"


def backup_originals(files):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for fn in files:
        dest = BACKUP_DIR / fn.name
        if not dest.exists():
            shutil.copy2(fn, dest)
            print(f"  备份 {fn.name} → {dest.relative_to(SRC_DIR)}")
        else:
            print(f"  备份已存在,跳过: {dest.relative_to(SRC_DIR)}")


def collect_marks_to_apply():
    """返回 (marks={dpu:set(point_name)}, pairs_analog, pairs_digital)"""
    marks = defaultdict(set)
    pairs_a = []
    pairs_d = []
    for fn in sorted(SRC_DIR.glob("*.csv")):
        dpu = fn.stem
        pts = load_points(str(fn))
        for p in pair_analog(pts, dpu):
            marks[dpu].add(p["cmd"])
            marks[dpu].add(p["fb"])
            pairs_a.append(p)
        for p in pair_digital(pts, dpu):
            marks[dpu].add(p["cmd"])
            marks[dpu].add(p["fb"])
            pairs_d.append(p)
    return marks, pairs_a, pairs_d


def apply_marks_to_csv(fn: Path, marks_for_dpu: set) -> tuple:
    """
    对单个 CSV 文件就地应用 OPC 通讯勾选
    返回 (修改行数, 改前为Y已跳过的行数)
    """
    with open(fn, "r", encoding="gbk", errors="replace", newline="") as f:
        raw_lines = f.read().splitlines(keepends=False)

    # 把每一行解析成字段;用 io.StringIO + csv.reader/writer
    # 我们不能简单 split(','),因为有些字段可能含引号(虽然这里看着没有)
    out_lines = []
    n_changed = 0
    n_already_y = 0
    for i, line in enumerate(raw_lines):
        if i < 2 or not line:
            # 第 0/1 行是 #VERSION 和 ~表头, 原样保留
            out_lines.append(line)
            continue
        # 解析
        reader = csv.reader(io.StringIO(line))
        try:
            cols = next(reader)
        except StopIteration:
            out_lines.append(line)
            continue
        if len(cols) < OPC_COL_INDEX + 1:
            out_lines.append(line)
            continue
        point_name = cols[1].strip()
        if point_name not in marks_for_dpu:
            out_lines.append(line)
            continue
        if cols[OPC_COL_INDEX] == "Y":
            n_already_y += 1
            out_lines.append(line)
            continue
        cols[OPC_COL_INDEX] = "Y"
        # 重新 csv 序列化(保持原 quoting 风格 — 这里源文件没用引号,QUOTE_MINIMAL 就行)
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="")
        writer.writerow(cols)
        out_lines.append(buf.getvalue())
        n_changed += 1

    # 写回 GBK(原始编码)
    with open(fn, "w", encoding="gbk", errors="replace", newline="") as f:
        f.write("\r\n".join(out_lines))
        f.write("\r\n")

    return n_changed, n_already_y


def write_summary_md(marks: dict, pairs_a: list, pairs_d: list, results: dict):
    lines = []
    lines.append("# OPC 通讯勾选清单")
    lines.append("")
    lines.append("> 本文件由 `tools/mark_opc_communication.py` 自动生成。")
    lines.append("> 已对 `YQ3SIM-IO/*.csv` 的第 22 列(OPC 通讯)做批量勾选。")
    lines.append("> 原始 CSV 备份在 `YQ3SIM-IO/.原始备份/`,可随时回滚。")
    lines.append("")
    lines.append("## 1. 勾选规则")
    lines.append("")
    lines.append("按 `src/sim_engine/io_pairing_gen` 算法:")
    lines.append("")
    lines.append("- 仅看 `HW.<2字母><数字>.PV` 格式的点")
    lines.append("- KKS 设备根 = 单元2位+系统3字母+序2位+设备类2字母(如 `30HAG21AA`)")
    lines.append("- **Analog 配对**:同设备根下 AQ(1010 指令)与 AI(1019 反馈)都存在 → 配对")
    lines.append("- **Digital 配对**:同设备根下 DQ 与 DI 都存在 → 配对(直通,无滞后)")
    lines.append("- 描述含 备用/TEST/TO CCS/来自DPU/至PECCS/需确认/描述文本/心跳/SOC/设定 → 剔除")
    lines.append("- 配对成功的 cmd 端 + fb 端均勾 OPC 通讯 = Y")
    lines.append("")
    lines.append("## 2. 各 DPU 变更摘要")
    lines.append("")
    lines.append("| DPU | Analog 配对 | Digital 配对 | 实际改 N→Y | 已是 Y 跳过 |")
    lines.append("|---|---:|---:|---:|---:|")
    all_dpus = sorted(set(marks.keys())
                      | {p["dpu"] for p in pairs_a}
                      | {p["dpu"] for p in pairs_d})
    for dpu in all_dpus:
        n_changed, n_skipped = results.get(dpu, (0, 0))
        n_a = sum(1 for p in pairs_a if p["dpu"] == dpu)
        n_d = sum(1 for p in pairs_d if p["dpu"] == dpu)
        lines.append(f"| {dpu} | {n_a} | {n_d} | {n_changed} | {n_skipped} |")
    lines.append(f"| **合计** | **{len(pairs_a)}** | **{len(pairs_d)}** | "
                 f"**{sum(r[0] for r in results.values())}** | "
                 f"**{sum(r[1] for r in results.values())}** |")
    lines.append("")
    lines.append("## 3. Analog 配对清单 (AQ↔AI, FirstOrder T=2.0s)")
    lines.append("")
    cur_dpu = None
    for p in pairs_a:
        if p["dpu"] != cur_dpu:
            cur_dpu = p["dpu"]
            lines.append(f"### {cur_dpu}")
            lines.append("")
            lines.append("| 设备(KKS) | AQ 指令 | AI 反馈 | 描述 |")
            lines.append("|---|---|---|---|")
        lines.append(f"| `{p['device']}` | `{p['cmd']}` | `{p['fb']}` | {p['desc']} |")
    lines.append("")
    lines.append("## 4. Digital 配对清单 (DQ↔DI, DirectThrough 直通)")
    lines.append("")
    lines.append("> ⚠️ NTVDPU DI 通道实际不可写(详 CLAUDE.md §8.3)。在线 write 会失败,")
    lines.append("> DI 真实生效需要在 CCMStudio 端用 MUX 把「我们写的软点」选择为 DI 来源。")
    lines.append("")
    cur_dpu = None
    for p in pairs_d:
        if p["dpu"] != cur_dpu:
            cur_dpu = p["dpu"]
            lines.append(f"### {cur_dpu}")
            lines.append("")
            lines.append("| 设备(KKS) | DQ 指令 | DI 反馈 | 描述 |")
            lines.append("|---|---|---|---|")
        lines.append(f"| `{p['device']}` | `{p['cmd']}` | `{p['fb']}` | {p['desc']} |")
    lines.append("")
    lines.append("## 5. 用户下一步")
    lines.append("")
    lines.append("1. 用 CCMStudio 打开 YQ3SIM 工程,把修改后的 CSV 导回每个 DPU 的点表")
    lines.append("2. 在线组态下装到 NTVDPU,等待生效(单点验证: 用 UaExpert 浏览对应 AI 通道,确认 HR/LR 节点出现)")
    lines.append("3. 通知 Claude 跑 `py -3.12 -m tools.generate_yaml_from_pairs`")
    lines.append("   → 自动生成 `config/models.generated.yaml + connections.generated.yaml`(新 ref 架构格式)")
    lines.append("4. 用 `py -3.12 -m src.cli run --online --duration 10` 联机闭环验证")
    lines.append("")
    lines.append("## 5. 回滚方法")
    lines.append("")
    lines.append("```bash")
    lines.append("cp YQ3SIM-IO/.原始备份/*.csv YQ3SIM-IO/")
    lines.append("```")
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n摘要已写入: {SUMMARY_MD}")


def main():
    print(f"扫描 {SRC_DIR}...")
    files = sorted(SRC_DIR.glob("*.csv"))
    if not files:
        print("未找到 CSV 文件")
        return 1

    print(f"\n[1/3] 备份原始 CSV 到 {BACKUP_DIR.relative_to(SRC_DIR.parent)}")
    backup_originals(files)

    print(f"\n[2/3] 跑配对算法 (analog + digital) + 收集要勾的点")
    marks, pairs_a, pairs_d = collect_marks_to_apply()
    print(f"  Analog 配对: {len(pairs_a)}, Digital 配对: {len(pairs_d)}")
    for dpu in sorted(marks.keys()):
        print(f"  {dpu}: {len(marks[dpu])} 个点")

    print(f"\n[3/3] 就地修改 CSV (col[{OPC_COL_INDEX}] = 'Y')")
    results = {}
    for fn in files:
        dpu = fn.stem
        marks_for_dpu = marks.get(dpu, set())
        if not marks_for_dpu:
            print(f"  {fn.name}: 无需修改 (本 DPU 无可配对 analog)")
            results[dpu] = (0, 0)
            continue
        n_changed, n_skipped = apply_marks_to_csv(fn, marks_for_dpu)
        results[dpu] = (n_changed, n_skipped)
        print(f"  {fn.name}: 改 {n_changed} 行, 跳过 {n_skipped} 行(已是 Y)")

    write_summary_md(marks, pairs_a, pairs_d, results)
    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
