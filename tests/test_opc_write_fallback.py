# -*- coding: utf-8 -*-
"""OPCClient.write_value 类型回退测试 — NTVDPU DI 写 schema 会随通道状态翻转

实测 (2026-06-13): 同一节点 (DPU3005.HW.DI010402.PV) 在 6-10 是"读 Boolean/只收 Float",
之后翻转为"只收 Boolean、拒 Float"。fallback 必须双向 + _FORCE_FLOAT_NODES 缓存可逆,
否则缓存方向跟服务器当前 schema 相反时, 该节点每周期死循环 BadTypeMismatch。

跑法: py -3.12 -m pytest tests/test_opc_write_fallback.py -v
"""
import asyncio

import pytest
from asyncua import ua

from src.opc_client.client import OPCClient

NID = "ns=0;s=DPU3005.HW.DI010402.PV"


class _FakeNode:
    """假 OPC 节点: 只接受指定 VariantType 的写入, 其余抛 BadTypeMismatch"""

    def __init__(self, accept_vt, read_vt=ua.VariantType.Boolean, read_val=True):
        self.accept_vt = accept_vt          # None = 全部拒绝
        self.writes = []                    # 记录每次写尝试的 VariantType
        self._dv = ua.DataValue(ua.Variant(read_val, read_vt))

    async def read_data_value(self, raise_on_bad_status=True):
        return self._dv

    async def write_value(self, dv):
        vt = dv.Value.VariantType
        self.writes.append(vt)
        if self.accept_vt is None or vt != self.accept_vt:
            raise ua.uaerrors.BadTypeMismatch()


def _make_client(fake_node):
    c = OPCClient("opc.tcp://fake:0")
    c._get_node = lambda nid: fake_node     # 不真连, 注入假节点
    return c


@pytest.fixture(autouse=True)
def _clean_cache():
    """类级缓存隔离: 每个测试前后清空"""
    OPCClient._FORCE_FLOAT_NODES.clear()
    yield
    OPCClient._FORCE_FLOAT_NODES.clear()


def test_cached_float_but_server_wants_boolean_self_heals():
    """缓存说 Float、服务器已翻转只收 Boolean → 应回退 Boolean 成功 + 缓存移除"""
    fake = _FakeNode(accept_vt=ua.VariantType.Boolean)
    c = _make_client(fake)
    OPCClient._FORCE_FLOAT_NODES.add(NID)

    asyncio.run(c.write_value(NID, True))

    assert fake.writes == [ua.VariantType.Float, ua.VariantType.Boolean]
    assert NID not in OPCClient._FORCE_FLOAT_NODES, "缓存应翻转移除"
    # 自愈后第二次写: 直接 Boolean 一发命中
    asyncio.run(c.write_value(NID, False))
    assert fake.writes[-1] == ua.VariantType.Boolean
    assert len(fake.writes) == 3


def test_boolean_rejected_float_accepted_caches():
    """原方向 (6-10 实测): 读 Boolean 写要 Float → Float 重试成功 + 入缓存"""
    fake = _FakeNode(accept_vt=ua.VariantType.Float)
    c = _make_client(fake)

    asyncio.run(c.write_value(NID, True))

    assert fake.writes == [ua.VariantType.Boolean, ua.VariantType.Float]
    assert NID in OPCClient._FORCE_FLOAT_NODES
    # 缓存生效: 第二次直接 Float
    asyncio.run(c.write_value(NID, False))
    assert fake.writes[-1] == ua.VariantType.Float
    assert len(fake.writes) == 3


def test_both_types_rejected_raises():
    """两个方向都被拒 → 异常抛给上层 (write_values 记日志不中断循环)"""
    fake = _FakeNode(accept_vt=None)
    c = _make_client(fake)

    with pytest.raises(ua.uaerrors.BadTypeMismatch):
        asyncio.run(c.write_value(NID, True))
    assert len(fake.writes) == 2            # 原类型 + 另一边各试一次, 不无限重试
    assert NID not in OPCClient._FORCE_FLOAT_NODES
