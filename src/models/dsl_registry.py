# -*- coding: utf-8 -*-
"""DSL 模型工厂注册表 — viewer DSL 与模型库之间唯一的挂接点

加新容量 preset(如 660MW)= 此处加一条 + 一份参数 yaml, runtime/脚本语法不动:
    "CCS_660": ModelFactorySpec(arity=3, pins=("PST", "HM", "NE"),
                                params_path="config/ccs_models/xxx-660mw.yaml",
                                make=lambda p: CcsUscOtbt(p)),
"""
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .ccs_usc_otbt import CcsUscOtbt, load_params


@dataclass(frozen=True)
class ModelFactorySpec:
    arity: int                # DSL 工厂函数入参个数
    pins: Tuple[str, ...]     # 输出管脚名, 顺序 = model.step() 返回元组顺序
    params_path: str          # 参数 yaml 路径
    make: Callable            # params dict → 模型实例 (实例须有 step(*inputs, dt) -> tuple)


MODEL_FACTORIES = {
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


def get_factory_params(fname: str) -> Optional[dict]:
    if fname in _params_cache:
        return _params_cache[fname]
    if fname in _params_err:
        return None
    try:
        _params_cache[fname] = load_params(MODEL_FACTORIES[fname].params_path)
        return _params_cache[fname]
    except Exception as e:          # KeyError(未注册) / FileNotFoundError / yaml 错都走这
        _params_err[fname] = str(e)
        return None


def get_factory_error(fname: str) -> Optional[str]:
    """诊断面板用: 参数加载失败的错因"""
    return _params_err.get(fname)
