# -*- coding: utf-8 -*-
"""Flask Web 仪表板 — 只读查看模型组态和最新 CSV 数据"""
import csv
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml
from flask import Flask, jsonify, render_template_string, request

from src.engine import GraphRunner, TagMap, DataRecorder
from . import runtime as rt

logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置文件路径(可由 CLI 参数覆盖)
CONFIG = {
    "models": "config/models.generated.yaml",
    "connections": "config/connections.generated.yaml",
    "tagmap": "config/tagmap.generated.yaml",
    "csv": "data/run.csv",
}

# 点表目录 + 文件名模式 — 当前用"简化"版
POINT_TABLE_DIR = "YQ3SIM-IO/SIMPLE/简化"
POINT_TABLE_GLOB = "*[_-]S.csv"   # 3001_S.csv / 3038-S.csv 都匹配


def _dpu_from_filename(stem: str) -> str:
    """从文件名 stem 解析 DPU 名: '3001_S' / '3038-S' → 'DPU3001' / 'DPU3038'"""
    base = stem.replace("_S", "").replace("-S", "").replace("_FULL", "")
    if base.isdigit():
        return f"DPU{base}"
    return stem  # 退路: 老 'DPU3013.csv' 命名直接用

# Web 编辑器允许的文件白名单(防止任意路径写入)
EDITABLE_FILES = [
    "models.yaml", "connections.yaml", "tagmap.yaml",
    "models.generated.yaml", "connections.generated.yaml", "tagmap.generated.yaml",
]
CONFIG_DIR = Path("config")


# ---------- 数据加载 ----------

def _load_blocks() -> List[Dict[str, Any]]:
    """加载块清单 [{name, type, params, desc, dpu}]"""
    p = Path(CONFIG["models"])
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    out = []
    for b in doc.get("blocks", []):
        # 从 name 推断 DPU(name 形如 DPU3013_AQ010101)
        name = b.get("name", "")
        dpu = name.split("_", 1)[0] if "_" in name else "?"
        out.append({
            "name": name,
            "type": b.get("type", "?"),
            "params": b.get("params", {}),
            "desc": b.get("desc", ""),
            "dpu": dpu,
        })
    return out


def _load_connections() -> List[Dict[str, str]]:
    p = Path(CONFIG["connections"])
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("connections") or []


def _load_tagmap() -> List[Dict[str, Any]]:
    p = Path(CONFIG["tagmap"])
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("tags") or []


