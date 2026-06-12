# -*- coding: utf-8 -*-
"""
从 io_pairing_gen 配对结果生成新架构 YAML

输入:  {io_full_dir}/*.csv 跑 pair_analog  (由 project.yaml: io_full_dir 决定)
输出:
  projects/<工程>/generated/models.generated.yaml       — 每对 = 1 个 FirstOrder block
  projects/<工程>/generated/connections.generated.yaml  — 无横向连接(每个 block 独立: OPC→block→OPC)
  projects/<工程>/generated/tagmap.generated.yaml       — 每个 block 2 个 tag (in 读AQ / out 写AI)
  projects/<工程>/generated/_OPC_配对组态清单.md         — 人类可读

设计:
- 块命名: {dpu}_{kks_device}, 如 DPU3013_30HAG21AA — 跨 DPU 唯一
- 每个块是 FirstOrder(K=1, T=transform.T), 对应"阀门指令 → 一阶惯性 → 阀位反馈"
- in 端口 ← OPC AQ (DCS 写过来的指令值)
- out 端口 → OPC AI (我们写回去当反馈, 走 HR/LR 双写)
"""
import sys
from pathlib import Path

import yaml

from src import project as _prj
from src.sim_engine.io_pairing_gen import generate

SRC_DIR = str(_prj.paths().io_full_dir)    # 全量点表目录 (project.yaml: io_full_dir)
_OUT_DIR = _prj.paths().generated_dir
SUMMARY_MD = _OUT_DIR / "_OPC_配对组态清单.md"


def pair_to_block_and_tags(p: dict) -> tuple:
    """
    单对配对 → (block_dict, [in_tag, out_tag])

    - analog 配对  → FirstOrder + ai_hr_lr 写模式 (走 HR/LR 双写)
    - digital 配对 → DirectThrough(立即直通) + 普通 write_value (Bool)
    """
    dpu = p["dpu"]
    device = p["device"]
    template = p.get("template", "analog")
    # 用 cmd 点短码(HW.XX######.PV → XX######) 作唯一标识
    # 例: HW.AQ010101.PV → AQ010101
    cmd_short = p["cmd"].replace("HW.", "").replace(".PV", "")
    block_name = f"{dpu}_{cmd_short}"

    cmd_node = f"ns=0;s={dpu}.{p['cmd']}"
    fb_node = f"ns=0;s={dpu}.{p['fb']}"

    if template == "analog":
        T = float(p["transform"]["T"])
        block = {
            "name": block_name,
            "type": "FirstOrder",
            "params": {"K": 1.0, "T": T},
            "desc": p["desc"],
        }
        # AI 走 HR/LR 双写; ai_base = 去掉 .PV
        ai_base = f"ns=0;s={dpu}.{p['fb'].rsplit('.', 1)[0]}"
        in_tag = {
            "tag": f"{block_name}.in",
            "opc_node": cmd_node,
            "direction": "in",
            "dtype": "Float",
            "desc": f"{device} 指令 (AQ {p['cmd']})",
        }
        out_tag = {
            "tag": f"{block_name}.out",
            "opc_node": fb_node,
            "direction": "out",
            "dtype": "Float",
            "write_mode": "ai_hr_lr",
            "channel_base": ai_base,
            "desc": f"{device} 反馈 (AI {p['fb']})",
        }
    elif template == "digital":
        block = {
            "name": block_name,
            "type": "DirectThrough",
            "params": {"T": 0.0},
            "desc": p["desc"],
        }
        in_tag = {
            "tag": f"{block_name}.in",
            "opc_node": cmd_node,
            "direction": "in",
            "dtype": "Bool",
            "desc": f"{device} 指令 (DQ {p['cmd']})",
        }
        out_tag = {
            "tag": f"{block_name}.out",
            "opc_node": fb_node,
            "direction": "out",
            "dtype": "Bool",
            # 注意: DI 写实际不生效, 需 CCMStudio 端 MUX 配合 (CLAUDE.md §8.3)
            "desc": f"{device} 反馈 (DI {p['fb']}) [DCS 端需 MUX]",
        }
    else:
        raise ValueError(f"未知 template: {template}")
    return block, [in_tag, out_tag]


def _yaml_dump(data) -> str:
    """统一 dump 风格 - allow_unicode + 不排序 + 块流"""
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=200,
    )


