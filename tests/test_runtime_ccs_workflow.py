# -*- coding: utf-8 -*-
"""viewer 上载/预演遇到 CCS 工厂模型时不能崩溃。"""

import src.viewer.runtime as rt


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
            if "AI010601" in node:
                vals.append(600.0)
            elif "AI010605" in node:
                vals.append(600.0)
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