def _load_latest_csv() -> Dict[str, Any]:
    """读最新 CSV 的最后一行 + 表头"""
    p = Path(CONFIG["csv"])
    if not p.exists():
        return {"available": False, "path": str(p)}
    with open(p, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if len(rows) < 2:
        return {"available": False, "path": str(p), "msg": "CSV 内容不足"}
    header = rows[0]
    last = rows[-1]
    data = dict(zip(header, last))
    return {
        "available": True,
        "path": str(p),
        "rows": len(rows) - 1,
        "header": header,
        "latest": data,
    }


# ---------- API ----------

@app.route("/api/blocks")
def api_blocks():
    return jsonify(_load_blocks())


@app.route("/api/connections")
def api_connections():
    return jsonify(_load_connections())


@app.route("/api/tagmap")
def api_tagmap():
    return jsonify(_load_tagmap())


@app.route("/api/latest")
def api_latest():
    return jsonify(_load_latest_csv())


@app.route("/api/summary")
def api_summary():
    blocks = _load_blocks()
    conns = _load_connections()
    tags = _load_tagmap()
    csv_info = _load_latest_csv()
    type_count: Dict[str, int] = {}
    dpu_count: Dict[str, int] = {}
    for b in blocks:
        type_count[b["type"]] = type_count.get(b["type"], 0) + 1
        dpu_count[b["dpu"]] = dpu_count.get(b["dpu"], 0) + 1
    in_count = sum(1 for t in tags if t.get("direction") == "in")
    out_count = sum(1 for t in tags if t.get("direction") == "out")
    return jsonify({
        "blocks_total": len(blocks),
        "blocks_by_type": type_count,
        "blocks_by_dpu": dpu_count,
        "connections_total": len(conns),
        "tags_total": len(tags),
        "tags_in": in_count,
        "tags_out": out_count,
        "csv": csv_info,
        "config_files": CONFIG,
    })


# ---------- 主页面 ----------

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>DCS 仿真组态查看器</title>
<style>
* { box-sizing: border-box; }
body { font-family: "Consolas", "Microsoft YaHei", monospace; font-size: 12px;
       margin: 0; padding: 0; background: #fff; color: #222; }
header { background: #111; color: #eee; padding: 6px 12px; display: flex;
         justify-content: space-between; align-items: center; }
header h1 { font-size: 13px; margin: 0; font-weight: normal; }
header .meta { font-size: 11px; color: #aaa; }
header .meta b { color: #fff; }
main { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1px;
       background: #ddd; height: calc(100vh - 30px); }
section { background: #fff; overflow-y: auto; padding: 8px 10px; }
section h2 { font-size: 12px; margin: 0 0 8px; color: #000; border-bottom: 1px solid #000;
             padding-bottom: 3px; position: sticky; top: -8px; background: #fff; }
.row { padding: 2px 0; border-bottom: 1px dotted #ddd; }
.row:hover { background: #f6f6f6; }
.name { color: #000; font-weight: bold; }
.type { color: #666; }
.desc { color: #888; font-style: italic; }
.dpu-grp { margin-top: 6px; }
.dpu-grp summary { cursor: pointer; padding: 3px 5px; background: #eee; font-weight: bold;
                   border-left: 3px solid #000; }
.dpu-grp summary:hover { background: #ddd; }
table { width: 100%; border-collapse: collapse; }
table th, table td { text-align: left; padding: 2px 4px; border-bottom: 1px dotted #eee; }
table th { background: #f0f0f0; position: sticky; top: 20px; }
.val { font-family: monospace; color: #000; text-align: right; }
.empty { color: #999; font-style: italic; padding: 10px; }
.refresh-btn { background: #333; color: #fff; border: 0; padding: 3px 8px; cursor: pointer;
               font-family: inherit; font-size: 11px; }
.refresh-btn:hover { background: #000; }
code { font-family: monospace; background: #f0f0f0; padding: 0 3px; }
.auto-flag { color: #0a0; font-size: 10px; }
</style>
</head>
<body>
<header>
  <h1>DCS 仿真组态查看器 <span class="meta" id="summary">加载中...</span></h1>
  <span class="meta">
    刷新数据区 <span class="auto-flag">●自动</span> 每 2 秒 |
    <button class="refresh-btn" onclick="loadAll()">手动刷新</button> |
    <a href="/script" style="color:#0f0;text-decoration:none;font-weight:bold">🔌 赋值脚本 →</a>
    <a href="/edit" style="color:#fb6;text-decoration:none">🛠️ YAML 编辑器</a>
  </span>
</header>
<main>
  <section id="blocks-section">
    <h2>Blocks (<span id="blocks-count">…</span>)</h2>
    <div id="blocks"></div>
  </section>
  <section id="conns-section">
    <h2>Connections (<span id="conns-count">…</span>)</h2>
    <div id="conns"></div>
    <h2 style="margin-top: 15px;">TagMap (<span id="tags-count">…</span>)</h2>
    <div id="tags"></div>
  </section>
  <section id="latest-section">
    <h2>最新仿真数据 <span style="font-size:10px;color:#999">(每 2 秒刷新)</span></h2>
    <div id="latest"></div>
  </section>
</main>

<script>
async function fetchJson(url) {
  const r = await fetch(url);
  return r.json();
}

async function loadSummary() {
  const s = await fetchJson('/api/summary');
  document.getElementById('summary').innerHTML =
    `Blocks <b>${s.blocks_total}</b> · ` +
    `Connections <b>${s.connections_total}</b> · ` +
    `Tags <b>${s.tags_total}</b> (in=${s.tags_in}, out=${s.tags_out})` +
    (s.csv.available ? ` · CSV <b>${s.csv.rows}</b> 行` : ` · <span style="color:#f80">无 CSV</span>`);
  document.getElementById('blocks-count').textContent = s.blocks_total +
    ' = ' + Object.entries(s.blocks_by_type).map(([k,v]) => `${k}×${v}`).join(' + ');
  document.getElementById('conns-count').textContent = s.connections_total;
  document.getElementById('tags-count').textContent = s.tags_total;
}

async function loadBlocks() {
  const blocks = await fetchJson('/api/blocks');
  const byDpu = {};
  for (const b of blocks) {
    if (!byDpu[b.dpu]) byDpu[b.dpu] = [];
    byDpu[b.dpu].push(b);
  }
  const out = [];
  for (const dpu of Object.keys(byDpu).sort()) {
    const items = byDpu[dpu];
    const inner = items.map(b => `
      <div class="row">
        <span class="name">${b.name}</span>
        <span class="type">[${b.type}]</span>
        <code>${JSON.stringify(b.params).replace(/"/g,'')}</code><br>
        <span class="desc">${b.desc || ''}</span>
      </div>`).join('');
    out.push(`<details class="dpu-grp" open>
      <summary>${dpu} (${items.length})</summary>
      ${inner}
    </details>`);
  }
  document.getElementById('blocks').innerHTML = out.join('') ||
    '<div class="empty">无 Block</div>';
}

async function loadConns() {
  const conns = await fetchJson('/api/connections');
  if (conns.length === 0) {
    document.getElementById('conns').innerHTML =
      '<div class="empty">无横向连接 — 配对模式下每块独立 (OPC→block→OPC)</div>';
  } else {
    document.getElementById('conns').innerHTML = '<table><tr><th>from</th><th>→</th><th>to</th></tr>' +
      conns.map(c => `<tr><td><code>${c.from}</code></td><td>→</td><td><code>${c.to}</code></td></tr>`).join('') +
      '</table>';
  }
  const tags = await fetchJson('/api/tagmap');
  if (tags.length === 0) {
    document.getElementById('tags').innerHTML = '<div class="empty">无 tagmap</div>';
  } else {
    const rows = tags.map(t => {
      const dirStyle = t.direction === 'in' ? 'color:#070' : 'color:#700';
      return `<tr><td><code>${t.tag}</code></td>` +
             `<td style="${dirStyle}">${t.direction || '?'}</td>` +
             `<td>${t.dtype || ''}</td>` +
             `<td style="font-size:10px;color:#888"><code>${t.opc_node || ''}</code></td></tr>`;
    }).join('');
    document.getElementById('tags').innerHTML = '<table><tr><th>tag</th><th>方向</th><th>dtype</th><th>OPC 节点</th></tr>' + rows + '</table>';
  }
}

async function loadLatest() {
  const r = await fetchJson('/api/latest');
  if (!r.available) {
    document.getElementById('latest').innerHTML =
      `<div class="empty">无 CSV 数据 (${r.path || '?'}). 先跑 <code>py -3.12 -m src.cli run ...</code> 生成数据。</div>`;
    return;
  }
  const rows = Object.entries(r.latest).map(([k, v]) => {
    if (k === 'time_s') {
      return `<tr><td style="color:#06a;font-weight:bold">t = ${v} s</td><td class="val" colspan="0">(共 ${r.rows} 行)</td></tr>`;
    }
    return `<tr><td><code>${k}</code></td><td class="val">${v}</td></tr>`;
  }).join('');
  document.getElementById('latest').innerHTML = '<table>' + rows + '</table>';
}

async function loadAll() {
  await Promise.all([loadSummary(), loadBlocks(), loadConns()]);
  await loadLatest();
}

loadAll();
// 数据区每 2 秒自动刷新(组态区不重刷,因为不应该运行时变)
setInterval(loadLatest, 2000);
setInterval(loadSummary, 2000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


# ---------- 编辑器 ----------

def _file_kind(filename: str) -> str:
    """models / connections / tagmap / unknown"""
    if filename.startswith("models"):
        return "models"
    if filename.startswith("connections"):
        return "connections"
    if filename.startswith("tagmap"):
        return "tagmap"
    return "unknown"


def _validate_yaml_content(filename: str, content: str) -> tuple:
    """
    校验内容,返回 (ok, error_msg, parsed_doc)
    1. yaml 语法
    2. 顶层结构 (blocks / connections / tags 键 + list 类型)
    """
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return False, f"YAML 语法错误: {e}", None
    if doc is not None and not isinstance(doc, dict):
        return False, "顶层应为 mapping (字典)", None
    doc = doc or {}
    kind = _file_kind(filename)
    if kind == "models":
        v = doc.get("blocks")
        if v is not None and not isinstance(v, list):
            return False, "models 文件应为 {blocks: [...]}", None
    elif kind == "connections":
        v = doc.get("connections")
        if v is not None and not isinstance(v, list):
            return False, "connections 文件应为 {connections: [...]}", None
    elif kind == "tagmap":
        v = doc.get("tags")
        if v is not None and not isinstance(v, list):
            return False, "tagmap 文件应为 {tags: [...]}", None
    return True, "", doc


def _deep_load_test(filename: str) -> tuple:
    """
    写盘后做完整加载校验, 失败返回 (False, error_msg)
    - models / connections: 跑 GraphRunner.from_yaml(同后缀的配套)
    - tagmap: 跑 TagMap.from_yaml
    """
    kind = _file_kind(filename)
    try:
        if kind in ("models", "connections"):
            is_gen = filename.endswith(".generated.yaml")
            suffix = ".generated.yaml" if is_gen else ".yaml"
            mp = CONFIG_DIR / f"models{suffix}"
            cp = CONFIG_DIR / f"connections{suffix}"
            if not mp.exists() or not cp.exists():
                return True, f"(配套文件不全, 跳过深度校验: {mp.name}/{cp.name})"
            GraphRunner.from_yaml(str(mp), str(cp), dt=0.2)
        elif kind == "tagmap":
            TagMap.from_yaml(str(CONFIG_DIR / filename))
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


@app.route("/api/yaml/<filename>")
def api_yaml_get(filename: str):
    if filename not in EDITABLE_FILES:
        return jsonify({"error": f"不在白名单: {filename}"}), 400
    p = CONFIG_DIR / filename
    if not p.exists():
        return jsonify({"content": "", "exists": False, "filename": filename})
    return jsonify({
        "content": p.read_text(encoding="utf-8"),
        "exists": True,
        "filename": filename,
        "size": p.stat().st_size,
    })


@app.route("/api/yaml/<filename>", methods=["POST"])
def api_yaml_save(filename: str):
    if filename not in EDITABLE_FILES:
        return jsonify({"ok": False, "error": f"不在白名单: {filename}"}), 400
    content = (request.get_json(force=True) or {}).get("content", "")
    if not isinstance(content, str):
        return jsonify({"ok": False, "error": "content 必须是字符串"}), 400

    # 1. 语法 + 结构校验
    ok, err, _ = _validate_yaml_content(filename, content)
    if not ok:
        return jsonify({"ok": False, "stage": "syntax", "error": err}), 400

    p = CONFIG_DIR / filename
    backup_path = p.with_suffix(p.suffix + ".bak")

    # 2. 备份当前(若存在)
    backup_content = None
    if p.exists():
        backup_content = p.read_text(encoding="utf-8")
        backup_path.write_text(backup_content, encoding="utf-8")

    # 3. 写入
    p.write_text(content, encoding="utf-8")

    # 4. 完整加载校验
    ok, err = _deep_load_test(filename)
    if not ok:
        # 回滚
        if backup_content is not None:
            p.write_text(backup_content, encoding="utf-8")
            rollback_msg = "已回滚到改前内容"
        else:
            p.unlink(missing_ok=True)
            rollback_msg = "已删除新建的文件"
        return jsonify({
            "ok": False, "stage": "deep_load", "error": err,
            "rollback": rollback_msg,
        }), 400

    return jsonify({
        "ok": True,
        "msg": f"已保存 {filename} ({len(content)} 字节)",
        "backup": str(backup_path.name) if backup_content else None,
    })


@app.route("/api/yaml/<filename>/rollback", methods=["POST"])
def api_yaml_rollback(filename: str):
    """从 .bak 恢复"""
    if filename not in EDITABLE_FILES:
        return jsonify({"ok": False, "error": f"不在白名单: {filename}"}), 400
    p = CONFIG_DIR / filename
    backup_path = p.with_suffix(p.suffix + ".bak")
    if not backup_path.exists():
        return jsonify({"ok": False, "error": "无 .bak 备份"}), 404
    p.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
    return jsonify({"ok": True, "msg": f"已从 {backup_path.name} 恢复"})


@app.route("/edit")
def edit_page():
    return render_template_string(EDIT_HTML, files=EDITABLE_FILES)


# ---------- 赋值脚本(主功能)----------

SCRIPT_PATH = CONFIG_DIR / "script.txt"
BACKUP_DIR = CONFIG_DIR / "script_backups"   # 时间戳备份目录
BACKUP_KEEP = 30                              # 保留最近 N 个

def _make_script_backup(reason: str = "save"):
    """把当前 script.txt 备份到 script_backups/script_YYYYMMDD_HHMMSS_<reason>.txt
    旧备份只留最近 BACKUP_KEEP 个。返回备份文件名(或 None)。
    """
    import shutil
    from datetime import datetime
    if not SCRIPT_PATH.exists(): return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c for c in reason if c.isalnum() or c in "-_")[:20] or "save"
    bk = BACKUP_DIR / f"script_{ts}_{safe_reason}.txt"
    shutil.copy2(SCRIPT_PATH, bk)
    # 清理: 只留最近 N 个
    backups = sorted(BACKUP_DIR.glob("script_*.txt"))
    for old in backups[:-BACKUP_KEEP]:
        try: old.unlink()
        except OSError: pass
    return bk.name


@app.route("/api/script")
def api_script_get():
    if not SCRIPT_PATH.exists():
        return jsonify({"content": "", "exists": False})
    return jsonify({
        "content": SCRIPT_PATH.read_text(encoding="utf-8"),
        "exists": True,
        "size": SCRIPT_PATH.stat().st_size,
    })


@app.route("/api/script", methods=["POST"])
def api_script_save():
    content = (request.get_json(force=True) or {}).get("content", "")
    # 解析校验
    try:
        pairs = rt.parse_script(content)
    except rt.ParseError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    # 备份后写: 单 .bak (上一份) + 时间戳备份(历史链)
    if SCRIPT_PATH.exists():
        bak = SCRIPT_PATH.with_suffix(".txt.bak")
        bak.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        _make_script_backup("save")
    SCRIPT_PATH.parent.mkdir(exist_ok=True)
    SCRIPT_PATH.write_text(content, encoding="utf-8")
    rt.log_event("save", f"💾 保存脚本 ({len(content)} 字节, {len(pairs)} 对)",
                 {"bytes": len(content), "pairs": len(pairs)})
    # 保存(=编译): 自动清理过期 state — 新脚本不再引用的 LAG/RS/CCS key 直接剪掉,
    # 让镜像下次保存不带历史残留
    pruned = rt.prune_state_to_pairs(pairs)
    prune_msg = ""
    total_pruned = pruned["lag"] + pruned["rs"] + pruned["ccs"]
    if total_pruned > 0:
        prune_msg = f" · 🧹 清理过期 {total_pruned} 项"
    # OPC 循环在跑 → 自动在线下装 (0 断流, 不用点 ▶ 运行)
    hot_swap_msg = ""
    hot_swapped = False
    if rt.get_status().get("running"):
        swap_result = rt.swap_pairs(pairs)
        hot_swapped = bool(swap_result.get("hot_swapped"))
        if hot_swapped:
            hot_swap_msg = f" · ♻ 在线下装 (0 断流)"
    return jsonify({
        "ok": True,
        "msg": f"已保存 ({len(content)} 字节, {len(pairs)} 对赋值){hot_swap_msg}{prune_msg}",
        "pairs_count": len(pairs),
        "hot_swapped": hot_swapped,
        "pruned": pruned,
    })


@app.route("/api/script/generate", methods=["POST"])
def api_script_generate():
    """按工艺规则自动生成脚本 (analog + digital 全部)"""
    text = rt.generate_script_from_tagmap(CONFIG["tagmap"])
    return jsonify({"ok": True, "content": text})


@app.route("/api/script/swap_pairs", methods=["POST"])
def api_script_swap_pairs():
    """在线下装: 不停 OPC 循环, 原子替换 pairs.
    body 可选 {content}; 不传则从 config/script.txt 读.
    """
    body = request.get_json(force=True, silent=True) or {}
    content = body.get("content")
    if content is None:
        if not SCRIPT_PATH.exists():
            return jsonify({"ok": False, "error": "config/script.txt 不存在"}), 400
        content = SCRIPT_PATH.read_text(encoding="utf-8")
    try:
        pairs = rt.parse_script(content)
    except rt.ParseError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not pairs:
        return jsonify({"ok": False, "error": "脚本为空"}), 400
    return jsonify(rt.swap_pairs(pairs))


def _rewrite_var_desc(content: str, var_name: str, new_desc: str):
    """对 content 里每个 $var 出现:
    - 已跟 (...) → 替换括号内描述
    - 未跟 () → 在后面插入 (新描述)
    跳过注释 + 跳过"描述括号"内部 (描述定义: `(` 紧跟 NODE/VAR 后, 无空格).
    数学表达式 (...) 和函数调用 LIMIT(...) 不算描述, 里面的 $var 照常处理.
    返回 (新内容, 替换次数).
    """
    import re as _re
    out_lines = []
    total = 0
    for line in content.split("\n"):
        if line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        # 切掉行尾 # 注释
        cut = -1
        d = 0
        for i, ch in enumerate(line):
            if ch in "([（": d += 1
            elif ch in ")]）": d = max(0, d - 1)
            elif ch == "#" and d == 0:
                if i == 0 or line[i-1].isspace():
                    cut = i; break
        code = line[:cut] if cut >= 0 else line
        tail = line[cut:] if cut >= 0 else ""

        new_code = []
        i = 0
        desc_depth = 0   # 仅追踪"描述括号"的深度, 数学/函数 () 不算
        n = len(code)
        while i < n:
            ch = code[i]
            # 已在描述内部 — 只跟踪括号嵌套, 不找 $var
            if desc_depth > 0:
                if ch in "(（": desc_depth += 1
                elif ch in ")）": desc_depth -= 1
                new_code.append(ch); i += 1
                continue
            # 描述外: 判断当前 ( 是不是描述开始 (紧跟 NODE/VAR 后)
            if ch in "(（":
                is_desc = False
                if i > 0 and (code[i-1].isalnum() or code[i-1] in "$._"):
                    # 走回去找 token
                    j = i
                    while j > 0 and (code[j-1].isalnum() or code[j-1] in "$._"):
                        j -= 1
                    token = code[j:i]
                    # $var 或 DPU... 才算描述; FUNC (全大写) 是函数调用, 不算
                    if token.startswith("$"):
                        is_desc = True
                    elif token.startswith("DPU") and len(token) >= 7:
                        is_desc = True
                if is_desc:
                    desc_depth = 1
                new_code.append(ch); i += 1
                continue
            if ch in ")）":
                # 描述外的 ) 是表达式收尾, 直接抄
                new_code.append(ch); i += 1
                continue
            # 找 $var
            if ch == "$":
                m = _re.match(r"\$[A-Za-z_]\w*", code[i:])
                if m and m.group(0) == var_name:
                    end = i + len(var_name)
                    j = end
                    while j < n and code[j].isspace():
                        j += 1
                    if j < n and code[j] in "(（":
                        # 已有 (...) 描述, 找配对右括号 (允许嵌套)
                        op = code[j]; cp = ")" if op == "(" else "）"
                        k = j + 1; d2 = 1
                        while k < n and d2 > 0:
                            if code[k] in "(（": d2 += 1
                            elif code[k] in ")）": d2 -= 1
                            k += 1
                        if d2 == 0:
                            new_code.append(var_name + "(" + new_desc + ")")
                            i = k
                            total += 1
                            continue
                    new_code.append(var_name + "(" + new_desc + ")")
                    i = end
                    total += 1
                    continue
            new_code.append(ch); i += 1
        out_lines.append("".join(new_code) + tail)
    return "\n".join(out_lines), total


@app.route("/api/script/var/rename_desc", methods=["POST"])
def api_var_rename_desc():
    """修改 $var 描述, 在脚本里所有出现位置统一更新.
    body: {var: "$Vh_A", new_desc: "新描述"}
    OPC 点的描述不动 (来自点表, 只读).
    """
    import re as _re
    body = request.get_json(force=True) or {}
    var_name = (body.get("var") or "").strip()
    new_desc = (body.get("new_desc") or "").strip()
    if not var_name.startswith("$") or not _re.match(r"^\$[A-Za-z_]\w*$", var_name):
        return jsonify({"ok": False, "error": f"非法的 $var 名: {var_name!r}"}), 400
    # 描述里禁用 () 和 # 和换行 (会破坏 parse), 自动替换成全角/空格
    new_desc = (new_desc
                .replace("\n", " ").replace("\r", " ")
                .replace("(", "[").replace(")", "]")
                .replace("（", "[").replace("）", "]")
                .replace("#", "＃"))[:60]   # 限 60 字
    if not SCRIPT_PATH.exists():
        return jsonify({"ok": False, "error": "config/script.txt 不存在"}), 400
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    new_content, count = _rewrite_var_desc(content, var_name, new_desc)
    if count == 0:
        return jsonify({"ok": False,
                        "error": f"脚本里没找到 {var_name} (检查名字大小写, 或 $var 是否已被定义)"}), 404
    # parse 校验
    try:
        rt.parse_script(new_content)
    except rt.ParseError as e:
        return jsonify({"ok": False,
                        "error": f"重命名后脚本 parse 失败: {e}, 已放弃 (脚本未变)"}), 400
    # 备份 + 写
    if SCRIPT_PATH.exists():
        bak = SCRIPT_PATH.with_suffix(".txt.bak")
        bak.write_text(content, encoding="utf-8")
        _make_script_backup("rename_desc")
    SCRIPT_PATH.write_text(new_content, encoding="utf-8")
    rt.log_event("save",
                 f"✏ 重命名 {var_name} 描述 → '{new_desc[:20]}{'...' if len(new_desc) > 20 else ''}' ({count} 处)",
                 {"var": var_name, "count": count, "new_desc": new_desc})
    return jsonify({"ok": True, "count": count, "new_desc": new_desc,
                    "msg": f"✏ {var_name} 描述已更新到 {count} 处"})


@app.route("/api/script/run", methods=["POST"])
def api_script_run():
    body = request.get_json(force=True) or {}
    dt = float(body.get("dt", 0.2))
    # 用脚本文件 or 请求里的 content
    content = body.get("content")
    if content is None:
        if not SCRIPT_PATH.exists():
            return jsonify({"ok": False, "error": "无 config/script.txt,先保存"}), 400
        content = SCRIPT_PATH.read_text(encoding="utf-8")
    try:
        pairs = rt.parse_script(content)
    except rt.ParseError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not pairs:
        return jsonify({"ok": False, "error": "脚本为空(全是注释?)"}), 400
    # 已在运行: 先 stop 再 start (热重启, 新加的赋值立刻生效)
    was_running = rt.get_status().get("running", False)
    if was_running:
        rt.stop()
    ok, msg = rt.start(pairs, dt=dt)
    if was_running and ok:
        msg = f"♻ 热重启: {msg}"
    return jsonify({"ok": ok, "msg": msg, "pairs_count": len(pairs)})


@app.route("/api/script/stop", methods=["POST"])
def api_script_stop():
    ok, msg = rt.stop()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/script/status")
def api_script_status():
    return jsonify(rt.get_status())


@app.route("/api/script/validate", methods=["POST"])
def api_script_validate():
    """逐行解析脚本, 返回每行错误 + 警告(不执行). 编辑器用来给错误/警告行染色.

    - errors:   parse 失败 (红波浪 + 浅红底)
    - warnings: parse 过但可疑 — 当前只查全角字符 (黄波浪 + 浅黄底)
                 全角 (），：；。 等中文输入法残留, parser 部分接受, 但建议改半角
    DSL 行行独立, 一行喂 parse_script() 单独 try/catch 就拿到全部错误.
    """
    import re as _re
    body = request.get_json(force=True, silent=True) or {}
    content = body.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "缺少 content"}), 400
    errors = []
    warnings = []
    # 描述括号里的全角不算 — 描述允许任意中文, 否则全是误报
    DESC_PAREN_RE = _re.compile(r"\([^()（）]*\)|（[^()（）]*）")
    FULLWIDTH_CHARS = "（），；：。？！"
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rt.parse_script(line)
        except rt.ParseError as e:
            msg = str(e)
            msg = msg.replace("第 1 行格式错", "格式错")\
                     .replace("第 1 行左/右不能为空", "左/右不能为空")\
                     .replace("第 1 行左边无效", "左边无效")\
                     .replace("第 1 行右边无效", "右边无效")
            errors.append({"line": i, "msg": msg})
            continue
        except Exception as e:
            errors.append({"line": i, "msg": str(e)})
            continue
        # 警告: 全角字符. 但描述括号里的全角不算, 先把描述抠掉再扫
        # 行尾 # 注释里的也不算
        cmt_idx = -1
        depth = 0
        for j, ch in enumerate(line):
            if ch in "([（": depth += 1
            elif ch in ")]）": depth = max(0, depth - 1)
            elif ch == "#" and depth == 0 and (j == 0 or line[j-1].isspace()):
                cmt_idx = j; break
        code_part = line[:cmt_idx] if cmt_idx >= 0 else line
        # 反复抠掉描述括号 (含嵌套, 用 while 直到没得抠)
        scan = code_part
        while True:
            new = DESC_PAREN_RE.sub("", scan)
            if new == scan: break
            scan = new
        bads = [c for c in scan if c in FULLWIDTH_CHARS]
        if bads:
            uniq = "".join(sorted(set(bads)))
            warnings.append({"line": i,
                "msg": f"全角字符 {uniq} (建议改半角, 否则容易混乱)"})
    return jsonify({"ok": True,
                    "errors": errors, "count": len(errors),
                    "warnings": warnings, "warn_count": len(warnings)})


@app.route("/api/opc/endpoint")
def api_opc_endpoint_get():
    """读 OPC 端点配置 (mode + 两个 URL + 当前 url + 是否在跑)"""
    cfg = rt.get_endpoint_config()
    cfg["running"] = rt.get_status().get("running", False)
    return jsonify(cfg)


@app.route("/api/opc/endpoint", methods=["POST"])
def api_opc_endpoint_set():
    """切换 mode + 可选更新 vm_url. 在跑时拒绝, 让用户先停"""
    body = request.get_json(force=True, silent=True) or {}
    mode = body.get("mode")
    vm_url = body.get("vm")
    if rt.get_status().get("running"):
        return jsonify({"ok": False,
                        "error": "OPC 循环在运行, 请先点【■ 停止】再切换端点"}), 409
    try:
        cfg = rt.set_endpoint_mode(mode, vm_url=vm_url)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    cfg["running"] = False
    return jsonify({"ok": True, **cfg})


@app.route("/api/opc/probe")
def api_opc_probe():
    """OPC 端点探活 (TCP 级, 后台 5s 自动探, 此接口读最近结果, 不阻塞).

    返回 {url, mode, ok, latency_ms, error, ts, running}
    - ok=True: TCP 通 (NTVDPU + 防火墙都没拦)
    - ok=False: 不通 + error 说明
    - ok=None: 探活还没跑过 (启动瞬间)
    """
    res = rt.get_probe_status()
    res["running"] = rt.get_status().get("running", False)
    return jsonify(res)


@app.route("/api/opc/probe", methods=["POST"])
def api_opc_probe_force():
    """立即重新探活一次 (不等 5s 周期). 切换端点 / 改 VM URL 后用."""
    res = rt._probe_once_and_store()
    res["running"] = rt.get_status().get("running", False)
    return jsonify(res)


# 由 __main__.py 在启动时填充 — 重启时原样传给新进程
_ORIGINAL_ARGV: list = []


@app.route("/api/viewer/restart", methods=["POST"])
def api_viewer_restart():
    """从 UI 重启 viewer 进程 (改了 Python 代码后, 不用回终端 Ctrl+C).

    流程:
      1. 校验 OPC 未运行 (运行中拒绝 — 防误操作中断通讯)
      2. 用相同参数 spawn 一个 detached trampoline 子进程
      3. trampoline 等 1.5s (给当前进程释放 5002 端口的时间) 后启新 viewer
      4. 当前进程返回响应, 300ms 后 os._exit(0)
    """
    import os
    import subprocess
    import threading

    if rt.get_status().get("running"):
        return jsonify({
            "ok": False,
            "error": "OPC 循环在运行, 请先点【■ 停止】再重启"
        }), 409

    import sys as _sys
    cwd = os.getcwd()
    user_args = list(_ORIGINAL_ARGV)
    inner = [_sys.executable, "-m", "src.viewer"] + user_args

    # 跨平台 detached 启动小桥接进程, 等 1.5s 让父进程释放端口后再起新 viewer
    py_code = (
        "import time, subprocess\n"
        "time.sleep(1.5)\n"
        f"subprocess.Popen({inner!r}, cwd={cwd!r})\n"
    )
    try:
        if _sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [_sys.executable, "-c", py_code],
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                close_fds=True, cwd=cwd,
            )
        else:
            subprocess.Popen(
                [_sys.executable, "-c", py_code],
                start_new_session=True, close_fds=True, cwd=cwd,
            )
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"派生新进程失败: {e}"}), 500

    rt.log_event("system",
                 f"↻ 用户从 UI 触发 viewer 重启 (args={user_args})",
                 {"args": user_args})

    # 返回响应后短暂等待, 然后退出 — 让响应能完整送达浏览器
    def _bye():
        import time as _t
        _t.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_bye, daemon=True).start()

    return jsonify({
        "ok": True,
        "msg": "viewer 重启中, 约 3 秒后会自动重连"
    })


@app.route("/api/script/debug")
def api_script_debug():
    """完整 debug 包 (状态/摘要/失败 top/日志), 用于沟通调试"""
    return jsonify(rt.get_debug())


@app.route("/api/script/events")
def api_script_events():
    """事件时间线 (启停/重连/镜像/清状态等), 新→旧"""
    return jsonify({"events": rt.get_events()})


@app.route("/api/script/reset_state", methods=["POST"])
def api_script_reset_state():
    """清空持久状态 (RS/LAG/中间变量) — '从头开始'仿真"""
    n = rt.reset_persistent_state()
    return jsonify({"ok": True, "cleared": n,
                    "msg": f"已清: RS {n['rs']} / LAG {n['lag']} / $var {n['var']}"})


@app.route("/api/script/reinit", methods=["POST"])
def api_script_reinit():
    """初始化跟踪态 — 清 LAG/$var/last_written, 保留 RS 锁存和镜像文件"""
    n = rt.reinit_tracking_state()
    return jsonify({"ok": True, "cleared": n,
                    "msg": f"已重置初值: LAG {n['lag']} / $var {n['var']} 清 (RS 锁存保留, 镜像保留)"})


@app.route("/api/script/reinit_from_dcs", methods=["POST"])
def api_script_reinit_from_dcs():
    """从 DCS 读当前值, 把直接写 OPC 的 LAG 的 lag_state 设为 DCS 值 (VM 镜像还原后用)"""
    return jsonify(rt.reinit_lag_from_dcs())


@app.route("/api/script/dryrun_preview", methods=["POST"])
def api_script_dryrun_preview():
    """干运行预演: 读 DCS 现状 → 算一遍 → 报告 (computed vs actual vs diff vs risk)"""
    return jsonify(rt.dryrun_preview())


@app.route("/api/script/state/save", methods=["POST"])
def api_script_state_save():
    """显式保存当前状态镜像 (RS/LAG/中间变量)"""
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(rt.save_state_snapshot(force=bool(body.get("force"))))


@app.route("/api/script/state/restore", methods=["POST"])
def api_script_state_restore():
    """从镜像恢复状态. body 里可选 {"path": "snapshot_YYYYMMDD_HHMMSS.json"} 从历史副本恢复"""
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(rt.restore_state_snapshot(path=body.get("path")))


@app.route("/api/script/state/backups")
def api_script_state_backups():
    """列出所有历史镜像副本"""
    return jsonify({"items": rt.list_snapshot_backups()})


@app.route("/api/script/state/backup/delete", methods=["POST"])
def api_script_state_backup_delete():
    """删除指定的历史镜像副本"""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("path") or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".json"):
        return jsonify({"ok": False, "error": f"非法历史镜像名: {name!r}"}), 400
    p = rt._SNAPSHOT_BAK_DIR / name
    if not p.exists():
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    try:
        p.unlink()
        rt.log_event("snapshot", f"🗑 删除历史镜像 {name}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/script/state/info")
def api_script_state_info():
    """当前镜像信息 (?detail=1 时返回每个模块的具体值)"""
    detail = request.args.get("detail") in ("1", "true", "yes")
    return jsonify(rt.get_snapshot_info(with_detail=detail))


@app.route("/api/script/state/delete", methods=["POST"])
def api_script_state_delete():
    """删除镜像文件"""
    from src.viewer.runtime import _SNAPSHOT_PATH
    try:
        if _SNAPSHOT_PATH.exists():
            _SNAPSHOT_PATH.unlink()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/script/backups")
def api_script_backups_list():
    """列出所有时间戳备份(新→旧)"""
    from datetime import datetime
    if not BACKUP_DIR.exists():
        return jsonify({"items": []})
    items = []
    for fn in sorted(BACKUP_DIR.glob("script_*.txt"), reverse=True):
        st = fn.stat()
        items.append({
            "name": fn.name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return jsonify({"items": items, "keep": BACKUP_KEEP})


@app.route("/api/script/backups/<name>")
def api_script_backup_get(name):
    """获取指定备份内容(供前端预览/恢复用)"""
    # 文件名级校验
    if not name.startswith("script_") or not name.endswith(".txt") \
       or any(c in name for c in ("/", "\\", ":", "..", "\x00")):
        return jsonify({"error": "非法备份名"}), 400
    fn = BACKUP_DIR / name
    # 路径级校验: 解析后必须在 BACKUP_DIR 内 (防符号链/特殊解析)
    try:
        real = fn.resolve(strict=False)
        bdir = BACKUP_DIR.resolve(strict=False)
        if not str(real).startswith(str(bdir)):
            return jsonify({"error": "非法路径"}), 400
    except (OSError, ValueError):
        return jsonify({"error": "路径解析失败"}), 400
    if not fn.exists():
        return jsonify({"error": "备份不存在"}), 404
    return jsonify({"content": fn.read_text(encoding="utf-8"), "name": name})


@app.route("/api/script/backup", methods=["POST"])
def api_script_backup_make():
    """打备份。两种用法:
       - 不传 content:备份当前盘上 script.txt
       - 传 content:把 content 直接写到备份目录(用于备份 editor 未保存内容)
    """
    from datetime import datetime
    body = request.get_json(force=True, silent=True) or {}
    reason = body.get("reason", "manual")
    content = body.get("content")
    safe_reason = "".join(c for c in reason if c.isalnum() or c in "-_")[:20] or "manual"

    if content is not None:
        # 备份 editor 内容
        if not content.strip():
            return jsonify({"ok": False, "error": "editor 为空,不备份"}), 400
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk = BACKUP_DIR / f"script_{ts}_{safe_reason}.txt"
        bk.write_text(content, encoding="utf-8")
        # 清理: 只留最近 N 个
        backups = sorted(BACKUP_DIR.glob("script_*.txt"))
        for old in backups[:-BACKUP_KEEP]:
            try: old.unlink()
            except OSError: pass
        return jsonify({"ok": True, "msg": f"已备份 editor → {bk.name}", "name": bk.name})

    name = _make_script_backup(safe_reason)
    if name is None:
        return jsonify({"ok": False, "error": "脚本文件不存在"}), 400
    return jsonify({"ok": True, "msg": f"已备份 → {name}", "name": name})


# 缓存 symbols(扫一次 CSV 即可,IO 点表是静态的)
_SYMBOLS_CACHE = None

@app.route("/api/script/symbols")
def api_script_symbols():
    """所有 IO 点(短码/描述/KKS/方向)— 给编辑器自动补全用"""
    global _SYMBOLS_CACHE
    if _SYMBOLS_CACHE is None:
        from src.sim_engine.io_pairing_gen import load_points
        import glob as _g
        import csv as _csv
        import re as _re
        items = []
        # 优先用"简化"目录, 找不到回退老路径
        candidates = sorted(_g.glob(f"{POINT_TABLE_DIR}/{POINT_TABLE_GLOB}"))
        if not candidates:
            candidates = sorted(_g.glob("YQ3SIM-IO/DPU*.csv"))
            candidates = [c for c in candidates if "_" not in Path(c).stem
                          or Path(c).stem.startswith("DPU")]
        # SH 段正则: SH<图号>.<块名>.<端子>
        # 块名允许字母/数字/下划线任意组合 (PROYR 纯字母, AALMZML 纯字母, PRO21120, X_VALVE 都得过)
        SH_RE = _re.compile(r"^(SH\d+)\.([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)$")
        for fn in candidates:
            dpu = _dpu_from_filename(Path(fn).stem)
            # (1) HW.XX0000.PV — 走 load_points (严格筛)
            for p in load_points(fn):
                short = p["name"].replace("HW.", "").replace(".PV", "")
                items.append({
                    "label": f"{dpu}.{short}",
                    "desc": (p.get("desc") or "").strip(),
                    "kks": (p.get("kks") or "").strip(),
                    "code": p.get("code") or "",
                })
            # (2) SH<图号>.<块名>.<端子> — 自己扫(组态块端子,可读可写)
            try:
                lines = Path(fn).read_bytes().decode("gbk", errors="replace").splitlines()
                for ln in lines[2:]:
                    try: row = next(_csv.reader([ln]))
                    except: continue
                    if len(row) < 5: continue
                    name = row[1].strip()
                    m = SH_RE.match(name)
                    if not m: continue
                    items.append({
                        "label": f"{dpu}.{name}",   # DPU3013.SH0500.PRO21120.IN
                        "desc": row[2].strip(),
                        "kks": (row[3].strip() if len(row) > 3 else ""),
                        "code": m.group(3),         # IN / OUT / PV (端子类型)
                    })
            except Exception as e:
                logger.warning(f"扫 SH 段失败 [{fn}]: {e}")
        _SYMBOLS_CACHE = items
    return jsonify({"items": _SYMBOLS_CACHE, "count": len(_SYMBOLS_CACHE)})


@app.route("/api/script/symbols/reload", methods=["POST"])
def api_script_symbols_reload():
    """点表 CSV 改了之后重新加载 symbols (清缓存)"""
    global _SYMBOLS_CACHE
    _SYMBOLS_CACHE = None
    # 立即重新加载一次
    return api_script_symbols()


@app.route("/api/script/symbols/from_opc", methods=["POST"])
def api_script_symbols_from_opc():
    """从 OPC Server 浏览实际点表, merge 进 symbols 缓存。
    优势:不依赖 CSV 同步, 新增/删除的硬件点立即可见。
    缺点:OPC BrowseName 只有点名,描述/KKS 仍需依靠 CSV 补充。
    """
    import asyncio
    from src.opc_client.client import OPCClient

    body = request.get_json(force=True, silent=True) or {}
    dpus = body.get("dpus") or []
    opc_url = body.get("opc_url", "opc.tcp://localhost:9440")

    # 默认: 浏览简化目录里所有 *_S.csv 对应的 DPU
    if not dpus:
        seen = set()
        import glob as _g
        for fn in _g.glob(f"{POINT_TABLE_DIR}/{POINT_TABLE_GLOB}"):
            seen.add(_dpu_from_filename(Path(fn).stem))
        # 回退
        if not seen:
            for fn in _g.glob("YQ3SIM-IO/DPU*.csv"):
                stem = Path(fn).stem
                if "_" in stem: continue
                seen.add(stem)
        dpus = sorted(seen)

    async def _run():
        client = OPCClient(opc_url)
        try:
            await client.connect(retry_count=2, retry_interval=2.0)
        except Exception as e:
            return None, f"OPC 连接失败: {e}"
        try:
            all_pts = []
            for dpu in dpus:
                pts = await client.browse_hw_points(dpu)
                for p in pts:
                    p["dpu"] = dpu
                all_pts.extend(pts)
            return all_pts, None
        finally:
            try: await client.disconnect()
            except Exception: pass

    try:
        pts, err = asyncio.run(_run())
    except Exception as e:
        return jsonify({"ok": False, "error": f"浏览异常: {e}"}), 500
    if err:
        return jsonify({"ok": False, "error": err}), 502

    # merge 进 _SYMBOLS_CACHE — 已有的(CSV 已扫到)保留描述/KKS, 新点用 OPC 名
    global _SYMBOLS_CACHE
    if _SYMBOLS_CACHE is None:
        api_script_symbols()  # 触发首次加载
    by_label = {s["label"]: s for s in (_SYMBOLS_CACHE or [])}
    added = 0
    for p in pts:
        label = f"{p['dpu']}.{p['name']}"
        if label not in by_label:
            by_label[label] = {
                "label": label, "desc": "",
                "kks": "", "code": p["code"],
                "_from_opc": True,
            }
            added += 1
    _SYMBOLS_CACHE = list(by_label.values())
    return jsonify({
        "ok": True,
        "msg": f"OPC 浏览 {len(dpus)} 个 DPU, 共 {len(pts)} 个 HW 点, 新增 {added} 个",
        "count": len(_SYMBOLS_CACHE),
        "added": added,
        "dpus": dpus,
    })


@app.route("/script")
def script_page():
    return render_template_string(SCRIPT_HTML)


SCRIPT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>赋值脚本 - DCS 仿真</title>
<style>
* { box-sizing: border-box; }
body { font-family: "Consolas", "Microsoft YaHei", monospace; font-size: 12px;
       margin: 0; padding: 0; background: #fff; color: #222; }
header { background: #111; color: #eee; padding: 6px 12px; display: flex;
         justify-content: space-between; align-items: center; }
header h1 { font-size: 13px; margin: 0; font-weight: normal; }
header a { color: #8cf; text-decoration: none; margin-left: 10px; }
header a:hover { text-decoration: underline; }
.toolbar { background: #f2f2f2; padding: 8px 12px; border-bottom: 1px solid #ccc;
           display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar button {
    font-family: inherit; font-size: 12px; padding: 5px 12px;
    border: 1px solid #888; background: #fff; cursor: pointer;
}
.toolbar button.primary { background: #0a0; color: #fff; border-color: #0a0; font-weight: bold; }
.toolbar button.primary:hover { background: #080; }
.toolbar button.stop { background: #c33; color: #fff; border-color: #c33; }
.toolbar button.stop:hover { background: #a00; }
.toolbar button:hover { background: #eee; }
.toolbar .sep { width: 1px; background: #ccc; height: 22px; }
.toolbar .grp-label { font-size: 10px; color: #888; margin-right: 2px;
                       text-transform: uppercase; letter-spacing: 0.3px; user-select: none; }
.toolbar.toolbar-2nd { padding: 6px 12px; background: #f8f8f8; border-top: 0; }
.toolbar.toolbar-2nd button { font-size: 11px; padding: 4px 10px; }
.toolbar.toolbar-2nd .grp-label { font-size: 9.5px; }
/* 下拉折叠菜单 (低频按钮组) */
.dropdown-wrap { position: relative; display: inline-block; }
.dropdown-menu { position: absolute; top: calc(100% + 2px); left: 0; z-index: 200;
                  background: #fff; border: 1px solid #888;
                  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
                  min-width: 150px; padding: 4px 0; }
.dropdown-menu button { display: block; width: 100%; text-align: left;
                        border: 0; border-radius: 0; background: transparent;
                        padding: 6px 14px; font-size: 11px; cursor: pointer; }
.dropdown-menu button:hover { background: #eef6ff; }
#status { padding: 6px 12px; min-height: 22px; font-size: 11px;
          background: #f8f8f8; border-bottom: 1px solid #ddd;
          font-family: monospace; white-space: pre-wrap; }
#cursorpos { float: right; color: #888; font-family: monospace; font-size: 11px;
             padding: 2px 8px; background: #f0f0f0; border-radius: 2px;
             cursor: pointer; }
#cursorpos:hover { background: #ddd; color: #000; }
#status .ok { color: #060; }
#status .err { color: #900; }
#status .run { color: #06a; font-weight: bold; }
.hint { background: #fffce0; color: #555; padding: 6px 12px;
        border-bottom: 1px solid #eec; font-size: 11px; }
.hint code { background: #fff; padding: 0 4px; border: 1px solid #ddd; }
.main { display: grid; grid-template-columns: 2fr 1fr; gap: 1px; background: #ddd;
        height: calc(100vh - 230px); }
.main > section { background: #fff; overflow: auto; }
.ed-wrap { display: flex; height: 100%; background: #fff; }
.line-nums { background: #f5f5f5; color: #999; font-family: "Consolas", monospace;
             font-size: 12px; line-height: 1.5; padding: 10px 6px 10px 8px;
             text-align: right; user-select: none; border-right: 1px solid #ddd;
             white-space: pre; overflow: hidden; min-width: 36px; flex-shrink: 0; }
/* editor 容器 — textarea 在上(透明文字 + 真光标), highlight 层在下 */
.ed-stack { position: relative; flex: 1; height: 100%; overflow: hidden; }
.hl-layer, #editor {
  position: absolute; inset: 0; margin: 0; padding: 10px 12px;
  font-family: "Consolas", monospace; font-size: 12px;
  line-height: 1.5; tab-size: 2;
  border: 0; outline: none; resize: none;
  white-space: pre-wrap; word-wrap: break-word;
  overflow: auto;
}
.hl-layer { pointer-events: none; color: #222; background: transparent; }
/* token 区域 (hl-tag/hl-var, 都带 data-token) 启用 pointer-events, 让悬停 elementFromPoint 拿得到 */
.hl-layer [data-token] { pointer-events: auto; cursor: help; }
#editor { background: transparent; color: transparent; caret-color: #000; z-index: 2; }
/* 染色 */
.hl-comment { color: #888; font-style: italic; }
.hl-err     { background: #fee; color: #900; }
.hl-fn      { color: #06a; font-weight: bold; }
.hl-num     { color: #c60; }
.hl-tag     { color: #048; }
.hl-var     { color: #90c; font-weight: bold; }   /* 中间变量 $xxx */
.hl-desc    { color: #888; }
.hl-op      { color: #000; font-weight: bold; }
.hl-mathop  { color: #c06; font-weight: bold; }   /* + - * / ^ */
.hl-bad     { background: #fcc;                            /* 浅红底, 一眼可见 */
              text-decoration: underline wavy #c00;        /* 红色波浪下划线 */
              text-decoration-skip-ink: none; }
.hl-warn    { background: #ffd;                            /* 浅黄底, 警告整行(给 errBadge 用) */
              text-decoration: underline wavy #c90;        /* 橙色波浪下划线 */
              text-decoration-skip-ink: none; }
/* 字符级警告: 单个全角字符画红+橙底, 鼠标悬停看是哪个 */
.hl-warn-char { background: #fb3; color: #900; font-weight: bold;
                border: 1px solid #c60; border-radius: 2px;
                padding: 0 1px; margin: 0 -1px;
                cursor: help; }
.values-panel { padding: 8px 12px; }
.values-panel h3 { margin: 0 0 6px; font-size: 12px; border-bottom: 1px solid #000;
                   padding-bottom: 3px; }
.values-panel table { width: 100%; border-collapse: collapse; font-size: 11px; }
.values-panel td { padding: 1px 4px; border-bottom: 1px dotted #eee; }
.values-panel .val { text-align: right; color: #06a; font-weight: bold; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
       background: #ccc; vertical-align: middle; margin-right: 4px; }
.dot.run { background: #0a0; animation: blink 1s infinite; }
.dot.err { background: #c33; }
@keyframes blink { 50% { opacity: 0.3; } }

/* 自动补全弹层 */
#acpopup { position: absolute; z-index: 1000; background: #fff;
           border: 1px solid #555; box-shadow: 0 4px 14px rgba(0,0,0,.18);
           font-family: "Consolas", monospace; font-size: 11px;
           min-width: 560px; max-height: 320px; overflow-y: auto;
           display: none; }
#acpopup .ac-hdr { background: #111; color: #ddd; padding: 3px 8px;
                   font-size: 10px; position: sticky; top: 0; }
#acpopup .ac-item { padding: 3px 8px; cursor: pointer;
                    border-bottom: 1px solid #f0f0f0;
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#acpopup .ac-item.sel { background: #cde6ff; }
#acpopup .ac-item:hover { background: #eef6ff; }
.ac-label { color: #048; font-weight: bold; }
.ac-code  { color: #888; margin-left: 6px; font-size: 10px;
            display: inline-block; min-width: 22px; }
.ac-desc  { color: #222; margin-left: 8px; }
.ac-kks   { color: #aaa; margin-left: 8px; font-size: 10px; }
mark      { background: #ff8; padding: 0; }
.ac-pgbtn { background: #333; color: #fff; border: 1px solid #555;
            padding: 0 6px; font-size: 11px; cursor: pointer; margin-left: 2px; }
.ac-pgbtn:hover:not(:disabled) { background: #555; }
.ac-pgbtn:disabled { color: #666; cursor: not-allowed; }

/* 帮助文档样式 */
#helpbody h2 { font-size: 14px; margin: 18px 0 8px; padding-bottom: 4px;
               border-bottom: 2px solid #111; color: #111; }
#helpbody h3 { font-size: 13px; margin: 12px 0 6px; color: #333; }
#helpbody code, #helpbody pre { font-family: "Consolas", monospace; font-size: 12px;
                                background: #f5f5f5; padding: 1px 5px; border-radius: 2px; }
#helpbody pre { padding: 8px 12px; overflow-x: auto;
                border-left: 3px solid #888; margin: 6px 0; line-height: 1.4; }
#helpbody table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 11px; }
#helpbody th, #helpbody td { border: 1px solid #ddd; padding: 4px 8px; text-align: left;
                              vertical-align: top; }
#helpbody th { background: #f0f0f0; font-weight: bold; }
#helpbody td.fn { font-family: "Consolas", monospace; color: #048; white-space: nowrap; }
#helpbody td.ex { font-family: "Consolas", monospace; font-size: 10px; color: #555; }
#helpbody ul { margin: 4px 0 8px 18px; padding: 0; }
#helpbody li { margin: 2px 0; }
#helpbody .kbd { display: inline-block; padding: 1px 6px;
                 background: #eee; border: 1px solid #aaa; border-radius: 3px;
                 font-family: monospace; font-size: 11px; margin: 0 2px; }
</style>
</head>
<body>
<header>
  <h1>🔌 赋值脚本 — OPC 实时桥接</h1>
  <span>
    <a href="/">看板</a>
    <a href="/edit">YAML 编辑器</a>
  </span>
</header>
<div class="hint">
  <b>快速</b>:<code>@关键词</code> 补全 · <code>Ctrl+G</code> 跳行 · <code>Ctrl+/</code> 注释 · <code>F1</code> 完整帮助
  · 函数:<code>RS RS_NOT NOT AND OR ADD SUB MUL DIV POW SQRT ABS MAX MIN LIMIT SEL LAG CHAR CCS_1000 STEAM_T</code>
</div>
<!-- ────── 第 1 行: 工作流 (编辑 → 状态准备 → 运行) ────── -->
<div class="toolbar">
  <span class="grp-label">编辑</span>
  <button onclick="saveScript()" title="编辑器内容保存到 config/script.txt (自动备份). 想"放弃当前改动回到磁盘版"用 F5 刷新或点【↻ 重启 viewer】">💾 保存</button>
  <span class="sep"></span>

  <span class="grp-label">运行准备</span>
  <button onclick="openSnapshot()"
          style="background:#06a;color:#fff;border:1px solid #06a"
          title="下载: 把磁盘保存的算法状态 (RS/LAG/$var) 送回本项目内存, 跑起来后写入工程. 用在本项目重启 / NTVDPU 重启后. 模态框含保存/删除/清空等管理.">📤 下载</button>
  <button onclick="syncLagFromDcs()"
          style="background:#a06;color:#fff;border:1px solid #a06"
          title="上载: 读取工程当前值, 把所有 LAG/RS 初值锚到工程现状. 用在工程状态变了 (VM 镜像还原 / CCMStudio 重下) 之后. 必须 OPC 循环已停.">📥 上载</button>
  <button onclick="dryrunPreview()"
          style="background:#0a8;color:#fff;border:1px solid #0a8"
          title="预演: 读 DCS 现状 → 用脚本算一周期 → 显示 (算出 vs DCS 现状 vs 差值 vs 风险). 建议先 📥 上载 → 🔍 预演 → ▶ 运行.">🔍 预演</button>
  <span class="sep"></span>

  <span class="grp-label">执行</span>
  <button class="primary" onclick="runIt()" title="保存编辑器内容 + 启动 OPC 循环 (热重启)">▶ 运行</button>
  <button class="stop" onclick="stopIt()" title="停 OPC 循环, 内存状态保留">■ 停止</button>
</div>

<!-- ────── 第 2 行: 查看 + 工程辅助 + 端点 ────── -->
<div class="toolbar toolbar-2nd">
  <span class="grp-label">查看</span>
  <button onclick="openDebug()" title="运行状态 / 失败统计 / 日志 (沟通时复制)">🩺 诊断</button>
  <button onclick="openEvents()" title="事件时间线: 启停 / 重连 / 镜像 / 清状态 / 端点切换">📜 事件</button>
  <button onclick="openBackups()" title="脚本时间戳备份历史 (每次保存自动留底)">📚 备份</button>
  <button onclick="openHelp()" title="DSL 语法 / 函数 / 快捷键 (F1)">❓ 帮助</button>
  <button onclick="restartViewer()"
          title="改了 Python 代码后, 不用回终端 — 重启 viewer 进程, 浏览器自动重连. OPC 必须先停"
          style="border-color:#a90">↻ 重启 viewer</button>
  <span class="sep"></span>

  <span class="grp-label">工程</span>
  <div class="dropdown-wrap">
    <button onclick="toggleDropdown('engDrop', event)" title="工程初始化辅助 (建脚本时用, 日常用不上)">
      🔧 初始化 ▾
    </button>
    <div id="engDrop" class="dropdown-menu" style="display:none">
      <button onclick="genSample(); closeDropdown('engDrop')"
              title="按白名单自动生成样本脚本 (电机/阀门段)">📝 生成样本</button>
      <button onclick="reloadSymbols(); closeDropdown('engDrop')"
              title="点表 CSV 改了后重新加载 (无需重启)">🔄 刷新点表</button>
      <button onclick="syncFromOPC(); closeDropdown('engDrop')"
              title="从 NTVDPU 浏览实际点表 (兜底, CSV 没同步时用)">🔌 OPC 浏览</button>
    </div>
  </div>
  <span class="sep"></span>

  <span class="grp-label">OPC 端点</span>
  <button id="btnEpLocal" onclick="setEndpoint('local')"
          title="连本机的 NTVDPU (opc.tcp://127.0.0.1:9440)">本地</button>
  <button id="btnEpVm" onclick="setEndpoint('vm')"
          title="连虚拟机里的 NTVDPU (默认 192.168.31.39:9440, 改地址用[✎])">VM</button>
  <button onclick="editVmIp()" title="改 VM 的 IP/端口" style="padding:4px 8px">✎</button>
  <span id="epProbe" title="OPC 端点可达性 (TCP 探活, 5s 自动刷新)"
        style="font-size:11px;font-family:monospace;cursor:pointer;user-select:none"
        onclick="probeNow()">⏳</span>
  <span id="epUrl" style="font-size:10px;color:#888;font-family:monospace"></span>
</div>
<div id="status"><span id="cursorpos" onclick="gotoLine()" title="点击跳到指定行 (Ctrl+G)">行 1 列 1 · 共 0 行</span><span id="errBadge" onclick="gotoFirstErr()" title="点击跳到第一处错误行" style="display:none;background:#c33;color:#fff;padding:1px 8px;margin-right:6px;border-radius:3px;cursor:pointer;font-weight:bold"></span>就绪</div>
<div id="errBox" style="display:none;background:#fee;border-bottom:1px solid #fcc;
     padding:6px 12px;font-family:monospace;font-size:11px;color:#900;
     user-select:text;white-space:pre-wrap;word-break:break-all">
  <button onclick="copyErr()" title="复制错误到剪贴板"
          style="float:right;font-size:10px;padding:2px 6px;cursor:pointer">📋 复制</button>
  <button onclick="closeErr()" title="关掉"
          style="float:right;font-size:10px;padding:2px 6px;margin-right:4px;cursor:pointer">×</button>
  <span id="errMsg"></span>
</div>
<div class="main">
  <section style="padding:0">
    <div class="ed-wrap">
      <div class="line-nums" id="lineNums">1</div>
      <div class="ed-stack">
        <pre id="hl" class="hl-layer"></pre>
        <textarea id="editor" spellcheck="false"
                  placeholder="# 一行一对赋值,例如:&#10;# DPU3013.AI010502 = DPU3013.AQ010101&#10;# DPU3013.AI010503 = 50.0"></textarea>
      </div>
    </div>
  </section>
  <section class="values-panel">
    <h3>实时值(每 1 秒刷新)
      <span id="valsCount" style="font-weight:normal;font-size:10px;color:#888;float:right;"></span>
    </h3>
    <div style="display:flex;gap:6px;margin-bottom:6px;font-size:11px;align-items:center;">
      <label>DPU
        <select id="fltDpu" onchange="renderValsForced()" style="font-size:11px;padding:1px 3px;">
          <option value="">全部</option>
        </select>
      </label>
      <label>类型
        <select id="fltCode" onchange="renderValsForced()" style="font-size:11px;padding:1px 3px;">
          <option value="">全部</option>
          <optgroup label="硬件 IO">
            <option value="AI">AI</option><option value="AQ">AQ</option>
            <option value="DI">DI</option><option value="DQ">DQ</option>
            <option value="TC">TC</option><option value="RT">RT</option>
          </optgroup>
          <optgroup label="自定义">
            <option value="_ANALOG">自定义模拟量</option>
            <option value="_SWITCH" disabled>自定义开关量 (预留)</option>
          </optgroup>
        </select>
      </label>
      <label>角色
        <select id="fltRole" onchange="renderValsForced()" style="font-size:11px;padding:1px 3px;">
          <option value="">全部</option>
          <option value="对比">同时有写+读</option>
          <option value="写">仅写入(LHS)</option>
          <option value="读">仅读取(RHS 源)</option>
          <option value="diff">写读不一致 ⚠</option>
        </select>
      </label>
      <input id="fltKw" placeholder="描述关键字..." oninput="renderValsForced()"
             style="flex:1;font-size:11px;padding:1px 4px;">
    </div>
    <div id="vals" style="overflow:auto;"><div style="color:#999">未运行</div></div>
  </section>
</div>

<div id="acpopup"></div>

<!-- 帮助模态框 -->
<div id="helpmodal" style="display:none; position:fixed; inset:0; z-index:2100;
     background:rgba(0,0,0,.5);" onclick="if(event.target===this) closeHelp()">
  <div style="background:#fff; max-width:920px; margin:30px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:90vh; display:flex; flex-direction:column;">
    <div style="background:#111; color:#eee; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>❓ 赋值脚本帮助 — DSL / 函数库 / 快捷键</b>
      <span style="cursor:pointer;font-size:18px;" onclick="closeHelp()">×</span>
    </div>
    <div id="helpbody" style="overflow-y:auto; padding:14px 22px; font-size:12px;
         line-height:1.65; color:#222;"></div>
  </div>
</div>

<!-- 诊断模态框 -->
<div id="dbgmodal" style="display:none; position:fixed; inset:0; z-index:2200;
     background:rgba(0,0,0,.5);" onclick="if(event.target===this) closeDebug()">
  <div style="background:#fff; max-width:980px; margin:30px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:90vh; display:flex; flex-direction:column;">
    <div style="background:#111; color:#eee; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>🩺 诊断信息 — 运行状态 / 失败统计 / 日志</b>
      <span>
        <button onclick="copyDebug()" style="font-size:11px;padding:3px 10px;
                background:#0a0;color:#fff;border:0;cursor:pointer;">📋 复制全部</button>
        <span style="cursor:pointer;font-size:18px;margin-left:10px" onclick="closeDebug()">×</span>
      </span>
    </div>
    <div id="dbgbody" style="overflow-y:auto; padding:14px 22px; font-size:11px;
         line-height:1.55; color:#222; font-family:'Consolas',monospace;"></div>
  </div>
</div>

<!-- 预演风险评估模态框 -->
<div id="dryrunmodal" style="display:none; position:fixed; inset:0; z-index:2400;
     background:rgba(0,0,0,.4);" onclick="if(event.target===this) closeDryrun()">
  <div style="background:#fff; max-width:1100px; margin:30px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:90vh; display:flex; flex-direction:column;">
    <div style="background:#111; color:#eee; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>🔍 干运行预演 — 算出来要写的 vs DCS 现状 vs 风险评估</b>
      <span>
        <button onclick="dryrunPreview()" style="font-size:11px;padding:3px 10px;background:#06a;color:#fff;border:0;cursor:pointer;">⟳ 重新预演</button>
        <span style="cursor:pointer;font-size:18px;margin-left:10px" onclick="closeDryrun()">×</span>
      </span>
    </div>
    <div id="dryrunsummary" style="padding:6px 14px; background:#f5f5f5; font-size:11px; border-bottom:1px solid #ddd;"></div>
    <div style="padding:4px 14px; background:#fafafa; font-size:11px; border-bottom:1px solid #eee;">
      筛选:
      <label><input type="checkbox" id="drFltRisk" checked onchange="renderDryrun()"> 🔴 风险</label>
      <label><input type="checkbox" id="drFltWarn" checked onchange="renderDryrun()"> 🟠 警告</label>
      <label><input type="checkbox" id="drFltInfo" checked onchange="renderDryrun()"> 🟡 一般</label>
      <label><input type="checkbox" id="drFltOk" onchange="renderDryrun()"> 🟢 一致</label>
      <label><input type="checkbox" id="drFltNoAct" onchange="renderDryrun()"> ⚪ 无读值</label>
      <input id="drFltKw" type="text" placeholder="关键字..."
             oninput="renderDryrun()" style="font-size:11px;padding:1px 5px;margin-left:8px;width:140px">
    </div>
    <div id="dryrunbody" style="overflow-y:auto; padding:0; font-size:11px;
         line-height:1.45; color:#222; font-family:'Consolas',monospace;"></div>
  </div>
</div>

<!-- 事件时间线模态框 -->
<div id="evtmodal" style="display:none; position:fixed; inset:0; z-index:2250;
     background:rgba(0,0,0,.4);" onclick="if(event.target===this) closeEvents()">
  <div style="background:#fff; max-width:780px; margin:40px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:85vh; display:flex; flex-direction:column;">
    <div style="background:#111; color:#eee; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>📜 事件时间线 — 新→旧 (近 200 条, 仅当前 viewer 会话内存)</b>
      <span>
        <button onclick="refreshEvents()" style="font-size:11px;padding:3px 10px;
                background:#06a;color:#fff;border:0;cursor:pointer;">⟳ 刷新</button>
        <span style="cursor:pointer;font-size:18px;margin-left:10px" onclick="closeEvents()">×</span>
      </span>
    </div>
    <div id="evtbody" style="overflow-y:auto; padding:6px 0; font-size:11px;
         line-height:1.5; color:#222; font-family:'Consolas',monospace;"></div>
  </div>
</div>

<!-- 状态镜像模态框 -->
<div id="snapmodal" style="display:none; position:fixed; inset:0; z-index:2300;
     background:rgba(0,0,0,.4);" onclick="if(event.target===this) closeSnapshot()">
  <div style="background:#fff; max-width:880px; margin:40px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:85vh; display:flex; flex-direction:column;">
    <div style="background:#048; color:#fff; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>📤 下载 — 状态镜像管理 (RS / LAG / 中间变量)</b>
      <span style="cursor:pointer;font-size:18px;" onclick="closeSnapshot()">×</span>
    </div>
    <div style="padding:8px 14px; background:#fffce0; color:#666; font-size:11px; border-bottom:1px solid #eec">
      用于 NTVDPU 重启等场景:<b>重启前</b>点【💾 保存镜像】留底,<b>重启后</b>点【📤 下载镜像】把锁存状态写回工程。
      镜像文件:<code>data/state_snapshot.json</code>。
    </div>
    <div id="snapinfo" style="padding:12px 14px; overflow-y:auto; flex:1;"></div>
    <div style="padding:8px 14px; border-top:1px solid #eee; display:flex; gap:8px; flex-wrap:wrap">
      <button onclick="saveSnapshot()" style="background:#048;color:#fff;border:0;padding:6px 14px;cursor:pointer;font-size:12px">💾 保存镜像</button>
      <button id="snapRestoreBtn" onclick="restoreSnapshot()" style="background:#06a;color:#fff;border:0;padding:6px 14px;cursor:pointer;font-size:12px"
              title="下载: 把磁盘镜像加载进本项目内存, 跑起来后所有 LHS 强制重写一遍, 等于把保存的状态送回工程">📤 下载镜像</button>
      <button id="snapDeleteBtn" onclick="deleteSnapshot()" style="background:#999;color:#fff;border:0;padding:6px 14px;cursor:pointer;font-size:12px">🗑 删除镜像</button>
      <span style="flex:1"></span>
      <button onclick="reinitState()" style="background:#06a;color:#fff;border:0;padding:6px 14px;cursor:pointer;font-size:12px;margin-right:6px"
              title="改了脚本系数后用: 清 LAG/$var, 让滞后块按【跟踪脚本输入】重新初始化 (RS 锁存保留)">⏮ 重置初值</button>
      <button onclick="resetState()" style="background:#c60;color:#fff;border:0;padding:6px 14px;cursor:pointer;font-size:12px"
              title="核弹级: 清 RS/LAG/$var + 删镜像 — 从头开始仿真">🔥 清空状态</button>
    </div>
  </div>
</div>

<!-- 备份历史模态框 -->
<div id="bkmodal" style="display:none; position:fixed; inset:0; z-index:2000;
     background:rgba(0,0,0,.4);" onclick="if(event.target===this) closeBackups()">
  <div style="background:#fff; max-width:780px; margin:50px auto; padding:0;
       box-shadow:0 8px 32px rgba(0,0,0,.3); max-height:80vh; display:flex; flex-direction:column;">
    <div style="background:#111; color:#eee; padding:8px 14px; display:flex;
         justify-content:space-between; align-items:center;">
      <b>📚 备份历史</b>
      <span style="cursor:pointer;font-size:18px;" onclick="closeBackups()">×</span>
    </div>
    <div style="padding:6px 14px; background:#fffce0; color:#666; font-size:11px; border-bottom:1px solid #eec;">
      每次<b>保存</b>自动留时间戳备份。点【加载】把备份内容灌进编辑器(不会自动覆盖当前脚本,需手动再保存)。
    </div>
    <div id="bklist" style="overflow-y:auto; padding:0; font-size:11px;">载入中...</div>
  </div>
</div>

<script>
let _stickyErrUntil = 0;   // 错误信息粘到此时间戳, pollStatus 不覆盖
function setStatus(msg, cls) {
  // 保留光标位置指示器
  const cp = document.getElementById('cursorpos');
  const cpHtml = cp ? cp.outerHTML : '';
  document.getElementById('status').innerHTML =
    cpHtml + (cls ? `<span class="${cls}">${msg}</span>` : msg);
  if (cls === 'err') {
    _stickyErrUntil = Date.now() + 15000;   // 错误持续 15 秒不被定时刷新覆盖
    try { console.error('[viewer]', msg); } catch(e) {}   // 同步到 DevTools 永久留底
    showErr(msg);                           // 同步到 errBox (可选中/复制, 持久到关掉)
  }
}

// ===== 错误持久面板 (可复制) =====
function showErr(msg) {
  const box = document.getElementById('errBox');
  const span = document.getElementById('errMsg');
  if (!box || !span) return;
  // 文本化 (去掉 HTML 标记, 便于复制原始内容)
  const tmp = document.createElement('div'); tmp.innerHTML = msg;
  const text = (tmp.textContent || tmp.innerText || msg).trim();
  span.textContent = text;
  box.style.display = 'block';
}
function closeErr() { document.getElementById('errBox').style.display = 'none'; }
async function copyErr() {
  const txt = document.getElementById('errMsg').textContent || '';
  try {
    await navigator.clipboard.writeText(txt);
    setStatus('✓ 已复制错误到剪贴板', 'ok');
  } catch (e) {
    // 退路: 旧浏览器
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
    setStatus('✓ 已复制 (兼容模式)', 'ok');
  }
}

// ===== OPC 端点切换 (本地 / VM) =====
let _epCfg = null;   // {mode, local, vm, url, running}
async function refreshEndpoint() {
  try {
    const r = await fetch('/api/opc/endpoint');
    _epCfg = await r.json();
    const bl = document.getElementById('btnEpLocal');
    const bv = document.getElementById('btnEpVm');
    const url = document.getElementById('epUrl');
    if (bl) bl.className = (_epCfg.mode === 'local') ? 'primary' : '';
    if (bv) bv.className = (_epCfg.mode === 'vm') ? 'primary' : '';
    if (url) url.textContent = _epCfg.url || '';
  } catch(e) { /* 忽略 */ }
}
async function setEndpoint(mode) {
  const r = await fetch('/api/opc/endpoint', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: mode}),
  });
  const d = await r.json();
  if (!d.ok) { setStatus('✗ ' + d.error, 'err'); return; }
  await refreshEndpoint();
  refreshProbe();  // 切换后立即拉一次探活结果 (后端 set_endpoint_mode 已触发探活)
  setStatus(`✓ OPC 端点切到 [${mode==='vm'?'VM':'本地'}] ${d.url}`, 'ok');
}
async function editVmIp() {
  if (!_epCfg) await refreshEndpoint();
  const cur = (_epCfg && _epCfg.vm) || 'opc.tcp://192.168.135.142:9440';
  const v = prompt('VM 的 OPC URL (含协议+端口):', cur);
  if (!v) return;
  const r = await fetch('/api/opc/endpoint', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: (_epCfg && _epCfg.mode) || 'local', vm: v.trim()}),
  });
  const d = await r.json();
  if (!d.ok) { setStatus('✗ ' + d.error, 'err'); return; }
  await refreshEndpoint();
  refreshProbe();
  setStatus(`✓ VM URL 已更新: ${d.vm}`, 'ok');
}

// ===== OPC 端点可达性探活 — 3s 自动刷新 =====
// 端点旁的 [⏳/🟢/🔴] 状态点, 切换/改 URL 后立即重探一次, 不需用户点【▶ 运行】
async function refreshProbe() {
  try {
    const r = await fetch('/api/opc/probe');
    const d = await r.json();
    const el = document.getElementById('epProbe');
    if (!el) return;
    if (d.running) {
      el.textContent = '🟢';
      el.title = `OPC 循环运行中 — ${d.url}`;
    } else if (d.ok === true) {
      el.textContent = `🟢 ${d.latency_ms}ms`;
      el.title = `端口可达 (${d.host}:${d.port}, 探测 ${d.latency_ms}ms) — 可以点【▶ 运行】`;
    } else if (d.ok === false) {
      el.textContent = '🔴';
      el.title = `不通: ${d.error || '未知错误'}  (${d.url})`;
    } else {
      el.textContent = '⏳';
      el.title = '探活中…';
    }
  } catch(e) { /* 忽略 */ }
}
async function probeNow() {
  // 用户主动点状态点 → 立即强制探一次
  const el = document.getElementById('epProbe');
  if (el) { el.textContent = '⏳'; el.title = '探活中…'; }
  try {
    await fetch('/api/opc/probe', {method: 'POST'});
  } catch(e) { /* 忽略 */ }
  refreshProbe();
}
setInterval(refreshProbe, 3000);  // 3s 轮询

// ===== UI 重启 viewer 进程 =====
async function restartViewer() {
  if (!confirm('重启 viewer 进程?\n\n要求:\n• OPC 循环必须先停止\n• 浏览器会自动重连并刷新 (约 3 秒)\n• 内存里的脚本未保存的部分会丢, 已保存的脚本/状态镜像不受影响')) return;
  setStatus('⏳ 重启 viewer 中, 等新进程上线...', 'warn');
  try {
    const r = await fetch('/api/viewer/restart', {method: 'POST'});
    if (r.status === 409) {
      const d = await r.json();
      setStatus('✗ ' + d.error, 'err');
      return;
    }
    // 收到 200 后, 老进程 300ms 后就会退出 — 之后所有 fetch 会失败直到新进程起来
  } catch(e) {
    // 网络异常 — 也可能是老进程已经退了, 没事, 继续轮询新进程
  }
  // 轮询新进程: 每 500ms 试一次 /api/script/status, 最多 20 次 (10s)
  let tries = 0;
  const maxTries = 20;
  const probe = setInterval(async () => {
    tries++;
    try {
      const r = await fetch('/api/script/status', {cache: 'no-cache'});
      if (r.ok) {
        clearInterval(probe);
        setStatus('✓ 新 viewer 已上线, 正在刷新页面...', 'ok');
        setTimeout(() => location.reload(), 300);
        return;
      }
    } catch(e) { /* 还没起来 */ }
    if (tries >= maxTries) {
      clearInterval(probe);
      setStatus('✗ 重启超时 (10s), 请手动 F5 刷新; 若仍不行回终端看输出', 'err');
    }
  }, 500);
}

// ===== 语法高亮 =====
const FN_NAMES = ['RS_NOT','RS','NOT','AND','OR','ADD','SUB','MUL','DIV',
                  'POW','SQRT','ABS','MAX','MIN','LIMIT','SEL','LAG','CHAR',
                  'CCS_1000','STEAM_T'];
const FN_RE = new RegExp('\\b(' + FN_NAMES.join('|') + ')\\b(?=\\s*\\()', 'g');

function escHtml2(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 单行染色 — 不做合法性检查 (避免每次输入重计算红底, 拖累性能)
// 合法性靠【保存】按钮做一次性 parser 校验, 错了再跳错行
function hlLine(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return escHtml2(raw) || ' ';
  if (trimmed.startsWith('#')) {
    return `<span class="hl-comment">${escHtml2(raw)}</span>`;
  }
  // 行尾 # 注释
  let codeRaw = raw, cmtRaw = '';
  const m = raw.match(/(\s+#.*)$/);
  if (m) { codeRaw = raw.slice(0, m.index); cmtRaw = m[1]; }
  // 按 = 切两边染色; 没 = 也按整段染色, 不报错
  const eqIdx = codeRaw.indexOf('=');
  const body = eqIdx < 0
    ? colorPart(codeRaw)
    : colorPart(codeRaw.slice(0, eqIdx)) + '<span class="hl-op">=</span>' + colorPart(codeRaw.slice(eqIdx + 1));
  return body + (cmtRaw ? `<span class="hl-comment">${escHtml2(cmtRaw)}</span>` : '');
}

function colorPart(s) {
  // 拆 token 顺序:括号描述 → 函数名 → 短码 → 数字
  // 用 placeholder 避免重复替换
  let out = escHtml2(s);
  // 描述括号 (含半角/全角)
  out = out.replace(/(（[^）]*）|\([^()]*\))/g, '<span class="hl-desc">$1</span>');
  // 函数名 (大写跟着左括号)
  out = out.replace(FN_RE, '<span class="hl-fn">$1</span>');
  // 中间变量 $xxx — data-token 让悬停 elementFromPoint 直接拿到, 不用算坐标
  out = out.replace(/\$[A-Za-z_]\w*/g, m => `<span class="hl-var" data-token="${m}">${m}</span>`);
  // 短码 DPU3013.XXX[.YYY...] — 同上
  out = out.replace(/\bDPU\d{4}(?:\.[A-Z]+\d*[A-Z]*\w*)+/g,
                    m => `<span class="hl-tag" data-token="${m}">${m}</span>`);
  // 数字 (整数 / 小数 / 负数)
  out = out.replace(/(?<![\w.])(-?\d+(?:\.\d+)?)\b/g,
                    '<span class="hl-num">$1</span>');
  // 中缀运算符 + - * / ^ (不影响已染色的 hl-* span 标签里的运算符,
  //  因为标签里的 + - 出现在 class= 之外被忽略 — 我们只染色独立的)
  // 简化处理: 这里不在 span 内做替换会破坏标签结构,所以只在 escape 后的"裸字符"前做.
  // 由于 span 里的属性值是引号包裹的, 不含 + - * / ^,直接替换是安全的.
  out = out.replace(/(\^|\*\*|[+\-*/])(?![^<]*>)/g,
                    '<span class="hl-mathop">$1</span>');
  return out;
}

// debounce 高亮 / 行号 / 补全 — 输入连续时合并到最后一次
let _hlTimer = null, _acTimer = null, _lnTimer = null;
let _lastHlText = null;
function scheduleHighlight() {
  if (_hlTimer) clearTimeout(_hlTimer);
  _hlTimer = setTimeout(() => { _hlTimer = null; runHighlight(); }, 180);
}
function scheduleAC() {
  if (_acTimer) clearTimeout(_acTimer);
  _acTimer = setTimeout(() => { _acTimer = null; updateAC(); }, 60);
}
function scheduleLineNums() {
  if (_lnTimer) clearTimeout(_lnTimer);
  _lnTimer = setTimeout(() => { _lnTimer = null; updateLineNums(); }, 120);
}

// 解析错误/警告: {行号 → 文本}, 由 validateScript() 维护
let _errLines = new Map();
let _warnLines = new Map();

function runHighlight() {
  const ed = document.getElementById('editor');
  const hl = document.getElementById('hl');
  if (!ed || !hl) return;
  const val = ed.value || '';
  if (val === _lastHlText) {
    // 文本没变, 只同步滚动
    hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft;
    return;
  }
  _lastHlText = val;
  // 性能闸门: 超大文本(>40KB)时关闭高亮, 防止卡顿
  if (val.length > 40000) {
    hl.textContent = val;
    hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft;
    return;
  }
  const lines = val.split('\n');
  hl.innerHTML = lines.map((raw, i) => {
    let lineHtml = hlLine(raw);
    const lineNo = i + 1;
    if (_errLines.has(lineNo)) {
      const msg = _errLines.get(lineNo).replace(/"/g, '&quot;');
      return `<span class="hl-bad" title="✗ ${msg}">${lineHtml}</span>`;
    }
    if (_warnLines.has(lineNo)) {
      // 字符级: 每个全角字符单独 hl-warn-char (描述括号 hl-desc 跨度内的全角不标 — 那里允许中文)
      lineHtml = markFullwidthChars(lineHtml);
    }
    return lineHtml;
  }).join('\n');
  hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft;
}

// 把行 HTML 里的全角字符单独标 (避开 hl-desc 描述跨度 — 描述里允许中文)
// 输入是 hlLine 已染色的 HTML, 全角字符要么在 hl-desc 里(跳), 要么在裸文本里(标)
const FULLWIDTH_NAME = {
  '（': '左括号 (=半角 \"(\")',
  '）': '右括号 (=半角 \")\")',
  '，': '逗号 (=半角 \",\")',
  '；': '分号 (=半角 \";\")',
  '：': '冒号 (=半角 \":\")',
  '。': '句号 (=半角 \".\")',
  '？': '问号 (=半角 \"?\")',
  '！': '叹号 (=半角 \"!\")',
};
function markFullwidthChars(html) {
  // 用 token re 一次扫: 整段 hl-desc span 或 单个全角字符
  const re = /<span class="hl-desc">[\s\S]*?<\/span>|[（），；：。？！]/g;
  return html.replace(re, m => {
    if (m.startsWith('<')) return m;        // 保留描述跨度不动
    const name = FULLWIDTH_NAME[m] || m;
    return `<span class="hl-warn-char" title="⚠ 全角 ${name}">${m}</span>`;
  });
}

// ===== 组态错误标注 (每行单独解析, 错的行底色 + 鼠标悬停看错因) =====
async function validateScript() {
  const c = document.getElementById('editor').value || '';
  try {
    const r = await fetch('/api/script/validate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: c})
    });
    const d = await r.json();
    const em = new Map(); for (const e of (d.errors   || [])) em.set(e.line, e.msg);
    const wm = new Map(); for (const w of (d.warnings || [])) wm.set(w.line, w.msg);
    // 没有变化就别重渲, 省 CPU
    let same = em.size === _errLines.size && wm.size === _warnLines.size;
    if (same) for (const [k, v] of em) if (_errLines.get(k) !== v) { same = false; break; }
    if (same) for (const [k, v] of wm) if (_warnLines.get(k) !== v) { same = false; break; }
    if (!same) {
      _errLines = em;
      _warnLines = wm;
      _lastHlText = null;   // 强制下一次 runHighlight 重渲
      runHighlight();
    }
    // 错误 + 警告 badge (不打扰: 都 0 就清掉)
    const badge = document.getElementById('errBadge');
    if (badge) {
      const parts = [];
      if (em.size > 0) parts.push(`<span style="background:#c33;color:#fff;padding:1px 8px;border-radius:3px;font-weight:bold;cursor:pointer" onclick="gotoFirstErr()" title="点击跳到第一处">✗ ${em.size} 错(行 ${[...em.keys()].slice(0,5).join(',')}${em.size>5?'...':''})</span>`);
      if (wm.size > 0) parts.push(`<span style="background:#c90;color:#fff;padding:1px 8px;border-radius:3px;font-weight:bold;cursor:pointer" onclick="gotoFirstWarn()" title="点击跳到第一处 (全角字符等)">⚠ ${wm.size} 警告(行 ${[...wm.keys()].slice(0,5).join(',')}${wm.size>5?'...':''})</span>`);
      if (parts.length) {
        badge.innerHTML = parts.join(' ');
        badge.style.display = '';
        badge.style.background = 'transparent';
        badge.style.padding = '0';
      } else {
        badge.style.display = 'none';
      }
    }
  } catch(e) { /* 网络错忽略 */ }
}

let _valTimer = null;
function scheduleValidate() {
  if (_valTimer) clearTimeout(_valTimer);
  _valTimer = setTimeout(() => { _valTimer = null; validateScript(); }, 600);
}

// 编辑时不再后台校验, 用户改完保存才校验 (类似编译)
// 改了之后徽章先变灰说"未校验", 提醒用户下一步是保存
function markValidateStale() {
  const badge = document.getElementById('errBadge');
  if (!badge) return;
  badge.innerHTML = '<span style="background:#888;color:#fff;padding:1px 8px;border-radius:3px;font-weight:normal;" title="点击 [💾 保存] 或 [▶ 运行] 后校验">⏸ 未校验 (改后请保存)</span>';
  badge.style.display = '';
  badge.style.background = 'transparent';
  badge.style.padding = '0';
}

// ===== 行号 + 光标位置 =====
// 高频: 滚动时只同步行号 scrollTop(不重 split, 不重渲染)
function syncLineNumsScroll() {
  const ed = document.getElementById('editor');
  const ln = document.getElementById('lineNums');
  if (ed && ln) ln.scrollTop = ed.scrollTop;
}
// 低频: 行号文本 + 光标位置(只在 input/click/键盘变更光标时调)
function updateLineNums() {
  const ed = document.getElementById('editor');
  const ln = document.getElementById('lineNums');
  // 行数:用换行符计数,不 split(更快)
  const v = ed.value || '';
  let lines = 1;
  for (let i = 0; i < v.length; i++) if (v.charCodeAt(i) === 10) lines++;
  if (ln.dataset.lines !== String(lines)) {
    const arr = new Array(lines);
    for (let i = 0; i < lines; i++) arr[i] = (i + 1);
    ln.textContent = arr.join('\n');
    ln.dataset.lines = String(lines);
    ln.style.minWidth = lines >= 1000 ? '48px' : (lines >= 100 ? '40px' : '32px');
  }
  ln.scrollTop = ed.scrollTop;
  updateCursorPos();
}

function updateCursorPos() {
  const ed = document.getElementById('editor');
  const cp = document.getElementById('cursorpos');
  if (!ed || !cp) return;
  const pos = ed.selectionStart;
  const before = ed.value.slice(0, pos);
  const lineNo = before.split('\n').length;
  const colNo = pos - (before.lastIndexOf('\n') + 1) + 1;
  const total = (ed.value || '').split('\n').length;
  cp.textContent = `行 ${lineNo} 列 ${colNo} · 共 ${total} 行`;
}

// 跳到指定行(N 从 1 起)
function jumpToLine(n) {
  const ed = document.getElementById('editor');
  const lines = ed.value.split('\n');
  if (n < 1) n = 1;
  if (n > lines.length) n = lines.length;
  let pos = 0;
  for (let i = 0; i < n - 1; i++) pos += lines[i].length + 1;
  ed.focus();
  ed.setSelectionRange(pos, pos + (lines[n-1] || '').length);
  const lh = parseFloat(getComputedStyle(ed).lineHeight) || 18;
  ed.scrollTop = Math.max(0, (n - 1) * lh - ed.clientHeight / 3);
  updateLineNums();
}

// Ctrl+/ 注释切换 — 选区/当前行批量加 / 去 '# '
function toggleComment() {
  const ed = document.getElementById('editor');
  const s = ed.selectionStart, e = ed.selectionEnd;
  const before = ed.value.slice(0, s);
  const after = ed.value.slice(e);
  const lineStart = before.lastIndexOf('\n') + 1;
  let endNl = after.indexOf('\n');
  if (endNl < 0) endNl = after.length;
  const lineEnd = e + endNl;
  const block = ed.value.slice(lineStart, lineEnd);
  const lines = block.split('\n');
  const nonEmpty = lines.filter(l => l.trim());
  const allCommented = nonEmpty.length > 0 && nonEmpty.every(l => /^\s*#/.test(l));
  const newLines = allCommented
    ? lines.map(l => l.replace(/^(\s*)#\s?/, '$1'))
    : lines.map(l => l.trim() ? '# ' + l : l);
  const newBlock = newLines.join('\n');
  ed.value = ed.value.slice(0, lineStart) + newBlock + ed.value.slice(lineEnd);
  ed.setSelectionRange(lineStart, lineStart + newBlock.length);
  updateLineNums();
  scheduleHighlight();
}

// Tab / Shift+Tab 选区整体缩进
function indentSel(dedent) {
  const ed = document.getElementById('editor');
  const s = ed.selectionStart, e = ed.selectionEnd;
  const before = ed.value.slice(0, s);
  const after = ed.value.slice(e);
  const lineStart = before.lastIndexOf('\n') + 1;
  let endNl = after.indexOf('\n');
  if (endNl < 0) endNl = after.length;
  const lineEnd = e + endNl;
  const block = ed.value.slice(lineStart, lineEnd);
  const lines = block.split('\n');
  const newLines = dedent
    ? lines.map(l => l.replace(/^( {1,2}|\t)/, ''))
    : lines.map(l => '  ' + l);
  const newBlock = newLines.join('\n');
  ed.value = ed.value.slice(0, lineStart) + newBlock + ed.value.slice(lineEnd);
  ed.setSelectionRange(lineStart, lineStart + newBlock.length);
  updateLineNums();
  scheduleHighlight();
}

function gotoLine() {
  const ed = document.getElementById('editor');
  const total = (ed.value || '').split('\n').length;
  const ans = prompt(`跳到第几行?(1 ~ ${total})`, '');
  if (!ans) return;
  const n = parseInt(ans, 10);
  if (!n || n < 1 || n > total) { alert('行号非法'); return; }
  // 计算目标行起始字符位置
  const lines = ed.value.split('\n');
  let pos = 0;
  for (let i = 0; i < n - 1; i++) pos += lines[i].length + 1;
  ed.focus();
  ed.setSelectionRange(pos, pos + (lines[n-1] || '').length);
  // 滚动到可见
  const lineHeight = parseFloat(getComputedStyle(ed).lineHeight) || 18;
  ed.scrollTop = Math.max(0, (n - 1) * lineHeight - ed.clientHeight / 3);
  updateLineNums();
}

// 全局: {OPC 完整节点 → 信号名}, 实时值显示用
//   tagDescMap     — 脚本里 DPU3013.AI(信号名) 自定义描述 (优先)
//   symbolsDescMap — 从点表 (CSV) 加载的标准描述 (兜底)
let tagDescMap = {};
let symbolsDescMap = {};

function rebuildSymbolsDescMap() {
  symbolsDescMap = {};
  for (const s of symbols) {
    if (!s.desc) continue;
    // 把 label (DPU3013.AI010502 或 DPU3013.SH0500.PRO21120.IN) 转完整节点
    const label = s.label;
    let full;
    if (label.startsWith('$')) {
      full = label;
    } else if (label.includes('.SH') || label.split('.').length > 2) {
      full = `ns=0;s=${label}`;
    } else {
      const [dpu, point] = label.split('.', 2);
      full = `ns=0;s=${dpu}.HW.${point}.PV`;
    }
    symbolsDescMap[full] = s.desc;
  }
}

function rebuildDescMap() {
  const content = document.getElementById('editor').value || '';
  tagDescMap = {};
  // HW 单段: DPU3013.AI010502(信号名)
  let m;
  const reHw = /\b(DPU\d{4})\.([A-Z]+\d+)\s*\(([^)]+)\)/g;
  while ((m = reHw.exec(content)) !== null) {
    tagDescMap[`ns=0;s=${m[1]}.HW.${m[2]}.PV`] = m[3].trim();
  }
  // SH 多段: DPU3013.SH0500.PRO21120.IN(信号名)
  const reSh = /\b(DPU\d{4}\.SH\d+\.[A-Z]+\d+\.[A-Z]+)\s*\(([^)]+)\)/g;
  while ((m = reSh.exec(content)) !== null) {
    tagDescMap[`ns=0;s=${m[1]}`] = m[2].trim();
  }
  // 中间变量: $tmp_flow(描述)
  const reVar = /(\$[A-Za-z_]\w*)\s*\(([^)]+)\)/g;
  while ((m = reVar.exec(content)) !== null) {
    tagDescMap[m[1]] = m[2].trim();
  }
}

async function loadScript() {
  // 如果 editor 有内容,先 confirm + 备份(防误操作冲掉)
  const cur = document.getElementById('editor').value;
  // 首次启动时不弹 (editor 默认 placeholder, value 是空)
  if (cur.trim()) {
    if (!confirm('从盘重新加载 script.txt 会覆盖当前 editor 内容。\n点确定将先备份当前 editor;取消放弃。')) return;
    await fetch('/api/script/backup', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: cur, reason: 'before-reload'})});
  }
  const r = await fetch('/api/script');
  const d = await r.json();
  document.getElementById('editor').value = d.content || '';
  setStatus(d.exists ? `已加载 (${d.size} 字节)` : `脚本文件不存在,可新建或点【自动生成样本】`, 'ok');
  rebuildDescMap(); scheduleHighlight(); updateLineNums();
}

async function saveScript() {
  const c = document.getElementById('editor').value;
  const r = await fetch('/api/script', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content: c})});
  const d = await r.json();
  // 在线下装就让用户看到 ♻ 标记 (区别于普通保存)
  const icon = d.ok ? (d.hot_swapped ? '♻' : '✓') : '✗';
  setStatus(d.ok ? `${icon} ${d.msg}` : `✗ ${d.error}`, d.ok ? 'ok' : 'err');
  if (d.ok) {
    rebuildDescMap();
    validateScript();   // 保存成功 → 触发一次校验
    // A: 镜像面板正打开 → 刷新内存 vs 磁盘对照 (后端已自动 prune 过期 state)
    const sm = document.getElementById('snapmodal');
    if (sm && sm.style.display && sm.style.display !== 'none') {
      refreshSnapInfo();
    }
    return;
  }
  // 解析错误 → 自动跳到错误行
  const m = (d.error || '').match(/第\s*(\d+)\s*行/);
  if (m) jumpToLine(parseInt(m[1], 10));
}

async function runIt() {
  const c = document.getElementById('editor').value;
  // 先保存
  const sr = await fetch('/api/script', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content: c})});
  const sd = await sr.json();
  if (!sd.ok) { setStatus(`✗ 保存失败: ${sd.error}`, 'err'); return; }
  validateScript();   // 保存通过 → 触发一次完整校验 (徽章从 "未校验" 翻成 "✓N 错 / ⚠N 警告" 真实结果)
  // 启动
  const r = await fetch('/api/script/run', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: '{}'});
  const d = await r.json();
  setStatus(d.ok ? `▶ ${d.msg}` : `✗ ${d.msg || d.error}`, d.ok ? 'run' : 'err');
}

async function stopIt() {
  const r = await fetch('/api/script/stop', {method: 'POST'});
  const d = await r.json();
  setStatus(d.ok ? `■ ${d.msg}` : d.msg, d.ok ? 'ok' : '');
  // 立刻拉一次状态, 不等 1 秒轮询 — 否则切端点又被拦 "请先停止"
  pollStatus();
}

// ===== 帮助文档 =====
const HELP_HTML = `
<h2>1. DSL 基础</h2>
<p>每行一个赋值,格式 <code>左边 = 右边</code>。<code>#</code> 开头是注释。右边支持<b>完整中缀表达式 + 函数嵌套</b>。</p>
<pre># 注释行
DPU3013.AI010502 = DPU3013.AQ010101          # 直通
DPU3013.AI010502(指令) = DPU3013.AQ010101(反馈)   # 括号内是信号名(可读性, 解析忽略)
DPU3013.AI010502 = 50.0                       # 写常数
DPU3013.AI010604 = (DPU3013.SH0500.PRO27137.IN / 250) ^ 2   # 中缀表达式
$tmp = LIMIT(DPU3001.AI010101 + DPU3002.AI010101, 0, 100)    # 嵌套调用
DPU3013.AI010502 = $tmp * 0.5                  # 中间变量参与计算</pre>

<h3>1.0 运算符</h3>
<p>优先级(高 → 低):</p>
<table>
  <tr><th>运算符</th><th>优先级</th><th>结合</th><th>等价函数</th></tr>
  <tr><td><code>( )</code></td><td>最高</td><td>—</td><td>分组</td></tr>
  <tr><td><code>-x</code> (一元)</td><td>1</td><td>右</td><td>取负</td></tr>
  <tr><td><code>^</code> 或 <code>**</code></td><td>2</td><td>右</td><td>POW(x, n)</td></tr>
  <tr><td><code>*</code> <code>/</code></td><td>3</td><td>左</td><td>MUL / DIV</td></tr>
  <tr><td><code>+</code> <code>-</code></td><td>4</td><td>左</td><td>ADD / SUB</td></tr>
</table>
<p>逻辑运算继续用 <code>AND/OR/NOT</code> 函数(没引入 <code>&&</code> / <code>||</code>)。</p>
<p>短码 <code>DPU3013.AI010502</code> 自动展开为 <code>ns=0;s=DPU3013.HW.AI010502.PV</code>。左边是 <code>AI</code> 时自动走 HR/LR 双写。</p>

<h3>1.05 多目标赋值(批量同值)</h3>
<p>左边用 <b>逗号</b> 分隔多个目标,共享同一个右边表达式:</p>
<pre># 30 个 DI 一次置 0
DPU3044.DI010207, DPU3044.DI020206, DPU3044.DI010210 = 0

# 共享一个 RS 锁存 (state 也共享)
DPU3001.DI020401, DPU3002.DI020501 = RS(DPU3013.DQ010201, DPU3013.DQ010202)

# 描述括号里的逗号不影响切分
DPU3013.AI010604(主给水流量1), DPU3013.AI020404(主给水流量2) = 50</pre>

<h3>1.1 中间变量 (<code>$xxx</code>)</h3>
<p>用 <code>$</code> 前缀声明,任意命名 (字母/下划线开头)。<b>不写 OPC, 不读 OPC</b>, 只在仿真内存里传递,免去把无意义的中间结果通过 OPC 转一手:</p>
<pre># 给水流量缩放并限幅, 再分发给多个反馈
$fw_kgps = MUL(DPU3013.AQ_total_tph, 0.2778)
$fw_safe = LIMIT($fw_kgps, 0, 1500)
DPU3001.AI010101 = $fw_safe
DPU3002.AI010101 = $fw_safe
DPU3003.AI010101 = $fw_safe</pre>
<p>类型不强制:<code>real / bool</code> 都行,根据右边函数返回类型自动适配。
<b>使用顺序</b>:先 <code>$x = ...</code>(赋值)后引用;同周期内顺序计算。
如果"用了之后再赋值"会有一拍延迟(用上一周期的值),工程上一般可接受。</p>

<p><b>信号名注释</b>:跟 OPC 节点一样,中间变量也支持 <code>$xxx(描述)</code> 写法 — 括号里随便写中文,解析器自动忽略,纯可读性:</p>
<pre># 带名字, 维护时一眼看明白每个变量代表什么物理量
$Vh_A(A磨进口热风调门指令) = DPU3002.AQ010402(A磨进口热风电动调节风门指令)
$Vc_A(A磨进口冷风调门指令) = DPU3002.AQ010403(A磨进口冷风电动调节风门指令)
$M_A(A给煤机给煤指令)      = DPU3002.AQ010401(A给煤机给煤指令)

# LHS / RHS 都可以带, 也可以只 LHS 带 RHS 不带
$Vsum_A(总有效风量) = $Vh_A(热风) + $Vc_A(冷风) * 0.5

# 描述里的逗号不会被当多目标切分
$Tmix(混合温度, 加权平均) = ADD(MUL($Vh_A, 200), MUL($Vc_A, 25)) / ($Vh_A + $Vc_A)</pre>

<h2>2. 函数库</h2>
<p>右边支持函数调用。<b>参数可以是常数或 OPC 节点,不支持嵌套</b>(嵌套用多行+中间 LHS 暂存)。</p>

<h3>锁存/逻辑</h3>
<table>
  <tr><th>函数</th><th>含义</th><th>示例</th></tr>
  <tr><td class="fn">RS(S, R)</td>
      <td>SR 锁存:S=1→Q=1,R=1→Q=0,都=0→保持。开反馈用。</td>
      <td class="ex">DI020401 = RS(DQ_开命令, DQ_关命令)</td></tr>
  <tr><td class="fn">RS_NOT(S, R)</td>
      <td>等价于 NOT(RS(S, R))。关反馈用。</td>
      <td class="ex">DI020402 = RS_NOT(DQ_开命令, DQ_关命令)</td></tr>
  <tr><td class="fn">NOT(x)</td>
      <td>取反。x 真则假, x 假则真。</td>
      <td class="ex">DI_关位 = NOT(DI_开位)</td></tr>
  <tr><td class="fn">AND(a, b)</td><td>逻辑与</td><td class="ex">允许 = AND(条件1, 条件2)</td></tr>
  <tr><td class="fn">OR(a, b)</td><td>逻辑或</td><td class="ex">报警 = OR(高报, 低报)</td></tr>
</table>

<h3>算术</h3>
<table>
  <tr><th>函数</th><th>含义</th><th>示例</th></tr>
  <tr><td class="fn">ADD(a, b)</td><td>a + b</td><td class="ex">AI_总流量 = ADD(AI_A流量, AI_B流量)</td></tr>
  <tr><td class="fn">SUB(a, b)</td><td>a − b</td><td class="ex">偏差 = SUB(测量值, 设定值)</td></tr>
  <tr><td class="fn">MUL(a, b)</td><td>a × b (常用于单位换算)</td><td class="ex">AI_kg/s = MUL(AI_t/h, 0.2778)</td></tr>
  <tr><td class="fn">DIV(a, b)</td><td>a ÷ b (b=0 时返 0)</td><td class="ex">AI_压差_MPa = DIV(AI_压差_kPa, 1000)</td></tr>
  <tr><td class="fn">POW(x, n)</td><td>x 的 n 次方</td><td class="ex">$sq = POW($flow_norm, 2)</td></tr>
  <tr><td class="fn">SQRT(x)</td><td>开平方 (x &lt; 0 返 0)</td><td class="ex">AI_v = SQRT(AI_压差)</td></tr>
  <tr><td class="fn">ABS(x)</td><td>绝对值</td><td class="ex">$dev = ABS(SUB(AI_测量, 设定))</td></tr>
  <tr><td class="fn">MAX(a, b) / MIN(a, b)</td><td>取大 / 取小</td><td class="ex">AI_最高温 = MAX(AI_T1, AI_T2)</td></tr>
  <tr><td class="fn">LIMIT(x, lo, hi)</td><td>限幅: max(lo, min(hi, x))</td><td class="ex">AI_反馈 = LIMIT(AQ_指令, 0, 100)</td></tr>
</table>

<h3>选择 / 仿真</h3>
<table>
  <tr><th>函数</th><th>含义</th><th>示例</th></tr>
  <tr><td class="fn">SEL(cond, a, b)</td>
      <td>cond 真返 a, 否则返 b (相当于 if-then-else)</td>
      <td class="ex">AI_选用 = SEL(DI_用A, AI_A测量, AI_B测量)</td></tr>
  <tr><td class="fn">LAG(x, T)</td>
      <td>一阶滞后: y[k] = y[k-1] + dt/T·(x − y[k-1])。<br>
          T = 时间常数(秒)。模拟阀门/汽机响应延迟。</td>
      <td class="ex">AI_流量反馈 = LAG(AQ_阀位指令, 3.0)</td></tr>
  <tr><td class="fn">CHAR(x, x0,y0, x1,y1, ...)</td>
      <td>折线特性曲线(变长)。<br>
          按 (x,y) 点对定义,段内线性插值,端点外取最近值。<br>
          至少 2 个点(5 参数),参数总数必须是奇数。</td>
      <td class="ex">$flow = CHAR(AI_x, 0,0, 25,5, 50,50, 75,80, 100,100)</td></tr>
</table>

<h3>协调模型 — USC-OTBT 1000MW (3 入 3 出)</h3>
<p>论文 Fan/Su/Wang/Lee 2021 (Energy 226:120425) 的完整 CCS 动态模型。
   <b>实例化 → 读管脚</b>:工厂函数 <code>CCS_1000(...)</code> 返回一个模型实例,绑到 <code>$实例名</code>;
   后续用 <code>$实例名.PST/.HM/.NE</code> 读 3 个输出管脚。参数文件:
   <code>config/ccs_models/usc-otbt-1000mw.yaml</code>(后续加 660MW 等 preset 时新增 <code>CCS_660</code> 工厂即可,脚本不动)。</p>
<table>
  <tr><th>语法</th><th>含义</th></tr>
  <tr><td class="fn">$inst = CCS_1000(uB, Dfw, ut)</td>
      <td>实例化论文 1000MW 模型, 绑到 <code>$inst</code>。
          <code>uB</code>=煤量指令(kg/s), <code>Dfw</code>=给水流量(kg/s), <code>ut</code>=调门开度(0~1)。
          每周期推进一步, 同一周期内被多次赋值不会重复积分。</td></tr>
  <tr><td class="fn">$inst.PST</td><td>主汽压力 (MPa)</td></tr>
  <tr><td class="fn">$inst.HM</td><td>分离器焓 (kJ/kg)</td></tr>
  <tr><td class="fn">$inst.NE</td><td>机组负荷 (MW)</td></tr>
</table>
<p>调用示例:</p>
<pre># 1. 实例化 (1 次)
$YQ3(协调模型) = CCS_1000(DPU3013.AQ_UB, DPU3013.AQ_DFW, DPU3013.AQ_UT)
# 2. 读管脚 (任意次, 自由组合)
DPU3013.AI_PST(主汽压力)  = $YQ3.PST
DPU3013.AI_HM (分离器焓)  = $YQ3.HM
DPU3013.AI_NE (机组负荷)  = $YQ3.NE</pre>
<p><b>状态管理</b>:模型内部 4 个状态量(rB/pm/hm/Ne)+ 煤粉 τ=20s 延迟队列。
   点【🗑 重置初值】或【🔥 清空状态】会按 yaml seed(600.9MW THA 工况)重置;启动后约 10 分钟收敛到当前输入对应的稳态。
   <b>论文模型</b>静态参数 R²: k1=0.71、λ=0.88(残差较大),稳态 Ne 误差最大 6.6%(论文 Table 7)— 这是论文模型本身的精度,非实现 bug。</p>

<h3>水蒸气热力性质 — STEAM_T</h3>
<p>给焓 + 压力,计算水蒸气温度。后端走 IAPWS-IF97 工业标准,自动识别亚饱和水 / 过热蒸汽 / 超临界区。</p>
<table>
  <tr><th>函数</th><th>含义</th><th>入参 / 输出</th></tr>
  <tr><td class="fn">STEAM_T(h, p)</td>
      <td>水蒸气温度 (°C)</td>
      <td><code>h</code>: 比焓 (kJ/kg, 范围 10~4500)<br>
          <code>p</code>: 压力 (MPa, 范围 0.001~100)<br>
          越界返 SkipCycle (诊断面板可查)</td></tr>
</table>
<p>调用示例:</p>
<pre># 主蒸汽温度: 论文 THA 工况 (15.589 MPa, 3578 kJ/kg) → 600°C
DPU3013.AI_Tst(主汽温度) = STEAM_T(DPU3013.AQ_HST, DPU3013.AQ_PST)
# 给水温度: 跟焓压一起算 (跟 CCS 模型 hfw 配套)
DPU3013.AI_Tfw(给水温度) = STEAM_T(DPU3013.AQ_HFW, DPU3013.AQ_PECO)
# 跟协调模型搭: 用 $YQ3.HM 当焓输入算分离器温度
$T_sep(分离器温度) = STEAM_T($YQ3.HM, $YQ3.PST)
DPU3013.AI_TM = $T_sep</pre>

<h2>3. 快捷键</h2>
<table>
  <tr><th>键</th><th>动作</th></tr>
  <tr><td><span class="kbd">@关键字</span></td><td>触发自动补全(支持中英文模糊)</td></tr>
  <tr><td><span class="kbd">↑</span><span class="kbd">↓</span></td><td>补全列表上下移,跨页自动翻</td></tr>
  <tr><td><span class="kbd">PgUp</span><span class="kbd">PgDn</span></td><td>补全列表整页跳</td></tr>
  <tr><td><span class="kbd">Tab</span> / <span class="kbd">Enter</span></td><td>插入选中项,补全关闭</td></tr>
  <tr><td><b><span class="kbd">Ctrl</span>+<span class="kbd">Enter</span> / <span class="kbd">Shift</span>+<span class="kbd">Enter</span> / <span class="kbd">Shift</span>+<span class="kbd">Tab</span></b></td>
      <td><b>连选模式</b>:插入后自动加 <code>, @&lt;上次关键字&gt;</code>,补全留着继续搜(常见场景:挑几个同类点)。
      <br>需要换关键字时,直接用退格删掉后缀再敲新词;<code>@</code> 不删的话 popup 会一直留着。
      <br><b>注</b>:<code>Ctrl+Tab</code> 被浏览器拦截切标签页,用上面三种或 <code>Shift+Tab</code>。
      <br>鼠标:<b>Ctrl+左键 / Shift+左键</b> 也是连选。</td></tr>
  <tr><td><span class="kbd">Esc</span></td><td>关闭补全</td></tr>
  <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">G</span></td><td>跳转到指定行</td></tr>
  <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">/</span></td><td>注释/取消注释 当前行或选中块</td></tr>
  <tr><td><span class="kbd">Tab</span></td><td>选中块整体缩进 2 空格(无选区时插 2 空格)</td></tr>
  <tr><td><span class="kbd">Shift</span>+<span class="kbd">Tab</span></td><td>选中块反缩进</td></tr>
  <tr><td><span class="kbd">F1</span></td><td>本帮助</td></tr>
  <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">F</span></td><td>浏览器原生查找(可搜 textarea 内容)</td></tr>
</table>

<h2>4. 语法高亮颜色</h2>
<table>
  <tr><th>颜色</th><th>含义</th><th>示例</th></tr>
  <tr><td><span style="background:#888;color:#fff;padding:0 6px">灰</span></td><td>注释 / 描述括号</td><td><code style="color:#888"># 注释</code></td></tr>
  <tr><td><span style="background:#90c;color:#fff;padding:0 6px">紫</span></td><td>中间变量 $xxx</td><td><code style="color:#90c">$fw_safe</code></td></tr>
  <tr><td><span style="background:#06a;color:#fff;padding:0 6px">蓝</span></td><td>函数名</td><td><code style="color:#06a">RS LAG LIMIT</code></td></tr>
  <tr><td><span style="background:#048;color:#fff;padding:0 6px">深蓝</span></td><td>OPC 短码</td><td><code style="color:#048">DPU3013.AI010502</code></td></tr>
  <tr><td><span style="background:#c60;color:#fff;padding:0 6px">橙</span></td><td>数字常数</td><td><code style="color:#c60">50.0 3 -1.5</code></td></tr>
</table>

<h2>5. 实战示例</h2>

<h3>4.1 电机闭环(开反馈 + 关反馈)</h3>
<pre># A 给煤机: 启/停指令双稳态锁存
DPU3002.DI060301(A给煤机运行) = RS(DPU3002.DQ060202(启A给煤机), DPU3002.DQ060201(停A给煤机))
DPU3002.DI060302(A给煤机跳位) = RS_NOT(DPU3002.DQ060202(启A给煤机), DPU3002.DQ060201(停A给煤机))</pre>

<h3>4.2 阀门动叶 — AI 直通 + 一阶滞后</h3>
<pre># 直通: 指令立即等于反馈 (理想动作)
DPU3016.AI010301(动叶反馈) = DPU3016.AQ090201(动叶指令)
# 一阶滞后: 模拟执行机构 3 秒响应
DPU3016.AI010301(动叶反馈) = LAG(DPU3016.AQ090201(动叶指令), 3.0)</pre>

<h3>4.3 单位换算 / 限幅</h3>
<pre># 给水流量 t/h → kg/s
DPU3013.AI_kgps = MUL(DPU3013.AQ_tph, 0.2778)
# 阀门反馈限在 0~100 %
DPU3016.AI_反馈 = LIMIT(DPU3016.AQ_指令, 0, 100)</pre>

<h3>4.4 联锁逻辑</h3>
<pre># 允许启动 = 油压正常 AND 温度正常
DPU3030.DI_允许 = AND(DPU3030.DI_油压正常, DPU3030.DI_温度正常)
# 任一保护动作即跳闸
DPU3030.DI_跳闸 = OR(DPU3030.DI_轴温保护, DPU3030.DI_振动保护)</pre>

<h3>4.5 选择(冗余测量)</h3>
<pre># 用 A 测量优先, 故障切 B
DPU3013.AI_用 = SEL(DPU3013.DI_A正常, DPU3013.AI_A测量, DPU3013.AI_B测量)</pre>

<h2>6. 脚本备份</h2>
<p>每次会冲掉 editor 的操作都<b>自动备份</b>当前脚本到 <code>config/script_backups/</code>(保留最近 30 个):</p>
<ul>
<li>【💾 保存】 → <code>_save</code></li>
<li>【📝 生成样本】 → <code>_before-gen</code></li>
<li>【📚 备份历史 → 加载】 → <code>_before-restore</code></li>
</ul>
<p>反悔随时在【📚 备份历史】里找时间戳还原。</p>

<h2>7. 运行前工作流(下载 / 上载 / 预演)</h2>
<p>正常运行 ▶ 前一般要做的 3 步,从左到右就是工具栏顺序:</p>

<table>
  <tr><th>按钮</th><th>方向</th><th>用途</th><th>何时用</th></tr>
  <tr><td><b style="background:#06a;color:#fff;padding:1px 8px">📤 下载</b></td>
      <td>磁盘镜像 → 内存 → (运行后) → 工程</td>
      <td>把保存的算法历史状态还原回来</td>
      <td>本项目 viewer 重启 / NTVDPU 重启后</td></tr>
  <tr><td><b style="background:#a06;color:#fff;padding:1px 8px">📥 上载</b></td>
      <td>工程 → 本项目内存</td>
      <td>读 DCS 当前值, 锚定 LAG/RS state, 第 1 周期写出 = 工程现状 → 无扰起步</td>
      <td>工程状态变了 (VM 镜像还原 / CCMStudio 重下组态 / DCS 端被人改 AI)</td></tr>
  <tr><td><b style="background:#0a8;color:#fff;padding:1px 8px">🔍 预演</b></td>
      <td>(只读+算, 不写)</td>
      <td>干运行一周期, 显示每个 OPC LHS 的 (算出 vs DCS 现状 vs 差值 vs 风险)</td>
      <td>上载后, 运行前, 验证脚本</td></tr>
</table>

<h3>📤 下载(状态镜像管理)</h3>
<p>镜像 = <b>运行时锁存状态</b>(RS 触发器 Q 值 / LAG 累积值 / $var 当前值)。文件 <code>data/state_snapshot.json</code>(VS Code 可直接打开看)。点 📤 下载打开管理面板:</p>
<ul>
<li>【💾 保存镜像】 — 当前内存状态落盘。<b>空内存自动拦截</b>,防误覆盖。前一份留 <code>.bak</code></li>
<li>【📤 下载镜像】 — 磁盘 → 内存。清 last_written → 跑起来后所有 LHS 强制重写一次,等于把保存状态"下载"回工程</li>
<li>【🗑 删除镜像】 — 删磁盘文件</li>
<li>【⏮ 重置初值】 — 清 LAG/$var 内存,RS 保留,镜像保留 → 下周期 LAG 跟踪脚本输入立即稳态(改了脚本系数想立即看新稳态时用)</li>
<li>【🔥 清空状态】 — 核弹级:清 RS/LAG/$var + 删镜像</li>
<li>详情表显示每个 RS/LAG/$var 当前值 + <b>"用在哪几个赋值行"</b></li>
</ul>

<h3>📥 上载(4 步无扰跟踪起步)</h3>
<ol>
<li>检查 OPC 状态(单独探活, ~10ms,不通直接给清晰错)</li>
<li>读 DCS 当前值 — 所有 LHS(用于锚定 LAG/RS state)+ 所有 RHS 引用的 OPC 节点(灌进面板"读取"列)</li>
<li>无扰跟踪同步:
  <ul>
  <li>LAG: <code>lag_state[key] = DCS 值</code>(<b>不跨 $var 边界</b>,避免温度值塞给煤量 LAG)</li>
  <li>RS: <code>Q = DCS 值</code></li>
  <li>RS_NOT: <code>Q = NOT DCS 值</code>(反算,因为 RS_NOT 输出 = NOT Q)</li>
  <li><code>last_written = {}</code>(强制下周期重写)</li>
  <li>读到的 DCS 值灌进 <code>last_read</code> — 右侧"实时值"面板"读取"列立刻反映</li>
  </ul>
</li>
<li>等用户点【▶ 运行】, 第 1 周期写出 = DCS 现状 → 0 跳变, 后续按各 τ 平滑过渡到算法目标</li>
</ol>
<p><b>前置</b>:OPC 循环必须已停(避免抢 NTVDPU session)。</p>

<h3>🔍 预演(风险评估)</h3>
<p>读 DCS 现状 → 用脚本算一周期 → 比较"算出来要写的" vs "DCS 现在的实际值" → 评估风险:</p>
<ul>
<li><b style="color:#c00">🔴 风险</b>(差 ≥ 50%) — 大概率公式错 / 量纲不对 / 写死常数跟 DCS 量级不一致</li>
<li><b style="color:#c80">🟠 警告</b>(差 10-50%) — 可能锚定不到位 / 单位换算缺失</li>
<li><b style="color:#a80">🟡 一般</b>(差 1-10%) — 小偏差, 通常无害</li>
<li><b style="color:#080">🟢 一致</b>(差 &lt; 1%) — 公式跟 DCS 现状吻合,运行安全</li>
<li><b style="color:#888">⚪ 无读值</b> — DCS 不返回 / 节点不存在</li>
</ul>
<p><b>判定容忍度</b>:LAG/RS 类(预期已被 📥 上载锚定)更严格(0.5% / 5%);其他类宽松(1% / 10% / 50%)。</p>
<p><b>推荐序列</b>:📥 上载 → 🔍 预演 → 检查 🔴 / 🟠 项 → 没问题 → ▶ 运行;有问题 → 改脚本 → 重新预演直到清零。</p>

<h2>8. NTVDPU / VM 重启场景</h2>
<p>viewer 加了 <b>OPC 自动重连</b>(连续 10 周期读失败触发);重连成功后内存 RS/LAG/$var 状态保留, last_written 自动清空 → 下周期所有 LHS 强制重写一次。所以 NTVDPU 只重启 / 试用授权 2h 重启 → <b>不需要手动干预</b>。</p>
<p>需要手动操作的情况:</p>
<table>
  <tr><th>场景</th><th>动作</th></tr>
  <tr><td>仅 NTVDPU 试用授权重启,viewer 没动</td><td>无需操作, OPC 自动重连接管</td></tr>
  <tr><td>viewer 进程也重启了</td><td>📤 下载 → 拉回上次保存的内存状态 → ▶ 运行</td></tr>
  <tr><td>VM 镜像还原(NTVDPU 端 AI 跳了)</td><td>📥 上载 → 锚定到工程现状 → ▶ 运行</td></tr>
  <tr><td>CCMStudio 重下了组态,逻辑变了</td><td>📥 上载 + 🔍 预演 双管齐下</td></tr>
</table>

<h2>9. 诊断面板</h2>
<p>【🩺 诊断】 — 一键收集运行状态 / 失败统计 / 日志,沟通时点【📋 复制全部】贴给同事。</p>
<p>状态栏会出现的 badge(直接点开打开诊断):</p>
<ul>
<li><b style="background:#a0c;color:#fff;padding:0 4px">⛔ N 对跳过</b> — 右边某节点持续读不到 → 整对赋值无效(常见:SH 段端子 NTVDPU 没暴露读权限)</li>
<li><b style="background:#c00;color:#fff;padding:0 4px">⚠ 写后未生效</b> — 写了但 DCS 端持续 ≥ 1 秒不一致(常见:DI 上游被组态强驱动)</li>
<li><b style="background:#c60;color:#fff;padding:0 4px">写失败 N 节点</b> — OPC server 拒绝写(BadTypeMismatch 等)</li>
<li><b style="background:#888;color:#fff;padding:0 4px">读失败 N 节点</b> — OPC 节点不存在 / 卡件未连</li>
</ul>

<h2>10. 事件时间线 / 错误标注 / 配置项</h2>

<h3>📜 事件 (工具栏)</h3>
<p>显式事件时间线(新→旧, 仅当前 viewer 进程内存,最近 200 条)。记录:</p>
<ul>
<li>▶ 启动 / ■ 停止 OPC 循环</li>
<li>🔗 OPC 重连成功 / ⚠ OPC 断连</li>
<li>📸 镜像保存 / 📤 下载镜像 / 🔥 清空状态 / ⏮ 重置初值</li>
<li>📥 上载 (含 LAG/RS 同步个数)</li>
<li>💾 保存脚本 / ✏ 重命名 $var 描述</li>
<li>🔌 OPC 端点切换</li>
</ul>
<p>用法:数据"调变" / 系统行为不符预期时,先翻一下 📜 事件,确认是不是热重启 / 自动重连 / 锚定操作引起的。</p>

<h3>错误标注(parse 校验,自动后台跑)</h3>
<p>编辑器输入 600ms 后自动校验,错的行直接在编辑器里标:</p>
<ul>
<li><span style="background:#fcc;text-decoration:underline wavy #c00;padding:0 4px">红波浪 + 浅红底</span> — parse 错(无 =, 未知函数, 括号不闭等), 悬停看错因</li>
<li><span style="background:#fb3;color:#900;border:1px solid #c60;border-radius:2px;padding:0 2px">单字符黄红框</span> — 全角字符(<code>（），；：。？！</code> 中文输入法残留),悬停看是哪个</li>
<li>顶部 badge: <span style="background:#c33;color:#fff;padding:0 6px;border-radius:2px;font-weight:bold;font-size:10px">✗ N 错(行 X,Y...)</span> + <span style="background:#c90;color:#fff;padding:0 6px;border-radius:2px;font-weight:bold;font-size:10px">⚠ N 警告</span> — 点击跳到第一处</li>
</ul>

<h3>$var 描述同步编辑 (右侧实时值面板)</h3>
<p>$var 行右边有 ✏ 按钮,点开改描述,自动同步到脚本里<b>所有出现位置</b>(LHS + 所有 RHS 引用),自动备份。OPC 点 (DPU.XXX) 没 ✏ — 它们的描述从 CSV 点表来,只读。</p>

<h3>悬停看变量实时值</h3>
<p>鼠标放任意 <code>$var</code> 或 <code>DPU.XXX</code> 上,200ms 后弹浮窗显示:</p>
<ul>
<li><code>$var = 25.345 (中间变量)</code></li>
<li><code>DPU.AI = 写: 60.30  读: 60.30</code></li>
<li><code>DPU.DI = 写: TRUE  读: TRUE</code></li>
</ul>
<p>用浏览器 DOM 元素探测(<code>data-token</code> 属性 + elementFromPoint),换行/中文/Tab 都不会偏。</p>

<h3>OPC 端点切换 (工具栏: OPC 本地 / VM)</h3>
<p>配置文件 <code>config/opc_endpoints.yaml</code>(机器相关,<code>.gitignore</code> 排除):</p>
<pre>mode: local                          # local | vm  (上次选择)
local: opc.tcp://127.0.0.1:9440      # NTVDPU 跑在本机
vm:    opc.tcp://192.168.31.39:9440  # NTVDPU 跑在虚拟机, LAN IP 视实际改</pre>
<p>点 [本地] / [VM] 立即持久化 + 下次点 ▶ 运行 用新地址。点 ✎ 改 VM URL(IP 变了不用打开 yaml,UI 直接改)。<b>运行中不允许切换</b>,会提示先停。</p>

<h3>实时值面板的智能刷新</h3>
<ul>
<li>OPC 停止时不重写 HTML(数据没变,省 CPU,不破坏选中)</li>
<li>用户在面板里选中文字时跳过那一帧(不冲掉选中)</li>
<li>筛选框改变时强制重渲(<code>renderValsForced</code>)</li>
</ul>

<h3>🔧 初始化(工程辅助下拉,低频)</h3>
<p>工具栏第 2 行【🔧 初始化 ▾】下拉里:</p>
<ul>
<li>📝 生成样本 — 按白名单自动生成样本脚本(电机/阀门段)</li>
<li>🔄 刷新点表 — 点表 CSV 改了后重新加载 @ 补全数据(无需重启)</li>
<li>🔌 OPC 浏览 — 从 NTVDPU 浏览实际点表(兜底, CSV 没同步时用)</li>
</ul>
<p>这些一般只在工程初次建立时用,日常用不到所以折叠收纳。</p>

<h2>11. 常见问题</h2>
<h3>Q: @ 搜不到点?</h3>
<p>① 中文输入法 — 拼音期间不弹,选完字才搜。② 该点不在已扫的 DPU CSV 里 — 点【🔄 刷新点表】。③ 关键词太严,试 <b>空格分多个词</b>(<code>@A给煤 启</code>)。</p>

<h3>Q: 改了脚本怎么生效?</h3>
<p>点【💾 保存】然后点【▶ 运行】 — 已经在跑会 <b>热重启</b>(状态栏提示 ♻),自动接管新 pairs。RS/LAG/$var 状态不丢。</p>

<h3>Q: 保存失败提示"第 N 行错"?</h3>
<p>保存按钮自动跳到错误行并选中整行,根据状态栏提示修。常见:括号不闭合、参数个数不对、未知函数名(大小写敏感)、运算符前后空格。</p>

<h3>Q: DI 写不进 / 写后未生效?</h3>
<p>① DI 通道 OPC <b>可直接写 PV</b>(NT6000 有 1 秒延迟),正常情况下应生效。② 如果【🩺 诊断 → 写后未生效】持续报某些 DI,说明 <b>DCS 组态层有上游块在驱动它</b>,我们写完下个扫描周期被覆盖。<b>解决</b>:CCMStudio 端断开 DI 上游 / 改用 MUX / 写组态软点(SH 段)。</p>

<h3>Q: SH 段读到 None 怎么办?</h3>
<p><code>DPU.SH0xxx.PROxxxxx.IN</code> 是组态块输入端子,NTVDPU 通常 <b>不让外部读这种节点</b>(返回 None)→ 整对赋值被静默跳过(⛔ badge)。解决:CCMStudio 把 IN 上游断线,或改用同块的 <code>.OUT</code> 端子。</p>

<h3>Q: NTVDPU 试用授权到期重启怎么办?</h3>
<p>看 <b>第 8 节</b>(NTVDPU / VM 重启场景) — 简版:仅 NTVDPU 重启不用管(自动重连),viewer 自己被重启了点 📤 下载,VM 镜像还原后点 📥 上载。</p>

<h3>Q: 镜像里 RS 全是 0?</h3>
<p>说明你保存时内存里就没有 RS 状态(可能 viewer 刚重启 / 脚本没运行 / 全部 SkipCycle 中)。先【▶ 运行】跑一会儿让 RS 算出值,再保存。<b>空内存保存已被拦截</b>,防止误覆盖。</p>

<h3>Q: 嵌套函数 / 中缀运算符支持吗?</h3>
<p>✓ 都支持。可写 <code>LIMIT(MUL(x, 0.5), 0, 100)</code> 或 <code>LIMIT(x * 0.5, 0, 100)</code> 或 <code>(x / 250) ^ 2</code>。中间变量 <code>$xxx</code> 在表达式里也通用。</p>

<p style="margin-top:24px;color:#888;font-size:11px;border-top:1px dotted #ccc;padding-top:10px">
按 <span class="kbd">Esc</span> 或点 × 关闭。
</p>
`;

function openHelp() {
  document.getElementById('helpbody').innerHTML = HELP_HTML;
  document.getElementById('helpmodal').style.display = 'block';
}
function closeHelp() {
  document.getElementById('helpmodal').style.display = 'none';
}

// ===== 诊断模态 =====
let _lastDebug = null;
async function openDebug() {
  document.getElementById('dbgbody').innerHTML = '<span style="color:#999">载入中...</span>';
  document.getElementById('dbgmodal').style.display = 'block';
  try {
    const r = await fetch('/api/script/debug');
    const d = await r.json();
    _lastDebug = d;
    document.getElementById('dbgbody').innerHTML = renderDebug(d);
  } catch (e) {
    document.getElementById('dbgbody').innerHTML = `<span style="color:#c00">载入失败: ${e}</span>`;
  }
}
function closeDebug() { document.getElementById('dbgmodal').style.display = 'none'; }

// ===== 事件时间线 =====
async function openEvents() {
  document.getElementById('evtmodal').style.display = 'block';
  await refreshEvents();
}
function closeEvents() { document.getElementById('evtmodal').style.display = 'none'; }

const EVT_STYLE = {
  'run':       { bg: '#e6f4ea', color: '#0a0', label: '运行' },
  'stop':      { bg: '#eee',    color: '#555', label: '停止' },
  'opc':       { bg: '#e0f0ff', color: '#06a', label: 'OPC' },
  'opc-err':   { bg: '#fee',    color: '#c33', label: 'OPC 错' },
  'snapshot':  { bg: '#fff4d6', color: '#a60', label: '镜像' },
  'state':     { bg: '#fde',    color: '#c33', label: '状态' },
  'save':      { bg: '#f0f0f0', color: '#444', label: '保存' },
  'endpoint':  { bg: '#e0f0ff', color: '#06a', label: '端点' },
  'error':     { bg: '#fcc',    color: '#900', label: '错' },
};
function _fmtTs(ts) {
  const d = new Date(ts * 1000);
  const pad = n => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function _ageStr(ts) {
  const dt = Date.now() / 1000 - ts;
  if (dt < 60) return `${dt.toFixed(0)}s 前`;
  if (dt < 3600) return `${(dt / 60).toFixed(0)}m 前`;
  return `${(dt / 3600).toFixed(1)}h 前`;
}
async function refreshEvents() {
  const body = document.getElementById('evtbody');
  body.innerHTML = '<div style="padding:20px;color:#999">载入中...</div>';
  try {
    const r = await fetch('/api/script/events');
    const d = await r.json();
    const evs = d.events || [];
    if (!evs.length) {
      body.innerHTML = '<div style="padding:30px;color:#999;text-align:center">还没事件 — 启动 / 保存镜像 / 切端点 后会出现在这里</div>';
      return;
    }
    body.innerHTML = evs.map(e => {
      const s = EVT_STYLE[e.category] || { bg: '#f5f5f5', color: '#666', label: e.category };
      const detail = (e.detail && Object.keys(e.detail).length)
        ? `<div style="color:#888;font-size:10px;margin-top:2px;margin-left:78px">${
            Object.entries(e.detail).map(([k, v]) =>
              `${k}=${JSON.stringify(v).replace(/"/g, '')}`).join(' · ')
          }</div>` : '';
      return `<div style="padding:5px 14px;border-bottom:1px solid #f0f0f0;display:flex;align-items:flex-start;gap:10px;">
        <span style="color:#888;font-size:10px;min-width:60px">${_fmtTs(e.ts)}</span>
        <span style="background:${s.bg};color:${s.color};padding:1px 6px;border-radius:2px;
              font-size:10px;font-weight:bold;min-width:42px;text-align:center">${s.label}</span>
        <div style="flex:1">
          <div>${e.msg}</div>
          ${detail}
        </div>
        <span style="color:#aaa;font-size:10px;white-space:nowrap">${_ageStr(e.ts)}</span>
      </div>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<div style="padding:20px;color:#c00">载入失败: ${e}</div>`;
  }
}

async function resetState() {
  if (!confirm('清空 RS/LAG/中间变量 持久状态?\n用于"从头开始"仿真. 当前运行中的循环不受影响, 但本次开始所有锁存重置.')) return;
  const r = await fetch('/api/script/reset_state', {method: 'POST'});
  const d = await r.json();
  setStatus(d.ok ? `✓ ${d.msg}` : '✗ 失败', d.ok ? 'ok' : 'err');
  await refreshSnapInfo();   // 跟其他几个状态操作一致, 不再跳到诊断面板
}

async function reinitState() {
  if (!confirm('重置初值?\n\n· 清掉 LAG 一阶滞后内存 → 下周期所有 LAG 跟踪当前输入立即稳态(不再爬升)\n· 清掉 $中间变量 → 下周期按脚本重新计算\n· 清掉 last_written 写缓存 → 强制重写一遍同步 DCS\n· 保留 RS 锁存(开/关命令历史不丢)\n· 保留磁盘镜像文件\n\n适用: 改了脚本系数后想立即看到新稳态, 不想等 LAG 慢慢爬过去')) return;
  const r = await fetch('/api/script/reinit', {method: 'POST'});
  const d = await r.json();
  setStatus(d.ok ? `⏮ ${d.msg}` : '✗ 失败', d.ok ? 'ok' : 'err');
  openSnapshot();   // 刷新面板看清空效果
}

// ===== 下拉折叠菜单 (低频按钮组) =====
function toggleDropdown(id, ev) {
  // 先关其他所有下拉
  document.querySelectorAll('.dropdown-menu').forEach(m => {
    if (m.id !== id) m.style.display = 'none';
  });
  const d = document.getElementById(id);
  if (d) d.style.display = (d.style.display === 'none' || d.style.display === '') ? 'block' : 'none';
  if (ev) ev.stopPropagation();
}
function closeDropdown(id) {
  const d = document.getElementById(id);
  if (d) d.style.display = 'none';
}
// 点击外部 → 关掉所有下拉
document.addEventListener('click', (e) => {
  if (!e.target.closest('.dropdown-wrap')) {
    document.querySelectorAll('.dropdown-menu').forEach(m => m.style.display = 'none');
  }
});

// ===== 🔍 干运行预演 =====
let _dryrunData = null;
async function dryrunPreview() {
  document.getElementById('dryrunmodal').style.display = 'block';
  document.getElementById('dryrunbody').innerHTML =
    '<div style="padding:30px;color:#999;text-align:center">读 DCS 中, 模拟一周期...</div>';
  document.getElementById('dryrunsummary').innerHTML = '';
  try {
    const r = await fetch('/api/script/dryrun_preview', {method: 'POST'});
    const d = await r.json();
    if (!d.ok) {
      document.getElementById('dryrunbody').innerHTML =
        `<div style="padding:30px;color:#c00;text-align:center">✗ ${d.error}</div>`;
      return;
    }
    _dryrunData = d;
    // 汇总
    const s = d.summary;
    document.getElementById('dryrunsummary').innerHTML =
      `读 ${s.nodes_read} 节点 · 算 ${s.total} 个 OPC LHS · ` +
      `<span style="color:#c00;font-weight:bold">🔴 风险 ${s.risk}</span> · ` +
      `<span style="color:#c80;font-weight:bold">🟠 警告 ${s.warning}</span> · ` +
      `<span style="color:#a80">🟡 一般 ${s.info}</span> · ` +
      `<span style="color:#080">🟢 一致 ${s.ok}</span> · ` +
      `<span style="color:#888">⚪ 无读值 ${s.no_actual + s.non_numeric}</span> · ` +
      `跳过 ${s.skipped}`;
    renderDryrun();
  } catch (e) {
    document.getElementById('dryrunbody').innerHTML =
      `<div style="padding:30px;color:#c00">载入失败: ${e}</div>`;
  }
}
function closeDryrun() { document.getElementById('dryrunmodal').style.display = 'none'; }

function renderDryrun() {
  if (!_dryrunData) return;
  const items = _dryrunData.items;
  const showRisk = document.getElementById('drFltRisk').checked;
  const showWarn = document.getElementById('drFltWarn').checked;
  const showInfo = document.getElementById('drFltInfo').checked;
  const showOk   = document.getElementById('drFltOk').checked;
  const showNoAct= document.getElementById('drFltNoAct').checked;
  const kw = (document.getElementById('drFltKw').value || '').toLowerCase().trim();
  const filtered = items.filter(it => {
    if (it.risk === 'risk' && !showRisk) return false;
    if (it.risk === 'warning' && !showWarn) return false;
    if (it.risk === 'info' && !showInfo) return false;
    if (it.risk === 'ok' && !showOk) return false;
    if ((it.risk === 'no_actual' || it.risk === 'non_numeric') && !showNoAct) return false;
    if (kw) {
      const desc = (tagDescMap[it.lhs] || '').toLowerCase();
      if (!(it.lhs_short.toLowerCase().includes(kw) ||
            (it.kind||'').toLowerCase().includes(kw) ||
            desc.includes(kw))) return false;
    }
    return true;
  });
  const styleByRisk = {
    risk:     { bg: '#fee', color: '#c00', label: '🔴 风险' },
    warning:  { bg: '#fff3e0', color: '#c80', label: '🟠 警告' },
    info:     { bg: '#fffce0', color: '#a80', label: '🟡 一般' },
    ok:       { bg: '#f0f7f0', color: '#080', label: '🟢 一致' },
    no_actual:{ bg: '#f5f5f5', color: '#888', label: '⚪ 无读值' },
    non_numeric:{bg:'#f5f5f5', color:'#888', label: '⚪ 非数值' },
  };
  function fmtNum(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE';
    if (typeof v === 'number') return v.toFixed(3);
    return String(v);
  }
  // 描述从 tagDescMap (脚本里 (信号名称) 解析得来), 没有就空
  if (Object.keys(tagDescMap).length === 0) rebuildDescMap();
  const rows = filtered.slice(0, 500).map(it => {
    const sty = styleByRisk[it.risk] || styleByRisk.ok;
    const diffStr = (it.diff === null || it.diff === undefined) ? '—'
                   : (it.diff >= 0 ? '+' : '') + it.diff.toFixed(3);
    const relStr = (it.rel === null || it.rel === undefined) ? '' : ` (${(it.rel*100).toFixed(2)}%)`;
    const desc = tagDescMap[it.lhs] || '';
    return `<tr style="background:${sty.bg};border-bottom:1px solid #eee">
      <td style="padding:3px 8px"><span style="color:${sty.color};font-weight:bold">${sty.label}</span></td>
      <td style="padding:3px 8px"><code style="color:#048">${it.lhs_short}</code></td>
      <td style="padding:3px 8px;color:#222">${desc}</td>
      <td style="padding:3px 8px;color:#888">${it.kind}</td>
      <td style="padding:3px 8px;text-align:right"><b>${fmtNum(it.computed)}</b></td>
      <td style="padding:3px 8px;text-align:right;color:#06a">${fmtNum(it.actual)}</td>
      <td style="padding:3px 8px;text-align:right;color:${sty.color}"><b>${diffStr}</b>${relStr}</td>
    </tr>`;
  }).join('');
  document.getElementById('dryrunbody').innerHTML =
    `<table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead><tr style="background:#222;color:#eee;position:sticky;top:0;">
        <th style="padding:5px 8px;text-align:left;width:78px">风险</th>
        <th style="padding:5px 8px;text-align:left;width:170px">LHS 短码</th>
        <th style="padding:5px 8px;text-align:left">信号名称</th>
        <th style="padding:5px 8px;text-align:left;width:60px">类型</th>
        <th style="padding:5px 8px;text-align:right;width:90px">算出 (写入)</th>
        <th style="padding:5px 8px;text-align:right;width:90px">DCS 现状</th>
        <th style="padding:5px 8px;text-align:right;width:160px">差 (相对)</th>
      </tr></thead><tbody>${rows}</tbody></table>` +
    (filtered.length > 500 ? `<div style="padding:6px 12px;color:#999">... 还有 ${filtered.length-500} 项, 加筛选缩小</div>` : '');
}

async function syncLagFromDcs() {
  if (!confirm('上载?\n\n方向: 工程 (NTVDPU) → 本项目\n\n4 步流程:\n  1. 检查 OPC 状态 (确认工程可达, 不行直接给清晰错误)\n  2. 读 DCS 当前值 — 所有直接写 OPC 的 LHS (含 LAG / RS / RS_NOT)\n  3. 无扰跟踪处理:\n       · LAG (含嵌套, 穿过 $var/LIMIT 等): lag_state = DCS 值\n       · RS (合位/开位等): Q = DCS 值\n       · RS_NOT (跳位/关位等): Q = NOT DCS 值 (反算)\n       · last_written 清空, 第 1 周期强制重写\n  4. 接着点【▶ 运行】, 第 1 周期写出去 = DCS 现状 → 零跳变,\n     后续按各 τ 平滑过渡到算法目标\n\n· 只动 LAG / RS state (RS_NOT 也用同一个 rs_state)\n· $var / 镜像文件 / 配置 都不变\n· 普通编辑保存的工作流不受影响\n\n前置: OPC 循环必须已停\n典型场景: VM 镜像还原 / CCMStudio 重下组态 / DCS 端被人工改过')) return;
  setStatus('📥 正在 4 步上载 (OPC 检查 → 读 → 无扰同步 → 待运行)...', 'run');
  const r = await fetch('/api/script/reinit_from_dcs', {method: 'POST'});
  const d = await r.json();
  if (d.ok) {
    setStatus(`📥 上载完成:\n${d.msg}`, 'ok');
    // 拉新 status (含刚灌进的 last_read), 强制刷一次面板 (停止状态下 panel 被冻结 skip)
    await pollStatus();
    renderValsForced();
  } else {
    setStatus(`✗ ${d.msg || d.error}`, 'err');
  }
}

async function openSnapshot() {
  document.getElementById('snapmodal').style.display = 'block';
  await refreshSnapInfo();
}
function closeSnapshot() { document.getElementById('snapmodal').style.display = 'none'; }

async function refreshSnapInfo() {
  const [infoR, stR] = await Promise.all([
    fetch('/api/script/state/info?detail=1'),
    fetch('/api/script/status'),
  ]);
  const info = await infoR.json();
  const st = await stR.json();
  const box = document.getElementById('snapinfo');
  const restoreBtn = document.getElementById('snapRestoreBtn');
  const delBtn = document.getElementById('snapDeleteBtn');

  // 顶部:内存 vs 磁盘对照表
  const memN = {rs: st.memory_rs_count||0, lag: st.memory_lag_count||0, var: st.memory_var_count||0};
  const dskN = {rs: info.exists ? info.rs_count : '—',
                lag: info.exists ? info.lag_count : '—',
                var: info.exists ? info.var_count : '—'};
  let html = `<table style="width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px;">
    <tr style="background:#f5f5f5;border-bottom:1px solid #ccc">
      <th style="text-align:left;padding:4px 8px;width:120px"></th>
      <th style="text-align:center;padding:4px 8px">RS 触发器</th>
      <th style="text-align:center;padding:4px 8px">LAG</th>
      <th style="text-align:center;padding:4px 8px">中间变量 $</th>
    </tr>
    <tr style="border-bottom:1px dotted #ddd">
      <td style="padding:3px 8px;color:#666">💾 当前内存</td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#048">${memN.rs}</b></td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#048">${memN.lag}</b></td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#048">${memN.var}</b></td>
    </tr>
    <tr>
      <td style="padding:3px 8px;color:#666">📸 磁盘镜像</td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#06a">${dskN.rs}</b></td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#06a">${dskN.lag}</b></td>
      <td style="text-align:center;padding:3px 8px"><b style="color:#06a">${dskN.var}</b></td>
    </tr>
  </table>`;

  if (!info.exists) {
    html += `<div style="color:#888;text-align:center;padding:10px">还没有保存过镜像</div>`;
    box.innerHTML = html;
    restoreBtn.disabled = true; restoreBtn.style.opacity = 0.4;
    delBtn.disabled = true; delBtn.style.opacity = 0.4;
    return;
  }
  if (info.error) {
    html += `<div style="color:#c00">镜像文件存在但读取失败: ${info.error}</div>`;
    box.innerHTML = html;
    return;
  }
  html += `<div style="color:#888;font-size:11px;margin-bottom:8px">
    保存时间 <b>${info.saved_at}</b> (${info.age_s} 秒前) ·
    文件 <code style="font-size:10px">${info.path||'data/state_snapshot.json'}</code> · ${info.size_bytes} B
  </div>`;

  // 详情表
  function fmtV(v) {
    if (v === true) return '<b style="color:#0a0">1</b>';
    if (v === false) return '<b style="color:#c00">0</b>';
    if (typeof v === 'number') return Number.isInteger(v) ? v : v.toFixed(3);
    return String(v);
  }
  function escH(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  if ((info.rs_detail || []).length) {
    html += `<details style="margin-top:10px" open>
      <summary style="cursor:pointer;color:#048;font-weight:bold;font-size:12px;padding:4px 0">▼ RS 触发器 (${info.rs_detail.length} 个)</summary>
      <div style="max-height:240px;overflow:auto;border:1px solid #ddd;margin-top:4px">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">
          <thead><tr style="background:#f5f5f5;position:sticky;top:0">
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">S (开指令)</th>
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">R (关指令)</th>
            <th style="text-align:center;padding:3px 6px;width:36px;border-bottom:1px solid #ccc">Q</th>
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">用在</th>
          </tr></thead><tbody>`;
    for (const it of info.rs_detail) {
      const [s, r] = it.args;
      const users = (it.users || []).map(escH).join('<br>') || '<span style="color:#aaa">(无引用)</span>';
      html += `<tr style="border-bottom:1px dotted #eee">
        <td style="padding:2px 6px;white-space:nowrap">${escH(s||'')}</td>
        <td style="padding:2px 6px;white-space:nowrap">${escH(r||'')}</td>
        <td style="text-align:center;padding:2px 6px">${fmtV(it.value)}</td>
        <td style="padding:2px 6px;color:#048">${users}</td>
      </tr>`;
    }
    html += `</tbody></table></div></details>`;
  }

  if ((info.lag_detail || []).length) {
    html += `<details style="margin-top:8px" open>
      <summary style="cursor:pointer;color:#048;font-weight:bold;font-size:12px;padding:4px 0">▼ LAG 一阶滞后 (${info.lag_detail.length} 个)</summary>
      <div style="max-height:160px;overflow:auto;border:1px solid #ddd;margin-top:4px">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">
          <thead><tr style="background:#f5f5f5;position:sticky;top:0">
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">输入</th>
            <th style="text-align:right;padding:3px 6px;width:50px;border-bottom:1px solid #ccc">T</th>
            <th style="text-align:right;padding:3px 6px;width:70px;border-bottom:1px solid #ccc">y</th>
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">用在</th>
          </tr></thead><tbody>`;
    for (const it of info.lag_detail) {
      const users = (it.users || []).map(escH).join('<br>') || '<span style="color:#aaa">(无引用)</span>';
      html += `<tr style="border-bottom:1px dotted #eee">
        <td style="padding:2px 6px">${escH(it.args[0]||'')}</td>
        <td style="text-align:right;padding:2px 6px">${escH(it.args[1]||'')}</td>
        <td style="text-align:right;padding:2px 6px">${fmtV(it.value)}</td>
        <td style="padding:2px 6px;color:#048">${users}</td>
      </tr>`;
    }
    html += `</tbody></table></div></details>`;
  }

  if (Object.keys(info.var_detail || {}).length) {
    html += `<details style="margin-top:8px" open>
      <summary style="cursor:pointer;color:#048;font-weight:bold;font-size:12px;padding:4px 0">▼ 中间变量 $xxx (${Object.keys(info.var_detail).length} 个)</summary>
      <div style="max-height:160px;overflow:auto;border:1px solid #ddd;margin-top:4px">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">
          <thead><tr style="background:#f5f5f5;position:sticky;top:0">
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">变量名</th>
            <th style="text-align:right;padding:3px 6px;width:80px;border-bottom:1px solid #ccc">当前值</th>
          </tr></thead><tbody>`;
    for (const [k, v] of Object.entries(info.var_detail)) {
      html += `<tr style="border-bottom:1px dotted #eee">
        <td style="padding:2px 6px;color:#90c"><b>${escH(k)}</b></td>
        <td style="text-align:right;padding:2px 6px">${fmtV(v)}</td>
      </tr>`;
    }
    html += `</tbody></table></div></details>`;
  }

  // 历史副本 (每次保存自动留时间戳, 最多 30 个)
  try {
    const bkR = await fetch('/api/script/state/backups');
    const bkData = await bkR.json();
    const items = bkData.items || [];
    html += `<details style="margin-top:10px" ${items.length ? 'open' : ''}>
      <summary style="cursor:pointer;color:#048;font-weight:bold;font-size:12px;padding:4px 0">
        ▼ 📚 历史副本 (${items.length} 份, 最多留 30)
      </summary>`;
    if (!items.length) {
      html += `<div style="color:#888;padding:6px 4px">还没有历史副本 — 每次【💾 保存镜像】会在 <code>data/snapshot_backups/</code> 自动留一份带时戳的副本</div>`;
    } else {
      html += `<div style="max-height:200px;overflow:auto;border:1px solid #ddd;margin-top:4px">
        <table style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace">
          <thead><tr style="background:#f5f5f5;position:sticky;top:0">
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">保存时间</th>
            <th style="text-align:left;padding:3px 6px;border-bottom:1px solid #ccc">文件名</th>
            <th style="text-align:right;padding:3px 6px;width:60px;border-bottom:1px solid #ccc">大小</th>
            <th style="text-align:center;padding:3px 6px;width:130px;border-bottom:1px solid #ccc">操作</th>
          </tr></thead><tbody>`;
      for (const it of items) {
        const when = new Date(it.ts * 1000).toLocaleString('zh-CN');
        const sz = it.size < 1024 ? `${it.size} B` : `${(it.size/1024).toFixed(1)} KB`;
        html += `<tr style="border-bottom:1px dotted #eee">
          <td style="padding:2px 6px">${when}</td>
          <td style="padding:2px 6px;color:#666">${it.name}</td>
          <td style="text-align:right;padding:2px 6px;color:#888">${sz}</td>
          <td style="text-align:center;padding:2px 6px">
            <button onclick="restoreBackup('${it.name}')" style="font-size:10px;padding:1px 6px;background:#06a;color:#fff;border:0;cursor:pointer" title="把这份历史镜像下载进内存">📤 恢复</button>
            <button onclick="deleteBackup('${it.name}')" style="font-size:10px;padding:1px 6px;background:#999;color:#fff;border:0;cursor:pointer" title="删除这份历史副本">🗑</button>
          </td>
        </tr>`;
      }
      html += `</tbody></table></div>`;
    }
    html += `</details>`;
  } catch(e) { /* 列历史失败不影响主视图 */ }

  box.innerHTML = html;
  restoreBtn.disabled = false; restoreBtn.style.opacity = 1;
  delBtn.disabled = false; delBtn.style.opacity = 1;
}

async function restoreBackup(name) {
  if (!confirm(`恢复历史副本 "${name}" 到内存?\n\n会覆盖当前 RS/LAG/$var 内存. 跑起来后所有 LHS 强制重写一遍.`)) return;
  setStatus('恢复历史镜像中...', 'run');
  const r = await fetch('/api/script/state/restore', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: name})});
  const d = await r.json();
  setStatus(d.ok ? `📤 已从 ${name} 恢复 (RS ${d.restored.rs}/LAG ${d.restored.lag}/$var ${d.restored.var})`
                : `✗ ${d.error}`, d.ok ? 'ok' : 'err');
  if (d.ok) refreshSnapInfo();
}

async function deleteBackup(name) {
  if (!confirm(`删除历史副本 "${name}"?\n\n不影响主镜像 data/state_snapshot.json. 操作不可撤销.`)) return;
  const r = await fetch('/api/script/state/backup/delete', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: name})});
  const d = await r.json();
  setStatus(d.ok ? `🗑 已删除 ${name}` : `✗ ${d.error}`, d.ok ? 'ok' : 'err');
  if (d.ok) refreshSnapInfo();
}

async function saveSnapshot() {
  // 先看内存里实际有多少
  const st = await (await fetch('/api/script/status')).json();
  const memN = `RS ${st.memory_rs_count||0} / LAG ${st.memory_lag_count||0} / $var ${st.memory_var_count||0}`;
  // 先看磁盘上现有镜像有多少
  const info = await (await fetch('/api/script/state/info')).json();
  const diskN = info.exists
    ? `RS ${info.rs_count} / LAG ${info.lag_count} / $var ${info.var_count} (${info.saved_at})`
    : '(还没有镜像)';
  const memEmpty = !st.memory_rs_count && !st.memory_lag_count && !st.memory_var_count;

  let prompt = `保存当前内存状态到镜像?\n\n当前内存: ${memN}\n磁盘镜像: ${diskN}\n\n`;
  if (memEmpty) {
    prompt += '⚠ 警告: 内存里全是空状态! 保存会用空状态覆盖已有镜像.\n';
    prompt += '建议先点【▶ 运行】跑一段时间, 让 RS 触发器算出值再保存.\n\n';
    prompt += '仍要强制保存空状态? (会覆盖磁盘镜像)';
    if (!confirm(prompt)) return;
    // 强制保存
    const r = await fetch('/api/script/state/save', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({force: true})});
    const d = await r.json();
    setStatus(d.ok ? `⚠ 已用空状态覆盖镜像` : '✗ ' + d.error, d.ok ? 'warn' : 'err');
  } else {
    if (!confirm(prompt + '点确定保存(会覆盖磁盘上的镜像).')) return;
    const r = await fetch('/api/script/state/save', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: '{}'});
    const d = await r.json();
    const msg = d.ok ? `✓ 镜像已保存: RS ${d.saved.rs} / LAG ${d.saved.lag} / $var ${d.saved.var}` : '✗ ' + d.error;
    setStatus(msg, d.ok ? 'ok' : 'err');
  }
  await refreshSnapInfo();
}

async function restoreSnapshot() {
  const info = await (await fetch('/api/script/state/info')).json();
  if (!info.exists) return;
  if (!confirm(`下载镜像?\n方向: 磁盘镜像 → 本项目内存 → (运行后) 工程\n镜像保存于: ${info.saved_at} (${info.age_s} 秒前)\n含: RS ${info.rs_count} / LAG ${info.lag_count} / $var ${info.var_count}\n\n当前内存状态会被覆盖.`)) return;
  const r = await fetch('/api/script/state/restore', {method: 'POST'});
  const d = await r.json();
  const msg = d.ok ? `📤 已下载 (镜像 ${d.saved_at}): RS ${d.restored.rs} / LAG ${d.restored.lag} / $var ${d.restored.var}` : '✗ ' + d.error;
  setStatus(msg, d.ok ? 'ok' : 'err');
  await refreshSnapInfo();
}

async function deleteSnapshot() {
  if (!confirm('删除镜像文件?\n删除后无法恢复,要重新保存才能再用.')) return;
  const r = await fetch('/api/script/state/delete', {method: 'POST'});
  const d = await r.json();
  setStatus(d.ok ? '✓ 镜像已删除' : '✗ ' + (d.error||''), d.ok ? 'ok' : 'err');
  await refreshSnapInfo();
}

function renderDebug(d) {
  function escH(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function tag(level) {
    const c = level === 'ERROR' ? '#c00' : level === 'WARNING' ? '#c80' : '#06a';
    return `<span style="color:${c};font-weight:bold;">${level}</span>`;
  }
  function ts(t) {
    const dt = new Date(t * 1000);
    return dt.toTimeString().slice(0, 8) + '.' + String(dt.getMilliseconds()).padStart(3, '0');
  }

  // 顶部状态
  let html = `<h3 style="margin:0 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px;font-family:sans-serif">▶ 运行状态</h3>`;
  html += `<pre style="background:#f5f5f5;padding:8px;margin:0;font-size:11px">`;
  html += `运行中:        ${d.running ? '是' : '否'}\n`;
  html += `运行时长:      ${d.uptime_s.toFixed(1)}s\n`;
  html += `周期:          ${d.cycle_count}\n`;
  html += `OPC 读累计:    ${d.read_count}\n`;
  html += `OPC 写累计:    ${d.write_count}\n`;
  html += `脚本对数:      ${d.pairs_count}\n`;
  html += `OPC URL:       ${d.opc_url}\n`;
  html += `周期 dt:       ${d.dt}s\n`;
  html += `最近错:        ${d.last_error || '(无)'}`;
  html += `</pre>`;

  // 静默跳过 — RHS 读不到导致整对跳过 (最易被忽略的"安静失效")
  if (d.top_skipped && d.top_skipped.length) {
    html += `<h3 style="margin:14px 0 8px;border-bottom:2px solid #a0c;padding-bottom:4px;font-family:sans-serif;color:#a0c">⛔ 被跳过的赋值 (${d.top_skipped.length}) — RHS 节点读不到 → 整对赋值无效</h3>`;
    html += `<div style="background:#faf0fc;padding:8px;font-size:11px">`;
    html += `<div style="color:#666;margin-bottom:6px;font-size:10px">原因: 右边某个 OPC 节点持续 read=None → 求值时 SkipCycle → LHS 不写 → DCS 不变. 常见: ① SH 段端子 NTVDPU 没暴露读权限 ② OPC 节点拼错 ③ 节点存在但 NTVDPU 卡件未配置.</div>`;
    html += `<table style="width:100%;border-collapse:collapse;">`;
    html += `<tr style="background:#f0d6f5"><th style="text-align:left;padding:2px 6px">LHS (被跳的赋值)</th><th style="text-align:left;padding:2px 6px">读不到的源节点</th><th style="text-align:right;padding:2px 6px;width:60px">次数</th></tr>`;
    for (const [lhs, n, cause] of d.top_skipped) {
      const lhsShort = String(lhs).replace('ns=0;s=', '').replace('.HW.', '.').replace(/\.PV$/, '');
      const causeShort = String(cause).replace('ns=0;s=', '').replace('.HW.', '.').replace(/\.PV$/, '');
      html += `<tr style="border-bottom:1px dotted #ecd"><td style="padding:2px 6px"><code>${escH(lhsShort)}</code></td><td style="padding:2px 6px"><code style="color:#a0c">${escH(causeShort)}</code></td><td style="text-align:right;padding:2px 6px;font-weight:bold">${n}</td></tr>`;
    }
    html += `</table></div>`;
  }
  // 写后未生效 (跟 DCS 实际值对比)
  if (d.write_ineffective && d.write_ineffective.length) {
    html += `<h3 style="margin:14px 0 8px;border-bottom:2px solid #c00;padding-bottom:4px;font-family:sans-serif;color:#c00">⚠ 写后未生效 (${d.write_ineffective.length}) — 持续 ≥ 1 秒不一致</h3>`;
    html += `<div style="background:#fef0f0;padding:8px;font-size:11px">`;
    html += `<div style="color:#666;margin-bottom:6px;font-size:10px">已扣除 NTVDPU 内部 1 秒写入延迟。这里列出的是<b>真的没写进</b>的. 常见原因: ① AI HR/LR 没暴露 · ② DI 上游被组态驱动 · ③ SH 端子被组态覆盖</div>`;
    html += `<table style="width:100%;border-collapse:collapse;">`;
    html += `<tr style="background:#fdd"><th style="text-align:left;padding:2px 6px">节点</th><th style="text-align:right;padding:2px 6px;width:60px">我们写</th><th style="text-align:right;padding:2px 6px;width:60px">DCS 实际</th><th style="text-align:right;padding:2px 6px;width:60px">持续</th></tr>`;
    for (const it of d.write_ineffective) {
      const w = it.wrote === true ? '1' : it.wrote === false ? '0' : (typeof it.wrote === 'number' ? it.wrote.toFixed(2) : String(it.wrote));
      const a = it.actual === true ? '1' : it.actual === false ? '0' : (typeof it.actual === 'number' ? it.actual.toFixed(2) : String(it.actual));
      const short = it.node.replace('ns=0;s=', '').replace('.HW.', '.').replace(/\.PV$/, '');
      html += `<tr style="border-bottom:1px dotted #fbb"><td style="padding:2px 6px"><code>${escH(short)}</code></td><td style="text-align:right;padding:2px 6px;color:#0a0;font-weight:bold">${w}</td><td style="text-align:right;padding:2px 6px;color:#c00;font-weight:bold">${a}</td><td style="text-align:right;padding:2px 6px;color:#666">${it.streak || 0}周期</td></tr>`;
    }
    html += `</table></div>`;
  }
  // pairs 摘要
  if (d.pairs_summary) {
    html += `<h3 style="margin:14px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px;font-family:sans-serif">▶ 脚本摘要</h3>`;
    html += '<div style="display:flex;gap:20px">';
    html += '<div><b>LHS 分类:</b><br>';
    for (const [k, v] of Object.entries(d.pairs_summary.lhs_by_type || {})) {
      html += `  ${escH(k)}: ${v}<br>`;
    }
    html += '</div>';
    html += '<div><b>函数使用:</b><br>';
    for (const [k, v] of Object.entries(d.pairs_summary.function_usage || {})) {
      html += `  ${escH(k)}: ${v}<br>`;
    }
    html += '</div></div>';
  }

  // 失败节点 Top
  if (d.top_read_fail && d.top_read_fail.length) {
    html += `<h3 style="margin:14px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px;font-family:sans-serif">▶ 读失败 Top 20</h3>`;
    html += '<pre style="background:#fef0f0;padding:8px;margin:0;font-size:11px">';
    for (const [node, n] of d.top_read_fail) html += `${String(n).padStart(6)}× ${escH(node)}\n`;
    html += '</pre>';
  }
  if (d.top_write_fail && d.top_write_fail.length) {
    html += `<h3 style="margin:14px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px;font-family:sans-serif">▶ 写失败 Top 20</h3>`;
    html += '<pre style="background:#fef0f0;padding:8px;margin:0;font-size:11px">';
    for (const [node, n] of d.top_write_fail) html += `${String(n).padStart(6)}× ${escH(node)}\n`;
    html += '</pre>';
  }

  // 日志
  html += `<h3 style="margin:14px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px;font-family:sans-serif">▶ 最近日志 (${(d.logs || []).length} 条)</h3>`;
  html += '<pre style="background:#fafafa;padding:8px;margin:0;font-size:11px;max-height:280px;overflow-y:auto">';
  for (const e of (d.logs || []).slice().reverse()) {
    html += `[${ts(e.ts)}] ${tag(e.level)} ${escH(e.logger)}: ${escH(e.msg)}\n`;
  }
  html += '</pre>';
  return html;
}

function copyDebug() {
  if (!_lastDebug) return;
  const d = _lastDebug;
  // 纯文本格式, 适合贴到聊天
  let txt = `=== LeDCSSIM 诊断报告 (${new Date().toISOString()}) ===\n\n`;
  txt += `[运行状态]\n`;
  txt += `  running=${d.running}, uptime=${d.uptime_s.toFixed(1)}s, cycle=${d.cycle_count}\n`;
  txt += `  read=${d.read_count}, write=${d.write_count}, pairs=${d.pairs_count}\n`;
  txt += `  opc=${d.opc_url}, dt=${d.dt}s\n`;
  txt += `  last_error: ${d.last_error || '(无)'}\n\n`;
  if (d.pairs_summary) {
    txt += `[脚本摘要]\n`;
    txt += `  LHS:  ${JSON.stringify(d.pairs_summary.lhs_by_type)}\n`;
    txt += `  函数: ${JSON.stringify(d.pairs_summary.function_usage)}\n\n`;
  }
  if (d.top_read_fail && d.top_read_fail.length) {
    txt += `[读失败 Top]\n`;
    for (const [node, n] of d.top_read_fail) txt += `  ${n}× ${node}\n`;
    txt += '\n';
  }
  if (d.top_write_fail && d.top_write_fail.length) {
    txt += `[写失败 Top]\n`;
    for (const [node, n] of d.top_write_fail) txt += `  ${n}× ${node}\n`;
    txt += '\n';
  }
  txt += `[日志 (最近 100 条)]\n`;
  const ts = t => new Date(t * 1000).toISOString().slice(11, 23);
  for (const e of d.logs || []) {
    txt += `  [${ts(e.ts)}] ${e.level} ${e.logger}: ${e.msg}\n`;
  }
  navigator.clipboard.writeText(txt).then(() => {
    setStatus('✓ 诊断报告已复制到剪贴板, 可粘贴', 'ok');
  }, err => {
    setStatus('✗ 复制失败: ' + err, 'err');
  });
}
// F1 打开帮助, Esc 关闭, 监听 Ctrl 状态(给 popup 显示连选模式)
document.addEventListener('keydown', (e) => {
  if (e.key === 'F1') { e.preventDefault(); openHelp(); }
  else if (e.key === 'Escape') {
    if (document.getElementById('helpmodal').style.display === 'block') closeHelp();
    if (document.getElementById('bkmodal').style.display === 'block') closeBackups();
    if (document.getElementById('dbgmodal').style.display === 'block') closeDebug();
    if (document.getElementById('snapmodal').style.display === 'block') closeSnapshot();
  }
  // Ctrl/Shift 按下 — popup hdr 切"连选 ON"
  let changed = false;
  if ((e.key === 'Control' || e.ctrlKey) && !_ctrlDown) { _ctrlDown = true; changed = true; }
  if ((e.key === 'Shift' || e.shiftKey) && !_shiftDown) { _shiftDown = true; changed = true; }
  if (changed && acState.active) renderAC();
});
document.addEventListener('keyup', (e) => {
  let changed = false;
  if (e.key === 'Control' && _ctrlDown) { _ctrlDown = false; changed = true; }
  if (e.key === 'Shift' && _shiftDown) { _shiftDown = false; changed = true; }
  if (changed && acState.active) renderAC();
});
// 窗口失焦时也清掉 Ctrl/Shift 状态 (alt+tab 切走防止 stuck)
window.addEventListener('blur', () => {
  if (_ctrlDown || _shiftDown) {
    _ctrlDown = false; _shiftDown = false;
    if (acState.active) renderAC();
  }
});

async function makeBackup() {
  const r = await fetch('/api/script/backup', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reason: 'manual'})});
  const d = await r.json();
  setStatus(d.ok ? `✓ ${d.msg}` : `✗ ${d.error}`, d.ok ? 'ok' : 'err');
}

async function openBackups() {
  document.getElementById('bkmodal').style.display = 'block';
  document.getElementById('bklist').innerHTML = '载入中...';
  const r = await fetch('/api/script/backups');
  const d = await r.json();
  const items = d.items || [];
  if (items.length === 0) {
    document.getElementById('bklist').innerHTML =
      '<div style="padding:20px;color:#999;text-align:center">还没有备份</div>';
    return;
  }
  document.getElementById('bklist').innerHTML =
    `<table style="width:100%;border-collapse:collapse;">` +
    `<thead style="background:#f8f8f8;position:sticky;top:0;">
       <tr style="border-bottom:1px solid #ccc;">
         <th style="text-align:left;padding:4px 10px;">时间</th>
         <th style="text-align:right;padding:4px 10px;">大小</th>
         <th style="text-align:left;padding:4px 10px;">文件名</th>
         <th style="text-align:center;padding:4px 10px;">操作</th>
       </tr>
     </thead><tbody>` +
    items.map(it => {
      const reason = (it.name.match(/script_\d+_\d+_(\w+)\.txt/) || [])[1] || '';
      const tag = reason ? `<span style="background:#eee;padding:0 4px;font-size:10px;color:#555;border-radius:2px;margin-left:6px;">${reason}</span>` : '';
      return `<tr style="border-bottom:1px solid #f0f0f0;">
        <td style="padding:3px 10px;font-family:monospace;">${it.mtime}${tag}</td>
        <td style="padding:3px 10px;text-align:right;color:#888;">${it.size} B</td>
        <td style="padding:3px 10px;font-family:monospace;color:#888;font-size:10px;">${it.name}</td>
        <td style="padding:3px 10px;text-align:center;">
          <button onclick="restoreBackup('${it.name}')"
                  style="font-size:11px;padding:2px 8px;cursor:pointer;">↩ 加载</button>
        </td>
      </tr>`;
    }).join('') +
    `</tbody></table>
     <div style="padding:8px 14px;color:#888;font-size:10px;border-top:1px solid #eee;">
       保留最近 ${d.keep} 个。超出自动清理(从旧的开始删)。
     </div>`;
}

function closeBackups() {
  document.getElementById('bkmodal').style.display = 'none';
}

async function restoreBackup(name) {
  if (!confirm(`把备份 ${name} 加载到编辑器?\n` +
               `当前 editor 内容会先打一个 "before-restore" 备份(不会丢)。\n` +
               `加载后不会自动保存,点【保存】才会写入 config/script.txt。`)) return;
  // 1) 加载前: 把 editor 当前内容先备份(防 textarea.value=X 不进 undo 栈)
  const cur = document.getElementById('editor').value;
  if (cur.trim()) {
    await fetch('/api/script/backup', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: cur, reason: 'before-restore'})});
  }
  // 2) 取目标备份内容
  const r = await fetch('/api/script/backups/' + encodeURIComponent(name));
  const d = await r.json();
  if (d.error) { alert('加载失败: ' + d.error); return; }
  // 3) 灌入 editor
  document.getElementById('editor').value = d.content || '';
  rebuildDescMap(); scheduleHighlight(); updateLineNums();
  closeBackups();
  setStatus(`✓ 已加载 ${name} — 当前 editor 内容已备份, 反悔可在 📚 找 _before-restore`, 'ok');
}

async function genSample() {
  // 关键: 覆盖前先备份当前 script.txt(用户辛苦写的内容不会丢)
  if (document.getElementById('editor').value.trim()) {
    if (!confirm('当前编辑器有内容,自动生成会覆盖。\n点【确定】将先打一个备份后再生成;点【取消】放弃。')) return;
    await fetch('/api/script/backup', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reason: 'before-gen'})});
  }
  const r = await fetch('/api/script/generate', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({only_analog: true})});
  const d = await r.json();
  if (d.ok) {
    document.getElementById('editor').value = d.content;
    rebuildDescMap(); scheduleHighlight(); updateLineNums();
    setStatus('✓ 已生成样本 (上一份已打备份, 见 📚 备份历史)', 'ok');
  } else {
    setStatus('✗ 生成失败', 'err');
  }
}

// 实时值缓存 — pollStatus 拉到后存这里, 筛选器变化时调用 renderVals 重渲染
let _valRows = [];   // [{full, short, dpu, code, desc, val, role}]

async function reloadSymbols() {
  setStatus('刷新点表中...', '');
  try {
    const r = await fetch('/api/script/symbols/reload', {method: 'POST'});
    const d = await r.json();
    symbols = d.items || [];
    rebuildSymbolsDescMap();
    setStatus(`✓ 点表已重载: ${d.count} 个点`, 'ok');
  } catch (e) {
    setStatus(`✗ 刷新失败: ${e}`, 'err');
  }
}

async function syncFromOPC() {
  if (!confirm('从 NTVDPU 实际浏览点表(连接 opc.tcp://localhost:9440)?\nCSV 已有的点保留描述/KKS,只补 OPC 里有但 CSV 没有的新点。')) return;
  setStatus('从 OPC 浏览中... (10-30秒)', 'run');
  try {
    const r = await fetch('/api/script/symbols/from_opc', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})});
    const d = await r.json();
    if (!d.ok) { setStatus(`✗ ${d.error}`, 'err'); return; }
    // 再拉一次 symbols 同步前端 (后端已合并到 _SYMBOLS_CACHE)
    const r2 = await fetch('/api/script/symbols');
    const d2 = await r2.json();
    symbols = d2.items || [];
    rebuildSymbolsDescMap();
    setStatus(`✓ ${d.msg}`, 'ok');
  } catch (e) {
    setStatus(`✗ OPC 浏览失败: ${e}`, 'err');
  }
}

// 停止时筛选框变了要强制重渲一次, 否则界面不响应 (inline onchange/oninput 调 renderValsForced)
let _forceVals = false;
function renderValsForced() { _forceVals = true; renderVals(); }

// 检查用户是否正在该 DOM 区域选中文字 — 是的话, 跳过本次刷新, 不打扰复制
function _hasActiveSelectionIn(el) {
  if (!el) return false;
  const sel = window.getSelection ? window.getSelection() : null;
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return false;
  try {
    const r = sel.getRangeAt(0);
    if (r.toString().length === 0) return false;
    return el.contains(r.startContainer) || el.contains(r.endContainer);
  } catch(e) { return false; }
}

function renderVals() {
  // OPC 循环没在跑 → 数据冻结, 没必要重写 HTML (省 CPU + 不破坏选中)
  // 但筛选条件变了要重渲 (用 _forceVals 标记)
  if (_lastStatus && _lastStatus.running === false && !_forceVals) return;
  _forceVals = false;
  // 用户在面板里有选中文字 → 这一次别重写 HTML, 否则选中会被冲掉无法复制
  const valBox = document.querySelector('.values-panel') || document.getElementById('valTbl')?.parentElement;
  if (_hasActiveSelectionIn(valBox)) return;
  const dpuSel = document.getElementById('fltDpu').value;
  const codeSel = document.getElementById('fltCode').value;
  const roleSel = document.getElementById('fltRole').value;
  const kw = (document.getElementById('fltKw').value || '').toLowerCase().trim();

  function _isDiff(a, b) {
    if (a === null || b === null || a === undefined || b === undefined) return false;
    const norm = x => (x === true ? 1 : x === false ? 0 : x);
    const na = norm(a), nb = norm(b);
    if (typeof na === 'number' && typeof nb === 'number') return Math.abs(na - nb) > 0.01;
    return String(na) !== String(nb);
  }
  // 分组 → 多 code 集合 (筛选 "自定义模拟量" = 任一 SH 段端子类型)
  // 自定义开关量目前为空集; 后端有开关类组态块时往 _SWITCH 数组里加 (如 LOGIC/RS/SR 等)
  const CODE_GROUPS = {
    _ANALOG: ['IN', 'OUT', 'PV'],
    _SWITCH: [],
  };
  const filtered = _valRows.filter(r => {
    if (dpuSel && r.dpu !== dpuSel) return false;
    if (codeSel) {
      const codes = CODE_GROUPS[codeSel];
      if (codes) { if (!codes.includes(r.code)) return false; }
      else if (r.code !== codeSel) return false;
    }
    if (roleSel === 'diff') {
      if (!_isDiff(r.writeVal, r.readVal)) return false;
    } else if (roleSel && r.role !== roleSel) {
      return false;
    }
    if (kw && !(r.desc.toLowerCase().includes(kw) || r.short.toLowerCase().includes(kw))) return false;
    return true;
  });

  const cnt = document.getElementById('valsCount');
  cnt.textContent = filtered.length === _valRows.length
    ? `${filtered.length} 项`
    : `${filtered.length} / ${_valRows.length} 项`;

  const box = document.getElementById('vals');
  if (filtered.length === 0) {
    box.innerHTML = '<div style="color:#999;padding:6px">无匹配</div>';
    return;
  }
  const shown = filtered.slice(0, 200);
  // 统一格式: 布尔(true/false) 和小整数 0/1 一律显示 "0" / "1", 其他数字保留 2 位小数
  function fmtVal(v) {
    if (v === null || v === undefined) return '';
    if (v === true)  return '1';
    if (v === false) return '0';
    if (typeof v === 'number') {
      return Number.isInteger(v) ? String(v) : v.toFixed(2);
    }
    return String(v);
  }
  // 差异判断: 归一到 String 后比 (true=1, false=0, 数字差>0.01 算不同)
  function isDiff(a, b) {
    if (a === null || b === null || a === undefined || b === undefined) return false;
    const norm = x => (x === true ? 1 : x === false ? 0 : x);
    const na = norm(a), nb = norm(b);
    if (typeof na === 'number' && typeof nb === 'number') return Math.abs(na - nb) > 0.01;
    return String(na) !== String(nb);
  }
  box.innerHTML =
    '<table style="width:100%;border-collapse:collapse;font-size:11px;">' +
    '<thead><tr style="background:#f5f5f5;border-bottom:1px solid #ccc;">' +
    '<th style="text-align:left;padding:2px 6px;">tag</th>' +
    '<th style="text-align:right;padding:2px 6px;width:55px;">写入</th>' +
    '<th style="text-align:right;padding:2px 6px;width:55px;">读取</th>' +
    '</tr></thead><tbody>' +
    shown.map(r => {
      const wTxt = fmtVal(r.writeVal);
      const rTxt = fmtVal(r.readVal);
      const diff = isDiff(r.writeVal, r.readVal);
      // 差异行:整行淡红底,提示"写了但实际不一致"
      const rowBg = diff ? 'background:#fef0f0' : '';
      // $var 行加 ✏ 按钮: 改描述 → 后端同步到脚本所有出现位置
      const isVar = r.short.startsWith('$');
      const editBtn = isVar
        ? `<span onclick="editVarDesc('${r.short.replace(/'/g,"\\'")}', '${(r.desc||'').replace(/'/g,"\\'")}')"
                style="cursor:pointer;color:#06a;margin-left:6px;font-size:10px;user-select:none"
                title="✏ 改 ${r.short} 描述 (统一生效到脚本里所有出现位置)">✏</span>`
        : '';
      return `<tr style="border-bottom:1px dotted #eee;${rowBg}">
        <td style="padding:1px 6px;">
          <code style="font-size:10px;color:#048">${r.short}</code>
          ${r.desc ? `<span style="color:#666;font-size:10px;margin-left:4px">${r.desc}</span>` : ''}
          ${editBtn}
        </td>
        <td style="padding:1px 6px;text-align:right;font-weight:bold;color:${wTxt?'#0a0':'#ccc'};">${wTxt || '—'}</td>
        <td style="padding:1px 6px;text-align:right;font-weight:bold;color:${rTxt?'#06a':'#ccc'};">${rTxt || '—'}</td>
      </tr>`;
    }).join('') +
    '</tbody></table>' +
    (filtered.length > 200 ? `<div style="color:#999;padding:4px 6px;font-size:10px">... 还有 ${filtered.length - 200} 项,加筛选缩小范围</div>` : '');
}

async function editVarDesc(varName, currentDesc) {
  const v = prompt(`修改 ${varName} 的描述\n` +
                   `(会同步到脚本里所有出现位置: LHS + 所有 RHS 引用)\n\n` +
                   `禁用字符 ( ) # 换行 会自动替换`,
                   currentDesc || '');
  if (v === null) return;                       // 用户取消
  if (v.trim() === (currentDesc || '').trim()) return;  // 没改
  const r = await fetch('/api/script/var/rename_desc', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({var: varName, new_desc: v.trim()})
  });
  const d = await r.json();
  if (!d.ok) { setStatus('✗ ' + d.error, 'err'); return; }
  // 重新加载脚本到编辑器
  await loadScript();
  rebuildDescMap();
  updateLineNums();
  scheduleHighlight();
  validateScript();
  setStatus(`✏ ${d.msg}`, 'ok');
  // 立刻刷一次实时值面板, 让新描述马上显示
  pollStatus();
}

function refillDpuFilter(dpus) {
  const sel = document.getElementById('fltDpu');
  const cur = sel.value;
  // 已有的选项(除 "全部")的 value 集合
  const exist = new Set(Array.from(sel.options).map(o => o.value));
  for (const d of dpus) {
    if (!exist.has(d)) sel.add(new Option(d, d));
  }
  if (cur) sel.value = cur;
}

async function pollStatus() {
  try {
    const r = await fetch('/api/script/status');
    const s = await r.json();
    _lastStatus = s;   // 缓存给悬停 tooltip 用 (零接口/零延迟)
    let html;
    if (s.running) {
      const dot = s.last_error ? '<span class="dot err"></span>' : '<span class="dot run"></span>';
      // 负荷颜色 (近 20 周期平均)
      const lp = s.load_pct;
      let lcolor = '#060';  // 绿
      if (lp > 80) lcolor = '#c00';
      else if (lp > 50) lcolor = '#c80';
      // 负荷进度条
      const barWidth = Math.min(100, lp);
      const bar = `<span style="display:inline-block;vertical-align:middle;width:80px;height:8px;background:#eee;border:1px solid #aaa;margin:0 4px;">` +
                  `<span style="display:block;height:100%;width:${barWidth}%;background:${lcolor};"></span></span>`;
      // 健康度 badge: 静默跳过 / 写后未生效 / 写失败 / 读失败
      const warns = [];
      if (s.skipped_pairs > 0)
        warns.push(`<span onclick="openDebug()" style="cursor:pointer;background:#a0c;color:#fff;padding:1px 6px;border-radius:2px;font-weight:bold;">⛔ ${s.skipped_pairs} 对跳过 (源读不到)</span>`);
      if (s.write_ineffective > 0)
        warns.push(`<span onclick="openDebug()" style="cursor:pointer;background:#c00;color:#fff;padding:1px 6px;border-radius:2px;font-weight:bold;">⚠ 写后未生效 ${s.write_ineffective}</span>`);
      if (s.write_fail_nodes > 0)
        warns.push(`<span onclick="openDebug()" style="cursor:pointer;background:#c60;color:#fff;padding:1px 6px;border-radius:2px;" title="累计 ${s.write_fail_total} 次">写失败 ${s.write_fail_nodes} 节点</span>`);
      if (s.read_fail_nodes > 0)
        warns.push(`<span onclick="openDebug()" style="cursor:pointer;background:#888;color:#fff;padding:1px 6px;border-radius:2px;" title="累计 ${s.read_fail_total} 次">读失败 ${s.read_fail_nodes} 节点</span>`);
      const warnHtml = warns.length ? '  ' + warns.join(' ') : '';
      html = `${dot} <span class="run">运行中</span>  ` +
             `周期 <b>${s.cycle_count}</b> · 读 <b>${s.read_count}</b> · 写 <b>${s.write_count}</b>  ` +
             `已运行 ${s.uptime_s.toFixed(1)}s · ${s.pairs_count} 对${warnHtml}` +
             `\n通讯负荷 ${bar}<b style="color:${lcolor}">${lp.toFixed(1)}%</b>` +
             `  (周期实际 <b>${s.avg_cycle_ms}</b>ms / 设定 ${s.dt_ms}ms · 读 ${s.avg_read_ms}ms · 写 ${s.avg_write_ms}ms)`;
      if (s.last_error) html += `\n<span class="err">最近错: ${s.last_error}</span>`;
    } else {
      const cls = s.last_error ? 'err' : '';
      html = `<span class="dot"></span> 未运行` +
             (s.cycle_count > 0 ? ` (上次跑了 ${s.cycle_count} 周期, 末次负荷 ${s.load_pct}%)` : '');
      if (s.last_error) html += `\n<span class="err">最近错: ${s.last_error}</span>`;
    }
    // 错误信息 sticky 期间不覆盖 status 区域 (其它 panel 照常更新)
    // 用户正在 status 里选中文字, 也别重写, 否则复制不动
    const statusEl = document.getElementById('status');
    if (Date.now() >= _stickyErrUntil && !_hasActiveSelectionIn(statusEl)) {
      statusEl.innerHTML = html;
    }
    // 实时值 — 合并 last_values(写, LHS) + last_read(读, RHS 源)
    const written = s.last_values || {};
    const readVals = s.last_read || {};
    const rows = [];
    const allKeys = new Set([...Object.keys(written), ...Object.keys(readVals)]);
    const dpus = new Set();
    for (const k of allKeys) {
      // 解析三种节点:
      //   1) $tmp_var                              — 中间变量
      //   2) ns=0;s=DPU3013.HW.AI010502.PV         — HW 硬件单段
      //   3) ns=0;s=DPU3013.SH0500.PRO21120.IN     — SH 组态段
      let dpu, code, shortLabel, m;
      if (k.startsWith('$')) {
        dpu = '$中间'; code = 'VAR'; shortLabel = k;
      } else if ((m = k.match(/^ns=0;s=([A-Z]+\d+)\.HW\.([A-Z]+)(\d+)\.PV$/))) {
        dpu = m[1]; code = m[2]; shortLabel = `${dpu}.${m[2]}${m[3]}`;
      } else if ((m = k.match(/^ns=0;s=([A-Z]+\d+)\.(SH\d+\.[A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)$/))) {
        // 块名允许纯字母 (PROYR / AALMZML) / 带下划线, 跟后端 app.py:1089 同步放宽
        dpu = m[1]; code = m[3]; shortLabel = `${dpu}.${m[2]}.${m[3]}`;
      } else {
        continue;
      }
      dpus.add(dpu);
      const writeVal = (k in written) ? written[k] : null;
      const readVal  = (k in readVals) ? readVals[k] : null;
      // role: 同时有写读 = "对比",仅写 = "写",仅读 = "读"
      const role = (writeVal !== null && readVal !== null) ? '对比'
                 : (writeVal !== null) ? '写' : '读';
      rows.push({
        full: k, short: shortLabel, dpu, code,
        desc: tagDescMap[k] || symbolsDescMap[k] || '',
        writeVal, readVal, role,
      });
    }
    rows.sort((a, b) => a.short.localeCompare(b.short));
    _valRows = rows;
    refillDpuFilter([...dpus].sort());

    if (rows.length === 0) {
      document.getElementById('vals').innerHTML =
        `<div style="color:#999;padding:6px">${s.running ? '等待第一次读/写...' : '未运行'}</div>`;
      document.getElementById('valsCount').textContent = '';
    } else {
      renderVals();
    }
  } catch(e) {}
}

// ===== 自动补全 =====
let symbols = [];
let acState = { active: false, items: [], sel: 0, anchor: 0, page: 0, pageSize: 12 };
let _ctrlDown = false, _shiftDown = false;

// 从脚本里扫所有 $xxx 中间变量,作为补全符号(去重)
function extractIntermediates(text) {
  const seen = new Set();
  const re = /\$[A-Za-z_]\w*/g;
  let m;
  while ((m = re.exec(text || '')) !== null) seen.add(m[0]);
  return [...seen].map(name => ({
    label: name,
    desc: '(中间变量)',
    kks: '',
    code: 'VAR',
  }));
}

async function loadSymbols() {
  try {
    const r = await fetch('/api/script/symbols');
    const d = await r.json();
    symbols = d.items || [];
    rebuildSymbolsDescMap();
  } catch(e) { console.warn('symbols 加载失败', e); }
}

function escHtml(s) {
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function hl(s, kw) {
  if (!kw) return escHtml(s);
  const lo = s.toLowerCase(), kwlo = kw.toLowerCase();
  const i = lo.indexOf(kwlo);
  if (i < 0) return escHtml(s);
  return escHtml(s.slice(0,i)) + '<mark>' + escHtml(s.slice(i, i+kw.length)) + '</mark>' + escHtml(s.slice(i+kw.length));
}

function hideAC() {
  document.getElementById('acpopup').style.display = 'none';
  acState.active = false;
}

// 模糊匹配: 优先级 includes > 空格分 token (全包含) > 子序列(按字符顺序出现)
// 返回排序后的 matches (最多 50)
function fuzzyMatch(symbols, kw) {
  const k = kw.toLowerCase();
  const tokens = k.split(/[\s]+/).filter(Boolean);

  // 子序列匹配: pattern 的每个字符在 text 中按顺序出现
  function subseq(text, pat) {
    let i = 0;
    for (let j = 0; j < text.length && i < pat.length; j++) {
      if (text[j] === pat[i]) i++;
    }
    return i === pat.length;
  }

  // 所有字符都出现(无序兜底,容忍动词位置不一致)
  function allCharsIn(text, pat) {
    for (const c of pat) { if (!text.includes(c)) return false; }
    return true;
  }

  // 单字段评分
  function fieldScore(text, kwlo) {
    if (!text) return 0;
    const t = text.toLowerCase();
    if (t === kwlo) return 200;
    if (t.startsWith(kwlo)) return 150;
    if (t.includes(kwlo)) return 100;
    // 空格 token: 都包含算 70
    if (tokens.length > 1 && tokens.every(tk => t.includes(tk))) return 70;
    // 子序列(顺序): 50 — 适合"A一次风机跳闸"这种描述
    if (subseq(t, kwlo)) return 50;
    // 兜底: 所有字符都出现(无序) — 适合"停A给煤机"这种动词前置
    if (allCharsIn(t, kwlo)) return 25;
    return 0;
  }

  const scored = [];
  for (const s of symbols) {
    const sLab = fieldScore(s.label, k);
    const sDsc = fieldScore(s.desc || '', k);
    const sKks = fieldScore(s.kks || '', k);
    // 综合: 取最高 + 同时多字段命中加分
    let sc = Math.max(sLab, sDsc * 1.0, sKks * 0.6);
    if (sDsc > 0 && sLab > 0) sc += 10;  // 描述+短码都命中, 加分
    // 短描述加权 (越短越精确)
    if (sDsc >= 100 && s.desc && s.desc.length <= 12) sc += 15;
    if (sc > 0) scored.push([sc, s]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, 300).map(x => x[1]);   // 最多 300, 配合分页
}

function updateAC() {
  const ed = document.getElementById('editor');
  const pos = ed.selectionStart;
  const before = ed.value.slice(0, pos);
  // 触发: @后面跟非空白/非括号/非等号(可含中文/英文/数字)
  const m = before.match(/@([^\s@()=]*)$/);
  if (!m) { hideAC(); return; }
  const kw = m[1];
  // 把脚本里出现的中间变量加入符号池
  const varSyms = extractIntermediates(ed.value);
  const pool = symbols.concat(varSyms);
  let matches;
  if (kw.length === 0) {
    matches = pool.slice(0, 30);
  } else {
    matches = fuzzyMatch(pool, kw);
  }
  if (matches.length === 0) { hideAC(); return; }
  acState.items = matches;
  acState.sel = 0;
  acState.page = 0;
  acState.anchor = pos - m[0].length;  // @ 的位置
  acState.kw = kw;
  renderAC();
  positionAC();
}

function renderAC() {
  const pop = document.getElementById('acpopup');
  const kw = acState.kw || '';
  const total = acState.items.length;
  const ps = acState.pageSize;
  const totalPages = Math.max(1, Math.ceil(total / ps));
  // sel 同步到对应页
  acState.page = Math.min(Math.max(0, Math.floor(acState.sel / ps)), totalPages - 1);
  const startIdx = acState.page * ps;
  const endIdx = Math.min(startIdx + ps, total);
  const pageItems = acState.items.slice(startIdx, endIdx);

  const modeTag = (_ctrlDown || _shiftDown)
    ? `<span style="background:#0a0;color:#fff;padding:1px 6px;margin-right:4px;border-radius:2px">连选 ON</span>`
    : `<span style="color:#888">单选模式</span>`;
  const hdrBg = (_ctrlDown || _shiftDown) ? 'background:#063' : 'background:#111';
  const hdr = `<div class="ac-hdr" style="${hdrBg}">
    ${modeTag} 共 ${total} 项 · 第 ${acState.page+1}/${totalPages} 页 ·
    Ctrl/Shift + Enter / 点击 = 连选(<b>保留搜索词</b>, 改词请直接编辑后缀)
    <span style="float:right">
      <button type="button" class="ac-pgbtn" data-act="prev" ${acState.page<=0?'disabled':''}>◀</button>
      <button type="button" class="ac-pgbtn" data-act="next" ${acState.page>=totalPages-1?'disabled':''}>▶</button>
    </span>
  </div>`;

  const rows = pageItems.map((it, j) => {
    const globalIdx = startIdx + j;
    return `<div class="ac-item ${globalIdx===acState.sel?'sel':''}" data-i="${globalIdx}">
      <span class="ac-label">${hl(it.label, kw)}</span>
      <span class="ac-code">${it.code}</span>
      <span class="ac-desc">${hl(it.desc||'(无描述)', kw)}</span>
      <span class="ac-kks">${hl(it.kks||'', kw)}</span>
    </div>`;
  }).join('');

  pop.innerHTML = hdr + rows;
  pop.style.display = 'block';
  acState.active = true;

  pop.querySelectorAll('.ac-item').forEach(el => {
    el.onmousedown = (e) => { e.preventDefault();
      acState.sel = +el.dataset.i;
      // Ctrl / Shift + 左键 = 连选(同 Ctrl/Shift + Enter)
      insertAC({continue: e.ctrlKey || e.shiftKey});
    };
    el.onmouseenter = () => {
      acState.sel = +el.dataset.i;
      pop.querySelectorAll('.ac-item').forEach(e2 =>
        e2.classList.toggle('sel', +e2.dataset.i === acState.sel));
    };
  });
  pop.querySelectorAll('.ac-pgbtn').forEach(b => {
    b.onmousedown = (e) => { e.preventDefault();
      const act = b.dataset.act;
      if (act === 'prev' && acState.page > 0) {
        acState.page--; acState.sel = acState.page * ps;
      } else if (act === 'next' && acState.page < totalPages - 1) {
        acState.page++; acState.sel = acState.page * ps;
      }
      renderAC();
    };
  });
  // 滚动当前选中可见(同页内)
  const sel = pop.querySelector('.ac-item.sel');
  if (sel) sel.scrollIntoView({block: 'nearest'});
}

function positionAC() {
  // 简单粗暴: textarea 左上角偏移 (无法精确算光标像素位置, 但够用)
  const ed = document.getElementById('editor');
  const rect = ed.getBoundingClientRect();
  const pop = document.getElementById('acpopup');
  pop.style.left = (rect.left + 40) + 'px';
  pop.style.top  = (rect.top + 80) + 'px';
}

function insertAC(opts) {
  opts = opts || {};
  if (!acState.active || !acState.items.length) return;
  const it = acState.items[acState.sel];
  const ed = document.getElementById('editor');
  ed.focus();
  const pos = ed.selectionStart;
  // 关键: 每次根据当前 textarea 重新算 anchor — 不信任过期的 acState.anchor
  // (debounce 60ms 内连续输字会让缓存的 anchor 过期, 导致替换错位)
  const beforeStr = ed.value.slice(0, pos);
  const m = beforeStr.match(/@([^\s@()=]*)$/);
  if (!m) { hideAC(); return; }   // @kw 不在光标前, 没法插入
  const anchor = pos - m[0].length;

  const before = ed.value.slice(0, anchor);
  const after = ed.value.slice(pos);
  const inserted = it.desc ? `${it.label}(${it.desc})` : it.label;
  // 连选时保留上次关键字, 光标落在 kw 末尾
  // 用户手动删除/改写后才切换搜索词
  const kw = m[1] || '';
  const tail = opts.continue ? `, @${kw}` : '';
  ed.value = before + inserted + tail + after;
  const np = before.length + inserted.length + tail.length;
  ed.setSelectionRange(np, np);
  if (opts.continue) {
    updateAC();   // updateAC 内部根据 ed.selectionStart 自动重算 anchor
  } else {
    hideAC();
  }
  updateLineNums();
  scheduleHighlight();
}

// ===== 悬停看变量实时值 =====
// 设计:
//   - 数据: 1Hz pollStatus 已经把 last_values/last_read 拉到前端 → 悬停时纯 Map 查
//   - 探测: 高亮层里每个 token (hl-var/hl-tag) 都有 data-token 属性, mousemove
//           时临时关 textarea 的 pointer-events, 用 elementFromPoint 直接拿那个
//           span. 浏览器替我处理换行 / CJK / tab / 滚动 / padding, 永远不会偏.
let _lastStatus = null;
let _hoverTip = null;
let _hoverTimer = null;

function _findTokenAt(ed, e) {
  // 临时屏蔽 textarea, 让 hl-layer 里有 pointer-events:auto 的 [data-token] span 露出来
  const orig = ed.style.pointerEvents;
  ed.style.pointerEvents = 'none';
  let target;
  try {
    target = document.elementFromPoint(e.clientX, e.clientY);
  } finally {
    ed.style.pointerEvents = orig;
  }
  if (target && target.dataset && target.dataset.token) return target.dataset.token;
  return null;
}

// 短码 → OPC 完整节点 (跟 backend short_to_full 对齐)
function _shortToFull(short) {
  if (short.startsWith('$')) return short;
  const parts = short.split('.');
  if (parts.length === 2) return `ns=0;s=${parts[0]}.HW.${parts[1]}.PV`;  // HW 单段
  return `ns=0;s=${short}`;  // SH 多段
}

function _formatVal(v) {
  if (v === null || v === undefined) return '<i style="color:#888">未读到</i>';
  if (typeof v === 'boolean') return v ? '<b style="color:#0a0">TRUE</b>' : '<b style="color:#c33">FALSE</b>';
  if (typeof v === 'number') return `<b style="color:#06a">${v.toFixed(3)}</b>`;
  return String(v);
}

function _lookupValue(token) {
  if (!_lastStatus) return { found: false, html: '<i style="color:#888">尚未运行 / 无数据</i>' };
  const lv = _lastStatus.last_values || {};
  const lr = _lastStatus.last_read   || {};
  if (token.startsWith('$')) {
    // $var 的值实际上是写到 last_values 里 (key 就是 $name), 不在 intermediates
    if (token in lv) return { found: true,
        html: `${_formatVal(lv[token])} <span style="color:#888">(中间变量)</span>` };
    return { found: false,
        html: '<i style="color:#888">未求值 (本周期上游断了 / 还没赋值)</i>' };
  }
  const full = _shortToFull(token);
  const written = lv[full];
  const read    = lr[full];
  const parts = [];
  if (written !== undefined) parts.push(`写: ${_formatVal(written)}`);
  if (read    !== undefined) parts.push(`读: ${_formatVal(read)}`);
  if (parts.length === 0) return { found: false,
      html: '<i style="color:#888">该点未在脚本中, 或本周期未读/写</i>' };
  return { found: true, html: parts.join(' &nbsp; ') };
}

function _showTip(e, html, token) {
  if (!_hoverTip) {
    _hoverTip = document.createElement('div');
    _hoverTip.id = 'varTip';
    _hoverTip.style.cssText = 'position:fixed;z-index:9999;background:#222;color:#fff;' +
      'padding:6px 10px;font-family:"Consolas",monospace;font-size:11px;border-radius:3px;' +
      'box-shadow:0 2px 8px rgba(0,0,0,0.4);pointer-events:none;max-width:480px;' +
      'line-height:1.5;white-space:nowrap';
    document.body.appendChild(_hoverTip);
  }
  _hoverTip.innerHTML = `<span style="color:#fc6">${token}</span> = ${html}`;
  _hoverTip.style.display = 'block';
  // 放鼠标右下 16px, 避免遮住光标
  let x = e.clientX + 16, y = e.clientY + 16;
  // 防止跑出右边/下边
  const rect = _hoverTip.getBoundingClientRect();
  if (x + rect.width  > window.innerWidth)  x = window.innerWidth  - rect.width  - 8;
  if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 8;
  _hoverTip.style.left = x + 'px';
  _hoverTip.style.top  = y + 'px';
}
function _hideTip() {
  if (_hoverTip) _hoverTip.style.display = 'none';
}

function _bindHover() {
  const ed = document.getElementById('editor');
  if (!ed) return;
  // 缓存最后一次的鼠标坐标 + token, 鼠标几乎没动就不重查
  let _lastX = -1, _lastY = -1, _lastToken = null;
  ed.addEventListener('mousemove', (e) => {
    // 鼠标在同一格内反复触发, 不重做
    if (Math.abs(e.clientX - _lastX) < 3 && Math.abs(e.clientY - _lastY) < 3) return;
    _lastX = e.clientX; _lastY = e.clientY;
    if (_hoverTimer) clearTimeout(_hoverTimer);
    _hoverTimer = setTimeout(() => {
      _hoverTimer = null;
      const token = _findTokenAt(ed, e);
      if (!token) {
        _lastToken = null;
        _hideTip();
        return;
      }
      // 同一 token 反复悬停, 已显示就别重渲
      if (token === _lastToken && _hoverTip && _hoverTip.style.display === 'block') {
        _showTip(e, _lookupValue(token).html, token);   // 只更新位置 + 值
        return;
      }
      _lastToken = token;
      const { html } = _lookupValue(token);
      _showTip(e, html, token);
    }, 150);
  });
  ed.addEventListener('mouseleave', () => {
    if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null; }
    _lastToken = null;
    _hideTip();
  });
  ed.addEventListener('scroll', () => { _lastToken = null; _hideTip(); }, { passive: true });
  ed.addEventListener('keydown', () => { _lastToken = null; _hideTip(); });
}

(function bindAC() {
  const ed = document.getElementById('editor');
  // 中文输入法 (IME) composition 期间, input 事件值是拼音,不要 fuzzy
  let composing = false;
  ed.addEventListener('compositionstart', () => { composing = true; });
  ed.addEventListener('compositionend',   () => { composing = false; scheduleAC(); scheduleLineNums(); scheduleHighlight(); markValidateStale(); });
  ed.addEventListener('input',  (e) => {
    if (composing) return;
    scheduleAC();          // fuzzy 50ms 后
    scheduleLineNums();    // 行号+光标 80ms 后
    scheduleHighlight();   // 高亮 80ms 后
    markValidateStale();   // 校验徽章变灰 "未校验 - 保存后看", 不发后端
    // 不再 scheduleValidate() — 整脚本逐行 parse + HTTP 在大脚本上明显卡顿,
    // 改为 [保存] / [运行] 时统一校验 (用户主动触发, 类似编译)
  });
  // scroll 事件高频, 只同步行号 + 高亮层 scrollTop, 不做任何重算
  ed.addEventListener('scroll', () => {
    syncLineNumsScroll();
    const hl = document.getElementById('hl');
    if (hl) { hl.scrollTop = ed.scrollTop; hl.scrollLeft = ed.scrollLeft; }
  }, { passive: true });
  ed.addEventListener('click',  () => { setTimeout(updateAC, 0); updateCursorPos(); });
  ed.addEventListener('keyup',  (e) => {
    // 方向键/Home/End/PgUp/PgDn 改变光标 — 只在补全未激活时更新
    if (!acState.active && ['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(e.key)) {
      updateCursorPos();
    }
  });
  // Ctrl+G 跳行 / Ctrl+/ 注释切换 / Tab 缩进
  ed.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === 'g') { e.preventDefault(); gotoLine(); return; }
    if (e.ctrlKey && e.key === '/') { e.preventDefault(); toggleComment(); return; }
    if (e.key === 'Tab' && !e.shiftKey && !acState.active) {
      e.preventDefault();
      const s = ed.selectionStart, en = ed.selectionEnd;
      // 单光标插 2 空格;选区跨行整体缩进
      if (s === en && ed.value.slice(s).indexOf('\n') !== ed.value.length - s) {
        const block = ed.value.slice(s, en);
        if (!block.includes('\n')) {
          ed.value = ed.value.slice(0, s) + '  ' + ed.value.slice(en);
          ed.setSelectionRange(s + 2, s + 2);
          updateLineNums();
          return;
        }
      }
      indentSel(false);
      return;
    }
    if (e.key === 'Tab' && e.shiftKey && !acState.active) {
      e.preventDefault();
      indentSel(true);   // shift+Tab 反缩进
    }
  });
  ed.addEventListener('keydown', (e) => {
    // IME 选字期间所有方向键/Enter 都让输入法处理
    if (e.isComposing || e.keyCode === 229 || composing) return;
    if (!acState.active) return;
    const N = acState.items.length, ps = acState.pageSize;
    if (e.key === 'ArrowDown') { e.preventDefault();
      acState.sel = (acState.sel + 1) % N; renderAC(); }
    else if (e.key === 'ArrowUp') { e.preventDefault();
      acState.sel = (acState.sel - 1 + N) % N; renderAC(); }
    else if (e.key === 'PageDown') { e.preventDefault();
      acState.sel = Math.min(N - 1, acState.sel + ps); renderAC(); }
    else if (e.key === 'PageUp') { e.preventDefault();
      acState.sel = Math.max(0, acState.sel - ps); renderAC(); }
    else if (e.key === 'Home' && e.ctrlKey) { e.preventDefault();
      acState.sel = 0; renderAC(); }
    else if (e.key === 'End' && e.ctrlKey) { e.preventDefault();
      acState.sel = N - 1; renderAC(); }
    else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      // 连选: Ctrl 或 Shift 修饰键
      // 注意: Ctrl+Tab 会被浏览器拦截(切标签页), 用 Ctrl+Enter / Shift+Enter / Shift+Tab 代替
      insertAC({continue: e.ctrlKey || e.shiftKey});
    }
    else if (e.key === 'Escape') { e.preventDefault(); hideAC(); }
  });
  ed.addEventListener('blur', () => setTimeout(() => {
    // 失焦后 150ms 检查: 如果焦点又回到 textarea (连选场景), 不关 popup
    if (document.activeElement === ed) return;
    hideAC();
  }, 150));
})();

function _gotoLine(lineNo) {
  const ed = document.getElementById('editor');
  const lines = (ed.value || '').split('\n');
  let off = 0;
  for (let i = 0; i < lineNo - 1; i++) off += lines[i].length + 1;
  ed.focus();
  ed.setSelectionRange(off, off + (lines[lineNo - 1] || '').length);
}
function gotoFirstErr() {
  if (!_errLines.size) return;
  _gotoLine(Math.min(...[..._errLines.keys()]));
}
function gotoFirstWarn() {
  if (!_warnLines.size) return;
  _gotoLine(Math.min(...[..._warnLines.keys()]));
}

loadScript().then(() => { updateLineNums(); runHighlight(); validateScript(); });
loadSymbols();
refreshEndpoint();
refreshProbe();
_bindHover();
setInterval(pollStatus, 1000);
pollStatus();
</script>
</body>
</html>
"""


EDIT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>组态编辑器 - DCS 仿真</title>
<style>
* { box-sizing: border-box; }
body { font-family: "Consolas", "Microsoft YaHei", monospace; font-size: 12px;
       margin: 0; padding: 0; background: #fff; color: #222; }
header { background: #111; color: #eee; padding: 6px 12px; display: flex;
         justify-content: space-between; align-items: center; }
header h1 { font-size: 13px; margin: 0; font-weight: normal; }
header a { color: #8cf; text-decoration: none; }
header a:hover { text-decoration: underline; }
.toolbar { background: #f2f2f2; padding: 8px 12px; border-bottom: 1px solid #ccc;
           display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar select, .toolbar button {
    font-family: inherit; font-size: 12px; padding: 4px 10px;
    border: 1px solid #888; background: #fff; cursor: pointer;
}
.toolbar button.primary { background: #111; color: #fff; border-color: #111; }
.toolbar button.primary:hover { background: #333; }
.toolbar button:hover { background: #eee; }
.toolbar button.danger { background: #c33; color: #fff; border-color: #c33; }
.toolbar button.danger:hover { background: #a00; }
.toolbar .info { color: #666; margin-left: auto; }
#status { padding: 6px 12px; min-height: 24px; font-size: 11px; }
#status.ok { background: #efe; color: #060; border-left: 3px solid #060; }
#status.err { background: #fee; color: #900; border-left: 3px solid #900;
              white-space: pre-wrap; }
#status.warn { background: #ffd; color: #960; border-left: 3px solid #960; }
#editor { width: 100%; border: 0; outline: none; padding: 10px 12px;
          font-family: "Consolas", monospace; font-size: 12px;
          line-height: 1.4; resize: none; tab-size: 2; }
.hint { background: #fffce0; color: #666; padding: 6px 12px;
        border-bottom: 1px solid #eec; font-size: 11px; }
.hint code { background: #fff; padding: 0 4px; border: 1px solid #ddd; }
</style>
</head>
<body>
<header>
  <h1>🛠️ 组态编辑器</h1>
  <span><a href="/">← 返回看板</a></span>
</header>
<div class="hint">
  <b>说明</b>: 保存按钮会自动 ① YAML 语法校验 ② 备份当前 → <code>.bak</code> ③ 写盘 ④ 跑 GraphRunner/TagMap 加载校验 ⑤ 失败自动回滚。
  改完后,跑 <code>py -3.12 -m src.cli run --online --duration 30 --models config/&lt;file&gt; ...</code> 应用。
</div>
<div class="toolbar">
  <label>文件:
    <select id="file" onchange="load()">
      {% for f in files %}<option value="{{f}}">{{f}}</option>{% endfor %}
    </select>
  </label>
  <button onclick="load()">⟳ 重新加载</button>
  <button class="primary" onclick="save()">💾 保存 (自动校验)</button>
  <button class="danger" onclick="rollback()">↶ 从 .bak 回滚</button>
  <span class="info" id="meta">--</span>
</div>
<div id="status">就绪</div>
<textarea id="editor" spellcheck="false" placeholder="(选择文件后加载)"
          style="height: calc(100vh - 200px);"></textarea>

<script>
function setStatus(msg, kind) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = kind || '';
}

async function load() {
  const f = document.getElementById('file').value;
  setStatus('加载中: ' + f, '');
  const r = await fetch('/api/yaml/' + encodeURIComponent(f));
  const d = await r.json();
  if (d.error) { setStatus('错: ' + d.error, 'err'); return; }
  document.getElementById('editor').value = d.content || '';
  document.getElementById('meta').textContent =
    d.exists ? `${d.size} 字节` : '(文件不存在,保存即创建)';
  setStatus(d.exists ? `已加载 ${f}` : `${f} 不存在,可新建`, 'ok');
}

async function save() {
  const f = document.getElementById('file').value;
  const content = document.getElementById('editor').value;
  setStatus('保存中...', '');
  const r = await fetch('/api/yaml/' + encodeURIComponent(f), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content}),
  });
  const d = await r.json();
  if (d.ok) {
    setStatus(`✓ ${d.msg}` + (d.backup ? `\n备份: config/${d.backup}` : ''), 'ok');
    document.getElementById('meta').textContent = content.length + ' 字节';
  } else {
    const stage = d.stage === 'syntax' ? '语法错' : d.stage === 'deep_load' ? '加载校验失败' : '错';
    setStatus(`✗ [${stage}] ${d.error}` + (d.rollback ? `\n→ ${d.rollback}` : ''), 'err');
  }
}

async function rollback() {
  const f = document.getElementById('file').value;
  if (!confirm(`确认从 ${f}.bak 恢复? 当前编辑器内容若未保存会丢失`)) return;
  const r = await fetch('/api/yaml/' + encodeURIComponent(f) + '/rollback', {method: 'POST'});
  const d = await r.json();
  if (d.ok) {
    setStatus('✓ ' + d.msg + ' (重新加载中)', 'ok');
    setTimeout(load, 300);
  } else {
    setStatus('✗ ' + d.error, 'err');
  }
}

load();
</script>
</body>
</html>
"""


def configure(models: Optional[str] = None,
              connections: Optional[str] = None,
              tagmap: Optional[str] = None,
              csv: Optional[str] = None) -> None:
    if models:
        CONFIG["models"] = models
    if connections:
        CONFIG["connections"] = connections
    if tagmap:
        CONFIG["tagmap"] = tagmap
    if csv:
        CONFIG["csv"] = csv


def run(host: str = "127.0.0.1", port: int = 5002, debug: bool = False) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"DCS 仿真组态查看器 — http://{host}:{port}")
    print(f"  models:      {CONFIG['models']}")
    print(f"  connections: {CONFIG['connections']}")
    print(f"  tagmap:      {CONFIG['tagmap']}")
    print(f"  csv:         {CONFIG['csv']}")
    print(f"  (Ctrl-C 退出)")
    # 启动后台 OPC 探活线程 — UI 顶栏的 🟢/🔴 状态点用它
    rt.start_probe_thread()
    app.run(host=host, port=port, debug=debug, use_reloader=False)
