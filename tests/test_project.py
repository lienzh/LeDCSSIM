# -*- coding: utf-8 -*-
"""src/project.py — 工程目录上下文测试 (pytest, 用 tmp_path 隔离)"""
from pathlib import Path

import src.project as prj


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(prj, "ACTIVE_PTR", tmp_path / "active.yaml")


def test_paths_layout_and_meta(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    root = tmp_path / "projects" / "demo"
    root.mkdir(parents=True)
    (root / "project.yaml").write_text(
        "display: 演示工程\nio_dir: SOME/IO\nio_full_dir: FULL/IO\n"
        "io_fallback_globs:\n  - OLD/DPU*.csv\n",
        encoding="utf-8")
    assert prj.list_projects() == ["demo"]
    assert prj.get_active() == "demo"      # 无指针 → 取第一个
    p = prj.paths()
    assert p.script == root / "script.txt"
    assert p.endpoints == root / "opc_endpoints.yaml"
    assert p.snapshot == root / "state" / "state_snapshot.json"
    assert p.snapshot_backups == root / "state" / "snapshot_backups"
    assert p.script_backups == root / "script_backups"
    assert p.generated_dir == root / "generated"
    assert p.io_dir == Path("SOME/IO")
    assert p.io_full_dir == Path("FULL/IO")
    assert p.io_fallback_globs == ["OLD/DPU*.csv"]
    assert p.display == "演示工程"


def test_io_dir_defaults_to_project_io(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "projects" / "bare").mkdir(parents=True)   # 无 project.yaml
    p = prj.paths("bare")
    assert p.io_dir == tmp_path / "projects" / "bare" / "io"
    assert p.io_full_dir == p.io_dir     # 未配置时 io_full_dir 跟随 io_dir
    assert p.io_glob == prj.DEFAULT_IO_GLOB
    assert p.display == "bare"


def test_set_active_and_underscore_excluded(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for n in ("aaa", "bbb", "_templates"):
        (tmp_path / "projects" / n).mkdir(parents=True)
    assert prj.list_projects() == ["aaa", "bbb"]   # _templates 不算工程
    prj.set_active("bbb")
    assert prj.get_active() == "bbb"
    try:
        prj.set_active("nope")
        assert False, "不存在的工程应当抛 ValueError"
    except ValueError:
        pass


def test_drivers_dir_project_first_then_config(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    root = tmp_path / "projects" / "demo"
    root.mkdir(parents=True)

    # 无工程级 drivers/ → 兜底 config/drivers
    p = prj.paths("demo")
    assert p.drivers_dir == Path("config/drivers")

    # 有工程级 drivers/ → 用工程自己的规则目录
    (root / "drivers").mkdir()
    p2 = prj.paths("demo")
    assert p2.drivers_dir == root / "drivers"
