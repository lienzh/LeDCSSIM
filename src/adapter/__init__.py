# -*- coding: utf-8 -*-
"""协议适配层 - 对上暴露与协议无关的批量读写接口"""
from .base import Adapter
from .opc_ua import OPCUAAdapter

__all__ = ["Adapter", "OPCUAAdapter"]