def write_summary_md(pairs_a: list, pairs_d: list):
    lines = []
    lines.append("# OPC 配对组态清单 (自动生成)")
    lines.append("")
    lines.append("> 由 `tools/generate_yaml_from_pairs.py` 从 YQ3SIM-IO/ 配对生成。")
    lines.append(f"> Analog: {len(pairs_a)} 对(FirstOrder),Digital: {len(pairs_d)} 对(DirectThrough)。")
    lines.append("")
    lines.append("## 1. 输出文件")
    lines.append("")
    gen = str(_OUT_DIR)
    lines.append("| 文件 | 用途 |")
    lines.append("|---|---|")
    lines.append(f"| `{gen}/models.generated.yaml` | block 实例化清单 |")
    lines.append(f"| `{gen}/connections.generated.yaml` | block 间连接(配对场景为空) |")
    lines.append(f"| `{gen}/tagmap.generated.yaml` | OPC 测点映射 |")
    lines.append("")
    lines.append("## 2. 用法")
    lines.append("")
    lines.append("```bash")
    lines.append("# 在线跑(需 NTVDPU 启动且 CSV 已导回工程生效)")
    lines.append("py -3.12 -m src.cli run --online --duration 30 \\")
    lines.append(f"    --models {gen}/models.generated.yaml \\")
    lines.append(f"    --connections {gen}/connections.generated.yaml \\")
    lines.append(f"    --tagmap {gen}/tagmap.generated.yaml")
    lines.append("```")
    lines.append("")
    lines.append("## 3. Analog 配对(FirstOrder)")
    lines.append("")
    cur_dpu = None
    for p in pairs_a:
        if p["dpu"] != cur_dpu:
            cur_dpu = p["dpu"]
            lines.append(f"### {cur_dpu}")
            lines.append("")
            lines.append("| 块名 | KKS 设备 | AQ 指令 | AI 反馈 | T | 描述 |")
            lines.append("|---|---|---|---|---:|---|")
        block_name = f"{p['dpu']}_{p['device']}"
        lines.append(
            f"| `{block_name}` | `{p['device']}` | `{p['cmd']}` | `{p['fb']}` "
            f"| {p['transform']['T']} | {p['desc']} |"
        )
    lines.append("")
    lines.append("## 4. Digital 配对(DirectThrough,直通)")
    lines.append("")
    lines.append("> ⚠️ DI 写实际不生效,需在 CCMStudio 端 MUX 配合。详 CLAUDE.md §8.3。")
    lines.append("")
    cur_dpu = None
    for p in pairs_d:
        if p["dpu"] != cur_dpu:
            cur_dpu = p["dpu"]
            lines.append(f"### {cur_dpu}")
            lines.append("")
            lines.append("| 块名 | KKS 设备 | DQ 指令 | DI 反馈 | 描述 |")
            lines.append("|---|---|---|---|---|")
        block_name = f"{p['dpu']}_{p['device']}"
        lines.append(
            f"| `{block_name}` | `{p['device']}` | `{p['cmd']}` | `{p['fb']}` "
            f"| {p['desc']} |"
        )
    lines.append("")
    lines.append("## 5. 调整建议")
    lines.append("")
    lines.append("- **时间常数 T(analog)** 当前全部默认 2.0s,后续按实际设备响应特性微调")
    lines.append("- **增益 K(analog)** 当前默认 1.0(指令 → 反馈 1:1)。如阀门指令与反馈量纲/量程不同,改 K")
    lines.append("- **digital 块** 当前用 DirectThrough 无滞后;如需短延迟去抖,改 `params.T` > 0")
    lines.append("- **块名** 用 `{DPU}_{KKS设备}` 唯一化;如希望短名(如就用 KKS),手动改 models.generated.yaml 即可")
    SUMMARY_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"摘要写入: {SUMMARY_MD}")


def main():
    print(f"扫描 {SRC_DIR} 并配对...")
    result = generate(SRC_DIR)
    pairs_a = result.get("analog", [])
    pairs_d = result.get("digital", [])
    print(f"  analog 配对: {len(pairs_a)} (→ FirstOrder)")
    print(f"  digital 配对: {len(pairs_d)} (→ DirectThrough)")

    blocks = []
    all_tags = []
    for p in pairs_a + pairs_d:
        b, ts = pair_to_block_and_tags(p)
        blocks.append(b)
        all_tags.extend(ts)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    models_path = _OUT_DIR / "models.generated.yaml"
    conns_path = _OUT_DIR / "connections.generated.yaml"
    tagmap_path = _OUT_DIR / "tagmap.generated.yaml"

    # models.yaml
    header = (
        f"# 自动生成 - 来自 {SRC_DIR}/ 的 AQ↔AI 配对\n"
        "# 重新生成: py -3.12 -m tools.generate_yaml_from_pairs\n"
        "# 手工修改建议放到独立的 models.yaml 里覆盖,不要直接改本文件\n\n"
    )
    models_path.write_text(header + _yaml_dump({"blocks": blocks}), encoding="utf-8")
    conns_path.write_text(
        header + _yaml_dump({"connections": []})
        + "\n# 配对场景下每个块独立, 没有 block 间横向连接\n"
        + "# 如需手动加连接(如 PID + Limiter 链),写在这里\n",
        encoding="utf-8",
    )
    tagmap_path.write_text(header + _yaml_dump({"tags": all_tags}), encoding="utf-8")

    print(f"  生成 {models_path} ({len(blocks)} 块)")
    print(f"  生成 {conns_path} (0 连接)")
    print(f"  生成 {tagmap_path} ({len(all_tags)} 测点)")

    write_summary_md(pairs_a, pairs_d)
    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
