# -*- coding: utf-8 -*-
"""工程创建向导/模板测试。"""
from pathlib import Path

import pytest

import src.project as prj
from src import project_wizard as wiz


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(prj, "ACTIVE_PTR", tmp_path / "active.yaml")


def _make_template(tmp_path):
    tpl = tmp_path / "projects" / "_templates" / "demo1000"
    tpl.mkdir(parents=True)
    src = tmp_path / "base_script.txt"
    src.write_text("$YQ3 = CCS_660(65.5, 452.8, 0.6733)\n$P = $YQ3.PST\n", encoding="utf-8")
    (tpl / "template.yaml").write_text(
        "display: Demo 1000\n"
        "description: test template\n"
        "defaults:\n"
        "  display: LH3\n"
        "  capacity_mw: 1000\n"
        "  model_factory: CCS_1000\n"
        "script:\n"
        f"  source: {src.as_posix()}\n"
        "  replacements:\n"
        "    CCS_660: \"{model_factory}\"\n"
        "    \"$YQ3\": \"$LH3\"\n",
        encoding="utf-8",
    )


def test_list_templates_and_create_project(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_template(tmp_path)

    templates = wiz.list_templates()
    assert templates[0]["name"] == "demo1000"
    assert templates[0]["model_factory"] == "CCS_1000"

    result = wiz.create_project_from_template(
        name="lh3",
        template="demo1000",
        overwrite=False,
        activate=True,
    )
    assert result["ok"] is True
    assert prj.get_active() == "lh3"

    p = prj.paths("lh3")
    assert p.io_dir == Path("projects/lh3/io/simple")
    assert p.io_full_dir == Path("projects/lh3/io/filtered")
    assert (p.root / "io" / "raw" / ".gitkeep").exists()

    script = p.script.read_text(encoding="utf-8")
    assert "CCS_1000" in script
    assert "$LH3" in script
    assert "CCS_660" not in script


def test_create_existing_requires_overwrite(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_template(tmp_path)
    wiz.create_project_from_template(name="lh3", template="demo1000")
    with pytest.raises(FileExistsError):
        wiz.create_project_from_template(name="lh3", template="demo1000")

    result = wiz.create_project_from_template(name="lh3", template="demo1000", overwrite=True)
    assert result["name"] == "lh3"


def test_overwrite_preserves_script_and_endpoints_by_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_template(tmp_path)
    wiz.create_project_from_template(name="lh3", template="demo1000")
    p = prj.paths("lh3")
    p.script.write_text("# 已调好的 LH3 脚本\n", encoding="utf-8")
    p.endpoints.write_text("mode: vm\nlocal: opc.tcp://old:9440\nvm: opc.tcp://vm:9440\n",
                           encoding="utf-8")

    result = wiz.create_project_from_template(name="lh3", template="demo1000", overwrite=True)

    assert sorted(result["preserved"]) == ["opc_endpoints.yaml", "script.txt"]
    assert p.script.read_text(encoding="utf-8") == "# 已调好的 LH3 脚本\n"
    assert "mode: vm" in p.endpoints.read_text(encoding="utf-8")


def test_overwrite_can_explicitly_reset_script_and_endpoints(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _make_template(tmp_path)
    wiz.create_project_from_template(name="lh3", template="demo1000")
    p = prj.paths("lh3")
    p.script.write_text("# 已调好的 LH3 脚本\n", encoding="utf-8")
    p.endpoints.write_text("mode: vm\nlocal: opc.tcp://old:9440\nvm: opc.tcp://vm:9440\n",
                           encoding="utf-8")

    result = wiz.create_project_from_template(
        name="lh3",
        template="demo1000",
        overwrite=True,
        overwrite_script=True,
        overwrite_endpoints=True,
    )

    assert result["preserved"] == []
    assert "CCS_1000" in p.script.read_text(encoding="utf-8")
    assert "mode: local" in p.endpoints.read_text(encoding="utf-8")


def test_template_source_can_be_relative_to_template_root(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    tpl = tmp_path / "projects" / "_templates" / "relative1000"
    tpl.mkdir(parents=True)
    (tpl / "script.txt").write_text(
        "# {{display}}\n"
        "${{project_var}} = {{model_factory}}(1, 2, 3)\n",
        encoding="utf-8",
    )
    (tpl / "template.yaml").write_text(
        "display: Relative 1000\n"
        "defaults:\n"
        "  display: LH4\n"
        "  capacity_mw: 1000\n"
        "  model_factory: CCS_1000\n"
        "script:\n"
        "  source: script.txt\n",
        encoding="utf-8",
    )

    wiz.create_project_from_template(name="lh4", template="relative1000")

    script = prj.paths("lh4").script.read_text(encoding="utf-8")
    assert "# LH4" in script
    assert "$LH4 = CCS_1000(1, 2, 3)" in script
