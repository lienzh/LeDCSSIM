# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.pairing_runner import PairingRunner


def _runner_with(analog):
    r = PairingRunner()
    r.load_dict({"analog": analog})
    return r


def test_direct_transform_assigns_command_to_feedback():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "direct"}},
    ])
    out = r.step({"HW.AQ01.PV": 42.0}, dt=0.2)
    assert out == {"HW.AI01.PV": 42.0}


def test_scale_transform_applies_gain_and_bias():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "scale", "gain": 2.0, "bias": 5.0}},
    ])
    out = r.step({"HW.AQ01.PV": 10.0}, dt=0.2)
    assert out["HW.AI01.PV"] == 25.0


def test_inertia_transform_lags_toward_command():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "inertia", "T": 2.0}},
    ])
    # 阶跃到 100，一步后应在 0 和 100 之间（未到稳态）
    out1 = r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    v1 = out1["HW.AI01.PV"]
    assert 0.0 < v1 < 100.0
    # 继续逼近，第二步应更接近 100
    out2 = r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    assert out2["HW.AI01.PV"] > v1


def test_missing_command_defaults_to_zero():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "direct"}},
    ])
    out = r.step({}, dt=0.2)
    assert out["HW.AI01.PV"] == 0.0


def test_command_and_feedback_tag_lists():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV", "transform": {"type": "direct"}},
        {"cmd": "HW.AQ02.PV", "fb": "HW.AI02.PV", "transform": {"type": "direct"}},
    ])
    assert r.get_command_tags() == ["HW.AQ01.PV", "HW.AQ02.PV"]
    assert r.get_feedback_tags() == ["HW.AI01.PV", "HW.AI02.PV"]


def test_reset_clears_inertia_state():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "inertia", "T": 2.0}},
    ])
    r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    r.reset()
    out = r.step({"HW.AQ01.PV": 0.0}, dt=0.2)
    assert out["HW.AI01.PV"] == 0.0
