# -*- coding: utf-8 -*-
"""画布架构清理后的残留(2026-06-13):仅保留两项已升格为正式工具的模块

- recorder.DataRecorder — 数据记录器(src/engine 复用)
- io_pairing_gen        — KKS 配对算法(viewer / tools 按路径直接 import)

画布期的 model / engine / graph_runner / demo_model / ccs_model / pairing_runner
已删除。新架构见 src/engine、src/models、src/project、src/viewer。
"""
from .recorder import DataRecorder

__all__ = ["DataRecorder"]
