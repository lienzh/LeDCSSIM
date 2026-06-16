# -*- coding: utf-8 -*-
"""viewer 点表增删服务。

raw 是原始归档, 只读; filtered/simple 是当前工程实际用于生成和运行的点表。
"""
import csv
import io
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


HW_POINT_RE = re.compile(r"^HW\.([A-Z]+)(\d+)\.PV$", re.IGNORECASE)
SH_POINT_RE = re.compile(r"^(SH\d+)\.([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)$", re.IGNORECASE)


def normalize_dpu(name: str) -> str:
    """DPU 文件名/短名归一化: 3001 / DPU3001_S -> DPU3001。"""
    base = Path(str(name)).stem.upper()
    base = base.replace("_S", "").replace("-S", "").replace("_FULL", "")
    if base.isdigit():
        return f"DPU{base}"
    if re.fullmatch(r"DPU\d{4}", base):
        return base
    return base


def normalize_point(name: str) -> str:
    return str(name or "").strip().upper()


def point_to_short(point: str) -> str:
    p = normalize_point(point)
    if p.startswith("HW.") and p.endswith(".PV"):
        return p[3:-3]
    return p


def point_code(point: str) -> str:
    """点名提取类型码: HW.AI010101.PV -> AI, SH0001.X.IN -> IN。"""
    p = normalize_point(point)
    m = HW_POINT_RE.match(p)
    if m:
        return m.group(1).upper()
    m = SH_POINT_RE.match(p)
    if m:
        return m.group(3).upper()
    return ""


def split_dpu_point(dpu: str, point: str) -> tuple[str, str]:
    """把 DPU + 点名归一成 (DPU3012, SH0098.X.PV / HW.AI010101.PV)。"""
    ndpu = normalize_dpu(dpu)
    p = str(point or "").strip()
    up = p.upper()
    if up.startswith("NS=0;S="):
        p = p[7:]
        up = p.upper()
    if up.startswith(ndpu + "."):
        p = p[len(ndpu) + 1:]
    return ndpu, p.strip()


def opc_node_from_item(item: dict) -> Optional[str]:
    dpu, point = split_dpu_point(item.get("dpu", ""), item.get("name", ""))
    if not dpu or not point:
        return None
    up = point.upper()
    if HW_POINT_RE.match(up):
        return f"ns=0;s={dpu}.{up}"
    if up.startswith("HW.") or up.startswith("SH"):
        return f"ns=0;s={dpu}.{up}"
    # 自动补硬件短码: AI010101 -> HW.AI010101.PV
    if re.match(r"^[A-Z]+\d+$", up):
        return f"ns=0;s={dpu}.HW.{up}.PV"
    return f"ns=0;s={dpu}.{up}"


def _decode_csv_bytes(data: bytes) -> tuple[str, str]:
    # NT6000 tag.csv 第一行可能含二进制签名, 数据区仍是 UTF-8 CSV。
    # 不能因为签名里有非法 UTF-8 字节就整体退到 GBK, 否则中文表头会被破坏。
    text = data.decode("utf-8-sig", errors="replace")
    if "~索引" in text:
        return text, "utf-8"
    return data.decode("gbk", errors="replace"), "gbk"


