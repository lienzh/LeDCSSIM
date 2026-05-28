# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.io_pairing_gen import load_points, is_soft, pair_analog, generate

# 一个最小点表：表头 + 1 个调节门(AQ指令+AI反馈) + 1 个软点(备用) + 1 个设定值
_CSV = """#VERSION,1,2026/5/28,4
~索引,测点名称,描述,设计编号,数据类型
1,HW.AQ010101.PV,暖管出口电动调整门指令,30HAG21AA1010,FLOAT
2,HW.AI010502.PV,暖管出口电动调整门位置,30HAG21AA1019,FLOAT
3,HW.AQ040502.PV,备用AQ-KM236A,30HAG21AA9999,FLOAT
4,HW.AQ020101.PV,主蒸汽压力设定#1,30CJA06DU001Q05,FLOAT
"""


def _write_gbk(tmp_path, text):
    fn = tmp_path / "DPU3013.csv"
    fn.write_bytes(text.encode("gbk"))
    return fn


def test_load_points_parses_hw_points(tmp_path):
    fn = _write_gbk(tmp_path, _CSV)
    pts = load_points(str(fn))
    names = {p["name"] for p in pts}
    assert "HW.AQ010101.PV" in names
    assert len(pts) == 4


def test_is_soft_filters_spare_and_setpoint():
    assert is_soft({"desc": "备用AQ-KM236A"}) is True
    assert is_soft({"desc": "主蒸汽压力设定#1"}) is True
    assert is_soft({"desc": "暖管出口电动调整门指令"}) is False


def test_pair_analog_matches_command_to_feedback_by_kks(tmp_path):
    fn = _write_gbk(tmp_path, _CSV)
    pts = load_points(str(fn))
    pairs = pair_analog(pts, "DPU3013")
    # 仅 30HAG21AA 设备应配对成功；软点/设定值被剔除；DU001 非设备模式
    assert len(pairs) == 1
    p = pairs[0]
    assert p["cmd"] == "HW.AQ010101.PV"
    assert p["fb"] == "HW.AI010502.PV"
    assert p["device"] == "30HAG21AA"
    assert p["template"] == "analog"
    assert p["transform"]["type"] == "inertia"
    assert p["online_writable"] is True


def test_generate_aggregates_directory(tmp_path):
    _write_gbk(tmp_path, _CSV)
    data = generate(str(tmp_path))
    assert "analog" in data
    assert len(data["analog"]) == 1
