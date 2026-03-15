# -*- coding: utf-8 -*-
"""
GraphRunner 综合测试

覆盖：
1. 基础功能（节点解析、拓扑排序、单步执行）
2. CCS 被控对象模型（反馈环路、多步仿真）
3. IL-IB 层间对接（tag 匹配）
4. L3 封装块展开
"""
import json
import math
import sys
import os
import tempfile
from pathlib import Path

# 确保可以 import 项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.graph_runner import GraphRunner


# ═══════════════════════════════════════════════════════════
# 辅助函数：快速构建 Drawflow JSON
# ═══════════════════════════════════════════════════════════

def make_drawflow(*nodes_list):
    """
    nodes_list: [(id, name/class, data, inputs_dict, outputs_dict), ...]
    inputs_dict:  {"input_1": {"connections": [{"node": "3", "output": "output_1"}]}}
    outputs_dict: {"output_1": {"connections": [{"node": "5", "input": "input_1"}]}}
    """
    data = {}
    for item in nodes_list:
        nid, cls, node_data, inputs, outputs = item
        data[str(nid)] = {
            "id": nid, "name": cls, "class": cls,
            "data": node_data,
            "inputs": inputs, "outputs": outputs,
            "pos_x": 0, "pos_y": 0,
        }
    return {"drawflow": {"Home": {"data": data}}}


def inp(src_id, src_port="output_1"):
    return {"connections": [{"node": str(src_id), "output": src_port}]}


def out(dst_id, dst_port="input_1"):
    return {"connections": [{"node": str(dst_id), "input": dst_port}]}


def no_conn():
    return {"connections": []}


# ═══════════════════════════════════════════════════════════
# 1. 基础功能测试
# ═══════════════════════════════════════════════════════════

def test_simple_chain():
    """input → gain(K=2) → output"""
    model = make_drawflow(
        (1, "input", {"tag": "x", "default": 5.0}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 2.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "y"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"x": 10.0}, 0.1)
    assert abs(result["y"] - 20.0) < 1e-9, f"Expected 20.0, got {result['y']}"
    print("  ✓ test_simple_chain")


def test_add_sub():
    """两输入加法、减法"""
    model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(3)}),
        (2, "input", {"tag": "b"}, {}, {"output_1": out(3, "input_2")}),
        (3, "ADD", {}, {"input_1": inp(1), "input_2": inp(2)}, {"output_1": out(4)}),
        (4, "output", {"tag": "sum"}, {"input_1": inp(3)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"a": 3.0, "b": 7.0}, 0.1)
    assert abs(result["sum"] - 10.0) < 1e-9
    print("  ✓ test_add_sub")


def test_multiply():
    """乘法"""
    model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(3)}),
        (2, "input", {"tag": "b"}, {}, {"output_1": out(3, "input_2")}),
        (3, "multiply", {}, {"input_1": inp(1), "input_2": inp(2)}, {"output_1": out(4)}),
        (4, "output", {"tag": "prod"}, {"input_1": inp(3)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"a": 4.0, "b": 5.0}, 0.1)
    assert abs(result["prod"] - 20.0) < 1e-9
    print("  ✓ test_multiply")


def test_sum_with_signs():
    """带符号的 sum 块"""
    model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(3)}),
        (2, "input", {"tag": "b"}, {}, {"output_1": out(3, "input_2")}),
        (3, "sum", {"signs": "+-"}, {"input_1": inp(1), "input_2": inp(2)}, {"output_1": out(4)}),
        (4, "output", {"tag": "diff"}, {"input_1": inp(3)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"a": 10.0, "b": 3.0}, 0.1)
    assert abs(result["diff"] - 7.0) < 1e-9
    print("  ✓ test_sum_with_signs")