def _csv_line(cols: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow(cols)
    return buf.getvalue()


@dataclass
class CsvTable:
    path: Path
    encoding: str
    binary_prefix: bytes
    prefix: list[str]
    header: list[str]
    rows: list[list[str]]

    @classmethod
    def read(cls, path: Path) -> "CsvTable":
        raw = path.read_bytes()
        text, enc = _decode_csv_bytes(raw)
        header_pat = "~索引".encode("utf-8" if enc == "utf-8" else "gbk")
        header_byte_idx = raw.find(header_pat)
        version_byte_idx = raw.rfind(b"#VERSION", 0, header_byte_idx if header_byte_idx >= 0 else len(raw))
        data_start = version_byte_idx if version_byte_idx >= 0 else (header_byte_idx if header_byte_idx >= 0 else 0)
        binary_prefix = raw[:data_start] if data_start > 0 else b""
        if data_start > 0:
            text = raw[data_start:].decode(enc, errors="replace")
        lines = text.splitlines()
        if len(lines) < 2:
            raise ValueError(f"点表内容不足: {path}")
        header_idx = None
        for i, line in enumerate(lines):
            if line.lstrip("\ufeff").startswith("~索引"):
                header_idx = i
                break
        if header_idx is None:
            raise ValueError(f"未找到点表表头(~索引): {path}")
        prefix = lines[:header_idx]
        header = next(csv.reader([lines[header_idx]]))
        rows = []
        for line in lines[header_idx + 1:]:
            if not line:
                continue
            rows.append(next(csv.reader([line])))
        return cls(path=path, encoding=enc, binary_prefix=binary_prefix,
                   prefix=prefix, header=header, rows=rows)

    @classmethod
    def empty_like(cls, path: Path, source: "CsvTable") -> "CsvTable":
        return cls(
            path=path,
            encoding=source.encoding,
            binary_prefix=b"",
            prefix=list(source.prefix),
            header=list(source.header),
            rows=[],
        )

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = list(self.prefix)
        lines.append(_csv_line(self.header))
        for row in self.rows:
            lines.append(_csv_line(row))
        text = "\r\n".join(lines) + "\r\n"
        self.path.write_bytes(self.binary_prefix + text.encode(self.encoding, errors="replace"))

    def col_index(self, title: str, fallback: Optional[int] = None) -> Optional[int]:
        try:
            return self.header.index(title)
        except ValueError:
            return fallback

    @property
    def name_idx(self) -> int:
        idx = self.col_index("测点名称", 1)
        return 1 if idx is None else idx

    @property
    def desc_idx(self) -> int:
        idx = self.col_index("描述", 2)
        return 2 if idx is None else idx

    @property
    def kks_idx(self) -> int:
        idx = self.col_index("设计编号", 3)
        return 3 if idx is None else idx

    @property
    def dtype_idx(self) -> int:
        idx = self.col_index("数据类型", 4)
        return 4 if idx is None else idx

    @property
    def opc_idx(self) -> Optional[int]:
        return self.col_index("OPC输出", 22)

    def point_names(self) -> set[str]:
        out = set()
        for row in self.rows:
            if len(row) > self.name_idx:
                out.add(normalize_point(row[self.name_idx]))
        return out

    def find_row(self, point: str) -> Optional[list[str]]:
        target = normalize_point(point)
        for row in self.rows:
            if len(row) > self.name_idx and normalize_point(row[self.name_idx]) == target:
                return list(row)
        return None

    def remove_points(self, points: set[str]) -> int:
        targets = {normalize_point(p) for p in points}
        old = len(self.rows)
        self.rows = [
            row for row in self.rows
            if not (len(row) > self.name_idx and normalize_point(row[self.name_idx]) in targets)
        ]
        if len(self.rows) != old:
            self.reindex()
        return old - len(self.rows)

    def append_row(self, row: list[str]) -> bool:
        name = normalize_point(row[self.name_idx] if len(row) > self.name_idx else "")
        if not name or name in self.point_names():
            return False
        cols = list(row)
        if len(cols) < len(self.header):
            cols.extend([""] * (len(self.header) - len(cols)))
        elif len(cols) > len(self.header):
            cols = cols[:len(self.header)]
        if self.opc_idx is not None and self.opc_idx < len(cols):
            cols[self.opc_idx] = "Y"
        self.rows.append(cols)
        self.reindex()
        return True

    def ensure_row(self, row: list[str]) -> str:
        """确保点存在且 OPC输出=Y。返回 added / updated / exists。"""
        target = normalize_point(row[self.name_idx] if len(row) > self.name_idx else "")
        if not target:
            return "exists"
        for existing in self.rows:
            if len(existing) > self.name_idx and normalize_point(existing[self.name_idx]) == target:
                if self.opc_idx is not None and self.opc_idx < len(existing) and existing[self.opc_idx] != "Y":
                    existing[self.opc_idx] = "Y"
                    return "updated"
                return "exists"
        return "added" if self.append_row(row) else "exists"

    def reindex(self) -> None:
        for i, row in enumerate(self.rows, 1):
            if row:
                row[0] = str(i)


def _list_csv_files(dir_path: Path) -> list[Path]:
    if not dir_path.exists():
        return []
    files = [p for p in dir_path.glob("*.csv") if p.is_file()]
    files.extend(p for p in dir_path.glob("DPU*/tag.csv") if p.is_file())
    return sorted(files)


def _dpu_from_table_path(path: Path) -> str:
    if path.stem.lower() == "tag" and path.parent.name.upper().startswith("DPU"):
        return normalize_dpu(path.parent.name)
    return normalize_dpu(path.stem)


def _find_table_file(dir_path: Path, dpu: str, *, simple: bool = False) -> Optional[Path]:
    ndpu = normalize_dpu(dpu)
    for fn in _list_csv_files(dir_path):
        if _dpu_from_table_path(fn) == ndpu:
            return fn
    expected = f"{ndpu}_S.csv" if simple else f"{ndpu}.csv"
    return dir_path / expected


def _read_optional(path: Optional[Path]) -> Optional[CsvTable]:
    if path and path.exists():
        return CsvTable.read(path)
    return None


def _backup_file(path: Path, backup_root: Path, reason: str) -> Optional[str]:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_root / ts / reason / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


def _backup_nt_file(path: Path, backup_root: Path, reason: str, nt_home: Path) -> Optional[str]:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        rel = path.resolve().relative_to(nt_home.resolve())
    except Exception:
        rel = Path(path.name)
    dest = backup_root / ts / reason / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return str(dest)


def _nt6000_home(project_paths) -> Optional[Path]:
    attr = getattr(project_paths, "nt6000_home", None)
    candidates = []
    if attr:
        candidates.append(Path(attr))
    env = os.environ.get("NT6000_HOME")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("D:/NT6000V5"))
    for p in candidates:
        if p.exists() and (p / "project").exists():
            return p
    return None


