# -*- coding: utf-8 -*-
"""脚本生成引擎 — 规则驱动.

机制搬自 runtime.generate_script_from_tagmap, 设备知识改由 DriverRules 提供。
YQ3 输出必须与重构前金标准逐字一致。
"""
import csv as _csv
import glob as _glob
from collections import defaultdict
from pathlib import Path

from src import project as proj
from src.sim_engine.io_pairing_gen import load_points, pair_analog, pair_digital, is_soft

from .gateway import gateway_lines_from_csv
from .rules import load_rules


def _recommend_cmd(fb_pt: dict, cmd_pts: list, threshold: float = 0.55):
    """
    为未配对反馈推荐最可能的指令 (基于 KKS 前缀 + 描述相似度)

    返回 (best_cmd_pt, score) 或 (None, 0)
    """
    from difflib import SequenceMatcher
    fb_desc = fb_pt.get("desc", "") or ""
    fb_kks = fb_pt.get("kks", "") or ""
    # 反馈描述中去掉常见反馈词,留下信号本体
    fb_core = (fb_desc.replace("位置", "").replace("反馈", "")
                       .replace("运行", "").replace("状态", "")
                       .replace("开", "").replace("关", "").strip())
    best, best_score = None, 0.0
    for c in cmd_pts:
        score = 0.0
        c_kks = c.get("kks", "") or ""
        c_desc = c.get("desc", "") or ""
        # KKS 设备根 (前 9 位: 30HAG21AA) 完全相同 = 极强信号
        if fb_kks[:12] and c_kks[:12] and fb_kks[:12] == c_kks[:12]:
            score += 0.6
        # KKS 前 5 位相同 (30HAG)
        elif fb_kks[:5] and c_kks[:5] and fb_kks[:5] == c_kks[:5]:
            score += 0.25
        # 描述相似度(去掉反馈词后)
        if fb_core and c_desc:
            c_core = (c_desc.replace("指令", "").replace("输出", "")
                            .replace("开", "").replace("关", "").strip())
            sim = SequenceMatcher(None, fb_core, c_core).ratio()
            score += sim * 0.5
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= threshold else (None, best_score)


def _device_instance(desc, spec):
    """从描述里提取实例标识 (A/B/3A/...) - 取 include 关键词之前的最后字母数字"""
    import re
    if not desc:
        return ""
    for inc_word in spec["include"]:
        idx = desc.find(inc_word)
        if idx >= 0:
            prefix = desc[:idx].rstrip()
            m = re.search(r'([A-Z#0-9]{1,4})$', prefix)
            return m.group(1) if m else ""
    return ""


def _fmt_node(dpu, pt):
    """点对象 → 'DPU3013.AI010502(描述)' 格式"""
    short = pt["name"].replace("HW.", "").replace(".PV", "")
    desc = (pt.get("desc", "") or "").replace("(", "[").replace(")", "]")
    return f"{dpu}.{short}({desc})" if desc else f"{dpu}.{short}"


