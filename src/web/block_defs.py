# -*- coding: utf-8 -*-
"""
功能块库定义加载器

从 config/block_library.yaml 加载 R600C 宏命令功能块定义，
供 Web 组态界面 /api/blocks 使用。
"""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_LIBRARY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "block_library.yaml"

# 缓存
_categories: List[dict] = []
_blocks: List[dict] = []
_loaded = False


def _load():
    """加载功能块库 YAML（带缓存）"""
    global _categories, _blocks, _loaded
    if _loaded:
        return

    if not _LIBRARY_PATH.exists():
        logger.warning(f"功能块库文件不存在: {_LIBRARY_PATH}")
        _loaded = True
        return

    with open(_LIBRARY_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    _categories = data.get("categories", [])
    _blocks = data.get("blocks", [])
    _loaded = True
    logger.info(f"功能块库已加载: {len(_categories)} 分类, {len(_blocks)} 功能块")


def get_categories() -> List[dict]:
    """获取所有分类"""
    _load()
    return _categories


def get_blocks() -> List[dict]:
    """获取所有功能块定义"""
    _load()
    return _blocks


def get_blocks_by_category(category_id: str) -> List[dict]:
    """按分类获取功能块"""
    _load()
    return [b for b in _blocks if b.get("category") == category_id]


def get_block(block_id: str) -> Optional[dict]:
    """按 ID 获取单个功能块定义"""
    _load()
    for b in _blocks:
        if b.get("id") == block_id:
            return b
    return None


def get_category_color(category_id: str) -> str:
    """获取分类颜色"""
    _load()
    for c in _categories:
        if c.get("id") == category_id:
            return c.get("color", "#64748b")
    return "#64748b"


def reload():
    """强制重新加载"""
    global _loaded
    _loaded = False
    _load()