def _nt6000_tag_paths(project_paths, dpu: str) -> tuple[list[Path], list[dict], Optional[Path]]:
    """定位 NT6000 project/temp/target 三份 tag.csv。"""
    home = _nt6000_home(project_paths)
    if home is None:
        return [], [{"dpu": dpu, "reason": "未找到 NT6000_HOME 或 D:/NT6000V5"}], None

    ndpu = normalize_dpu(dpu)
    matches = sorted((home / "project").glob(f"*/network/*/DPU/{ndpu}/tag.csv"))
    if not matches:
        return [], [{"dpu": ndpu, "reason": f"NT6000 project 下找不到 {ndpu}/tag.csv"}], home
    if len(matches) > 1:
        return [], [{"dpu": ndpu, "reason": f"NT6000 project 下有多个 {ndpu}/tag.csv, 不自动选择"}], home

    project_tag = matches[0]
    rel = project_tag.relative_to(home / "project")
    # <工程名>/network/<network名>/DPU/<DPU>/tag.csv
    parts = rel.parts
    if len(parts) < 6 or parts[1].lower() != "network":
        return [], [{"dpu": ndpu, "reason": f"无法解析 NT6000 工程路径: {project_tag}"}], home
    network_name = parts[2]
    paths = [
        project_tag,
        home / "temp" / "network" / network_name / "DPU" / ndpu / "tag.csv",
        home / "target" / "network" / network_name / "DPU" / ndpu / "tag.csv",
    ]
    warnings = [
        {"dpu": ndpu, "path": str(p), "reason": "NT6000 tag.csv 不存在"}
        for p in paths if not p.exists()
    ]
    return [p for p in paths if p.exists()], warnings, home


