# -*- coding: utf-8 -*-
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.engine import SimEngine
from src.sim_engine.graph_runner import GraphRunner
from src.sim_engine.pairing_runner import PairingRunner


def _empty_graph():
    graph = GraphRunner()
    graph.load({"drawflow": {"Home": {"data": {}}}})
    return graph


def test_offline_run_merges_pairing_feedback_into_recorder():
    pairing = PairingRunner()
    pairing.load_dict({"analog": [
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV", "transform": {"type": "direct"}},
    ]})

    engine = SimEngine(_empty_graph(), step_size=0.2, pairing_runner=pairing)
    asyncio.run(engine.run_offline(duration=0.4,
                                   initial_inputs={"HW.AQ01.PV": 73.0}))

    # 反馈点应出现在记录列中，最后值等于指令（direct）
    assert "HW.AI01.PV" in engine.recorder.columns
    _, values = engine.recorder.get_series("HW.AI01.PV")
    assert values[-1] == 73.0


def test_engine_without_pairing_still_runs():
    engine = SimEngine(_empty_graph(), step_size=0.2)  # 不传 pairing
    asyncio.run(engine.run_offline(duration=0.4, initial_inputs={}))
    assert engine.step_count >= 1
