# -*- coding: utf-8 -*-
"""DSL 模型工厂注册表 — viewer DSL 与模型库之间唯一的挂接点

加新容量 preset(如 660MW)= 此处加一条 + 一份参数 yaml, runtime/脚本语法不动:
    "CCS_660": ModelFactorySpec(arity=3, pins=("PST", "HM", "NE"),
                                params_path="config/ccs_models/xxx-660mw.yaml",
                                make=lambda p: CcsUscOtbt(p)),
"""
from dataclasses import dataclass
from copy import deepcopy
from typing import Callable, Optional, Tuple

import yaml

from src import project as proj
from .ccs_usc_otbt import CcsUscOtbt, load_params


@dataclass(frozen=True)
class ModelFactorySpec:
    arity: int                # DSL 工厂函数入参个数
    pins: Tuple[str, ...]     # 输出管脚名, 顺序 = model.step() 返回元组顺序
    params_path: str          # 参数 yaml 路径
    make: Callable            # params dict → 模型实例 (实例须有 step(*inputs, dt) -> tuple)


MODEL_FACTORIES = {
    # $YQ3 = CCS_660(uB, Dfw, ut);  读管脚: $YQ3.PST / $YQ3.HM / $YQ3.NE
    "CCS_660": ModelFactorySpec(
        arity=3,
        pins=("PST", "HM", "NE"),
        params_path="config/ccs_models/usc-otbt-660mw.yaml",
        make=lambda params: CcsUscOtbt(params),
    ),
    # $YQ3 = CCS_1000(uB, Dfw, ut);  读管脚: $YQ3.PST / $YQ3.HM / $YQ3.NE
    "CCS_1000": ModelFactorySpec(
        arity=3,
        pins=("PST", "HM", "NE"),
        params_path="config/ccs_models/usc-otbt-1000mw.yaml",
        make=lambda params: CcsUscOtbt(params),
    ),
}

# 参数懒加载缓存 — 失败缓存错因, 不反复读盘 (沿用原 _get_ccs_params 语义)
_params_cache: dict = {}
_params_err: dict = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 dict, 用工程覆盖值替换基准参数。"""
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def load_project_overrides() -> dict:
    """读取当前工程的模型参数覆盖文件。缺文件返回空 dict。"""
    try:
        p = proj.paths().model_overrides
    except Exception:
        return {}
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{p} 顶层必须是 mapping")
    return doc


def get_base_params(fname: str) -> Optional[dict]:
    """只读基准 preset, 不叠加工程覆盖。viewer 参数面板用。"""
    try:
        return load_params(MODEL_FACTORIES[fname].params_path)
    except Exception as e:
        _params_err[fname] = str(e)
        return None


def get_factory_params(fname: str) -> Optional[dict]:
    if fname in _params_cache:
        return _params_cache[fname]
    if fname in _params_err:
        return None
    try:
        params = load_params(MODEL_FACTORIES[fname].params_path)
        overrides = load_project_overrides().get(fname) or {}
        if overrides:
            if not isinstance(overrides, dict):
                raise ValueError(f"{fname} 覆盖值必须是 mapping")
            params = _deep_merge(params, overrides)
        _params_cache[fname] = params
        return _params_cache[fname]
    except Exception as e:          # KeyError(未注册) / FileNotFoundError / yaml 错都走这
        _params_err[fname] = str(e)
        return None


def get_factory_error(fname: str) -> Optional[str]:
    """诊断面板用: 参数加载失败的错因"""
    return _params_err.get(fname)


def clear_param_cache() -> None:
    """清参数缓存。工程覆盖文件改动后调用, 下一次建模型会重读。"""
    _params_cache.clear()
    _params_err.clear()
