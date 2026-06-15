# -*- coding: utf-8 -*-
"""工程创建向导和容量模板实例化。

模板目录默认放在 projects/_templates/<template>/template.yaml。
创建工程时只写当前工程自己的目录, 不修改其它工程。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src import project as proj


NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _template_root() -> Path:
    return proj.PROJECTS_ROOT / "_templates"


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是 mapping")
    return data


def list_templates(template_root: Optional[Path] = None) -> List[dict]:
    """列出可用模板。"""
    root = template_root or _template_root()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        meta_path = p / "template.yaml"
        if not meta_path.exists():
            continue
        meta = _read_yaml(meta_path)
        defaults = meta.get("defaults") or {}
        out.append({
            "name": p.name,
            "display": meta.get("display") or p.name,
            "description": meta.get("description") or "",
            "capacity_mw": defaults.get("capacity_mw"),
            "model_factory": defaults.get("model_factory"),
        })
    return out


def load_template(name: str, template_root: Optional[Path] = None) -> dict:
    """读取模板定义。"""
    if not NAME_RE.match(name):
        raise ValueError(f"非法模板名: {name!r}")
    root = template_root or _template_root()
    meta_path = root / name / "template.yaml"
    if not meta_path.exists():
        raise ValueError(f"模板不存在: {name}")
    meta = _read_yaml(meta_path)
    meta["_name"] = name
    meta["_root"] = root / name
    return meta


def _render_project_yaml(
    name: str,
    display: str,
    template_name: str,
    capacity_mw: int,
    model_factory: str,
) -> str:
    return (
        f"# {display} 工程\n"
        "# 由工程创建向导生成; io/raw=原始点表, io/filtered=粗筛点表, io/simple=最终精简点表\n"
        f"display: {display}\n"
        f"template: {template_name}\n"
        f"capacity_mw: {capacity_mw}\n"
        f"model_factory: {model_factory}\n"
        f"io_dir: projects/{name}/io/simple\n"
        f"io_full_dir: projects/{name}/io/filtered\n"
        f"io_raw_dir: projects/{name}/io/raw\n"
        f"io_filtered_dir: projects/{name}/io/filtered\n"
        f"io_simple_dir: projects/{name}/io/simple\n"
        "io_fallback_globs:\n"
        f"  - projects/{name}/io/filtered/DPU*.csv\n"
    )


def _render_endpoints(local_url: str, vm_url: str, mode: str = "local") -> str:
    return (
        "# OPC 端点选择 — viewer 顶栏 [本地] [VM] 切换写这里\n"
        f"mode: {mode}\n"
        f"local: {local_url}\n"
        f"vm:    {vm_url}\n"
    )


def _render_script(meta: dict, variables: Dict[str, Any]) -> str:
    script_cfg = meta.get("script") or {}
    source = script_cfg.get("source")
    if not source:
        body = script_cfg.get("content") or ""
    else:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = Path(meta.get("_root", ".")) / source_path
        if not source_path.exists():
            raise FileNotFoundError(f"模板脚本源不存在: {source_path}")
        body = source_path.read_text(encoding="utf-8")

    replacements = script_cfg.get("replacements") or {}
    for old, new in replacements.items():
        body = body.replace(str(old), str(new).format(**variables))

    # 最后兜底替换通用占位符。
    for key, val in variables.items():
        body = body.replace("{{" + key + "}}", str(val))
    if not body.endswith("\n"):
        body += "\n"
    return body


def _copy_optional_dir(src: Path, dst: Path, overwrite: bool) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _copy_optional_dir(item, target, overwrite)
        elif overwrite or not target.exists():
            shutil.copy2(item, target)


def create_project_from_template(
    *,
    name: str,
    template: str,
    display: Optional[str] = None,
    capacity_mw: Optional[int] = None,
    model_factory: Optional[str] = None,
    local_url: Optional[str] = None,
    vm_url: Optional[str] = None,
    mode: str = "local",
    overwrite: bool = False,
    overwrite_script: bool = False,
    overwrite_endpoints: bool = False,
    activate: bool = False,
    template_root: Optional[Path] = None,
) -> dict:
    """按模板创建或更新工程。

    overwrite=True 覆盖向导管理的工程元数据和模板驱动文件, 但默认保留已有
    script.txt / opc_endpoints.yaml, 避免重跑模板时破坏已调好的脚本和 OPC 端点。
    如确需重置脚本或端点, 分别传 overwrite_script / overwrite_endpoints。
    不删除已有 io/raw、io/filtered、io/simple 中的数据。
    """
    if not NAME_RE.match(name):
        raise ValueError(f"非法工程名: {name!r}")
    meta = load_template(template, template_root=template_root)
    defaults = meta.get("defaults") or {}
    display = display or defaults.get("display") or name.upper()
    capacity_mw = int(capacity_mw or defaults.get("capacity_mw") or 1000)
    model_factory = str(model_factory or defaults.get("model_factory") or "CCS_1000")
    local_url = str(local_url or defaults.get("local_url") or "opc.tcp://127.0.0.1:9440")
    vm_url = str(vm_url or defaults.get("vm_url") or "opc.tcp://192.168.135.142:9440")

    root = proj.PROJECTS_ROOT / name
    existing = root.exists()
    if existing and not overwrite:
        raise FileExistsError(f"工程已存在: {name}; 如需更新请传 overwrite=True")

    for rel in ("io/raw", "io/filtered", "io/simple", "script_backups", "state", "generated"):
        (root / rel).mkdir(parents=True, exist_ok=True)
        keep = root / rel / ".gitkeep"
        if not keep.exists():
            keep.write_text("\n", encoding="utf-8")

    variables = {
        "project_name": name,
        "project_var": re.sub(r"\W+", "_", name.upper()),
        "display": display,
        "capacity_mw": capacity_mw,
        "model_factory": model_factory,
    }
    (root / "project.yaml").write_text(
        _render_project_yaml(name, display, template, capacity_mw, model_factory),
        encoding="utf-8",
    )
    script_path = root / "script.txt"
    endpoints_path = root / "opc_endpoints.yaml"
    preserved = []
    if (not existing) or overwrite_script or not script_path.exists():
        script_path.write_text(_render_script(meta, variables), encoding="utf-8")
    else:
        preserved.append("script.txt")
    if (not existing) or overwrite_endpoints or not endpoints_path.exists():
        endpoints_path.write_text(
            _render_endpoints(local_url, vm_url, mode=mode),
            encoding="utf-8",
        )
    else:
        preserved.append("opc_endpoints.yaml")
    (root / "PROJECT.md").write_text(
        f"# {display}\n\n"
        f"- 模板: `{template}`\n"
        f"- 容量: `{capacity_mw}MW`\n"
        f"- 协调模型: `{model_factory}`\n"
        "- 点表目录: `io/raw` / `io/filtered` / `io/simple`\n",
        encoding="utf-8",
    )

    template_dir = meta["_root"]
    _copy_optional_dir(template_dir / "drivers", root / "drivers", overwrite=overwrite)

    if activate:
        proj.set_active(name)

    return {
        "ok": True,
        "name": name,
        "display": display,
        "template": template,
        "capacity_mw": capacity_mw,
        "model_factory": model_factory,
        "root": str(root),
        "script": str(root / "script.txt"),
        "project_yaml": str(root / "project.yaml"),
        "activated": activate,
        "preserved": preserved,
    }
