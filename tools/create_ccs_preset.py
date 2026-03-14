# -*- coding: utf-8 -*-
"""创建 CCS 被控对象模型预置组态"""
import json
import urllib.request

def node(nid, name, data, cls, html, inputs, outputs, px, py):
    return {
        "id": nid, "name": name, "data": data, "class": cls,
        "typenode": False, "html": html,
        "inputs": inputs, "outputs": outputs,
        "pos_x": px, "pos_y": py,
    }

def conn(target_node, target_port):
    return [{"node": str(target_node), "output": target_port}]

def conn_in(source_node, source_port):
    return [{"node": str(source_node), "input": source_port}]

def make_html(title, subtitle, color="#3b82f6"):
    return (f"<div><div class='node-title' style='border-left:3px solid {color};"
            f"padding-left:8px;'>{title}</div>"
            f"<div class='node-type'>{subtitle}</div></div>")

nodes = {}

# 输入信号
nodes["1"] = node(1, "input", {"name": "coal_flow", "default": 200}, "input",
    make_html("输入: coal_flow", "给煤量 (t/h) 默认=200", "#10b981"),
    {}, {"output_1": {"connections": conn(3, "input_1")}}, 30, 80)

nodes["2"] = node(2, "input", {"name": "valve_position", "default": 0.7}, "input",
    make_html("输入: valve_position", "调门开度 默认=0.7", "#10b981"),
    {}, {"output_1": {"connections": conn(8, "input_1")}}, 30, 330)

# 给煤惯性 T=60s
nodes["3"] = node(3, "Inertia", {"K": 1.0, "T": 60}, "Inertia",
    make_html("给煤惯性", "Inertia K=1 T=60s"),
    {"input_1": {"connections": conn_in(1, "output_1")}},
    {"output_1": {"connections": conn(4, "input_1")}}, 230, 80)

# K1 煤量热值系数
nodes["4"] = node(4, "gain", {"K": 2.4}, "gain",
    make_html("K1 煤量热值", "gain K=2.4", "#f59e0b"),
    {"input_1": {"connections": conn_in(3, "output_1")}},
    {"output_1": {"connections": conn(5, "input_1")}}, 430, 80)

# 能量平衡 (热输入 - 蒸汽带走)
nodes["5"] = node(5, "sum", {"signs": "+-"}, "sum",
    make_html("能量平衡 +/-", "热输入 - 蒸汽流量", "#f59e0b"),
    {"input_1": {"connections": conn_in(4, "output_1")},
     "input_2": {"connections": conn_in(9, "output_1")}},
    {"output_1": {"connections": conn(6, "input_1")}}, 620, 120)

# K3 锅炉蓄热积分
nodes["6"] = node(6, "Integrator", {"K": 0.00015, "low": 0, "high": 30}, "Integrator",
    make_html("K3 蓄热积分", "Integrator K=0.00015"),
    {"input_1": {"connections": conn_in(5, "output_1")}},
    {"output_1": {"connections": conn(7, "input_1")}}, 810, 120)

# 压力限幅 0-30 MPa
nodes["7"] = node(7, "Limiter", {"low": 0, "high": 30}, "Limiter",
    make_html("压力限幅", "Limiter 0~30 MPa", "#8b5cf6"),
    {"input_1": {"connections": conn_in(6, "output_1")}},
    {"output_1": {"connections": conn(11, "input_1") + conn(8, "input_2")}},
    1010, 120)

# 阀位 × 压力 (非线性交叉)
nodes["8"] = node(8, "multiply", {}, "multiply",
    make_html("阀位 x 压力", "非线性交叉耦合", "#f59e0b"),
    {"input_1": {"connections": conn_in(2, "output_1")},
     "input_2": {"connections": conn_in(7, "output_1")}},
    {"output_1": {"connections": conn(9, "input_1")}}, 280, 310)

# K2 蒸汽流量系数
nodes["9"] = node(9, "gain", {"K": 51.3}, "gain",
    make_html("K2 蒸汽流量", "gain K=51.3", "#f59e0b"),
    {"input_1": {"connections": conn_in(8, "output_1")}},
    {"output_1": {"connections": conn(5, "input_2") + conn(10, "input_1")}},
    480, 310)

# 功率响应惯性 T=15s
nodes["10"] = node(10, "Inertia", {"K": 1.0, "T": 15}, "Inertia",
    make_html("功率惯性", "Inertia K=1 T=15s"),
    {"input_1": {"connections": conn_in(9, "output_1")}},
    {"output_1": {"connections": conn(12, "input_1")}}, 680, 310)

# 输出: 主汽压力
nodes["11"] = node(11, "output", {"name": "main_steam_pressure", "default": 16.7}, "output",
    make_html("输出: main_steam_pressure", "主蒸汽压力 (MPa)"),
    {"input_1": {"connections": conn_in(7, "output_1")}},
    {}, 1200, 100)

# 输出: 功率
nodes["12"] = node(12, "output", {"name": "unit_power", "default": 600}, "output",
    make_html("输出: unit_power", "发电机功率 (MW)"),
    {"input_1": {"connections": conn_in(10, "output_1")}},
    {}, 880, 310)

drawflow = {"drawflow": {"Home": {"data": nodes}}}

payload = json.dumps({
    "name": "CCS_model",
    "drawflow": drawflow,
    "layer": "IB",
    "description": "CCS 被控对象模型 (2入2出): 煤量+调门 -> 主汽压力+功率"
}, ensure_ascii=False).encode("utf-8")

req = urllib.request.Request(
    "http://127.0.0.1:5001/api/model/save",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
resp = urllib.request.urlopen(req)
result = json.loads(resp.read())
print(f"CCS_model 创建成功: {result}")
print(f"节点数: {len(nodes)}")
for nid, n in sorted(nodes.items(), key=lambda x: int(x[0])):
    print(f"  #{nid:>2s} {n['name']:15s} ({n['pos_x']},{n['pos_y']})")
