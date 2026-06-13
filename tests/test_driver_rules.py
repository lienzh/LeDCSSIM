# -*- coding: utf-8 -*-
"""DriverRules — 读 drivers/*.yaml + 设备匹配测试"""
import src.project as prj
from src.viewer.gen.rules import load_rules


def test_load_yq3_rules():
    rules = load_rules(prj.paths("yq3"))     # yq3 无工程级 drivers → 兜底 config/drivers
    assert "故障" in rules.vocab["fault"]
    assert "开 " in rules.vocab["start_cmd"]          # 尾空格词保住
    assert len(rules.devices) == 12
    assert "动叶" in rules.motor_exclude_common


def test_match_device_valve_before_motor():
    rules = load_rules(prj.paths("yq3"))
    # "送风机动叶" 含 "送风机"(motor)也含 "送风机动叶"(valve) → 必须先判 valve
    d = rules.match_device("A送风机动叶位置反馈")
    assert d["type"] == "valve" and d["name"] == "送风机动叶"
    # 纯电机
    m = rules.match_device("A给煤机运行")
    assert m["type"] == "motor" and m["name"] == "给煤机"


def test_match_device_motor_exclude():
    rules = load_rules(prj.paths("yq3"))
    # "给煤机润滑油泵" 命中给煤机 include, 但润滑油/油泵在共用排除 → 不算电机本体
    assert rules.match_device("A给煤机润滑油泵运行") is None


def test_match_device_none_for_unknown():
    rules = load_rules(prj.paths("yq3"))
    assert rules.match_device("主蒸汽压力") is None
