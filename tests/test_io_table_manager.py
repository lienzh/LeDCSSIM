# -*- coding: utf-8 -*-
from types import SimpleNamespace
from pathlib import Path
import csv

from src.viewer import io_table_manager as mgr


HEADER = (
    "~索引,测点名称,描述,设计编号,数据类型,读写属性,驱动类型,测点地址,单位,测点类型,"
    "工程上限,工程下限,优先级,安全区,报警区,历史存储,报警类型,高高,高,低,低低,"
    "历史缓存,OPC输出,语音报警,采集周期,历史库名称,自定义标题,自定义标题,自定义标题,"
    "自定义标题,自定义标题,自定义标题,自定义标题,自定义标题,自定义标题,自定义标题,签名,许可,"
    "报警确认时是否需要签名"
)


def _row(idx, name, desc, kks, opc="N"):
    cols = [""] * len(next(csv.reader([HEADER])))
    cols[0] = str(idx)
    cols[1] = name
    cols[2] = desc
    cols[3] = kks
    cols[4] = "FLOAT"
    cols[7] = name
    cols[22] = opc
    return ",".join(cols)


def _write_table(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "#VERSION,2,2025/1/30 9:27,4\n" + HEADER + "\n" + "\n".join(rows) + "\n"
    path.write_text(text, encoding="utf-8")


def _write_nt_tag(path: Path, rows: list[str]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = b"\xef\xbb\xbf\x00NT60\xf5\x01\x00\x00\x00\x00\n"
    text = "#VERSION,2,2025/1/30 9:27,4\n" + HEADER + "\n" + "\n".join(rows) + "\n"
    path.write_bytes(prefix + text.encode("utf-8"))
    return prefix


def _paths(tmp_path):
    root = tmp_path / "projects" / "demo"
    return SimpleNamespace(
        root=root,
        io_raw_dir=root / "io" / "raw",
        io_filtered_dir=root / "io" / "filtered",
        io_simple_dir=root / "io" / "simple",
    )


def test_search_raw_marks_filtered_and_simple_status(tmp_path):
    pp = _paths(tmp_path)
    _write_table(pp.io_raw_dir / "DPU3001.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101"),
        _row(2, "HW.AQ010102.PV", "指令2", "30AAA01AA1010"),
    ])
    _write_table(pp.io_filtered_dir / "DPU3001.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101", opc="Y"),
    ])
    _write_table(pp.io_simple_dir / "DPU3001_S.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101", opc="Y"),
    ])

    data = mgr.search_raw_points(pp, query="压力")
    assert data["count"] == 1
    assert data["items"][0]["dpu"] == "DPU3001"
    assert data["items"][0]["in_filtered"] is True
    assert data["items"][0]["in_simple"] is True


def test_search_raw_supports_nested_nt6000_tag_csv_and_non_hw_points(tmp_path):
    pp = _paths(tmp_path)
    tag = pp.io_raw_dir / "DPU3002" / "tag.csv"
    tag.parent.mkdir(parents=True)
    text = (
        "NT6000-SIGNATURE-BYTES\n"
        "#VERSION,2,2025/1/30 9:27,4\n"
        + HEADER + "\n"
        + _row(1, "SH0031.BALM1.PV", "A磨一次风流量故障", "30HFE01AA101") + "\n"
        + _row(2, "HW.AI010101.PV", "一次风量", "30HFE01CF101") + "\n"
    )
    tag.write_text(text, encoding="utf-8")

    data = mgr.search_raw_points(pp, query="BALM")

    assert data["raw_dir"].endswith("io\\raw") or data["raw_dir"].endswith("io/raw")
    assert data["count"] == 1
    assert data["items"][0]["dpu"] == "DPU3002"
    assert data["items"][0]["name"] == "SH0031.BALM1.PV"
    assert data["items"][0]["code"] == "PV"


