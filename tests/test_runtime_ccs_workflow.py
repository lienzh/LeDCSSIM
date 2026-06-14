# -*- coding: utf-8 -*-
"""viewer 上载/预演遇到 CCS 工厂模型时不能崩溃。"""

import src.viewer.runtime as rt
from src.models.steam import steam_h_from_Tp


class _FakeOPCClient:
    def __init__(self, url: str):
        self.url = url

    async def connect(self, retry_count=0, retry_interval=0.0):
        return None

    async def disconnect(self):
        return None

    async def read_values(self, nodes):
        vals = []
        for node in nodes:
            if "DI060501" in node:
                vals.append(True)
            elif "DI060502" in node:
                vals.append(False)
            elif "AI010601" in node:
                vals.append(19.0)
            elif "AI010605" in node:
                vals.append(425.0)
            elif "TC040203" in node:
                vals.append(420.0)
            elif ".DQ" in node:
                vals.append(False)
            else:
                vals.append(1.0)
        return vals


def test_upload_and_preview_support_ccs_factory(monkeypatch):
    """CCS_660 在上载刷新/预演干运行里需要 ccs_state 临时副本。"""
    monkeypatch.setattr(rt, "OPCClient", _FakeOPCClient)
    s = rt._STATE
    old = {
        "pairs": s.pairs,
        "running": s.running,
        "lag_state": s.lag_state,
        "rs_state": s.rs_state,
        "intermediates": s.intermediates,
        "ccs_state": s.ccs_state,
        "last_written": s.last_written,
        "last_read": s.last_read,
        "last_values": s.last_values,
        "cycle_count": s.cycle_count,
    }
    try:
        s.running = False
        s.pairs = rt.parse_script(
            "$YQ3(协调模型) = CCS_660(65.5, 452.8, 0.6733)\n"
            "DPU3013.AI010601(主蒸汽压力1) = LAG($YQ3.NE, 10)\n"
            "DPU3013.AI010605(发电机功率1) = $YQ3.NE\n"
        )
        s.lag_state = {}
        s.rs_state = {}
        s.intermediates = {}
        s.ccs_state = {}
        s.last_written = {}
        s.last_read = {}
        s.last_values = {}
        s.cycle_count = 10

        upload = rt.reinit_lag_from_dcs("opc.tcp://fake:0")
        assert upload["ok"], upload
        assert upload["synced_lag"] == 1
        assert "$YQ3" in s.intermediates

        preview = rt.dryrun_preview("opc.tcp://fake:0")
        assert preview["ok"], preview
        assert preview["summary"]["total"] == 2
        assert any("AI010605" in item["lhs"] for item in preview["items"])
    finally:
        s.pairs = old["pairs"]
        s.running = old["running"]
        s.lag_state = old["lag_state"]
        s.rs_state = old["rs_state"]
        s.intermediates = old["intermediates"]
        s.ccs_state = old["ccs_state"]
        s.last_written = old["last_written"]
        s.last_read = old["last_read"]
        s.last_values = old["last_values"]
        s.cycle_count = old["cycle_count"]


def test_upload_anchors_ccs_outputs_to_dcs_actual(monkeypatch):
    """上载应把 CCS 模型的 NE/PST 状态锚到项目实际功率/压力。"""
    monkeypatch.setattr(rt, "OPCClient", _FakeOPCClient)
    s = rt._STATE
    old = {
        "pairs": s.pairs,
        "running": s.running,
        "lag_state": s.lag_state,
        "rs_state": s.rs_state,
        "intermediates": s.intermediates,
        "ccs_state": s.ccs_state,
        "last_written": s.last_written,
        "last_read": s.last_read,
        "last_values": s.last_values,
        "cycle_count": s.cycle_count,
    }
    try:
        s.running = False
        s.pairs = rt.parse_script(
            "$YQ3(协调模型) = CCS_660(44.6, 351.8, 0.51)\n"
            "$SIM_PT = $YQ3.PST\n"
            "$SIM_MW = $YQ3.NE\n"
            "DPU3013.AI010601(主蒸汽压力1) = $SIM_PT\n"
            "DPU3013.AI010605(发电机功率1) = $SIM_MW\n"
        )
        s.lag_state = {}
        s.rs_state = {}
        s.intermediates = {}
        s.ccs_state = {}
        s.last_written = {}
        s.last_read = {}
        s.last_values = {}
        s.cycle_count = 30

        upload = rt.reinit_lag_from_dcs("opc.tcp://fake:0")
        assert upload["ok"], upload
        assert upload["synced_ccs"] == 1
        handle = next(iter(s.ccs_state.values()))
        ccs_state = handle.model.get_state()
        assert abs(ccs_state["rB"] * 3.6 - 161.14545454545456) < 1e-6
        assert ccs_state["delay_q"] == []
        assert handle.hold_once is True

        preview = rt.dryrun_preview("opc.tcp://fake:0")
        assert preview["ok"], preview
        by_lhs = {item["lhs_short"]: item for item in preview["items"]}
        assert by_lhs["DPU3013.AI010605"]["computed_num"] == 425.0
        assert by_lhs["DPU3013.AI010601"]["computed_num"] == 19.0
    finally:
        s.pairs = old["pairs"]
        s.running = old["running"]
        s.lag_state = old["lag_state"]
        s.rs_state = old["rs_state"]
        s.intermediates = old["intermediates"]
        s.ccs_state = old["ccs_state"]
        s.last_written = old["last_written"]
        s.last_read = old["last_read"]
        s.last_values = old["last_values"]
        s.cycle_count = old["cycle_count"]


