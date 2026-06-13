# -*- coding: utf-8 -*-
"""柜间段提供器 — csv 驱动 / 兜底注释"""
from src.viewer.gen.gateway import gateway_lines_from_csv


def test_gateway_lines_from_csv(tmp_path):
    csv = tmp_path / "gateway.csv"
    csv.write_text(
        "目标信号,来源,描述\n"
        "DPU3013.AI010101,DPU3044.AQ020202,MEH转速\n"
        "DPU3013.AI010102,50.0,固定偏置\n",
        encoding="utf-8",
    )
    lines = gateway_lines_from_csv(csv)
    assert "DPU3013.AI010101(MEH转速) = DPU3044.AQ020202" in lines
    assert "DPU3013.AI010102(固定偏置) = 50.0" in lines


def test_gateway_csv_missing_returns_empty(tmp_path):
    assert gateway_lines_from_csv(tmp_path / "nope.csv") == []
