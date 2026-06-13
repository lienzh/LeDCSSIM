# -*- coding: utf-8 -*-
"""DSL 模型工厂注册表测试 — 注册表驱动 FUNC_ARITY / _eval_rhs"""
from src.models.dsl_registry import MODEL_FACTORIES, get_factory_params
import src.viewer.runtime as rt


def test_registry_has_ccs_1000():
    spec = MODEL_FACTORIES["CCS_1000"]
    assert spec.arity == 3
    assert spec.pins == ("PST", "HM", "NE")


def test_registry_has_ccs_660():
    spec = MODEL_FACTORIES["CCS_660"]
    assert spec.arity == 3
    assert spec.pins == ("PST", "HM", "NE")
    assert spec.params_path.endswith("usc-otbt-660mw.yaml")


def test_func_arity_merged_from_registry():
    """FUNC_ARITY 必须从注册表合并 — 加新工厂不改 runtime"""
    for name, spec in MODEL_FACTORIES.items():
        assert rt.FUNC_ARITY.get(name) == spec.arity
        assert name in rt.SUPPORTED_FUNCS


def test_eval_ccs_1000_via_registry():
    """$m = CCS_1000(uB, Dfw, ut) 返回把柄, 管脚齐全且只积分一次"""
    s = rt._State()
    s.cycle_count = 1
    rhs = ("CCS_1000", [65.5, 452.8, 0.6733])
    handle = rt._eval_rhs(rhs, {}, s, dt=0.2)
    assert set(handle.outputs) == {"PST", "HM", "NE"}
    assert handle.outputs["NE"] > 0
    # 同周期再算一次 → 不重复积分 (last_cycle 去重)
    h2 = rt._eval_rhs(rhs, {}, s, dt=0.2)
    assert h2 is handle


def test_eval_ccs_660_via_registry():
    """$m = CCS_660(uB, Dfw, ut) 走注册表实例化"""
    s = rt._State()
    s.cycle_count = 1
    rhs = ("CCS_660", [65.5, 452.8, 0.6733])
    handle = rt._eval_rhs(rhs, {}, s, dt=0.2)
    assert set(handle.outputs) == {"PST", "HM", "NE"}
    assert handle.outputs["NE"] > 0


def test_unknown_factory_params_returns_none():
    assert get_factory_params("CCS_NOPE") is None