def test_integrator():
    """积分器多步累积"""
    model = make_drawflow(
        (1, "input", {"tag": "x"}, {}, {"output_1": out(2)}),
        (2, "Integrator", {"K": 1.0, "low": -1000, "high": 1000},
         {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "integral"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    # 10步, 输入=1.0, dt=0.1 → 积分 = 1.0 * 0.1 * 10 = 1.0
    for _ in range(10):
        result = runner.step({"x": 1.0}, 0.1)
    assert abs(result["integral"] - 1.0) < 1e-6, f"Expected ~1.0, got {result['integral']}"
    print("  ✓ test_integrator")


def test_inertia():
    """一阶惯性阶跃响应"""
    model = make_drawflow(
        (1, "input", {"tag": "x"}, {}, {"output_1": out(2)}),
        (2, "Inertia", {"K": 1.0, "T": 1.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "y"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    # 阶跃输入 1.0，运行 5T (5s, dt=0.01)
    for _ in range(500):
        result = runner.step({"x": 1.0}, 0.01)
    # 5T 后应接近 1.0 (> 0.99)
    assert result["y"] > 0.99, f"Expected >0.99, got {result['y']}"
    print("  ✓ test_inertia")


def test_constant():
    """常量节点"""
    model = make_drawflow(
        (1, "CON", {"value": 42.0}, {}, {"output_1": out(2)}),
        (2, "output", {"tag": "val"}, {"input_1": inp(1)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({}, 0.1)
    assert abs(result["val"] - 42.0) < 1e-9
    print("  ✓ test_constant")


def test_default_input():
    """输入未提供时使用默认值"""
    model = make_drawflow(
        (1, "input", {"tag": "x", "default": 99.0}, {}, {"output_1": out(2)}),
        (2, "output", {"tag": "y"}, {"input_1": inp(1)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({}, 0.1)  # 不提供 x
    assert abs(result["y"] - 99.0) < 1e-9
    print("  ✓ test_default_input")


def test_reset():
    """重置后状态归零"""
    model = make_drawflow(
        (1, "input", {"tag": "x"}, {}, {"output_1": out(2)}),
        (2, "Integrator", {"K": 1.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "y"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    for _ in range(10):
        runner.step({"x": 1.0}, 0.1)
    runner.reset()
    result = runner.step({"x": 0.0}, 0.1)
    assert abs(result["y"]) < 1e-9
    print("  ✓ test_reset")


def test_get_info():
    """get_info 返回正确的元信息"""
    model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 1.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "b"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    info = runner.get_info()
    assert info["node_count"] == 3
    assert len(info["inputs"]) == 1
    assert len(info["outputs"]) == 1
    assert info["inputs"][0]["tag"] == "a"
    assert info["outputs"][0]["tag"] == "b"
    print("  ✓ test_get_info")


# ═══════════════════════════════════════════════════════════
# 2. CCS 被控对象模型测试（反馈环路）
# ═══════════════════════════════════════════════════════════

def build_ccs_model():
    """构建 CCS 模型的 Drawflow JSON（与 create_ccs_preset.py 一致）"""
    def node_dict(nid, cls, data, inputs, outputs):
        return {
            "id": nid, "name": cls, "class": cls,
            "data": data, "inputs": inputs, "outputs": outputs,
            "pos_x": 0, "pos_y": 0,
        }

    nodes = {}
    nodes["1"] = node_dict(1, "input", {"tag": "coal_flow", "default": 200}, {},
                           {"output_1": out(3)})
    nodes["2"] = node_dict(2, "input", {"tag": "valve_position", "default": 0.7}, {},
                           {"output_1": out(8)})
    # 给煤惯性 T=60s
    nodes["3"] = node_dict(3, "Inertia", {"K": 1.0, "T": 60}, {"input_1": inp(1)},
                           {"output_1": out(4)})
    # K1 煤量热值系数
    nodes["4"] = node_dict(4, "gain", {"K": 2.4}, {"input_1": inp(3)},
                           {"output_1": out(5)})
    # 能量平衡 +/-
    nodes["5"] = node_dict(5, "sum", {"signs": "+-"},
                           {"input_1": inp(4), "input_2": inp(9)},
                           {"output_1": out(6)})
    # K3 蓄热积分
    nodes["6"] = node_dict(6, "Integrator", {"K": 0.00015, "low": 0, "high": 30},
                           {"input_1": inp(5)}, {"output_1": out(7)})
    # 压力限幅
    nodes["7"] = node_dict(7, "Limiter", {"low": 0, "high": 30},
                           {"input_1": inp(6)}, {"output_1": {"connections":
                               [{"node": "11", "input": "input_1"},
                                {"node": "8", "input": "input_2"}]}})
    # 阀位 × 压力
    nodes["8"] = node_dict(8, "multiply", {},
                           {"input_1": inp(2), "input_2": inp(7)},
                           {"output_1": out(9)})
    # K2 蒸汽流量系数
    nodes["9"] = node_dict(9, "gain", {"K": 51.3},
                           {"input_1": inp(8)},
                           {"output_1": {"connections":
                               [{"node": "5", "input": "input_2"},
                                {"node": "10", "input": "input_1"}]}})
    # 功率响应惯性
    nodes["10"] = node_dict(10, "Inertia", {"K": 1.0, "T": 15},
                            {"input_1": inp(9)}, {"output_1": out(12)})
    # 输出
    nodes["11"] = node_dict(11, "output", {"tag": "main_steam_pressure"},
                            {"input_1": inp(7)}, {})
    nodes["12"] = node_dict(12, "output", {"tag": "unit_power"},
                            {"input_1": inp(10)}, {})

    return {"drawflow": {"Home": {"data": nodes}}}


def test_ccs_model_loads():
    """CCS 模型能正确加载（含反馈环路）"""
    model = build_ccs_model()
    runner = GraphRunner()
    runner.load(model)
    assert runner.node_count == 12
    tags_in = runner.get_input_tags()
    tags_out = runner.get_output_tags()
    assert "coal_flow" in tags_in
    assert "valve_position" in tags_in
    assert "main_steam_pressure" in tags_out
    assert "unit_power" in tags_out
    print("  ✓ test_ccs_model_loads")


def test_ccs_model_step():
    """CCS 模型多步运行，压力和功率应有合理值"""
    model = build_ccs_model()
    runner = GraphRunner()
    runner.load(model)

    dt = 0.2
    inputs = {"coal_flow": 200.0, "valve_position": 0.7}

    # 运行 100 步（20s）
    for _ in range(100):
        result = runner.step(inputs, dt)

    pressure = result["main_steam_pressure"]
    power = result["unit_power"]

    # 压力应在合理范围 (0~30 MPa)
    assert 0 < pressure < 30, f"Pressure out of range: {pressure}"
    # 功率应为正
    assert power > 0, f"Power should be positive: {power}"
    print(f"  ✓ test_ccs_model_step (P={pressure:.2f}MPa, W={power:.1f}MW)")


def test_ccs_model_steady_state():
    """CCS 模型长时间运行应趋向稳态"""
    model = build_ccs_model()
    runner = GraphRunner()
    runner.load(model)

    dt = 0.2
    inputs = {"coal_flow": 200.0, "valve_position": 0.7}

    # 运行 3000 步（600s = 10分钟）
    prev_pressure = 0.0
    for i in range(3000):
        result = runner.step(inputs, dt)

    # 再运行 100 步，检查变化率
    p1 = result["main_steam_pressure"]
    for _ in range(100):
        result = runner.step(inputs, dt)
    p2 = result["main_steam_pressure"]

    # 变化应很小（趋于稳态）
    change_rate = abs(p2 - p1) / max(abs(p1), 1e-6)
    assert change_rate < 0.01, f"Not converging: {p1:.4f} → {p2:.4f} ({change_rate:.4%})"
    print(f"  ✓ test_ccs_model_steady_state (P={p2:.2f}MPa, change={change_rate:.6%})")


def test_ccs_coal_step_response():
    """CCS 模型对煤量阶跃的响应方向正确"""
    model = build_ccs_model()
    runner = GraphRunner()
    runner.load(model)
    dt = 0.2

    # 先建立稳态 (5分钟)
    for _ in range(1500):
        result = runner.step({"coal_flow": 200.0, "valve_position": 0.7}, dt)
    p_before = result["main_steam_pressure"]

    # 增加煤量 200 → 220
    for _ in range(500):
        result = runner.step({"coal_flow": 220.0, "valve_position": 0.7}, dt)
    p_after = result["main_steam_pressure"]

    # 煤量增加 → 压力升高
    assert p_after > p_before, f"Pressure should rise: {p_before:.4f} → {p_after:.4f}"
    print(f"  ✓ test_ccs_coal_step_response (ΔP={p_after-p_before:+.4f}MPa)")


# ═══════════════════════════════════════════════════════════
# 3. IL-IB 层间对接测试
# ═══════════════════════════════════════════════════════════

def test_il_ib_connection():
    """
    IL 层: input(tag=raw_temp) → gain(K=0.01) → output(tag=temperature)
    IB 层: input(tag=temperature) → gain(K=2) → output(tag=result)

    IL output(tag=temperature) → IB input(tag=temperature) 自动对接
    外部输入: raw_temp, 外部输出: result
    """
    il_json = make_drawflow(
        (1, "input", {"tag": "raw_temp"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 0.01}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "temperature"}, {"input_1": inp(2)}, {}),
    )
    ib_json = make_drawflow(
        (1, "input", {"tag": "temperature"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 2.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "result"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(ib_json, il_json)

    # 外部输入应只有 raw_temp（temperature 已被内部对接消费）
    input_tags = runner.get_input_tags()
    output_tags = runner.get_output_tags()
    assert "raw_temp" in input_tags, f"Missing raw_temp in {input_tags}"
    assert "temperature" not in input_tags, f"temperature should be consumed: {input_tags}"
    assert "result" in output_tags, f"Missing result in {output_tags}"

    # 执行: raw_temp=1000 → IL gain(K=0.01) → 10 → IB gain(K=2) → 20
    result = runner.step({"raw_temp": 1000.0}, 0.1)
    assert abs(result["result"] - 20.0) < 1e-9, f"Expected 20.0, got {result['result']}"
    print("  ✓ test_il_ib_connection")


def test_il_ib_reverse_connection():
    """
    反向对接：IB output(tag=control_output) → IL input(tag=control_output)

    IB: input(tag=pv) → gain(K=0.5) → output(tag=control_output)
    IL: input(tag=control_output) → gain(K=100) → output(tag=raw_output)
    """
    il_json = make_drawflow(
        (1, "input", {"tag": "control_output"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 100.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "raw_output"}, {"input_1": inp(2)}, {}),
    )
    ib_json = make_drawflow(
        (1, "input", {"tag": "pv"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 0.5}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "control_output"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(ib_json, il_json)

    input_tags = runner.get_input_tags()
    output_tags = runner.get_output_tags()
    assert "pv" in input_tags
    assert "control_output" not in input_tags, f"control_output should be consumed"
    assert "raw_output" in output_tags

    # pv=10 → IB gain(0.5) → 5 → IL gain(100) → 500
    result = runner.step({"pv": 10.0}, 0.1)
    assert abs(result["raw_output"] - 500.0) < 1e-9, f"Expected 500.0, got {result['raw_output']}"
    print("  ✓ test_il_ib_reverse_connection")


def test_il_ib_bidirectional():
    """双向对接：正向+反向同时存在"""
    il_json = make_drawflow(
        (1, "input", {"tag": "raw_sensor"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 0.1}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "sensor_pv"}, {"input_1": inp(2)}, {}),
        (4, "input", {"tag": "ctrl_out"}, {}, {"output_1": out(5)}),
        (5, "gain", {"K": 10.0}, {"input_1": inp(4)}, {"output_1": out(6)}),
        (6, "output", {"tag": "raw_ctrl"}, {"input_1": inp(5)}, {}),
    )
    ib_json = make_drawflow(
        (1, "input", {"tag": "sensor_pv"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 3.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "ctrl_out"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(ib_json, il_json)

    input_tags = runner.get_input_tags()
    output_tags = runner.get_output_tags()
    assert "raw_sensor" in input_tags
    assert "sensor_pv" not in input_tags
    assert "ctrl_out" not in input_tags
    assert "raw_ctrl" in output_tags

    # raw_sensor=100 → IL(0.1) → 10 → IB(3.0) → 30 → IL(10.0) → 300
    result = runner.step({"raw_sensor": 100.0}, 0.1)
    assert abs(result["raw_ctrl"] - 300.0) < 1e-9, f"Expected 300.0, got {result['raw_ctrl']}"
    print("  ✓ test_il_ib_bidirectional")


# ═══════════════════════════════════════════════════════════
# 4. L3 封装块展开测试
# ═══════════════════════════════════════════════════════════

def test_l3_expansion():
    """
    L3 子块：input → gain(K=5) → output
    主图：input(tag=x) → L3_test_sub → output(tag=y)

    预期: x=10 → L3内部 gain(K=5) → 50
    """
    # 创建临时 L3 模型文件
    l3_model = make_drawflow(
        (1, "input", {"tag": "in1"}, {}, {"output_1": out(2)}),
        (2, "gain", {"K": 5.0}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "out1"}, {"input_1": inp(2)}, {}),
    )

    # 保存到 config/models/L3_test_sub.json
    model_dir = Path(__file__).resolve().parent.parent / "config" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    l3_path = model_dir / "L3_test_sub.json"
    with open(l3_path, "w", encoding="utf-8") as f:
        json.dump(l3_model, f)

    try:
        # 主图：使用 L3_test_sub 块
        main_model = make_drawflow(
            (1, "input", {"tag": "x"}, {}, {"output_1": out(2)}),
            (2, "L3_test_sub", {}, {"input_1": inp(1)}, {"output_1": out(3)}),
            (3, "output", {"tag": "y"}, {"input_1": inp(2)}, {}),
        )
        runner = GraphRunner()
        runner.load(main_model)

        # L3 节点应被展开
        assert runner.node_count > 3, f"L3 should expand, got {runner.node_count} nodes"

        result = runner.step({"x": 10.0}, 0.1)
        assert abs(result["y"] - 50.0) < 1e-9, f"Expected 50.0, got {result['y']}"
        print("  ✓ test_l3_expansion")
    finally:
        l3_path.unlink(missing_ok=True)


def test_l3_multi_io():
    """
    L3 子块有多个输入输出：
    L3: input(a), input(b) → ADD → gain(K=2) → output(sum), output(double_sum)
    主图: input(x), input(y) → L3 → output(result)
    """
    l3_model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(3)}),
        (2, "input", {"tag": "b"}, {}, {"output_1": out(3, "input_2")}),
        (3, "ADD", {}, {"input_1": inp(1), "input_2": inp(2)}, {"output_1": out(4)}),
        (4, "gain", {"K": 2.0}, {"input_1": inp(3)}, {"output_1": out(5)}),
        (5, "output", {"tag": "result"}, {"input_1": inp(4)}, {}),
    )

    model_dir = Path(__file__).resolve().parent.parent / "config" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    l3_path = model_dir / "L3_adder.json"
    with open(l3_path, "w", encoding="utf-8") as f:
        json.dump(l3_model, f)

    try:
        main_model = make_drawflow(
            (1, "input", {"tag": "x"}, {}, {"output_1": out(3)}),
            (2, "input", {"tag": "y"}, {}, {"output_1": out(3, "input_2")}),
            (3, "L3_adder", {}, {"input_1": inp(1), "input_2": inp(2)},
             {"output_1": out(4)}),
            (4, "output", {"tag": "result"}, {"input_1": inp(3)}, {}),
        )
        runner = GraphRunner()
        runner.load(main_model)

        result = runner.step({"x": 3.0, "y": 7.0}, 0.1)
        # (3 + 7) * 2 = 20
        assert abs(result["result"] - 20.0) < 1e-9, f"Expected 20.0, got {result['result']}"
        print("  ✓ test_l3_multi_io")
    finally:
        l3_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════
# 5. 边界与兼容性测试
# ═══════════════════════════════════════════════════════════

def test_name_fallback():
    """旧格式用 name 而非 tag 的兼容性"""
    model = make_drawflow(
        (1, "input", {"name": "old_input", "default": 42.0}, {}, {"output_1": out(2)}),
        (2, "output", {"name": "old_output"}, {"input_1": inp(1)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    assert "old_input" in runner.get_input_tags()
    assert "old_output" in runner.get_output_tags()
    result = runner.step({"old_input": 99.0}, 0.1)
    assert abs(result["old_output"] - 99.0) < 1e-9
    print("  ✓ test_name_fallback")


def test_limiter_block():
    """Limiter 功能块"""
    model = make_drawflow(
        (1, "input", {"tag": "x"}, {}, {"output_1": out(2)}),
        (2, "Limiter", {"low": 0, "high": 100}, {"input_1": inp(1)}, {"output_1": out(3)}),
        (3, "output", {"tag": "y"}, {"input_1": inp(2)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"x": 150.0}, 0.1)
    assert abs(result["y"] - 100.0) < 1e-9
    result = runner.step({"x": -10.0}, 0.1)
    assert abs(result["y"] - 0.0) < 1e-9
    print("  ✓ test_limiter_block")


def test_high_low_select():
    """HS / LS 选择"""
    model = make_drawflow(
        (1, "input", {"tag": "a"}, {}, {"output_1": out(3)}),
        (2, "input", {"tag": "b"}, {}, {"output_1": out(3, "input_2")}),
        (3, "HS", {}, {"input_1": inp(1), "input_2": inp(2)}, {"output_1": out(4)}),
        (4, "output", {"tag": "max_val"}, {"input_1": inp(3)}, {}),
    )
    runner = GraphRunner()
    runner.load(model)
    result = runner.step({"a": 5.0, "b": 8.0}, 0.1)
    assert abs(result["max_val"] - 8.0) < 1e-9
    print("  ✓ test_high_low_select")


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import logging
    import io
    # Windows GBK 兼容
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stderr)])

    passed = 0
    failed = 0
    errors = []

    sections = [
        ("基础功能", [
            test_simple_chain, test_add_sub, test_multiply,
            test_sum_with_signs, test_integrator, test_inertia,
            test_constant, test_default_input, test_reset, test_get_info,
        ]),
        ("CCS 被控对象模型", [
            test_ccs_model_loads, test_ccs_model_step,
            test_ccs_model_steady_state, test_ccs_coal_step_response,
        ]),
        ("IL-IB 层间对接", [
            test_il_ib_connection, test_il_ib_reverse_connection,
            test_il_ib_bidirectional,
        ]),
        ("L3 封装块展开", [
            test_l3_expansion, test_l3_multi_io,
        ]),
        ("边界与兼容性", [
            test_name_fallback, test_limiter_block, test_high_low_select,
        ]),
    ]

    for section_name, tests in sections:
        print(f"\n{'─' * 50}")
        print(f"  {section_name}")
        print(f"{'─' * 50}")
        for test_func in tests:
            try:
                test_func()
                passed += 1
            except Exception as e:
                failed += 1
                errors.append((test_func.__name__, str(e)))
                print(f"  ✗ {test_func.__name__}: {e}")

    print(f"\n{'═' * 50}")
    print(f"  结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 个测试")
    if errors:
        print(f"\n  失败详情:")
        for name, err in errors:
            print(f"    {name}: {err}")
    print(f"{'═' * 50}")

    sys.exit(1 if failed > 0 else 0)