def generate(project_paths=None) -> str:
    """按工艺规则从点表生成 DSL 脚本草稿."""
    # 优先用工程配置的点表目录, 找不到回退 io_fallback_globs
    pp = project_paths or proj.paths()
    rules = load_rules(pp)
    csv_files = sorted(pp.io_dir.glob(pp.io_glob))
    if csv_files:
        def _dpu_of(p): return "DPU" + p.stem.replace("_S","").replace("-S","")
    else:
        for pat in pp.io_fallback_globs:
            csv_files = sorted(Path(f) for f in _glob.glob(pat))
            if csv_files:
                break
        def _dpu_of(p): return p.stem

    DPU_SCOPE = sorted({_dpu_of(p) for p in csv_files})
    csv_by_dpu = {_dpu_of(p): p for p in csv_files}

    # 全部点 + 描述
    desc_map = {}     # {(dpu, name): desc}
    all_points = {}   # {dpu: [points...]} - load_points 风格
    for dpu in DPU_SCOPE:
        fn = csv_by_dpu.get(dpu)
        if not fn or not fn.exists():
            continue
        for ln in fn.read_bytes().decode("gbk", errors="replace").splitlines()[2:]:
            try: r = next(_csv.reader([ln]))
            except: continue
            if len(r) > 2:
                desc_map[(dpu, r[1].strip())] = r[2].strip()
        all_points[dpu] = load_points(str(fn))

    # 配对
    pairs_a, pairs_d = [], []
    for dpu, pts in all_points.items():
        pairs_a.extend(pair_analog(pts, dpu))
        pairs_d.extend(pair_digital(pts, dpu))

    # 已配对点集合 (用于过滤未配对)
    paired = set()
    for p in pairs_a + pairs_d:
        paired.add((p["dpu"], p["cmd"]))
        paired.add((p["dpu"], p["fb"]))

    # 同 DPU 的全部指令池(用于推荐候选)
    cmds_by_dpu = {dpu: {"AQ": [], "DQ": []} for dpu in DPU_SCOPE}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] in ("AQ", "DQ") and not is_soft(p):
                cmds_by_dpu[dpu][p["code"]].append(p)

    # 未配对的硬件 IO (只看 AI/DI 反馈端)
    unpaired_by_dpu = {dpu: {"AI": [], "DI": []} for dpu in DPU_SCOPE}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] not in ("AI", "DI"): continue
            if is_soft(p): continue
            if (dpu, p["name"]) in paired: continue
            unpaired_by_dpu[dpu][p["code"]].append(p)

    # 构造输出
    lines = [
        "# 赋值脚本 — 反馈(信号名) = 指令(信号名)",
        "# 括号内为信号名称, 仅作可读性, 解析时自动忽略",
        "# 修改 / 注释 / 新增按需; 短码 'DPU3013.AI010502' 自动展开为 ns=0;s=DPU3013.HW.AI010502.PV",
        "",
    ]

    def short(name_full: str) -> str:
        return name_full.replace("HW.", "").replace(".PV", "")

    def fmt(dpu, full_name):
        desc = desc_map.get((dpu, full_name), "")
        body = f"{dpu}.{short(full_name)}"
        clean = desc.replace("(", "[").replace(")", "]")
        return f"{body}({clean})" if clean else body

    # 同 DPU 同 KKS 设备根的 DQ 池 (找 RS 启停)
    dq_by_dpu_kks = {}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] == "DQ" and not is_soft(p):
                kr = (p.get("kks", "") or "")[:12]
                if kr:
                    dq_by_dpu_kks.setdefault((dpu, kr), []).append(p)

    # 已自动配对查找: {(dpu, fb_name): cmd_name}
    auto_pair_cmd = {}
    for p in pairs_a + pairs_d:
        auto_pair_cmd[(p["dpu"], p["fb"])] = p["cmd"]

    # 同 DPU 全部 AQ 池(给 AI 推荐用)
    aq_by_dpu = {dpu: [p for p in pts if p["code"] == "AQ" and not is_soft(p)]
                 for dpu, pts in all_points.items()}

    # === 按 KKS 设备分组生成 ===
    # 每个设备 (DPU, KKS_root) → 组装本设备所有相关信号
    OPEN_FB_WORDS = rules.vocab["open_fb"]
    CLOSE_FB_WORDS = rules.vocab["close_fb"]
    LOCAL_WORDS = rules.vocab["local"]   # 反义远方 = 0

    # 排除"保护跳闸"等非手动操作指令 — 不能作为 RS 的 set/reset
    CMD_EXCLUDE_WORDS = rules.vocab["cmd_exclude"]

    def _is_real_cmd(desc):
        return not any(k in desc for k in CMD_EXCLUDE_WORDS)

    def _is_open_cmd(desc):
        d = (desc or "").strip()
        if not _is_real_cmd(d): return False
        # 开头模式: "启A给煤机" / "开X电动门" — 首字 启/开,且第二字非空格
        if len(d) >= 2 and d[0] in ("启", "开") and d[1] != " ":
            return True
        return (any(k in d for k in rules.vocab["start_cmd"]) and
                not any(k in d for k in rules.vocab["stop_cmd"]))

    def _is_close_cmd(desc):
        d = (desc or "").strip()
        if not _is_real_cmd(d): return False
        # 开头模式: "停A给煤机" / "关X电动门"
        if len(d) >= 2 and d[0] in ("停", "关") and d[1] != " ":
            return True
        return (any(k in d for k in rules.vocab["stop_cmd"]) and
                not any(k in d for k in rules.vocab["start_cmd"]))

    def _should_skip_pt(pt):
        """跳过: 软点(备用等) / 空描述"""
        if is_soft(pt): return True
        desc = (pt.get("desc", "") or "").strip()
        if not desc: return True
        return False

    # === 按 KKS 设备根分组 (前 9 位) + 白名单仅过滤设备类型 ===
    # 同 KKS 下的指令和反馈自动绑;不同 KKS (如主体 vs FSSS 输出) 自动隔离
    motor_groups = defaultdict(lambda: {"DQ": [], "DI": [], "AQ": [], "AI": [], "spec": None})
    valve_groups = defaultdict(lambda: {"AQ": [], "AI": [], "DI": [], "DQ": [], "spec": None})

    for dpu, pts in all_points.items():
        for p in pts:
            if _should_skip_pt(p): continue
            desc = (p.get("desc","") or "").strip()
            if p["code"] not in ("DQ", "DI", "AQ", "AI"): continue
            kks_root = (p.get("kks","") or "")[:9]
            if not kks_root: continue   # 无 KKS 跳过

            # 白名单匹配 (描述必须含白名单关键词)
            spec = rules.match_device(desc)
            if not spec:
                continue
            key = (dpu, kks_root)
            if spec.get("type") == "valve":
                valve_groups[key][p["code"]].append(p)
                if valve_groups[key]["spec"] is None:
                    valve_groups[key]["spec"] = spec
                continue
            if spec.get("type") == "motor":
                motor_groups[key][p["code"]].append(p)
                if motor_groups[key]["spec"] is None:
                    motor_groups[key]["spec"] = spec

    SECTION_ORDER = [
        ("电机设备层 (开关量, RS 触发器)", "motor"),
        ("阀门设备层 (模拟量, AI 直通)", "valve"),
        ("柜间通讯 (MEH / DEH / DCS) - 待实现", "gateway"),
        ("模型层 - 待实现", "model"),
    ]
    sections = {key: [] for _, key in SECTION_ORDER}

    stats = defaultdict(int)
    consumed_fb = set()

    def fmt_pt(dpu, pt):
        return fmt(dpu, pt["name"])

    def _device_name(pts):
        """从同设备所有点的描述中提取公共前缀作为设备名"""
        descs = [(p.get("desc","") or "").strip() for p in pts if (p.get("desc","") or "").strip()]
        if not descs: return ""
        # 取最长公共前缀
        prefix = descs[0]
        for d in descs[1:]:
            while not d.startswith(prefix):
                prefix = prefix[:-1]
                if not prefix: return descs[0][:20]
        # 截掉末尾的标点/数字/编号(如 "A引风机#1 " → "A引风机")
        prefix = prefix.rstrip(" #0123456789()（）-")
        return prefix if len(prefix) >= 2 else descs[0][:20]

    # === 段 1: 电机设备层 ===
    for key in sorted(motor_groups.keys()):
        dpu, kks_root = key
        dev = motor_groups[key]
        dis = dev["DI"]; dqs = dev["DQ"]
        if not dis: continue
        dev_name = dev["spec"]["name"] if dev.get("spec") else ""
        inst = _device_instance((dis[0].get("desc","") or ""), dev["spec"]) if dev.get("spec") else ""
        # 找开/关指令
        open_cmd = next((c for c in dqs if _is_open_cmd(c.get("desc",""))), None)
        close_cmd = next((c for c in dqs if _is_close_cmd(c.get("desc",""))), None)
        block_lines = []
        for di in dis:
            d = di.get("desc","") or ""
            lhs = fmt_pt(dpu, di)
            # 跳过常数赋值
            if any(k in d for k in rules.vocab["fault"]): stats["skip_fault"]+=1; continue
            if any(k in d for k in LOCAL_WORDS): stats["skip_local"]+=1; continue
            if any(k in d for k in rules.vocab["remote"]): stats["skip_remote"]+=1; continue
            if any(k in d for k in CLOSE_FB_WORDS) and not any(k in d for k in OPEN_FB_WORDS):
                if open_cmd and close_cmd:
                    block_lines.append(f"{lhs} = RS_NOT({fmt_pt(dpu, open_cmd)}, {fmt_pt(dpu, close_cmd)})     # 关反馈 = !RS")
                    stats["rs_not"] += 1; consumed_fb.add((dpu, di["name"]))
                elif close_cmd:
                    block_lines.append(f"{lhs} = {fmt_pt(dpu, close_cmd)}     # 关反馈 = 关指令")
                    stats["single_close"] += 1; consumed_fb.add((dpu, di["name"]))
                elif open_cmd:
                    block_lines.append(f"{lhs} = NOT({fmt_pt(dpu, open_cmd)})     # 关反馈 = !开指令")
                    stats["single_close"] += 1; consumed_fb.add((dpu, di["name"]))
                else:
                    stats["skip_no_cmd"] += 1
            elif any(k in d for k in OPEN_FB_WORDS) or any(k in d for k in rules.vocab["run"]):
                if open_cmd and close_cmd:
                    block_lines.append(f"{lhs} = RS({fmt_pt(dpu, open_cmd)}, {fmt_pt(dpu, close_cmd)})     # 开反馈 = RS")
                    stats["rs"] += 1; consumed_fb.add((dpu, di["name"]))
                elif open_cmd:
                    block_lines.append(f"{lhs} = {fmt_pt(dpu, open_cmd)}     # 开反馈 = 开指令")
                    stats["single_open"] += 1; consumed_fb.add((dpu, di["name"]))
                elif close_cmd:
                    block_lines.append(f"{lhs} = NOT({fmt_pt(dpu, close_cmd)})     # 开反馈 = !关指令")
                    stats["single_open"] += 1; consumed_fb.add((dpu, di["name"]))
                else:
                    stats["skip_no_cmd"] += 1
            else:
                stats["skip_other"] += 1
        if block_lines:
            title = f"{inst}{dev_name}" if inst else dev_name
            sections["motor"].append(f"# --- {title} @ {dpu} (KKS:{kks_root}) ---")
            sections["motor"].extend(block_lines)
            sections["motor"].append("")

    # === 段 2: 阀门设备层 (AI = AQ 直通) ===
    for key in sorted(valve_groups.keys()):
        dpu, kks_root = key
        dev = valve_groups[key]
        ais = dev["AI"]; aqs = dev["AQ"]
        if not ais: continue
        dev_name = dev["spec"]["name"] if dev.get("spec") else ""
        inst = _device_instance((ais[0].get("desc","") or ""), dev["spec"]) if dev.get("spec") else ""
        ai_lines = []
        for ai in ais:
            cmd_name = auto_pair_cmd.get((dpu, ai["name"]))
            if cmd_name:
                cmd_pt = next((c for c in aqs if c["name"] == cmd_name), None)
                if cmd_pt:
                    ai_lines.append(f"{fmt_pt(dpu, ai)} = {fmt_pt(dpu, cmd_pt)}     # AI 直通")
                    stats["pair"] += 1; consumed_fb.add((dpu, ai["name"]))
        # 如果没自动配对,尝试同设备下唯一 AQ
        if not ai_lines and len(aqs) == 1:
            for ai in ais:
                ai_lines.append(f"{fmt_pt(dpu, ai)} = {fmt_pt(dpu, aqs[0])}     # AI 直通(单 AQ 假设)")
                stats["pair"] += 1; consumed_fb.add((dpu, ai["name"]))
        if ai_lines:
            title = f"{inst}{dev_name}" if inst else dev_name
            sections["valve"].append(f"# --- {title} @ {dpu} (KKS:{kks_root}) ---")
            sections["valve"].extend(ai_lines)
            sections["valve"].append("")

    # === 柜间通讯段 (扫所有点,不管 is_soft 过滤,识别柜间关键词) ===
    # 柜间点通常在 _GATEWAY_WORDS 描述里,可能被 is_soft 过滤掉,要单独识别
    import csv as _csv2
    csv_gateway = gateway_lines_from_csv(pp.root / "gateway.csv")
    if csv_gateway:
        sections["gateway"].extend(csv_gateway)
        stats["gateway"] = len(csv_gateway)
    else:
        gateway_by_dpu = {}
        for dpu in DPU_SCOPE:
            fn = csv_by_dpu.get(dpu)
            if not fn or not fn.exists(): continue
            gw = []
            for ln in fn.read_bytes().decode("gbk", errors="replace").splitlines()[2:]:
                try: r = next(_csv2.reader([ln]))
                except: continue
                if len(r) < 3: continue
                name = r[1].strip()
                desc = r[2].strip()
                if not name.startswith("HW.") or not name.endswith(".PV"): continue
                if (dpu, name) in consumed_fb: continue
                if not desc or "备用" in desc: continue   # 空描述/备用 — 跳过
                if any(k in desc for k in rules.vocab["gateway"]):
                    gw.append((name, desc))
            if gw:
                gateway_by_dpu[dpu] = gw

        sections["gateway"].append("# (柜间通讯 — MEH/DEH/DCS 之间通讯点,待用户细化白名单后实现)")
        sections["gateway"].append("# 目前发现的潜在柜间点 (注释 — 仅参考):")
        total_gw = 0
        for dpu, items in gateway_by_dpu.items():
            for name, desc in items[:5]:  # 每 DPU 只列 5 个示例
                short_name = name.replace("HW.", "").replace(".PV", "")
                desc_clean = desc.replace("(", "[").replace(")", "]")
                sections["gateway"].append(f"# {dpu}.{short_name}({desc_clean}) = ???")
                total_gw += 1
        stats["gateway"] = total_gw
    sections["model"].append("# (模型层 — 待实现)")

    # === 输出 ===
    for sec_label, sec_key in SECTION_ORDER:
        body = sections[sec_key]
        if not body: continue
        lines.append("")
        lines.append("# " + "═" * 64)
        lines.append(f"# 【{sec_label}】")
        lines.append("# " + "═" * 64)
        lines.extend(body)

    # 头部统计 - 白名单严格模式
    motor_active = stats['rs'] + stats['rs_not'] + stats['single_open'] + stats['single_close']
    header_stats = [
        f"# 白名单严格模式 (只处理列出的设备类型):",
        f"#   1. 电机设备层 (送风机/引风机/一次风机/给煤机/磨煤机/前置泵/凝结水泵)",
        f"#      → {motor_active} 行真闭环 (RS:{stats['rs']} + RS_NOT:{stats['rs_not']} "
        f"+ 单开:{stats['single_open']} + 单关:{stats['single_close']})",
        f"#   2. 阀门设备层 (除氧器主/副调节阀, 送风机/引风机/一次风机动叶)",
        f"#      → {stats['pair']} 行 AI 直通",
        f"#   3. 柜间通讯 (MEH/DEH/DCS) — 待实现",
        f"#   4. 模型层 — 待实现",
        f"#   ─────────────",
        f"#   不在白名单的所有点: 不入脚本 (在 _FULL.csv 已暴露, 后续按需手动加)",
        "",
    ]
    lines[3:3] = header_stats
    return "\n".join(lines)