def test_upload_infers_ccs_hm_from_separator_temperature(monkeypatch):
    """上载应能用分离器出口温度 + 已锚定压力反算 CCS HM。"""
    monkeypatch.setattr(rt, "OPCClient", _FakeOPCClient)
    s = rt._STATE
    old = {
        "pairs": s.pairs,
        "running": s.running,
        "lag_state": s.lag_state,
        "rs_state": s.rs_state,
        "intermediates": s.intermediates,
        "ccs_state": s.ccs_state,
        "last_written": s.last_written,
        "last_read": s.last_read,
        "last_values": s.last_values,
        "cycle_count": s.cycle_count,
    }
    try:
        s.running = False
        s.pairs = rt.parse_script(
            "$YQ3(协调模型) = CCS_660(44.6, 351.8, 0.51)\n"
            "$SIM_PT = $YQ3.PST\n"
            "$SIM_MW = $YQ3.NE\n"
            "$SIM_Hm = $YQ3.HM\n"
            "$T_sep(分离器温度) = STEAM_T($SIM_Hm, $SIM_PT+1)\n"
            "DPU3013.AI010601(主蒸汽压力1) = $SIM_PT\n"
            "DPU3013.AI010605(发电机功率1) = $SIM_MW\n"
            "DPU3013.TC040203(A分离器出口蒸汽温度1) = LAG($T_sep, 120)\n"
        )
        s.lag_state = {}
        s.rs_state = {}
        s.intermediates = {}
        s.ccs_state = {}
        s.last_written = {}
        s.last_read = {}
        s.last_values = {}
        s.cycle_count = 30

        upload = rt.reinit_lag_from_dcs("opc.tcp://fake:0")
        assert upload["ok"], upload
        assert upload["synced_ccs"] == 1
        assert upload["synced_ccs_hm_from_temp"] == 1
        handle = next(iter(s.ccs_state.values()))
        expected_hm = steam_h_from_Tp(420.0, 20.0)
        assert expected_hm is not None
        assert abs(handle.model.get_state()["hm"] - expected_hm) < 1e-6

        preview = rt.dryrun_preview("opc.tcp://fake:0")
        assert preview["ok"], preview
        by_lhs = {item["lhs_short"]: item for item in preview["items"]}
        assert by_lhs["DPU3013.TC040203"]["computed_num"] == 420.0
    finally:
        s.pairs = old["pairs"]
        s.running = old["running"]
        s.lag_state = old["lag_state"]
        s.rs_state = old["rs_state"]
        s.intermediates = old["intermediates"]
        s.ccs_state = old["ccs_state"]
        s.last_written = old["last_written"]
        s.last_read = old["last_read"]
        s.last_values = old["last_values"]
        s.cycle_count = old["cycle_count"]


def test_upload_anchors_rs_hidden_behind_var(monkeypatch):
    """合位=$Mill_A、跳位=NOT($Mill_A) 时, 上载要反算 $Mill_A 背后的 RS。"""
    monkeypatch.setattr(rt, "OPCClient", _FakeOPCClient)
    s = rt._STATE
    old = {
        "pairs": s.pairs,
        "running": s.running,
        "lag_state": s.lag_state,
        "rs_state": s.rs_state,
        "intermediates": s.intermediates,
        "ccs_state": s.ccs_state,
        "last_written": s.last_written,
        "last_read": s.last_read,
        "last_values": s.last_values,
        "cycle_count": s.cycle_count,
    }
    try:
        s.running = False
        s.pairs = rt.parse_script(
            "$Mill_A = RS(DPU3002.DQ060213(A磨煤机合闸命令), "
            "DPU3002.DQ060214(A磨煤机跳闸命令))\n"
            "DPU3002.DI060501(A磨煤机合位) = $Mill_A\n"
            "DPU3002.DI060502(A磨煤机跳位1) = NOT($Mill_A)\n"
        )
        s.lag_state = {}
        s.rs_state = {}
        s.intermediates = {}
        s.ccs_state = {}
        s.last_written = {}
        s.last_read = {}
        s.last_values = {}
        s.cycle_count = 20

        upload = rt.reinit_lag_from_dcs("opc.tcp://fake:0")
        assert upload["ok"], upload
        assert upload["synced_rs"] == 1

        preview = rt.dryrun_preview("opc.tcp://fake:0")
        assert preview["ok"], preview
        by_lhs = {item["lhs_short"]: item for item in preview["items"]}
        assert by_lhs["DPU3002.DI060501"]["computed"] is True
        assert by_lhs["DPU3002.DI060501"]["actual"] is True
        assert by_lhs["DPU3002.DI060501"]["risk"] == "ok"
        assert by_lhs["DPU3002.DI060502"]["computed"] is False
        assert by_lhs["DPU3002.DI060502"]["actual"] is False
        assert by_lhs["DPU3002.DI060502"]["risk"] == "ok"
    finally:
        s.pairs = old["pairs"]
        s.running = old["running"]
        s.lag_state = old["lag_state"]
        s.rs_state = old["rs_state"]
        s.intermediates = old["intermediates"]
        s.ccs_state = old["ccs_state"]
        s.last_written = old["last_written"]
        s.last_read = old["last_read"]
        s.last_values = old["last_values"]
        s.cycle_count = old["cycle_count"]
