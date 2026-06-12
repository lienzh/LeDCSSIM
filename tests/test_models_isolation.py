# -*- coding: utf-8 -*-
"""红线: src/models 不得依赖 viewer / Flask / asyncua.

模型库必须可被离线脚本纯 import (将来控制优化/参数整定不经 OPC 直接调模型),
反向依赖一旦混进来, 这条路就断了. 架构再变也保留本测试.
"""
import re
from pathlib import Path

FORBIDDEN = re.compile(
    r"^\s*(from|import)\s+(src\.viewer|src\.opc_client|src\.adapter|flask|asyncua)",
    re.M,
)


def test_models_package_is_standalone():
    files = list(Path("src/models").rglob("*.py"))
    assert files, "src/models 下应有模型文件"
    for py in files:
        text = py.read_text(encoding="utf-8")
        m = FORBIDDEN.search(text)
        assert m is None, f"{py} 引用了禁止依赖: {m.group(0).strip()}"
