# -*- coding: utf-8 -*-
"""生成器金标准回归 — 重构前后 YQ3 输出必须逐字一致.

抓取见计划 Task 1 Step 1. 重构 (drivers 外置 + gen/ 引擎) 期间此测试始终绿,
即证明零行为变化. 若需有意改输出, 必须同步重抓金标准并在 commit 说明.
"""
from pathlib import Path

import src.viewer.runtime as rt

GOLDEN = Path("tests/fixtures/yq3_generated_golden.txt")


def test_generator_matches_golden():
    expected = GOLDEN.read_text(encoding="utf-8")
    actual = rt.generate_script_from_tagmap("")
    assert actual == expected, "生成器输出与金标准不一致 — 重构改变了行为"


def test_new_engine_matches_golden():
    """gen.generator.generate() 输出 == 金标准 (与旧函数逐字一致)"""
    from src.viewer.gen import generate
    import src.project as prj

    actual = generate(prj.paths("yq3"))
    assert actual == GOLDEN.read_text(encoding="utf-8"), "新引擎输出偏离金标准"
