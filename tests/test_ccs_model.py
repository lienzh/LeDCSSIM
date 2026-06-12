# -*- coding: utf-8 -*-
"""USC-OTBT 协调模型 (Fan 2021) 单元测试

跑法:
    py -3.12 -m tests.test_ccs_model
也支持 pytest:
    py -3.12 -m pytest tests/test_ccs_model.py -v
"""
import math
import sys
import traceback

from src.models.ccs_usc_otbt import CcsUscOtbt, load_params


PARAMS = load_params("config/ccs_models/usc-otbt-1000mw.yaml")


# ---------- 1. 参数文件加载 ----------

def test_params_load():
    """yaml 关键字段都到位"""
    assert "static_poly" in PARAMS
    assert "dyn" in PARAMS
    assert "steam" in PARAMS
    assert "seed" in PARAMS
    for name in ("hfw", "k1", "k2", "lam", "alpha"):
        coeffs = PARAMS["static_poly"][name]
        assert len(coeffs) == 4, f"{name} 应有 4 个三次多项式系数, 实际 {len(coeffs)}"


# ---------- 2. 模型实例化 + 初始稳态 ----------

def test_reset_to_seed():
    """reset() 后状态等于 yaml seed"""
    m = CcsUscOtbt(PARAMS)
    seed = PARAMS["seed"]
    assert abs(m.Ne - seed["Ne0"]) < 1e-6
    assert abs(m.rB - seed["uB0"]) < 1e-6
    assert abs(m.hm - seed["hm0"]) < 1e-6
    assert abs(m.pm - seed["pm0"]) < 1e-6


# ---------- 3. 稳态自洽 — 输入保持稳态 600s, 输出不发散 ----------

def test_steady_state_stability():
    """600.9MW THA 工况稳态输入跑 600 秒, Ne 不发散"""
    m = CcsUscOtbt(PARAMS)
    seed = PARAMS["seed"]
    dt = 0.2
    pst_init, hm_init, Ne_init = None, None, None
    for i in range(int(600 / dt)):
        pst, hm, Ne = m.step(seed["uB0"], seed["Dfw0"], seed["ut0"], dt)
        if i == 0:
            pst_init, hm_init, Ne_init = pst, hm, Ne
    # 论文模型本身静态拟合残差 (k1 R²=0.71, λ R²=0.88) 导致非完美稳态
    # 允许 600s 后 Ne 漂移 < 10% (实测 ~5.7%)
    assert math.isfinite(Ne), f"Ne 发散到非有限值: {Ne}"
    drift_pct = abs(Ne - Ne_init) / Ne_init * 100
    assert drift_pct < 10, f"600s 稳态漂移 {drift_pct:.1f}% > 10%"


# ---------- 4. 阶跃响应方向 ----------

def _settle_then_step(uB, Dfw, ut, T_step=300):
    """先稳 100s, 再阶跃跑 T_step 秒, 返回前后变化量"""
    m = CcsUscOtbt(PARAMS)
    seed = PARAMS["seed"]
    dt = 0.2
    for _ in range(int(100 / dt)):
        m.step(seed["uB0"], seed["Dfw0"], seed["ut0"], dt)
    pst0, hm0, Ne0 = m.step(seed["uB0"], seed["Dfw0"], seed["ut0"], dt)
    for _ in range(int(T_step / dt)):
        pst, hm, Ne = m.step(uB, Dfw, ut, dt)
    return (pst - pst0, hm - hm0, Ne - Ne0)


def test_step_uB_up():
    """煤量 ↑ → 主汽压、分离器焓、机组负荷 全升 (热量增加)"""
    dpst, dhm, dNe = _settle_then_step(75.5, 452.8, 0.6733)
    assert dpst > 0, f"煤量↑ pst 应升, 实际 {dpst:+.3f}"
    assert dhm > 0, f"煤量↑ hm 应升, 实际 {dhm:+.1f}"
    assert dNe > 0, f"煤量↑ Ne 应升, 实际 {dNe:+.2f}"


def test_step_Dfw_up_lowers_hm():
    """给水 ↑ → 分离器焓下降 (热量被更多工质稀释)"""
    dpst, dhm, dNe = _settle_then_step(65.5, 482.8, 0.6733)
    assert dhm < 0, f"给水↑ hm 应降, 实际 {dhm:+.1f}"


def test_step_ut_up_lowers_pst():
    """调门 ↑ → 主汽压下降 (流通增大, 压力放空)"""
    dpst, dhm, dNe = _settle_then_step(65.5, 452.8, 0.7733)
    assert dpst < 0, f"调门↑ pst 应降, 实际 {dpst:+.3f}"


# ---------- 5. 数值鲁棒性 — 极限输入不出 NaN ----------

def test_extreme_inputs_no_nan():
    """极大/极小输入跑 500 步, 状态保持 finite"""
    for label, (uB, Dfw, ut) in [
        ("max", (120.0, 50 + 16 * 120, 1.0)),
        ("min", (30.0, 50.0, 0.5)),
    ]:
        m = CcsUscOtbt(PARAMS)
        for _ in range(500):
            m.step(uB, Dfw, ut, 0.2)
        assert math.isfinite(m.pm) and math.isfinite(m.hm) \
            and math.isfinite(m.Ne) and math.isfinite(m.rB), \
            f"{label}: 状态出非有限值 pm={m.pm} hm={m.hm} Ne={m.Ne} rB={m.rB}"


# ---------- 6. 煤粉延迟 — τ=20s 后才看到输入影响 ----------

def test_coal_delay():
    """煤量阶跃后 rB 在 ~20s 内变化甚少 (τ=20s 纯延迟)"""
    m = CcsUscOtbt(PARAMS)
    seed = PARAMS["seed"]
    dt = 0.2
    # 先稳态种子
    rB_start = m.rB
    # uB 大幅阶跃, 跑 10 秒 (< τ)
    for _ in range(int(10 / dt)):
        m.step(100.0, 800.0, 0.7, dt)
    rB_after_10s = m.rB
    # τ=20s 之内, uB 还没流出延迟队列 → rB 仍在 seed 附近
    assert abs(rB_after_10s - rB_start) < 5.0, \
        f"10s < τ=20s 内 rB 不应有大变化, 实际 {rB_start:.2f} -> {rB_after_10s:.2f}"


# ---------- 测试运行器 (无 pytest 也能跑) ----------

def _run_all():
    tests = [obj for name, obj in globals().items()
             if name.startswith("test_") and callable(obj)]
    n_pass = n_fail = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            n_pass += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            n_fail += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            traceback.print_exc()
            n_fail += 1
    print(f"\n{n_pass} passed, {n_fail} failed (total {len(tests)})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
