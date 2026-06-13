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
from typing import Optional, Tuple


def _as_points(points) -> list[tuple[float, float]]:
    """YAML 点列 → 按 x 升序的 float 点列。"""
    return sorted((float(x), float(y)) for x, y in points)


def interp_y(x: float, points) -> float:
    """分段线性插值: 用 x 查 y, 端点外钳位。"""
    pts = _as_points(points)
    if not pts:
        raise ValueError("插值点列为空")
    x = float(x)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def interp_x(y: float, points) -> float:
    """反向分段线性插值: 用 y 查 x, 端点外钳位。"""
    pts = sorted((float(y0), float(x0)) for x0, y0 in points)
    return interp_y(float(y), pts)


class CcsUscOtbt:
    """USC-OTBT 协调模型 — 3 入 3 出, 4 状态, 显式 Euler + 煤粉纯延迟队列"""

    def __init__(self, params: dict):
        self.p = params
        self._curves: dict = params.get("yq3_static_curves") or {}
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

    # ─── YQ3 静态曲线校准层 ─────────────────────────────────
    def has_yq3_curves(self) -> bool:
        return bool(self._curves)

    def curve_ne_from_ub(self, uB_kg_s: float) -> Optional[float]:
        """用煤量 kg/s 反查曲线目标负荷 MW。"""
        if not self._curves:
            return None
        return interp_x(float(uB_kg_s) * 3.6, self._curves["ne_to_ub_tph"])

    def curve_pst_from_ne(self, Ne: float) -> Optional[float]:
        """用负荷 MW 查曲线主汽压力 MPa。"""
        if not self._curves:
            return None
        return interp_y(float(Ne), self._curves["ne_to_pst_mpa"])

    def curve_dfw_from_ne(self, Ne: float) -> Optional[float]:
        """用负荷 MW 查曲线给水流量 kg/s。"""
        if not self._curves:
            return None
        return interp_y(float(Ne), self._curves["ne_to_dfw_tph"]) / 3.6

    def curve_targets(self, uB_kg_s: float) -> Optional[dict]:
        """按当前煤量给出曲线目标值。"""
        if not self._curves:
            return None
        ne = self.curve_ne_from_ub(uB_kg_s)
        pst = self.curve_pst_from_ne(ne)
        dfw = self.curve_dfw_from_ne(ne)
        ub_target = interp_y(ne, self._curves["ne_to_ub_tph"]) / 3.6
        return {"Ne": ne, "pst": pst, "Dfw": dfw, "uB": ub_target}

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

    def _pm_from_pst(self, pst: float) -> float:
        """由 pst = pm - (a·pm + b) 反算 pm。"""
        s = self.p["steam"]["dp"]
        a = float(s["a"])
        b = float(s["b"])
        if abs(1.0 - a) < 1e-9:
            return pst
        return (float(pst) + b) / (1.0 - a)

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
        curve_targets = self.curve_targets(uB)
        if curve_targets:
            # YQ3 静态曲线提供当前负荷下的水煤比参考, 原 Q1 仍负责给水偏差对焓的影响。
            alpha = curve_targets["Dfw"] / max(curve_targets["uB"], 1e-6)

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

        if curve_targets:
            # YQ3 曲线校准: NE/PST 的稳态目标贴合机组静态曲线, 动态仍走一阶平滑。
            # HM 不从过热度/温度反推, 仅按给水相对曲线基准的偏差做工程校正。
            tau_ne = float(self._curves.get("tau_ne", c3))
            tau_pst = float(self._curves.get("tau_pst", c3))
            tau_hm = float(self._curves.get("tau_hm", 120.0))
            tau_ne = max(tau_ne, dt)
            tau_pst = max(tau_pst, dt)
            tau_hm = max(tau_hm, dt)
            dNe_dt = (curve_targets["Ne"] - Ne) / tau_ne
            pm_target = self._pm_from_pst(curve_targets["pst"])
            dpm_dt = (pm_target - pm) / tau_pst
            hm_ref = float(self.p["seed"]["hm0"])
            hm_dfw_gain = float(self._curves.get("hm_dfw_gain", 2.0))
            hm_target = hm_ref - hm_dfw_gain * (Dfw - curve_targets["Dfw"])
            dhm_dt = (hm_target - hm) / tau_hm

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
