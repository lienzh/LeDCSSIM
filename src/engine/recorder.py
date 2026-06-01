# -*- coding: utf-8 -*-
"""
数据记录器(沿用 src/sim_engine/recorder.py 实现)

MVP 阶段只输出 CSV,不需要 Parquet/滚动/降频。
"""
# 直接从老模块导入,避免重复代码;老模块在阶段 B 清理时整体迁移过来
from src.sim_engine.recorder import DataRecorder

__all__ = ["DataRecorder"]
