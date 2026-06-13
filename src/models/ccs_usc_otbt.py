# -*- coding: utf-8 -*-
"""USC-OTBT 协调控制系统模型 (3 入 3 出)

实现自:
    Fan H., Su Z.-g., Wang P.-h., Lee K.Y.
    "A dynamic nonlinear model for a wide-load range operation of
     ultra-supercritical once-through boiler-turbine units"
    Energy 226 (2021) 120425

模型形态 (论文式 16):
    输入  U = [uB, Dfw, ut]    煤量指令 / 给水流量 / 调门开度
    输出  Y = [pst, hm, Ne]    主汽压力 / 分离器焓 / 机组负荷
    状态  X = [rB, pm, hm, Ne]

参数由 yaml 注入, 容量 preset 见 config/ccs_models/usc-otbt-660mw.yaml / usc-otbt-1000mw.yaml
"""
from __future__ import annotations

import math
from collections import deque
from typing import Tuple


class CcsUscOtbt:
    """USC-OTBT 协调模型 — 3 入 3 出, 4 状态, 显式 Euler + 煤粉纯延迟队列"""

    def __init__(self, params: dict):
        self.p = params
        # 煤粉纯延迟队列 (FIFO, 长度由 dt 在首次 step 时确定)
        self._delay_q: deque[float] = deque()
        self._delay_dt: float = 0.0
        # 4 状态量
        self.rB: float = 0.0
        self.pm: float = 0.0
        self.hm: float = 0.0
        self.Ne: float = 0.0
        # 上一步 ut, 计算 Δut 用 (节流损失)
        self._ut_prev: float = 0.0
        # 上次稳态种子参数(供 reset)
        self._seeded: bool = False
        self.reset()

    # ─── 静态参数: y = a·Ne³ + b·Ne² + c·Ne + d ──────────────
    def _poly(self, name: str, Ne: float) -> float:
        a, b, c, d = self.p["static_poly"][name]
        return ((a * Ne + b) * Ne + c) * Ne + d

    def hfw_of(self, Ne: float) -> float: return self._poly("hfw", Ne)
    def k1_of(self, Ne: float)  -> float: return self._poly("k1",  Ne)
    def k2_of(self, Ne: float)  -> float: return self._poly("k2",  Ne)
    def lam_of(self, Ne: float) -> float: return self._poly("lam", Ne)
    def alpha_of(self, Ne: float) -> float: return self._poly("alpha", Ne)

    # ─── 蒸汽热力性质 ──────────────────────────────────────────
    def _rho_m(self, pm: float, hm: float) -> float:
        s = self.p["steam"]["rho_m"]
        return s["a"] * pm * hm + s["b"] * hm + s["c"] * pm + s["d"]

    def _drho_dpm(self, hm: float) -> float:
        s = self.p["steam"]["rho_m"]
        return s["a"] * hm + s["c"]

    def _drho_dhm(self, pm: float) -> float:
        s = self.p["steam"]["rho_m"]
        return s["a"] * pm + s["b"]

    def _dTm_dhm(self, pm: float) -> float:
        s = self.p["steam"]["T_m_dh"]
        return s["a"] * pm + s["b"]

    def _dTm_dpm(self, hm: float) -> float:
        s = self.p["steam"]["T_m_dp"]
        return s["a"] * hm + s["b"]

    def _delta_p(self, pm: float) -> float:
        """主汽压降 Δp = a·pm + b → pst = pm - Δp"""
        s = self.p["steam"]["dp"]
        return s["a"] * pm + s["b"]

    def _f_pst_hst(self, pst: float, hst: float) -> float:
        """Dst = ut · f(pst, hst), 这里只算 f = a·pst / (hst + b)"""
        s = self.p["steam"]["Dst"]
        denom = hst + s["b"]
        if denom <= 0:
            return 0.0
        return s["a"] * pst / denom

    # ─── 状态初始化 (论文 Table 2 的 THA 工况附近) ──────────────
    def reset(self) -> None:
        seed = self.p["seed"]
        self.Ne = seed["Ne0"]
        self.rB = seed["uB0"]      # 稳态时 rB = uB
        self.hm = seed["hm0"]
        self.pm = seed["pm0"]
        self._ut_prev = seed["ut0"]
        self._delay_q.clear()
        self._delay_dt = 0.0
        self._seeded = True

    # ─── 一步积分 (显式 Euler) ────────────────────────────────
    def step(self, uB: float, Dfw: float, ut: float, dt: float) -> Tuple[float, float, float]:
        """推进 dt 秒, 返回 (pst, hm, Ne)
        显式 Euler; viewer dt=200ms vs 最短时间常数 c3=40s → 比 1:200, 数值稳定."""
        if dt <= 0:
            return self._outputs()
        # 重设延迟队列长度 (dt 变化时)
        tau = float(self.p["dyn"]["tau"])
        n_delay = max(1, int(math.ceil(tau / dt)))
        if self._delay_dt != dt or len(self._delay_q) == 0:
            self._delay_dt = dt
            # 用稳态值预填
            self._delay_q = deque([self.rB] * n_delay)

        # FIFO: append 当前 uB, popleft 拿"τ 秒前"的 uB
        self._delay_q.append(uB)
        if len(self._delay_q) > n_delay:
            uB_delayed = self._delay_q.popleft()
        else:
            uB_delayed = self._delay_q[0]

        # ── 状态量本地别名 ────────────────────────────────
        rB = self.rB
        pm = self.pm
        hm = self.hm
        Ne = self.Ne
        ut_prev = self._ut_prev

        # ── 静态参数(随 Ne 变化) ──────────────────────────
        hfw = self.hfw_of(Ne)
        k1  = self.k1_of(Ne)
        k2  = self.k2_of(Ne)
        lam = self.lam_of(Ne)
        alpha = self.alpha_of(Ne)

        # ── 中间量 ────────────────────────────────────────
        dp = self._delta_p(pm)
        pst = pm - dp
        hst = lam * hm
        f_val = self._f_pst_hst(pst, hst)
        Dst = ut * f_val                                   # 论文 (13)

        # 论文 (4): Q1 = k1·rB + μ/(Ne+γ)·(Dfw - α·rB)
        e = self.p["energy"]
        Q1 = k1 * rB + e["mu"] / (Ne + e["gamma"]) * (Dfw - alpha * rB)

        # 论文 c1/c2/d1/d2 — 由热力性质偏导给出 (论文 5.3 节展开)
        rho = self._rho_m(pm, hm)
        drho_dp = self._drho_dpm(hm)
        drho_dh = self._drho_dhm(pm)
        dT_dh   = self._dTm_dhm(pm)
        dT_dp   = self._dTm_dpm(hm)
        dyn = self.p["dyn"]
        Vm, cj, mj = dyn["Vm"], dyn["cj"], dyn["mj"]

        # b11 = Vm·∂ρ/∂p; b12 = Vm·∂ρ/∂h
        # b21 = Vm·hm·∂ρ/∂p + cj·mj·∂T/∂p
        # b22 = Vm·(hm·∂ρ/∂h + ρ) + cj·mj·∂T/∂h
        b11 = Vm * drho_dp
        b12 = Vm * drho_dh
        b21 = Vm * hm * drho_dp + cj * mj * dT_dp
        b22 = Vm * (hm * drho_dh + rho) + cj * mj * dT_dh

        # 论文 (11): c1 = b21 - b11·b22/b12; c2 = b22 - b12·b21/b11
        #          d1 = b22/b12;             d2 = b21/b11
        # 这里 b11, b12 都是分母, 防 0
        if abs(b11) < 1e-9 or abs(b12) < 1e-9:
            # 数值病态: 用上一步状态, 不积分这一步
            self._ut_prev = ut
            return self._outputs()
        c1 = b21 - b11 * b22 / b12
        c2 = b22 - b12 * b21 / b11
        d1 = b22 / b12
        d2 = b21 / b11

        # ── 节流损失 ΔQloss = η·Δut·pst ────────────────────
        # Δut 取"对上一步差分", 稳态时自动 0, 不影响稳态负荷
        # (论文 5.4 节描述 throttle loss 仅影响动态响应)
        d_ut = ut - ut_prev
        DQloss = e["eta"] * d_ut * pst

        # ── 4 个 ODE (显式 Euler) ─────────────────────────
        # 论文 (16) 完整状态方程
        c0 = dyn["c0"]
        c3 = dyn["c3"]

        # ẋ1 = -rB/c0 + uB(t-τ)/c0
        drB_dt = (-rB + uB_delayed) / c0

        # 论文 (9)(10): c1·dpm/dt = (hfw-d1)·Dfw + (d1-hst)·Dst + Q1
        #              c2·dhm/dt = (hfw-d2)·Dfw + (d2-hst)·Dst + Q1
        # 注: 论文式 16 写的是 "ẋ2 = (hfw-d1)/c1·u2 + ..." (u2=Dfw), 形式一致
        if abs(c1) < 1e-9 or abs(c2) < 1e-9:
            self._ut_prev = ut
            return self._outputs()
        dpm_dt = ((hfw - d1) * Dfw + (d1 - hst) * Dst + Q1) / c1
        dhm_dt = ((hfw - d2) * Dfw + (d2 - hst) * Dst + Q1) / c2

        # 论文 (15): dNe/dt = (k2·(hst-hfw)·Dst + ΔQloss)/c3 - Ne/c3
        dNe_dt = (k2 * (hst - hfw) * Dst + DQloss) / c3 - Ne / c3

        # ── Euler 推进 ────────────────────────────────────
        self.rB = rB + dt * drB_dt
        self.pm = pm + dt * dpm_dt
        self.hm = hm + dt * dhm_dt
        self.Ne = Ne + dt * dNe_dt
        self._ut_prev = ut

        # 状态数值保护(避免发散后传染)
        if not (math.isfinite(self.rB) and math.isfinite(self.pm)
                and math.isfinite(self.hm) and math.isfinite(self.Ne)):
            # 退回种子状态, 防止 NaN 持续传播
            self.reset()

        return self._outputs()

    def _outputs(self) -> Tuple[float, float, float]:
        """输出 (pst, hm, Ne) — pst = pm - g(pm)"""
        pst = self.pm - self._delta_p(self.pm)
        return (pst, self.hm, self.Ne)

    # ─── 序列化 (供 viewer 镜像保存/恢复) ─────────────────────
    def get_state(self) -> dict:
        return {
            "rB": self.rB, "pm": self.pm, "hm": self.hm, "Ne": self.Ne,
            "ut_prev": self._ut_prev,
            "delay_q": list(self._delay_q),
            "delay_dt": self._delay_dt,
        }

    def set_state(self, st: dict) -> None:
        self.rB = float(st.get("rB", self.rB))
        self.pm = float(st.get("pm", self.pm))
        self.hm = float(st.get("hm", self.hm))
        self.Ne = float(st.get("Ne", self.Ne))
        self._ut_prev = float(st.get("ut_prev", self._ut_prev))
        q = st.get("delay_q") or []
        self._delay_q = deque([float(x) for x in q])
        self._delay_dt = float(st.get("delay_dt", 0.0))


# ─── yaml 加载 ──────────────────────────────────────────────
def load_params(yaml_path: str) -> dict:
    """读 yaml 参数文件, 返回 dict (供 CcsUscOtbt(params=...))"""
    import yaml
    from pathlib import Path
    p = Path(yaml_path)
    if not p.exists():
        raise FileNotFoundError(f"CCS 模型参数文件不存在: {yaml_path}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