def _sync_nt6000_add(project_paths, rows_by_dpu: dict[str, dict[str, list[str]]]) -> dict:
    backup_root = project_paths.root / "io" / "backups"
    changed = []
    skipped = []
    backups = []
    for dpu, rows_by_name in rows_by_dpu.items():
        paths, warnings, home = _nt6000_tag_paths(project_paths, dpu)
        skipped.extend(warnings)
        if not paths or home is None:
            continue
        for path in paths:
            table = CsvTable.read(path)
            table_changed = False
            actions = []
            for name, row in rows_by_name.items():
                action = table.ensure_row(row)
                actions.append({"name": name, "action": action})
                if action in ("added", "updated"):
                    table_changed = True
            if table_changed:
                b = _backup_nt_file(path, backup_root, "nt6000-before-add", home)
                if b:
                    backups.append(b)
                table.write()
            changed.append({"dpu": dpu, "path": str(path), "changed": table_changed, "actions": actions})
    return {"changed": changed, "skipped": skipped, "backups": backups}


def _sync_nt6000_remove(project_paths, grouped: dict[str, set[str]]) -> dict:
    backup_root = project_paths.root / "io" / "backups"
    changed = []
    skipped = []
    backups = []
    for dpu, names in grouped.items():
        paths, warnings, home = _nt6000_tag_paths(project_paths, dpu)
        skipped.extend(warnings)
        if not paths or home is None:
            continue
        for path in paths:
            table = CsvTable.read(path)
            n_removed = table.remove_points(names)
            if n_removed:
                b = _backup_nt_file(path, backup_root, "nt6000-before-remove", home)
                if b:
                    backups.append(b)
                table.write()
            changed.append({"dpu": dpu, "path": str(path), "removed": n_removed})
    return {"changed": changed, "skipped": skipped, "backups": backups}


def _point_item_from_row(dpu: str, table: CsvTable, row: list[str]) -> dict:
    name = row[table.name_idx].strip() if len(row) > table.name_idx else ""
    desc = row[table.desc_idx].strip() if len(row) > table.desc_idx else ""
    kks = row[table.kks_idx].strip() if len(row) > table.kks_idx else ""
    dtype = row[table.dtype_idx].strip() if len(row) > table.dtype_idx else ""
    code = point_code(name)
    return {
        "dpu": normalize_dpu(dpu),
        "name": name,
        "short": point_to_short(name),
        "code": code,
        "desc": desc,
        "kks": kks,
        "dtype": dtype,
    }


def _status_maps(project_paths) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    filtered = set()
    simple = set()
    for dir_path, target in (
        (project_paths.io_filtered_dir, filtered),
        (project_paths.io_simple_dir, simple),
    ):
        for fn in _list_csv_files(dir_path):
            dpu = _dpu_from_table_path(fn)
            try:
                table = CsvTable.read(fn)
            except Exception:
                continue
            for point in table.point_names():
                target.add((dpu, point))
    return filtered, simple


def search_raw_points(project_paths, query: str = "", dpu: str = "", limit: int = 300) -> dict:
    """从当前工程 raw 原始点表搜索可加入的 OPC 点。"""
    q = str(query or "").strip().lower()
    ndpu = normalize_dpu(dpu) if dpu else ""
    filtered, simple = _status_maps(project_paths)
    items = []
    source_label = str(project_paths.io_raw_dir)
    for fn in _list_csv_files(project_paths.io_raw_dir):
        file_dpu = _dpu_from_table_path(fn)
        if ndpu and file_dpu != ndpu:
            continue
        table = CsvTable.read(fn)
        for row in table.rows:
            if len(row) <= table.name_idx:
                continue
            name = row[table.name_idx].strip()
            if not name or name == "测点名称":
                continue
            item = _point_item_from_row(file_dpu, table, row)
            hay = " ".join([item["dpu"], item["name"], item["short"], item["desc"], item["kks"], item["dtype"]]).lower()
            if q and q not in hay:
                continue
            key = (item["dpu"], normalize_point(item["name"]))
            item["in_filtered"] = key in filtered
            item["in_simple"] = key in simple
            items.append(item)
            if len(items) >= limit:
                return {
                    "items": items,
                    "count": len(items),
                    "truncated": True,
                    "raw_dir": source_label,
                }
    return {
        "items": items,
        "count": len(items),
        "truncated": False,
        "raw_dir": source_label,
    }


