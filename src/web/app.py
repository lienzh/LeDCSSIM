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

from flask import Flask, render_template, jsonify, request, send_file

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
MAPPING_FILE = CONFIG_DIR / "opc_mapping.yaml"
MODEL_DIR = CONFIG_DIR / "models"

app = Flask(__name__,
            template_folder=str(Path(__file__).parent / "templates"),
            static_folder=str(Path(__file__).parent / "static"))
app.config["JSON_AS_ASCII"] = False

# ── 全局状态 ──────────────────────────────────────────────
_sim_state = {
    "running": False,
    "mode": "offline",
    "engine": None,
    "thread": None,
    "sim_time": 0.0,
    "step_count": 0,
    "latest_data": {},
}


# ══════════════════════════════════════════════════════════
#  页面路由
# ══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/io")
@app.route("/signals")  # 兼容旧路由
def io_page():
    return render_template("io.html")


@app.route("/il")
def il_page():
    return render_template("il.html")


@app.route("/ib")
@app.route("/model")  # 兼容旧路由
def ib_page():
    return render_template("ib.html")


@app.route("/l3")
def l3_page():
    return render_template("l3.html")


@app.route("/run")
def run_page():
    return render_template("run.html")


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
    return jsonify({
        "server": data.get("server", ""),
        "dpu": data.get("dpu", ""),
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
    """读取/更新 OPC 连接配置"""
    data = _load_mapping_raw()
    if request.method == "POST":
        body = request.json
        data["server"] = body.get("server", data.get("server"))
        data["dpu"] = body.get("dpu", data.get("dpu"))
        _save_mapping_raw(data)
        return jsonify({"ok": True})
    return jsonify({"server": data.get("server", ""), "dpu": data.get("dpu", "")})


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


@app.route("/api/opc/browse", methods=["POST"])
def api_opc_browse():
    """浏览 OPC 节点树（在线扫描）"""
    import asyncio
    data = _load_mapping_raw()
    url = request.json.get("server", data.get("server", ""))
    parent_node = request.json.get("node", "")
    dpu = data.get("dpu", "DPU3013")

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
    """保存模型组态"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    payload = request.json
    name = payload.get("name", "default")
    filepath = MODEL_DIR / f"{name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
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
#  模型 IO 绑定 API
#  模型变量名 ↔ OPC信号名 的映射（model_bindings）
# ══════════════════════════════════════════════════════════

@app.route("/api/bindings", methods=["GET"])
def api_get_bindings():
    """获取模型绑定配置"""
    data = _load_mapping_raw()
    bindings = data.get("model_bindings", {})

    # 获取当前模型的 IO 列表
    from ..sim_engine import CCSPlantModel
    model = CCSPlantModel("temp")
    model_io = {
        "inputs": [{"name": s.name, "description": s.description, "unit": s.unit,
                     "default": s.default}
                    for s in model._input_specs.values()],
        "outputs": [{"name": s.name, "description": s.description, "unit": s.unit,
                      "default": s.default}
                     for s in model._output_specs.values()],
    }

    # 获取可绑定的 OPC 信号
    signals = [{"name": s.get("name"), "description": s.get("description", ""),
                "direction": s.get("direction", ""), "type": s.get("type", "")}
               for s in data.get("signals", [])]

    return jsonify({"bindings": bindings, "model_io": model_io, "signals": signals})


@app.route("/api/bindings", methods=["POST"])
def api_save_bindings():
    """保存模型绑定"""
    data = _load_mapping_raw()
    data["model_bindings"] = request.json.get("bindings", {})
    _save_mapping_raw(data)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════
#  仿真运行 API
# ══════════════════════════════════════════════════════════

@app.route("/api/sim/start", methods=["POST"])
def api_start_sim():
    """启动仿真"""
    if _sim_state["running"]:
        return jsonify({"error": "仿真已在运行中"}), 400

    params = request.json or {}
    mode = params.get("mode", "offline")
    duration = params.get("duration", 60)
    step_size = params.get("step_size", 0.2)
    valve_position = params.get("valve_position", 0.7)
    # 煤量调度: [{time: 0, coal: 200}, {time: 120, coal: 240}, ...]
    coal_schedule = params.get("coal_schedule", [{"time": 0, "coal": 250}])
    # 按时间排序
    coal_schedule.sort(key=lambda x: x["time"])

    from ..sim_engine import SimEngine, CCSPlantModel
    from ..opc_client import SignalMapping

    model = CCSPlantModel("CCS被控对象模型")
    mapping = SignalMapping.from_yaml(str(MAPPING_FILE))
    engine = SimEngine(model, mapping, step_size=step_size)

    _sim_state["engine"] = engine
    _sim_state["mode"] = mode
    _sim_state["running"] = True
    _sim_state["sim_time"] = 0.0
    _sim_state["step_count"] = 0

    def make_input_func(schedule, valve):
        """根据调度表生成输入函数"""
        def input_func(t):
            coal = schedule[0]["coal"]
            for entry in schedule:
                if t >= entry["time"]:
                    coal = entry["coal"]
            return {"coal_flow": coal, "valve_position": valve}
        return input_func

    input_func = make_input_func(coal_schedule, valve_position)

    # 计算初始稳态
    init_coal = coal_schedule[0]["coal"]
    init_power = model.K1 * init_coal
    init_pressure = init_power / (model.K2 * valve_position)

    def run_sim():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if mode == "online":
                initial = {
                    "coal_flow": init_coal,
                    "valve_position": valve_position,
                    "main_steam_pressure": init_pressure,
                    "unit_power": init_power,
                }
                # OPC 读回节点：验证写入
                readback_nodes = {}
                for name in model.output_names:
                    sig = mapping.get(name)
                    if sig and sig.channel_type.upper() == "AI":
                        readback_nodes[f"{name}_opc"] = sig.pv_node

                loop.run_until_complete(
                    engine.start(duration=duration,
                                 initial_values=initial,
                                 input_overrides=input_func,
                                 readback_nodes=readback_nodes))
            else:
                model.reset({
                    "coal_flow": init_coal,
                    "valve_position": valve_position,
                    "main_steam_pressure": init_pressure,
                    "unit_power": init_power,
                })
                loop.run_until_complete(
                    engine.run_offline(duration=duration,
                                       input_func=input_func,
                                       realtime=True))
        except Exception as e:
            logger.error(f"仿真异常: {e}", exc_info=True)
        finally:
            _sim_state["running"] = False
            loop.close()

    t = threading.Thread(target=run_sim, daemon=True)
    _sim_state["thread"] = t
    t.start()

    return jsonify({"ok": True, "mode": mode, "duration": duration,
                    "init_power": round(init_power, 2),
                    "init_pressure": round(init_pressure, 2)})


@app.route("/api/sim/stop", methods=["POST"])
def api_stop_sim():
    """停止仿真"""
    engine = _sim_state.get("engine")
    if engine and _sim_state["running"]:
        # 在引擎的事件循环中调用 stop
        engine._running = False
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
    timestamps = recorder._timestamps[start:]
    data = recorder._data[start:]
    columns = recorder.columns

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