def test_add_points_copies_raw_row_to_filtered_and_simple_with_opc_y(tmp_path):
    pp = _paths(tmp_path)
    _write_table(pp.io_raw_dir / "DPU3001.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101"),
        _row(2, "HW.AQ010102.PV", "指令2", "30AAA01AA1010"),
    ])
    _write_table(pp.io_filtered_dir / "DPU3001.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101", opc="Y"),
    ])
    _write_table(pp.io_simple_dir / "DPU3001_S.csv", [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101", opc="Y"),
    ])

    result = mgr.add_points(pp, [{"dpu": "DPU3001", "name": "HW.AQ010102.PV"}])

    assert result["touched_dpus"] == ["DPU3001"]
    for path in (pp.io_filtered_dir / "DPU3001.csv", pp.io_simple_dir / "DPU3001_S.csv"):
        table = mgr.CsvTable.read(path)
        assert [r[0] for r in table.rows] == ["1", "2"]
        added = table.find_row("HW.AQ010102.PV")
        assert added is not None
        assert added[22] == "Y"
    assert result["backups"]


def test_add_points_uses_raw_dir(tmp_path):
    pp = _paths(tmp_path)
    _write_table(pp.io_raw_dir / "DPU3002" / "tag.csv", [
        _row(1, "SH0031.BALM1.PV", "A磨一次风流量故障", "30HFE01AA101"),
    ])

    result = mgr.add_points(pp, [{"dpu": "DPU3002", "name": "SH0031.BALM1.PV"}])

    assert result["added"][0]["dpu"] == "DPU3002"
    assert (pp.io_filtered_dir / "DPU3002.csv").exists()
    assert (pp.io_simple_dir / "DPU3002_S.csv").exists()
    assert mgr.CsvTable.read(pp.io_simple_dir / "DPU3002_S.csv").find_row("SH0031.BALM1.PV")[22] == "Y"



def test_add_points_creates_missing_filtered_and_simple_files(tmp_path):
    pp = _paths(tmp_path)
    _write_table(pp.io_raw_dir / "DPU3010.csv", [
        _row(1, "HW.TC010102.PV", "给水温度", "30LAB90CT704"),
    ])

    result = mgr.add_points(pp, [{"dpu": "3010", "name": "HW.TC010102.PV"}])

    assert result["added"][0]["dpu"] == "DPU3010"
    assert (pp.io_filtered_dir / "DPU3010.csv").exists()
    assert (pp.io_simple_dir / "DPU3010_S.csv").exists()
    assert mgr.CsvTable.read(pp.io_simple_dir / "DPU3010_S.csv").find_row("HW.TC010102.PV")[22] == "Y"


def test_remove_points_only_changes_filtered_and_simple_not_raw(tmp_path):
    pp = _paths(tmp_path)
    rows = [
        _row(1, "HW.AI010101.PV", "压力1", "30AAA01CP101", opc="Y"),
        _row(2, "HW.AQ010102.PV", "指令2", "30AAA01AA1010", opc="Y"),
    ]
    _write_table(pp.io_raw_dir / "DPU3001.csv", rows)
    _write_table(pp.io_filtered_dir / "DPU3001.csv", rows)
    _write_table(pp.io_simple_dir / "DPU3001_S.csv", rows)

    result = mgr.remove_points(pp, [{"dpu": "DPU3001", "name": "HW.AI010101.PV"}])

    assert result["touched_dpus"] == ["DPU3001"]
    assert mgr.CsvTable.read(pp.io_raw_dir / "DPU3001.csv").find_row("HW.AI010101.PV") is not None
    for path in (pp.io_filtered_dir / "DPU3001.csv", pp.io_simple_dir / "DPU3001_S.csv"):
        table = mgr.CsvTable.read(path)
        assert table.find_row("HW.AI010101.PV") is None
        assert table.find_row("HW.AQ010102.PV") is not None
        assert [r[0] for r in table.rows] == ["1"]


def test_add_points_syncs_nt6000_project_temp_target_preserving_signature(tmp_path):
    pp = _paths(tmp_path)
    pp.nt6000_home = tmp_path / "NT6000V5"
    _write_table(pp.io_raw_dir / "DPU3001.csv", [
        _row(1, "SH0063.BALM1.PV", "燃油压力低", "", opc="N"),
    ])
    paths = [
        pp.nt6000_home / "project" / "demo" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
        pp.nt6000_home / "temp" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
        pp.nt6000_home / "target" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
    ]
    prefixes = [_write_nt_tag(p, []) for p in paths]

    result = mgr.add_points(pp, [{"dpu": "DPU3001", "name": "SH0063.BALM1.PV"}], sync_nt6000=True)

    assert len([x for x in result["nt6000"]["changed"] if x["changed"]]) == 3
    for p, prefix in zip(paths, prefixes):
        assert p.read_bytes().startswith(prefix)
        row = mgr.CsvTable.read(p).find_row("SH0063.BALM1.PV")
        assert row is not None
        assert row[22] == "Y"


