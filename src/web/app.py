# -*- coding: utf-8 -*-
"""
LeDCSsim Web 界面

提供信号配置、模型组态、仿真运行的可视化操作界面。

启动:
    py -3.12 -m src.web.app
"""
import asyncio
import json
import logging
import threading
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
MAPPING_FILE = CONFIG_DIR / "opc_mapping.yaml"
VARIABLES_FILE = CONFIG_DIR / "variables.yaml"
MODEL_DIR = CONFIG_DIR / "models"
MANIFEST_FILE = MODEL_DIR / "_manifest.json"

app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))
app.config["JSON_AS_ASCII"] = False


# ══════════════════════════════════════════════════════════
#  页面清单 (Manifest) 管理
# ══════════════════════════════════════════════════════════

def _load_manifest():
    """加载清单，若不存在则自动从现有文件生成"""
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return _auto_generate_manifest()


def _save_manifest(data):
    """保存清单"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _auto_generate_manifest():
    """扫描 MODEL_DIR/*.json，排除 _manifest.json，生成初始清单"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    pages = []
    for f in sorted(MODEL_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        name = f.stem
        if name.startswith("IL_"):
            layer = "IL"
        else:
            layer = "IB"
        pages.append({"id": name, "layer": layer, "name": name, "order": len(pages)})
    manifest = {"pages": pages}
    _save_manifest(manifest)
    return manifest


def _get_sidebar_tree():
    """为侧边栏构建页面树"""
    manifest = _load_manifest()
    tree = {"IL": [], "IB": []}
    for p in manifest.get("pages", []):
        layer = p.get("layer", "IB")
        if layer in tree:
            tree[layer].append(p)
    # 按 order 排序
    for layer in tree:
        tree[layer].sort(key=lambda p: p.get("order", 0))
    return tree


@app.context_processor
def inject_sidebar():
    """为所有模板注入侧边栏数据"""
    return {"sidebar_tree": _get_sidebar_tree()}

# ── 全局状态 ──────────────────────────────────────────────
_sim_state = {
    "running": False,
    "mode": "offline",
    "engine": None,
    "thread": None,
    "error": None,
}


# ══════════════════════════════════════════════════════════
#  页面路由
# ══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/io")
def io_page():
    return render_template("io.html")


def _redirect_to_first_page(layer: str, default_id: str, default_name: str):
    """层入口：重定向到该层第一个页面，无页面则新建默认页"""
    tree = _get_sidebar_tree()
    if tree[layer]:
        return redirect(f"/canvas/{layer}/{tree[layer][0]['id']}")
    manifest = _load_manifest()
    page = {"id": default_id, "layer": layer, "name": default_name, "order": 0}
    manifest["pages"].append(page)
    _save_manifest(manifest)
    filepath = MODEL_DIR / f"{default_id}.json"
    if not filepath.exists():
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"name": default_id, "layer": layer, "drawflow": {}}, f, ensure_ascii=False, indent=2)
    return redirect(f"/canvas/{layer}/{default_id}")


@app.route("/il")
def il_page():
    return _redirect_to_first_page("IL", "IL_preprocess", "信号预处理")


@app.route("/ib")
def ib_page():
    return _redirect_to_first_page("IB", "CCS_model", "CCS协调控制")


@app.route("/canvas/<layer>/<page_id>")
def canvas_page(layer, page_id):
    """统一画布页面"""
    layer = layer.upper()
    if layer not in ("IL", "IB"):
        return "无效的层: " + layer, 404
    # 从 manifest 获取 page_name
    manifest = _load_manifest()
    page_name = page_id
    for p in manifest.get("pages", []):
        if p["id"] == page_id:
            page_name = p.get("name", page_id)
            break
    return render_template("canvas.html", layer=layer, page_id=page_id, page_name=page_name)



@app.route("/run")
def run_page():
    return render_template("run.html")


@app.route("/variables")
def variables_page():
    return render_template("variables.html")


@app.route("/trend")
def trend_page():
    return render_template("trend.html")


# ══════════════════════════════════════════════════════════
#  变量管理 API
# ══════════════════════════════════════════════════════════

def _load_variables():
    """加载变量表"""
    import yaml
    if not VARIABLES_FILE.exists():
        return []
    with open(VARIABLES_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("variables", [])


def _save_variables(variables):
    """保存变量表"""
    import yaml
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(VARIABLES_FILE, "w", encoding="utf-8") as f:
        yaml.dump({"variables": variables}, f, allow_unicode=True,
                  default_flow_style=False, sort_keys=False)


@app.route("/api/variables", methods=["GET"])
def api_get_variables():
    """获取全部变量"""
    variables = _load_variables()
    # 附加运行时值
    engine = _sim_state.get("engine")
    if engine and engine.recorder.count > 0:
        latest = engine.recorder.get_latest() or {}
        for v in variables:
            tag = v.get("tag", "")
            if tag in latest:
                v["current_value"] = latest[tag]
    return jsonify({"variables": variables})


@app.route("/api/variables", methods=["POST"])
def api_add_variable():
    """添加变量"""
    variables = _load_variables()
    var = request.json
    tag = var.get("tag", "").strip()
    if not tag:
        return jsonify({"error": "tag 不能为空"}), 400
    for v in variables:
        if v["tag"] == tag:
            return jsonify({"error": f"变量 '{tag}' 已存在"}), 400
    variables.append(var)
    _save_variables(variables)
    return jsonify({"ok": True, "count": len(variables)})


@app.route("/api/variables/<tag>", methods=["PUT"])
def api_update_variable(tag):
    """更新变量"""
    variables = _load_variables()
    var = request.json
    for i, v in enumerate(variables):
        if v["tag"] == tag:
            variables[i] = var
            _save_variables(variables)
            return jsonify({"ok": True})
    return jsonify({"error": f"变量 '{tag}' 不存在"}), 404


@app.route("/api/variables/<tag>", methods=["DELETE"])
def api_delete_variable(tag):
    """删除变量"""
    variables = _load_variables()
    variables = [v for v in variables if v.get("tag") != tag]
    _save_variables(variables)
    return jsonify({"ok": True})


@app.route("/api/variables/sync-graph", methods=["POST"])
def api_sync_variables_from_graph():
    """从当前运行的仿真图中同步所有中间变量到变量表"""
    engine = _sim_state.get("engine")
    if not engine:
        return jsonify({"error": "仿真未运行，无法同步"}), 400

    variables = _load_variables()
    existing_tags = {v["tag"] for v in variables}

    # 从 recorder 的列名获取所有变量
    added = []
    for col in engine.recorder.columns:
        if col not in existing_tags:
            variables.append({
                "tag": col,
                "name": col,
                "type": "CALC",
                "unit": "",
                "description": "仿真中间变量（自动同步）",
            })
            added.append(col)
            existing_tags.add(col)

    if added:
        _save_variables(variables)
    return jsonify({"ok": True, "added": added, "total": len(variables)})


# ══════════════════════════════════════════════════════════
#  曲线数据 API
# ══════════════════════════════════════════════════════════

@app.route("/api/trend/data", methods=["GET"])
def api_trend_data():
    """获取曲线数据（指定变量 tag 列表）"""
    engine = _sim_state.get("engine")
    if not engine or engine.recorder.count == 0:
        return jsonify({"timestamps": [], "series": {}, "running": False})

    tags = request.args.get("tags", "").split(",")
    tags = [t.strip() for t in tags if t.strip()]
    n = request.args.get("n", 500, type=int)

    recorder = engine.recorder
    total = recorder.count
    start = max(0, total - n)
    timestamps, rows, columns = recorder.get_range(start)

    series = {}
    for tag in tags:
        if tag in columns:
            series[tag] = [row.get(tag) for row in rows]
        else:
            series[tag] = []

    return jsonify({
        "timestamps": timestamps,
        "series": series,
        "running": _sim_state["running"],
        "sim_time": engine.sim_time,
    })


# ══════════════════════════════════════════════════════════
#  信号配置 API
# ══════════════════════════════════════════════════════════

def _load_mapping_raw():
    """加载 YAML 原始数据"""
    import yaml
    if not MAPPING_FILE.exists():
        return {"server": "opc.tcp://localhost:9440", "dpu": "DPU3013",
                "signals": [], "redundancy": {}}
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "signals" not in data:
        data["signals"] = []
    if "redundancy" not in data:
        data["redundancy"] = {}
    return data


def _save_mapping_raw(data):
    """保存到 YAML"""
    import yaml
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False)


def _normalize_addr(addr: str) -> str:
    """
    归一化 OPC 地址，用于去重比较。
    去掉 'ns=0;s=', 's=' 前缀和 DPU 名前缀，统一大写。
    例:
      'ns=0;s=DPU3013.HW.AI010601' → 'HW.AI010601'
      's=DPU3013.SH0098'           → 'SH0098'
      'DPU3013.SH0098.PROFCF.IN'   → 'SH0098.PROFCF.IN'
      'SH0098.PROFCF.IN'           → 'SH0098.PROFCF.IN'
      'AI010601'                    → 'AI010601'  (channel 原样)
    """
    if not addr:
        return ""
    a = addr.strip()
    # 去 ns=0;s= 前缀
    if ";s=" in a:
        a = a.split(";s=", 1)[1]
    # 去 s= 前缀
    if a.startswith("s="):
        a = a[2:]
    # 去 DPU 前缀 (如 DPU3013.)
    parts = a.split(".", 1)
    if len(parts) > 1 and parts[0].upper().startswith("DPU"):
        a = parts[1]
    return a.upper()


def _get_signal_addr(sig: dict) -> str:
    """获取信号的归一化地址"""
    channel = sig.get("channel", "")
    node = sig.get("node", "")
    raw = channel if channel else node
    return _normalize_addr(raw)


@app.route("/api/signals", methods=["GET"])
def api_get_signals():
    data = _load_mapping_raw()
    dpus = data.get("dpus") or ([data["dpu"]] if data.get("dpu") else [])
    return jsonify({
        "server": data.get("server", ""),
        "dpus": dpus,
        "dpu": dpus[0] if dpus else "",  # 兼容旧前端
        "signals": data.get("signals", []),
        "redundancy": data.get("redundancy", {}),
    })


@app.route("/api/signals", methods=["POST"])
def api_add_signal():
    """添加信号"""
    data = _load_mapping_raw()
    sig = request.json

    # 按归一化地址去重
    new_addr = _get_signal_addr(sig)
    if new_addr:
        for s in data["signals"]:
            if _get_signal_addr(s) == new_addr:
                return jsonify({"error": f"地址 '{new_addr}' 已存在（信号 '{s['name']}'）"}), 400

    # 按名称去重
    if sig.get("name"):
        for s in data["signals"]:
            if s["name"] == sig["name"]:
                return jsonify({"error": f"信号名 '{sig['name']}' 已存在"}), 400

    data["signals"].append(sig)
    _save_mapping_raw(data)
    return jsonify({"ok": True, "count": len(data["signals"])})


@app.route("/api/signals/dedup", methods=["POST"])
def api_dedup_signals():
    """去重：按归一化地址合并重复信号，保留第一个"""
    data = _load_mapping_raw()
    seen_addrs = {}
    cleaned = []
    removed = []

    for s in data.get("signals", []):
        addr = _get_signal_addr(s)
        if not addr:
            # 无地址的信号（如文件夹节点）跳过
            removed.append(s.get("name", "?"))
            continue
        if addr in seen_addrs:
            removed.append(s.get("name", "?"))
        else:
            seen_addrs[addr] = s.get("name", "")
            cleaned.append(s)

    data["signals"] = cleaned
    _save_mapping_raw(data)
    return jsonify({
        "ok": True,
        "before": len(data.get("signals", [])) + len(removed),
        "after": len(cleaned),
        "removed": removed,
    })


@app.route("/api/signals/<name>", methods=["PUT"])
def api_update_signal(name):
    """更新信号"""
    data = _load_mapping_raw()
    sig = request.json
    for i, s in enumerate(data["signals"]):
        if s["name"] == name:
            data["signals"][i] = sig
            _save_mapping_raw(data)
            return jsonify({"ok": True})
    return jsonify({"error": f"信号 '{name}' 不存在"}), 404


@app.route("/api/signals/<name>", methods=["DELETE"])
def api_delete_signal(name):
    """删除信号"""
    data = _load_mapping_raw()
    data["signals"] = [s for s in data["signals"] if s["name"] != name]
    # 同时清理冗余配置
    if name in data.get("redundancy", {}):
        del data["redundancy"][name]
    _save_mapping_raw(data)
    return jsonify({"ok": True})


@app.route("/api/signals/import-csv", methods=["POST"])
def api_import_csv():
    """从科远导出的 CSV 导入信号"""
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    file = request.files["file"]
    content = file.read()

    # 尝试多种编码
    text = None
    for enc in ["gb18030", "gbk", "utf-8-sig", "utf-8"]:
        try:
            text = content.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return jsonify({"error": "无法识别文件编码"}), 400

    # 解析科远 CSV 格式
    lines = text.strip().split("\n")
    imported = []
    for line in lines:
        if line.startswith("#") or line.startswith("~"):
            continue
        cols = line.split(",")
        if len(cols) < 8:
            continue

        try:
            idx = int(cols[0])
        except ValueError:
            continue

        point_name = cols[1].strip()  # 如 HW.AI010601.PV
        description = cols[2].strip()
        point_addr = cols[7].strip()

        # 判断信号类型
        if ".AI" in point_name and ".PV" in point_name:
            # AI 通道 PV
            parts = point_name.split(".")
            channel = parts[1] if len(parts) >= 3 else ""
            range_high = float(cols[10]) if cols[10] else 100
            range_low = float(cols[11]) if cols[11] else 0
            imported.append({
                "point_name": point_name,
                "channel": channel,
                "type": "AI",
                "description": description,
                "range_low": range_low,
                "range_high": range_high,
                "pin": "PV",
            })
        elif ".AI" in point_name and (".HR" in point_name or ".LR" in point_name):
            # HR/LR 配置点，记录但不作为独立信号
            pin = "HR" if ".HR" in point_name else "LR"
            parts = point_name.split(".")
            channel = parts[1] if len(parts) >= 3 else ""
            imported.append({
                "point_name": point_name,
                "channel": channel,
                "type": "AI",
                "description": description,
                "pin": pin,
            })
        elif point_name.startswith("SH"):
            # 组态块
            imported.append({
                "point_name": point_name,
                "node": point_addr,
                "type": "block",
                "description": description,
                "pin": "",
            })

    return jsonify({"ok": True, "points": imported, "count": len(imported)})


@app.route("/api/signals/redundancy", methods=["POST"])
def api_update_redundancy():
    """更新冗余配置"""
    data = _load_mapping_raw()
    data["redundancy"] = request.json.get("redundancy", {})
    _save_mapping_raw(data)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════
#  OPC 连接与扫描 API
# ══════════════════════════════════════════════════════════

@app.route("/api/opc/config", methods=["GET", "POST"])
def api_opc_config():
    """读取/更新 OPC 连接配置（支持多 DPU）"""
    data = _load_mapping_raw()
    if request.method == "POST":
        body = request.json
        data["server"] = body.get("server", data.get("server"))
        # 兼容旧格式 dpu 字符串 → dpus 列表
        if "dpus" in body:
            data["dpus"] = body["dpus"]
            data.pop("dpu", None)
        elif "dpu" in body:
            data["dpus"] = [body["dpu"]] if body["dpu"] else []
            data.pop("dpu", None)
        _save_mapping_raw(data)
        return jsonify({"ok": True})
    # 返回时统一为 dpus 列表
    dpus = data.get("dpus") or ([data["dpu"]] if data.get("dpu") else [])
    return jsonify({"server": data.get("server", ""), "dpus": dpus})


@app.route("/api/opc/test", methods=["POST"])
def api_opc_test():
    """测试 OPC 连接"""
    import asyncio
    data = _load_mapping_raw()
    url = request.json.get("server", data.get("server", ""))

    async def _test():
        from ..opc_client import OPCClient
        client = OPCClient(url, timeout=5.0)
        try:
            await client.connect(retry_count=1, retry_interval=1.0)
            await client.disconnect()
            return True, "连接成功"
        except Exception as e:
            return False, str(e)

    loop = asyncio.new_event_loop()
    try:
        ok, msg = loop.run_until_complete(_test())
    finally:
        loop.close()

    return jsonify({"ok": ok, "message": msg})


@app.route("/api/opc/ping", methods=["GET"])
def api_opc_ping():
    """轻量级 OPC 连通性检测（短超时，无重试）"""
    import asyncio
    data = _load_mapping_raw()
    url = data.get("server", "opc.tcp://localhost:9440")

    async def _ping():
        from ..opc_client import OPCClient
        client = OPCClient(url, timeout=2.0)
        try:
            await client.connect(retry_count=1, retry_interval=0)
            await client.disconnect()
            return True
        except Exception:
            return False

    loop = asyncio.new_event_loop()
    try:
        ok = loop.run_until_complete(_ping())
    finally:
        loop.close()

    return jsonify({"ok": ok})


@app.route("/api/opc/read-batch", methods=["POST"])
def api_opc_read_batch():
    """批量读取信号的 OPC 在线值"""
    import asyncio
    data = _load_mapping_raw()
    url = data.get("server", "opc.tcp://localhost:9440")
    dpus = data.get("dpus") or ([data["dpu"]] if data.get("dpu") else ["DPU3013"])
    default_dpu = dpus[0] if dpus else "DPU3013"
    signals = data.get("signals", [])

    if not signals:
        return jsonify({"values": {}})

    # 构建每个信号的 OPC 节点 ID
    sig_nodes = []  # [(signal_name, node_id)]
    for sig in signals:
        node_id = None
        if sig.get("node"):
            # block 类型：直接用 node 字段（可能需要加 ns=0;s= 前缀）
            n = sig["node"]
            if not n.startswith("ns="):
                n = f"ns=0;s={n}"
            node_id = n
        elif sig.get("channel"):
            # AI/DI 通道：构建 DPU.HW.CHANNEL.PV
            ch = sig["channel"]
            node_id = f"ns=0;s={default_dpu}.HW.{ch}.PV"
        if node_id:
            sig_nodes.append((sig["name"], node_id))

    if not sig_nodes:
        return jsonify({"values": {}})

    async def _batch_read():
        from ..opc_client import OPCClient
        client = OPCClient(url, timeout=5.0)
        result = {}
        try:
            await client.connect(retry_count=1, retry_interval=1.0)
            node_ids = [nid for _, nid in sig_nodes]
            values = await client.read_values(node_ids)
            for (name, _), val in zip(sig_nodes, values):
                if val is not None:
                    result[name] = round(val, 4) if isinstance(val, float) else val
                else:
                    result[name] = None
            await client.disconnect()
        except Exception as e:
            return {"error": str(e)}
        return result

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_batch_read())
    finally:
        loop.close()

    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return jsonify(result), 500
    return jsonify({"values": result})


@app.route("/api/opc/browse", methods=["POST"])
def api_opc_browse():
    """浏览 OPC 节点树（在线扫描）"""
    import asyncio
    data = _load_mapping_raw()
    url = request.json.get("server", data.get("server", ""))
    parent_node = request.json.get("node", "")
    dpus = data.get("dpus") or ([data["dpu"]] if data.get("dpu") else ["DPU3013"])
    dpu = request.json.get("dpu", dpus[0] if dpus else "DPU3013")

    if not parent_node:
        parent_node = f"ns=0;s={dpu}"

    async def _browse():
        from ..opc_client import OPCClient
        client = OPCClient(url, timeout=5.0)
        try:
            await client.connect(retry_count=2, retry_interval=1.0)
            children = await client.browse_children(parent_node)
            await client.disconnect()
            return [{"name": name, "node_id": nid} for name, nid in children]
        except Exception as e:
            return {"error": str(e)}

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_browse())
    finally:
        loop.close()

    if isinstance(result, dict) and "error" in result:
        return jsonify(result), 500
    return jsonify({"parent": parent_node, "children": result})


@app.route("/api/opc/read", methods=["POST"])
def api_opc_read():
    """读取单个 OPC 节点的当前值"""
    import asyncio
    data = _load_mapping_raw()
    url = data.get("server", "")
    node_id = request.json.get("node_id", "")

    async def _read():
        from ..opc_client import OPCClient
        client = OPCClient(url, timeout=5.0)
        try:
            await client.connect(retry_count=1, retry_interval=1.0)
            val = await client.read_value(node_id)
            await client.disconnect()
            return {"value": val, "type": type(val).__name__}
        except Exception as e:
            return {"error": str(e)}

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_read())
    finally:
        loop.close()

    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


# ══════════════════════════════════════════════════════════
#  模型组态 API
# ══════════════════════════════════════════════════════════

@app.route("/api/blocks", methods=["GET"])
def api_get_blocks():
    """获取可用功能块列表（从 block_library.yaml 加载）"""
    from .block_defs import get_blocks
    blocks = get_blocks()
    return jsonify(blocks)


@app.route("/api/blocks/categories", methods=["GET"])
def api_get_categories():
    """获取功能块分类列表"""
    from .block_defs import get_categories
    return jsonify(get_categories())


@app.route("/api/blocks/<block_id>", methods=["GET"])
def api_get_block(block_id):
    """获取单个功能块定义"""
    from .block_defs import get_block
    b = get_block(block_id)
    if b is None:
        return jsonify({"error": f"功能块 '{block_id}' 不存在"}), 404
    return jsonify(b)


@app.route("/api/model/save", methods=["POST"])
def api_save_model():
    """保存模型组态，同步更新 manifest"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = request.json
    name = payload.get("name", "default")
    filepath = MODEL_DIR / f"{name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # 同步 manifest
    manifest = _load_manifest()
    found = False
    for p in manifest["pages"]:
        if p["id"] == name:
            found = True
            break
    if not found and not name.startswith("_"):
        layer = payload.get("layer", "IB")
        if name.startswith("IL_"):
            layer = "IL"
        manifest["pages"].append({
            "id": name, "layer": layer, "name": name,
            "order": len(manifest["pages"])
        })
        _save_manifest(manifest)
    return jsonify({"ok": True, "path": str(filepath)})


@app.route("/api/model/load/<name>", methods=["GET"])
def api_load_model(name):
    """加载模型组态"""
    filepath = MODEL_DIR / f"{name}.json"
    if not filepath.exists():
        return jsonify({"error": f"模型 '{name}' 不存在"}), 404
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route("/api/model/list", methods=["GET"])
def api_list_models():
    """列出已保存的模型"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    models = []
    for f in MODEL_DIR.glob("*.json"):
        models.append({"name": f.stem, "path": str(f)})
    return jsonify(models)


# ══════════════════════════════════════════════════════════
#  页面管理 API
# ══════════════════════════════════════════════════════════

@app.route("/api/pages", methods=["GET"])
def api_get_pages():
    """返回按层分组的页面列表"""
    return jsonify(_get_sidebar_tree())


@app.route("/api/pages", methods=["POST"])
def api_create_page():
    """新建页面"""
    data = request.json or {}
    layer = data.get("layer", "IB").upper()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400

    # 生成 ID：IL 层自动加前缀
    if layer == "IL" and not name.startswith("IL_"):
        page_id = f"IL_{name}"
    else:
        page_id = name.replace(" ", "_")

    # 检查重复
    manifest = _load_manifest()
    for p in manifest["pages"]:
        if p["id"] == page_id:
            return jsonify({"error": f"页面 '{page_id}' 已存在"}), 400

    # 创建空模型文件
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = MODEL_DIR / f"{page_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"name": page_id, "layer": layer, "drawflow": {}}, f, ensure_ascii=False, indent=2)

    # 更新清单
    manifest["pages"].append({
        "id": page_id, "layer": layer, "name": name,
        "order": len(manifest["pages"])
    })
    _save_manifest(manifest)
    return jsonify({"ok": True, "id": page_id, "layer": layer, "name": name})


@app.route("/api/pages/<page_id>", methods=["PUT"])
def api_rename_page(page_id):
    """重命名页面"""
    data = request.json or {}
    new_name = data.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "名称不能为空"}), 400
    manifest = _load_manifest()
    for p in manifest["pages"]:
        if p["id"] == page_id:
            p["name"] = new_name
            _save_manifest(manifest)
            return jsonify({"ok": True})
    return jsonify({"error": f"页面 '{page_id}' 不存在"}), 404


@app.route("/api/pages/<page_id>", methods=["DELETE"])
def api_delete_page(page_id):
    """删除页面（模型文件 + 清单条目）"""
    manifest = _load_manifest()
    manifest["pages"] = [p for p in manifest["pages"] if p["id"] != page_id]
    _save_manifest(manifest)
    # 删除模型文件
    filepath = MODEL_DIR / f"{page_id}.json"
    if filepath.exists():
        filepath.unlink()
    return jsonify({"ok": True})


@app.route("/api/pages/refs", methods=["GET"])
def api_get_page_refs():
    """扫描所有模型，收集 ref_out 标签及其所属页面"""
    manifest = _load_manifest()
    refs = []
    for page in manifest.get("pages", []):
        page_id = page["id"]
        filepath = MODEL_DIR / f"{page_id}.json"
        if not filepath.exists():
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                model = json.load(f)
            # 解析 drawflow 中的 ref_out 节点
            # CanvasEngine 导出格式: model.drawflow = {version, drawflow:{drawflow:{Home:{data:...}}}, meta:{...}}
            # meta 在最外层 drawflow 对象上，必须在剥离嵌套前先提取
            drawflow_data = model.get("drawflow", {})
            meta = {}
            if isinstance(drawflow_data, dict):
                meta = drawflow_data.get("meta", {})
                # 向下查找 meta（兼容不同嵌套深度）
                if not meta and "drawflow" in drawflow_data:
                    inner = drawflow_data["drawflow"]
                    if isinstance(inner, dict):
                        meta = inner.get("meta", {})
            node_block_map = meta.get("nodeBlockMap", {})
            node_data_map = meta.get("nodeDataMap", {})
            for node_id, block_type in node_block_map.items():
                if block_type == "ref_out":
                    data = node_data_map.get(node_id, {})
                    tag = data.get("tag", "")
                    if tag:
                        refs.append({
                            "tag": tag,
                            "page_id": page_id,
                            "layer": page.get("layer", "IB"),
                            "page_name": page.get("name", page_id),
                        })
        except Exception:
            continue
    return jsonify({"refs": refs})


@app.route("/api/search", methods=["GET"])
def api_search():
    """全局搜索：遍历所有页面，搜索 tag、块名、块类型、页面名"""
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify({"results": []})

    manifest = _load_manifest()
    results = []

    for page in manifest.get("pages", []):
        page_id = page["id"]
        page_name = page.get("name", page_id)
        layer = page.get("layer", "IB")

        # 页面名匹配
        if q in page_name.lower() or q in page_id.lower():
            score = 10 if page_name.lower().startswith(q) else 5
            results.append({
                "type": "page",
                "page_id": page_id,
                "page_name": page_name,
                "layer": layer,
                "node_id": None,
                "tag": "",
                "label": page_name,
                "score": score,
            })

        # 搜索页面内的节点
        filepath = MODEL_DIR / f"{page_id}.json"
        if not filepath.exists():
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                model = json.load(f)
            drawflow_data = model.get("drawflow", {})
            meta = {}
            if isinstance(drawflow_data, dict):
                meta = drawflow_data.get("meta", {})
                if not meta and "drawflow" in drawflow_data:
                    inner = drawflow_data["drawflow"]
                    if isinstance(inner, dict):
                        meta = inner.get("meta", {})
            node_block_map = meta.get("nodeBlockMap", {})
            node_data_map = meta.get("nodeDataMap", {})

            for node_id, block_type in node_block_map.items():
                data = node_data_map.get(node_id, {})
                tag = data.get("tag", "")
                block_name = data.get("_blockName", "")

                # 匹配字段
                searchable = f"{tag} {block_name} {block_type}".lower()
                if q not in searchable:
                    continue

                score = 0
                if tag and q in tag.lower():
                    score += 8
                    if tag.lower().startswith(q):
                        score += 4
                if block_name and q in block_name.lower():
                    score += 5
                if q in block_type.lower():
                    score += 3

                label = tag or block_name or block_type
                results.append({
                    "type": "node",
                    "page_id": page_id,
                    "page_name": page_name,
                    "layer": layer,
                    "node_id": node_id,
                    "tag": tag,
                    "label": label,
                    "score": score,
                })
        except Exception:
            continue

    # 按分数降序排列，取前 20
    results.sort(key=lambda r: r["score"], reverse=True)
    return jsonify({"results": results[:20]})


@app.route("/api/graph/info", methods=["POST"])
def api_graph_info():
    """解析整个工程的画布组态，返回图的输入输出信息（不执行）"""
    from ..sim_engine.graph_runner import GraphRunner

    manifest = _load_manifest()
    ib_pages = [p["id"] for p in manifest.get("pages", []) if p.get("layer") == "IB"]
    il_pages = [p["id"] for p in manifest.get("pages", []) if p.get("layer") == "IL"]

    if not ib_pages:
        return jsonify({"error": "工程中没有 IB 层页面"}), 400

    ib_jsons = []
    for page_id in ib_pages:
        path = MODEL_DIR / f"{page_id}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                ib_jsons.append(json.load(f))

    il_jsons = []
    for page_id in il_pages:
        path = MODEL_DIR / f"{page_id}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                il_jsons.append(json.load(f))

    try:
        runner = GraphRunner()
        runner.load(ib_jsons, il_jsons if il_jsons else None)
        return jsonify(runner.get_info())
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ══════════════════════════════════════════════════════════
#  仿真运行 API
# ══════════════════════════════════════════════════════════

@app.route("/api/sim/start", methods=["POST"])
def api_start_sim():
    """启动仿真（基于画布组态的图执行）"""
    if _sim_state["running"]:
        return jsonify({"error": "仿真已在运行中"}), 400

    params = request.json or {}
    mode = params.get("mode", "offline")
    duration = params.get("duration", 60)
    step_size = params.get("step_size", 0.2)
    # 用户在 Run 页面设定的初始输入值
    initial_inputs = params.get("initial_inputs", {})

    # 整个工程运行：从 manifest 加载所有 IB + IL 页面
    manifest = _load_manifest()
    ib_pages = [p["id"] for p in manifest.get("pages", []) if p.get("layer") == "IB"]
    il_pages = [p["id"] for p in manifest.get("pages", []) if p.get("layer") == "IL"]

    if not ib_pages:
        return jsonify({"error": "工程中没有 IB 层页面，请先组态"}), 400

    from ..sim_engine.graph_runner import GraphRunner

    # 加载画布组态
    runner = GraphRunner()

    # 加载 IB 层组态（支持多页）
    ib_jsons = []
    for page_id in ib_pages:
        ib_path = MODEL_DIR / f"{page_id}.json"
        if not ib_path.exists():
            return jsonify({"error": f"IB 层模型 '{page_id}' 不存在"}), 400
        with open(ib_path, "r", encoding="utf-8") as f:
            ib_jsons.append(json.load(f))

    # 加载 IL 层组态（可选，支持多页）
    il_jsons = []
    for page_id in il_pages:
        il_path = MODEL_DIR / f"{page_id}.json"
        if il_path.exists():
            with open(il_path, "r", encoding="utf-8") as f:
                il_jsons.append(json.load(f))

    try:
        runner.load(ib_jsons, il_jsons if il_jsons else None)
    except Exception as e:
        return jsonify({"error": f"组态解析失败: {e}"}), 400

    # 加载 OPC 映射和信号配置
    mapping_data = _load_mapping_raw()
    opc_url = mapping_data.get("server", "opc.tcp://localhost:9440")

    from ..sim_engine import SimEngine
    engine = SimEngine(runner, step_size=step_size)

    _sim_state["engine"] = engine
    _sim_state["mode"] = mode
    _sim_state["running"] = True

    _sim_state["error"] = None

    def run_sim():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if mode == "online":
                # 在线：连接 OPC，IO 层信号通过 OPC 读写
                from ..opc_client import OPCClient, SignalMapping
                opc_client = OPCClient(opc_url)
                mapping = SignalMapping.from_yaml(str(MAPPING_FILE))
                loop.run_until_complete(
                    engine.start(duration=duration,
                                 initial_inputs=initial_inputs,
                                 opc_client=opc_client,
                                 mapping=mapping))
            else:
                # 离线：输入使用用户设定的初始值，不连 OPC
                loop.run_until_complete(
                    engine.run_offline(duration=duration,
                                       initial_inputs=initial_inputs))
        except Exception as e:
            logger.error(f"仿真异常: {e}", exc_info=True)
            _sim_state["error"] = str(e)
        finally:
            _sim_state["running"] = False
            loop.close()

    t = threading.Thread(target=run_sim, daemon=True)
    _sim_state["thread"] = t
    t.start()

    # 返回图的输入输出信息
    graph_info = runner.get_info()
    return jsonify({"ok": True, "mode": mode, "duration": duration,
                    "inputs": graph_info.get("inputs", []),
                    "outputs": graph_info.get("outputs", [])})


@app.route("/api/sim/stop", methods=["POST"])
def api_stop_sim():
    """停止仿真"""
    engine = _sim_state.get("engine")
    if engine and _sim_state["running"]:
        engine.request_stop()
        return jsonify({"ok": True})
    return jsonify({"error": "仿真未在运行"}), 400


@app.route("/api/sim/status", methods=["GET"])
def api_sim_status():
    """获取仿真状态"""
    engine = _sim_state.get("engine")
    result = {
        "running": _sim_state["running"],
        "mode": _sim_state["mode"],
    }
    if _sim_state.get("error"):
        result["error"] = _sim_state["error"]
    if engine:
        result["sim_time"] = engine.sim_time
        result["step_count"] = engine.step_count
        latest = engine.recorder.get_latest()
        result["latest"] = latest
        result["data_count"] = engine.recorder.count
    return jsonify(result)


@app.route("/api/sim/data", methods=["GET"])
def api_sim_data():
    """获取仿真数据（最近 N 条）"""
    engine = _sim_state.get("engine")
    if not engine:
        return jsonify({"timestamps": [], "data": [], "columns": []})

    n = request.args.get("n", 200, type=int)
    recorder = engine.recorder
    total = recorder.count

    start = max(0, total - n)
    timestamps, data, columns = recorder.get_range(start)

    return jsonify({
        "timestamps": timestamps,
        "data": data,
        "columns": columns,
        "total": total,
    })


@app.route("/api/sim/values", methods=["GET"])
def api_sim_values():
    """获取当前仿真值（用于监视模式节点显示）"""
    engine = _sim_state.get("engine")
    if not engine or engine.recorder.count == 0:
        return jsonify({"values": {}, "running": _sim_state["running"]})
    latest = engine.recorder.get_latest() or {}
    return jsonify({
        "values": latest,
        "running": _sim_state["running"],
        "sim_time": engine.sim_time,
    })


@app.route("/api/sim/export", methods=["GET"])
def api_sim_export():
    """导出 CSV"""
    engine = _sim_state.get("engine")
    if not engine or engine.recorder.count == 0:
        return jsonify({"error": "无数据可导出"}), 400

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / "export.csv"
    engine.recorder.to_csv(str(filepath))
    return send_file(str(filepath), as_attachment=True,
                     download_name="simulation_data.csv")


# ══════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("  LeDCSsim - DCS 协调控制仿真平台")
    print("  ────────────────────────────────")
    print("  http://127.0.0.1:5001")
    print()

    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
