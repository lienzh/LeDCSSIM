# -*- coding: utf-8 -*-
"""工程脚本诊断测试。"""
from pathlib import Path

import src.project as prj
import src.viewer.runtime as rt


def _setup_project(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(prj, "ACTIVE_PTR", tmp_path / "active.yaml")
    root = tmp_path / "projects" / "demo"
    io_dir = root / "io" / "simple"
    io_dir.mkdir(parents=True)
    (root / "project.yaml").write_text(
        "display: Demo\n"
        "model_factory: CCS_1000\n",
        encoding="utf-8",
    )
    (io_dir / "DPU3001_S.csv").write_bytes(
        (
            "#VERSION,1\n"
            "~索引,测点名称,描述\n"
            "1,HW.AI010101.PV,反馈\n"
            "2,HW.AQ010101.PV,指令\n"
            "3,HW.DI010101.PV,开反馈\n"
            "4,HW.DQ010101.PV,开指令\n"
        ).encode("gbk")
    )
    prj.set_active("demo")
    return prj.paths("demo")


def test_validate_project_script_detects_unsafe_lhs(tmp_path, monkeypatch):
    pp = _setup_project(tmp_path, monkeypatch)

    result = rt.validate_project_script(
        "DPU3001.AQ010101 = 1\n"
        "DPU3001.AI010101 = DPU3001.AQ010101\n",
        project_paths=pp,
    )

    assert result["ok"] is False
    assert any(e["type"] == "unsafe_lhs_type" for e in result["errors"])
    assert result["summary"]["lhs_codes"]["AQ"] == 1


def test_validate_project_script_accepts_feedback_lhs_and_model_factory(tmp_path, monkeypatch):
    pp = _setup_project(tmp_path, monkeypatch)

    result = rt.validate_project_script(
        "$m = CCS_1000(1, 1, 1)\n"
        "DPU3001.AI010101 = $m.NE\n"
        "DPU3001.DI010101 = DPU3001.DQ010101\n",
        project_paths=pp,
    )

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["summary"]["used_factories"] == ["CCS_1000"]
    usage = result["summary"]["point_usage"]
    assert usage["AI"]["lhs"] == 1
    assert usage["DQ"]["rhs"] == 1
    assert usage["AQ"]["unused"] == 1


def test_validate_project_script_reports_missing_points(tmp_path, monkeypatch):
    pp = _setup_project(tmp_path, monkeypatch)

    result = rt.validate_project_script(
        "DPU3001.AI999999 = DPU3001.AQ010101\n",
        project_paths=pp,
    )

    assert result["ok"] is False
    assert any(e["type"] == "missing_lhs_point" for e in result["errors"])


def test_merge_manual_model_blocks_preserves_marked_section():
    generated = "# 自动生成设备段\nDPU3001.AI010101 = DPU3001.AQ010101\n"
    current = (
        "# 旧设备段\n"
        f"{rt.MANUAL_MODEL_BEGIN}\n"
        "$m = CCS_1000(1, 1, 1)\n"
        "DPU3001.AI010101 = $m.NE\n"
        f"{rt.MANUAL_MODEL_END}\n"
    )

    merged = rt.merge_manual_model_blocks(generated, current)
    merged2 = rt.merge_manual_model_blocks(merged, current)

    assert "# 自动生成设备段" in merged
    assert "$m = CCS_1000" in merged
    assert merged.count(rt.MANUAL_MODEL_BEGIN) == 1
    assert merged2.count(rt.MANUAL_MODEL_BEGIN) == 1