def test_add_existing_project_point_still_syncs_nt6000(tmp_path):
    pp = _paths(tmp_path)
    pp.nt6000_home = tmp_path / "NT6000V5"
    row = _row(1, "SH0063.BALM1.PV", "燃油压力低", "", opc="N")
    _write_table(pp.io_raw_dir / "DPU3001.csv", [row])
    _write_table(pp.io_filtered_dir / "DPU3001.csv", [row])
    _write_table(pp.io_simple_dir / "DPU3001_S.csv", [row])
    project_tag = pp.nt6000_home / "project" / "demo" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv"
    temp_tag = pp.nt6000_home / "temp" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv"
    target_tag = pp.nt6000_home / "target" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv"
    for p in (project_tag, temp_tag, target_tag):
        _write_nt_tag(p, [])

    result = mgr.add_points(pp, [{"dpu": "DPU3001", "name": "SH0063.BALM1.PV"}], sync_nt6000=True)

    assert result["added"] == []
    assert result["touched_dpus"] == ["DPU3001"]
    assert mgr.CsvTable.read(project_tag).find_row("SH0063.BALM1.PV") is not None


def test_remove_points_syncs_nt6000_project_temp_target_preserving_signature(tmp_path):
    pp = _paths(tmp_path)
    pp.nt6000_home = tmp_path / "NT6000V5"
    rows = [_row(1, "SH0063.BALM1.PV", "燃油压力低", "", opc="Y")]
    _write_table(pp.io_filtered_dir / "DPU3001.csv", rows)
    _write_table(pp.io_simple_dir / "DPU3001_S.csv", rows)
    paths = [
        pp.nt6000_home / "project" / "demo" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
        pp.nt6000_home / "temp" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
        pp.nt6000_home / "target" / "network" / "network3" / "DPU" / "DPU3001" / "tag.csv",
    ]
    prefixes = [_write_nt_tag(p, rows) for p in paths]

    result = mgr.remove_points(pp, [{"dpu": "DPU3001", "name": "SH0063.BALM1.PV"}], sync_nt6000=True)

    assert len([x for x in result["nt6000"]["changed"] if x["removed"] == 1]) == 3
    for p, prefix in zip(paths, prefixes):
        assert p.read_bytes().startswith(prefix)
        assert mgr.CsvTable.read(p).find_row("SH0063.BALM1.PV") is None


def test_mark_opc_visible_uses_browsed_hw_points(tmp_path):
    items = [{"dpu": "DPU3001", "name": "HW.AI010101.PV"}]
    opc = [{"dpu": "DPU3001", "name": "AI010101", "code": "AI"}]

    marked = mgr.mark_opc_visible(items, opc)

    assert marked["visible"] == 1
    assert marked["missing"] == 0
    assert marked["items"][0]["opc_visible"] is True


def test_opc_node_from_item_keeps_full_sh_path():
    item = {"dpu": "DPU3012", "name": "DPU3012.SH0098.AALMFCF.PV"}

    node = mgr.opc_node_from_item(item)

    assert node == "ns=0;s=DPU3012.SH0098.AALMFCF.PV"


def test_mark_opc_visible_uses_full_node_for_sh_points(tmp_path):
    items = [{"dpu": "DPU3012", "name": "SH0098.AALMFCF.PV"}]
    opc = [{
        "dpu": "DPU3012",
        "name": "SH0098.AALMFCF.PV",
        "code": "PV",
        "node": "ns=0;s=DPU3012.SH0098.AALMFCF.PV",
    }]

    marked = mgr.mark_opc_visible(items, opc)

    assert marked["visible"] == 1
    assert marked["missing"] == 0
    assert marked["items"][0]["opc_visible"] is True
