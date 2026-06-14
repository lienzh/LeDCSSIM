# -*- coding: utf-8 -*-
"""水蒸气热力性质 — 给 DSL 调用

走 IAPWS-IF97 (industry-standard formulation), 自动识别区域:
    Region 1: 亚饱和水 (T<350°C, p>p_sat(T))
    Region 2: 过热蒸汽 (p<100MPa, T>T_sat(p))
    Region 3: 超临界 (T>374°C 或 p>22.064MPa 附近)
    Region 5: 高温过热 (T>800°C)

DSL 暴露:
    STEAM_T(h, p) → T (°C)     入参: h kJ/kg, p MPa
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def steam_T_from_ph(h_kJkg: float, p_MPa: float) -> Optional[float]:
    """根据焓 + 压力计算水蒸气温度.

    Args:
        h_kJkg: 比焓 (kJ/kg), 一般范围 100~4000
        p_MPa:  压力 (MPa),   一般范围 0.001~100

    Returns:
        温度 (°C); 输入越界或求解失败时返 None (调用方按 SkipCycle 处理)
    """
    try:
        from iapws import IAPWS97
    except ImportError:
        logger.warning("iapws 未安装, STEAM_T 不可用 (pip install iapws)")
        return None

    if not (0.001 <= p_MPa <= 100):
        return None
    if not (10 <= h_kJkg <= 4500):
        return None

    try:
        s = IAPWS97(P=float(p_MPa), h=float(h_kJkg))
        if s.T is None:
            return None
        return float(s.T) - 273.15
    except (NotImplementedError, ValueError, ZeroDivisionError) as e:
        # 某些边界 iapws 抛 NotImplementedError; 静默返回 None
        return None
    except Exception as e:
        logger.warning(f"STEAM_T 求解失败 h={h_kJkg} p={p_MPa}: {e}")
        return None


def steam_h_from_Tp(T_C: float, p_MPa: float) -> Optional[float]:
    """根据温度 + 压力反算水蒸气比焓.

    Args:
        T_C:   温度 (°C)
        p_MPa: 压力 (MPa)

    Returns:
        比焓 (kJ/kg); 输入越界或求解失败时返 None.
    """
    try:
        from iapws import IAPWS97
    except ImportError:
        logger.warning("iapws 未安装, STEAM_T/反算焓不可用 (pip install iapws)")
        return None

    if not (0.001 <= p_MPa <= 100):
        return None
    T_K = float(T_C) + 273.15
    if not (273.15 <= T_K <= 2273.15):
        return None

    try:
        s = IAPWS97(P=float(p_MPa), T=T_K)
        if s.h is None:
            return None
        return float(s.h)
    except (NotImplementedError, ValueError, ZeroDivisionError):
        return None
    except Exception as e:
        logger.warning(f"反算焓失败 T={T_C} p={p_MPa}: {e}")
        return None
