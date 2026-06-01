# -*- coding: utf-8 -*-
"""
OPC 联机验证 - 批量读 tagmap 里所有 in 端 tag, 看通讯是否生效

输出:
- 连接状态
- 每个 in tag 的 (值, SourceTimestamp, 是否成功)
- 统计: 成功/失败按 DPU 分布
"""
import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

from src.engine import TagMap
from src.opc_client.client import OPCClient

logging.basicConfig(level=logging.WARNING)  # 静默 INFO 减少噪音

TAGMAP_PATH = "config/tagmap.generated.yaml"
OPC_URL = "opc.tcp://localhost:9440"
SAMPLE_N = 6   # 每个 DPU 抽样输出前 N 个详细


async def main():
    tm = TagMap.from_yaml(TAGMAP_PATH)
    in_tags = tm.tags_by_direction("in")
    out_tags = tm.tags_by_direction("out")
    print(f"tagmap 加载: {len(in_tags)} 个 in tag, {len(out_tags)} 个 out tag")
    print(f"连接 {OPC_URL} ...")

    client = OPCClient(OPC_URL)
    try:
        await client.connect(retry_count=3, retry_interval=2.0)
    except Exception as e:
        print(f"\n[FATAL] OPC 连接失败: {e}")
        return 1
    print("OPC 已连接 ✓\n")

    # 批量读
    node_ids = [tm.tag_to_node(t) for t in in_tags]
    print(f"批量读 {len(node_ids)} 个节点 ...")
    raw_values = await client.read_values(node_ids)

    # 也分别读 DataValue 取 SourceTimestamp (取前 N 个)
    sample_timestamps = {}
    for tag, node_id in list(zip(in_tags, node_ids))[:SAMPLE_N]:
        try:
            dv = await client.read_data_value(node_id)
            sample_timestamps[tag] = dv.SourceTimestamp
        except Exception:
            sample_timestamps[tag] = None

    await client.disconnect()

    # 统计
    by_dpu = defaultdict(lambda: {"ok": 0, "fail": 0, "samples": []})
    overall_ok = 0
    overall_fail = 0
    for tag, val in zip(in_tags, raw_values):
        dpu = tag.split("_", 1)[0] if "_" in tag else "?"
        entry = tm.get(tag)
        dtype = entry.get("dtype", "?")
        if val is None:
            by_dpu[dpu]["fail"] += 1
            overall_fail += 1
        else:
            by_dpu[dpu]["ok"] += 1
            overall_ok += 1
        if len(by_dpu[dpu]["samples"]) < SAMPLE_N:
            by_dpu[dpu]["samples"].append((tag, dtype, val, entry.get("desc", "")))

    # 输出统计
    print(f"\n{'='*78}")
    print(f"{'DPU':<10} {'tag 数':>6} {'成功读':>6} {'失败':>5}  {'成功率':>7}")
    print('-' * 78)
    for dpu in sorted(by_dpu.keys()):
        s = by_dpu[dpu]
        total = s["ok"] + s["fail"]
        rate = s["ok"] / total * 100 if total else 0
        print(f"{dpu:<10} {total:>6} {s['ok']:>6} {s['fail']:>5}  {rate:>6.1f}%")
    print('-' * 78)
    print(f"{'合计':<10} {len(in_tags):>6} {overall_ok:>6} {overall_fail:>5}  "
          f"{overall_ok/len(in_tags)*100:>6.1f}%")
    print('=' * 78)

    # 输出每 DPU 抽样
    for dpu in sorted(by_dpu.keys()):
        print(f"\n[{dpu}] 抽样前 {SAMPLE_N} 个:")
        print(f"  {'tag':<28} {'dtype':>5} {'值':>15}  描述")
        for tag, dtype, val, desc in by_dpu[dpu]["samples"]:
            val_str = "FAIL" if val is None else f"{val}"
            print(f"  {tag:<28} {dtype:>5} {val_str:>15}  {desc[:30]}")

    # 时间戳样本
    print(f"\n前 {SAMPLE_N} 个 SourceTimestamp 样本 (NTVDPU 服务端时间):")
    for tag, ts in sample_timestamps.items():
        print(f"  {tag:<28} {ts}")

    # 健康度提示
    if overall_fail == 0:
        print("\n[OK] 全部 in tag 读取成功 — 在线模式可以跑")
    elif overall_ok == 0:
        print("\n[FAIL] 全部失败 — 检查 NTVDPU 是否启动 / CCMStudio 是否下装")
    else:
        print(f"\n[PARTIAL] 部分成功 — 可能部分通道未在 CCMStudio 中开通")
        print("失败的 tag 多数是 DPU 端尚未配置 OPC 通讯,")
        print("回 CCMStudio 检查这些点的'OPC 通讯'列是否已勾选并下装")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
