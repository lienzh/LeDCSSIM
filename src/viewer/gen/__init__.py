# -*- coding: utf-8 -*-
"""脚本生成器 — 规则驱动 (drivers/*.yaml). 入口 generate(project_paths)."""


def generate(project_paths=None):
    from .generator import generate as _generate
    return _generate(project_paths)

__all__ = ["generate"]
