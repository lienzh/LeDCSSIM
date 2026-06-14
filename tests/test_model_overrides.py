# -*- coding: utf-8 -*-
"""工程级模型参数覆盖测试。"""

import src.project as prj
from src.models import dsl_registry as reg


def test_project_model_overrides_apply_to_factory_params(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(prj, "ACTIVE_PTR", tmp_path / "active.yaml")
    root = tmp_path / "projects" / "demo"
    root.mkdir(parents=True)
    prj.set_active("demo")

    reg.clear_param_cache()
    base = reg.get_base_params("CCS_660")
    assert base["dyn"]["c0"] != 777

    (root / "model_overrides.yaml").write_text(
        "CCS_660:\n"
        "  dyn:\n"
        "    c0: 777\n"
        "  energy:\n"
        "    eta: 9.5\n",
        encoding="utf-8",
    )
    reg.clear_param_cache()
    params = reg.get_factory_params("CCS_660")
    assert params["dyn"]["c0"] == 777
    assert params["energy"]["eta"] == 9.5
    assert params["dyn"]["c3"] == base["dyn"]["c3"]

    (root / "model_overrides.yaml").unlink()
    reg.clear_param_cache()
    restored = reg.get_factory_params("CCS_660")
    assert restored["dyn"]["c0"] == base["dyn"]["c0"]