def selected_points(project_paths, query: str = "", dpu: str = "", limit: int = 500) -> dict:
    """列出 simple 当前选中的硬件 IO 点。"""
    q = str(query or "").strip().lower()
    ndpu = normalize_dpu(dpu) if dpu else ""
    items = []
    filtered, simple = _status_maps(project_paths)
    for fn in _list_csv_files(project_paths.io_simple_dir):
        file_dpu = _dpu_from_table_path(fn)
        if ndpu and file_dpu != ndpu:
            continue
        table = CsvTable.read(fn)
        for row in table.rows:
            if len(row) <= table.name_idx:
                continue
            name = row[table.name_idx].strip()
            if not name or name == "测点名称":
                continue
            item = _point_item_from_row(file_dpu, table, row)
            hay = " ".join([item["dpu"], item["name"], item["short"], item["desc"], item["kks"], item["dtype"]]).lower()
            if q and q not in hay:
                continue
            key = (item["dpu"], normalize_point(item["name"]))
            item["in_filtered"] = key in filtered
            item["in_simple"] = key in simple
            items.append(item)
            if len(items) >= limit:
                return {"items": items, "count": len(items), "truncated": True}
    return {"items": items, "count": len(items), "truncated": False}


def _group_points(points: Iterable[dict]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for item in points:
        dpu = normalize_dpu(item.get("dpu", ""))
        name = normalize_point(item.get("name", ""))
        if not dpu or not name:
            continue
        grouped.setdefault(dpu, set()).add(name)
    return grouped


def add_points(project_paths, points: list[dict], sync_nt6000: bool = False) -> dict:
    """把 raw 中选中的点加入 filtered/simple。"""
    grouped = _group_points(points)
    if not grouped:
        raise ValueError("没有可添加的点")

    backup_root = project_paths.root / "io" / "backups"
    added = []
    skipped = []
    backups = []
    touched_dpus = set()
    rows_for_nt6000: dict[str, dict[str, list[str]]] = {}

    for dpu, names in grouped.items():
        raw_path = _find_table_file(project_paths.io_raw_dir, dpu, simple=False)
        if not raw_path or not raw_path.exists():
            skipped.extend({"dpu": dpu, "name": n, "reason": "原始备份点表不存在"} for n in sorted(names))
            continue
        raw_table = CsvTable.read(raw_path)
        filtered_path = _find_table_file(project_paths.io_filtered_dir, dpu, simple=False)
        simple_path = _find_table_file(project_paths.io_simple_dir, dpu, simple=True)
        filtered_table = _read_optional(filtered_path) or CsvTable.empty_like(filtered_path, raw_table)
        simple_table = _read_optional(simple_path) or CsvTable.empty_like(simple_path, raw_table)

        changed = False
        backups.extend(x for x in (
            _backup_file(filtered_path, backup_root, "before-add") if filtered_path and filtered_path.exists() else None,
            _backup_file(simple_path, backup_root, "before-add") if simple_path and simple_path.exists() else None,
        ) if x)
        for name in sorted(names):
            raw_row = raw_table.find_row(name)
            if raw_row is None:
                skipped.append({"dpu": dpu, "name": name, "reason": "raw 中找不到该点"})
                continue
            rows_for_nt6000.setdefault(dpu, {})[name] = raw_row
            f_added = filtered_table.append_row(raw_row)
            s_added = simple_table.append_row(raw_row)
            if f_added or s_added:
                changed = True
                touched_dpus.add(dpu)
                added.append({"dpu": dpu, "name": name, "filtered": f_added, "simple": s_added})
            else:
                skipped.append({"dpu": dpu, "name": name, "reason": "filtered/simple 已存在"})
        if changed:
            filtered_table.write()
            simple_table.write()

    nt6000 = _sync_nt6000_add(project_paths, rows_for_nt6000) if sync_nt6000 else {
        "changed": [], "skipped": [], "backups": []
    }
    for item in nt6000.get("changed", []):
        if item.get("changed"):
            touched_dpus.add(item.get("dpu", ""))
    touched_dpus.discard("")
    return {
        "ok": True,
        "added": added,
        "skipped": skipped,
        "touched_dpus": sorted(touched_dpus),
        "backups": backups,
        "nt6000": nt6000,
    }


def remove_points(project_paths, points: list[dict], sync_nt6000: bool = False) -> dict:
    """从 filtered/simple 删除点; raw 保持不动。"""
    grouped = _group_points(points)
    if not grouped:
        raise ValueError("没有可删除的点")

    backup_root = project_paths.root / "io" / "backups"
    removed = []
    skipped = []
    backups = []
    touched_dpus = set()

    for dpu, names in grouped.items():
        filtered_path = _find_table_file(project_paths.io_filtered_dir, dpu, simple=False)
        simple_path = _find_table_file(project_paths.io_simple_dir, dpu, simple=True)
        filtered_table = _read_optional(filtered_path)
        simple_table = _read_optional(simple_path)
        if not filtered_table and not simple_table:
            skipped.extend({"dpu": dpu, "name": n, "reason": "filtered/simple 点表不存在"} for n in sorted(names))
            continue
        backups.extend(x for x in (
            _backup_file(filtered_path, backup_root, "before-remove") if filtered_path and filtered_path.exists() else None,
            _backup_file(simple_path, backup_root, "before-remove") if simple_path and simple_path.exists() else None,
        ) if x)
        f_removed = filtered_table.remove_points(names) if filtered_table else 0
        s_removed = simple_table.remove_points(names) if simple_table else 0
        if f_removed and filtered_table:
            filtered_table.write()
        if s_removed and simple_table:
            simple_table.write()
        if f_removed or s_removed:
            touched_dpus.add(dpu)
            for n in sorted(names):
                removed.append({"dpu": dpu, "name": n})
        else:
            skipped.extend({"dpu": dpu, "name": n, "reason": "filtered/simple 未包含该点"} for n in sorted(names))

    nt6000 = _sync_nt6000_remove(project_paths, grouped) if sync_nt6000 else {
        "changed": [], "skipped": [], "backups": []
    }
    for item in nt6000.get("changed", []):
        if item.get("removed"):
            touched_dpus.add(item.get("dpu", ""))
    touched_dpus.discard("")
    return {
        "ok": True,
        "removed": removed,
        "skipped": skipped,
        "touched_dpus": sorted(touched_dpus),
        "backups": backups,
        "nt6000": nt6000,
    }


def mark_opc_visible(items: list[dict], opc_points: list[dict]) -> dict:
    """给点表项标记 OPC Browse 是否已经能看到。"""
    visible = set()
    visible_nodes = set()
    for p in opc_points:
        dpu = normalize_dpu(p.get("dpu", ""))
        name = normalize_point(p.get("name", ""))
        if dpu and name:
            if name.startswith("HW.") or name.startswith("SH"):
                visible.add((dpu, name))
            else:
                # browse_hw_points 返回短点名 AI010101, 点表里存 HW.AI010101.PV。
                visible.add((dpu, normalize_point(f"HW.{name}.PV")))
        node = p.get("node")
        if node:
            visible_nodes.add(str(node).upper())
    out = []
    ok_count = 0
    for item in items:
        key = (normalize_dpu(item.get("dpu", "")), normalize_point(item.get("name", "")))
        node = opc_node_from_item(item)
        it = dict(item)
        it["opc_visible"] = key in visible or (str(node).upper() in visible_nodes if node else False)
        if it["opc_visible"]:
            ok_count += 1
        out.append(it)
    return {"items": out, "visible": ok_count, "missing": len(out) - ok_count}
