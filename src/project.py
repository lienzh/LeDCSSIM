# -*- coding: utf-8 -*-
"""工程上下文 — "工程 = projects/<name>/ 目录" 的唯一真相源

所有组件 (viewer / src.cli / tools) 都从这里解析"当前工程"的文件路径:
    脚本 / 脚本备份 / 状态镜像 / OPC 端点 / 点表目录 / generated yaml

激活指针存 config/active_project.yaml (机器相关, gitignore):
    active: yq3
缺指针时取 projects/ 下第一个目录 (按名排序); `_` 开头的目录 (如 _templates) 不算工程.
"""
import logging
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path("projects")
ACTIVE_PTR = Path("config/active_project.yaml")

# 点表文件名默认模式 (project.yaml 可用 io_glob 覆盖)
DEFAULT_IO_GLOB = "*[_-]S.csv"


class ProjectPaths:
    """单个工程的全部文件路径"""

    def __init__(self, name: str):
        self.name = name
        # 注意: 直接引用模块全局 PROJECTS_ROOT, 不缓存到类属性
        # 这样测试 monkeypatch 才能生效
        self.root = PROJECTS_ROOT / name
        self.project_yaml = self.root / "project.yaml"
        self.script = self.root / "script.txt"
        self.script_backups = self.root / "script_backups"
        self.endpoints = self.root / "opc_endpoints.yaml"
        self.state_dir = self.root / "state"
        self.snapshot = self.state_dir / "state_snapshot.json"
        self.snapshot_backups = self.state_dir / "snapshot_backups"
        self.generated_dir = self.root / "generated"

        # 读取 project.yaml 元数据 (可选)
        meta = {}
        if self.project_yaml.exists():
            try:
                meta = yaml.safe_load(self.project_yaml.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning(f"解析 {self.project_yaml} 失败, 全部字段走默认: {e}")
                meta = {}

        # 工程显示名, 默认用目录名
        self.display = str(meta.get("display") or name)

        # 点表目录: 默认 projects/<name>/io, 可用 io_dir 指到仓库其它位置 (如 YQ3SIM-IO)
        self.io_dir = Path(meta.get("io_dir")) if meta.get("io_dir") else (self.root / "io")

        # 点表文件 glob 模式, 默认匹配 *_S.csv / *-S.csv (简化版点表)
        self.io_glob = str(meta.get("io_glob") or DEFAULT_IO_GLOB)

        # 找不到简化点表时的回退 glob 列表 (仓库根相对), 如老命名 YQ3SIM-IO/DPU*.csv
        self.io_fallback_globs = [str(g) for g in (meta.get("io_fallback_globs") or [])]

        # 全量点表目录 (tools/generate_yaml_from_pairs 用), 默认与 io_dir 相同
        self.io_full_dir = (
            Path(meta.get("io_full_dir")) if meta.get("io_full_dir") else self.io_dir
        )


def list_projects() -> List[str]:
    """列出 projects/ 下所有工程名 (排除 _ 开头的目录), 按名称排序"""
    if not PROJECTS_ROOT.exists():
        return []
    return sorted(
        p.name for p in PROJECTS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )


def get_active() -> str:
    """返回当前激活工程名.

    优先读 ACTIVE_PTR 指针文件; 文件不存在或指向不存在工程时, 取列表第一个.
    无工程时抛 RuntimeError.
    """
    names = list_projects()
    if not names:
        raise RuntimeError("projects/ 下没有任何工程目录 — 至少建一个 projects/<name>/")
    if ACTIVE_PTR.exists():
        try:
            doc = yaml.safe_load(ACTIVE_PTR.read_text(encoding="utf-8")) or {}
            if doc.get("active") in names:
                return doc["active"]
        except Exception:
            pass
    return names[0]


def set_active(name: str) -> None:
    """将工程 name 写入激活指针文件.

    工程不存在时抛 ValueError.
    """
    if name not in list_projects():
        raise ValueError(f"工程不存在: {name!r} (现有: {list_projects()})")
    ACTIVE_PTR.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PTR.write_text(
        f"# 当前激活工程 — viewer 顶栏切换写这里\nactive: {name}\n",
        encoding="utf-8",
    )


def paths(name: Optional[str] = None) -> ProjectPaths:
    """返回指定工程(或当前激活工程)的路径对象"""
    return ProjectPaths(name or get_active())
