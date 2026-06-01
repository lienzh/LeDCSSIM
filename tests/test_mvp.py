# -*- coding: utf-8 -*-
"""
MVP 阶段 A 验收测试

覆盖:
1. Block 单元 - CON / DirectThrough / FirstOrder 数学正确性
2. GraphRunner 拓扑 - 正常+代数环检测
3. 端到端离线 - 跑 30s 阶跃,检查最终值收敛

可用纯 stdlib 跑(不依赖 pytest),直接执行:
    py -3.12 -m tests.test_mvp
也可用 pytest:
    py -3.12 -m pytest tests/test_mvp.py -v
"""
import math
import sys
import tempfile
import traceback
from pathlib import Path

from src.models import CON, DirectThrough, FirstOrder
from src.engine import GraphRunner, AlgebraicLoopError, DataRecorder


# ---------- 1. Block 单元 ----------

def test_con():
    b = CON("c", {"value": 42.0})
    out = b.step({}, 0.2)
    assert out == {"out": 42.0}, out


def test_directthrough_no_lag():
    b = DirectThrough("d", {"T": 0.0})
    assert b.step({"in": 10.0}, 0.2) == {"out": 10.0}
    assert b.step({"in": -5.0}, 0.2) == {"out": -5.0}


def test_directthrough_with_lag():
    """T=1, dt=0.2, 阶跃从 0→1, alpha = 0.2/1.2 ≈ 0.1667"""
    b = DirectThrough("d", {"T": 1.0})
    b.reset(0.0)
    out = b.step({"in": 1.0}, 0.2)["out"]
    expected = 0.0 + (0.2 / 1.2) * (1.0 - 0.0)
    assert math.isclose(out, expected, rel_tol=1e-9), (out, expected)


def test_firstorder_step_response():
    """
    FirstOrder K=1, T=5, dt=0.2, 阶跃到 100
    - 5s 时 ≈ 100*(1 - e^-1) ≈ 63.2
    - 25s(5τ)时 > 99
    """
    b = FirstOrder("f", {"K": 1.0, "T": 5.0})
    b.reset(0.0)
    dt = 0.2
    t = 0.0
    y = 0.0
    while t < 25.0:
        y = b.step({"in": 100.0}, dt)["out"]
        t += dt
        if math.isclose(t, 5.0, abs_tol=dt / 2):
            assert 60 < y < 67, f"5s 时值应在 60-67 范围,实际 {y:.3f}"
    assert y > 99, f"25s(5τ)时值应 > 99,实际 {y:.3f}"


def test_firstorder_zero_T():
    """T <= 0 退化为纯比例 y = K*x"""
    b = FirstOrder("f", {"K": 2.0, "T": 0.0})
    out = b.step({"in": 3.0}, 0.2)["out"]
    assert out == 6.0, out


# ---------- 2. GraphRunner 拓扑 ----------

def _build_simple_chain():
    """CON(50) → DirectThrough → 拿到 50;同时 CON → FirstOrder"""
    blocks = {
        "src": CON("src", {"value": 50.0}),
        "fb": DirectThrough("fb", {"T": 0.0}),
        "flow": FirstOrder("flow", {"K": 1.0, "T": 5.0}),
    }
    conns = [
        ("src", "out", "fb", "in"),
        ("src", "out", "flow", "in"),
    ]
    return blocks, conns


def test_topo_sort_ok():
    blocks, conns = _build_simple_chain()
    r = GraphRunner(blocks, conns, dt=0.2)
    # src 必须在 fb 和 flow 之前
    assert r.order.index("src") < r.order.index("fb")
    assert r.order.index("src") < r.order.index("flow")


def test_topo_sort_step_executes():
    blocks, conns = _build_simple_chain()
    r = GraphRunner(blocks, conns, dt=0.2)
    snap = r.step_once()
    assert snap["src.out"] == 50.0
    assert snap["fb.out"] == 50.0
    # flow 一阶惯性第一步 y = 50 * 0.2/(5+0.2) ≈ 1.923
    assert 1.8 < snap["flow.out"] < 2.0, snap["flow.out"]


def test_algebraic_loop_detected():
    """两个 DirectThrough 互连 = 纯代数环,应报错"""
    blocks = {
        "a": DirectThrough("a", {"T": 0.0}),
        "b": DirectThrough("b", {"T": 0.0}),
    }
    conns = [
        ("a", "out", "b", "in"),
        ("b", "out", "a", "in"),
    ]
    try:
        GraphRunner(blocks, conns, dt=0.2)
    except AlgebraicLoopError as e:
        assert "代数环" in str(e)
        return
    raise AssertionError("应抛 AlgebraicLoopError")


def test_stateful_block_breaks_loop():
    """FirstOrder 作为有状态块,可以打破环路 — 不报错"""
    blocks = {
        "a": DirectThrough("a", {"T": 0.0}),
        "b": FirstOrder("b", {"K": 1.0, "T": 1.0}),
    }
    conns = [
        ("a", "out", "b", "in"),
        ("b", "out", "a", "in"),
    ]
    r = GraphRunner(blocks, conns, dt=0.2)
    # 跑几步不应崩
    for _ in range(10):
        r.step_once()


# ---------- 3. 端到端离线(读 YAML + 跑 30s + 检查 CSV)----------

def test_end_to_end_offline():
    """从 config/models.yaml + connections.yaml 加载,跑 30s,验证收敛"""
    rec = DataRecorder()
    r = GraphRunner.from_yaml(
        "config/models.yaml",
        "config/connections.yaml",
        dt=0.2,
        recorder=rec,
    )
    n_steps = int(30.0 / 0.2)
    for _ in range(n_steps):
        r.step_once()

    # 最终值检查
    final = r.get_output("flow_model", "out")
    assert final > 49.0, f"流量 30s 后应接近 50,实际 {final:.3f}"
    final_fb = r.get_output("valve_fb", "out")
    assert final_fb == 50.0, f"阀门反馈应等于指令,实际 {final_fb}"

    # CSV 导出可工作
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = str(Path(tmp) / "out.csv")
        rec.to_csv(csv_path)
        content = Path(csv_path).read_text(encoding="utf-8-sig")
        assert "flow_model.out" in content
        assert "valve_fb.out" in content


# ---------- 测试运行器 ----------

def _run_all():
    tests = [obj for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]
    n_pass = 0
    n_fail = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            n_pass += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            n_fail += 1
    print(f"\n{n_pass} passed, {n_fail} failed (total {len(tests)})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
