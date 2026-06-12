# -*- coding: utf-8 -*-
"""
OPC UA 客户端封装
负责与科远 NTVDPU 虚拟控制器通讯，支持批量读写。
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

from asyncua import Client, ua

logger = logging.getLogger(__name__)


class OPCClient:
    """
    OPC UA 客户端

    用法:
        client = OPCClient("opc.tcp://localhost:9440")
        await client.connect()

        # 批量读
        values = await client.read_values(["node_id_1", "node_id_2"])

        # 批量写
        await client.write_values({"node_id_1": 100.0, "node_id_2": 200.0})

        await client.disconnect()
    """

    def __init__(self, url: str, timeout: float = 10.0):
        """
        Args:
            url: OPC UA Server 地址, 如 "opc.tcp://localhost:9440"
            timeout: 会话超时时间, 秒
        """
        self.url = url
        self.timeout = timeout
        self._client: Optional[Client] = None
        self._connected = False
        # 节点对象缓存, 避免重复创建  {node_id_str: Node}
        self._node_cache: Dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, retry_count: int = 10, retry_interval: float = 3.0):
        """
        连接 OPC UA Server，失败自动重试

        Args:
            retry_count: 最大重试次数
            retry_interval: 重试间隔, 秒
        """
        self._client = Client(self.url)
        self._client.session_timeout = int(self.timeout * 1000)

        for attempt in range(1, retry_count + 1):
            try:
                await self._client.connect()
                self._connected = True
                logger.info(f"OPC UA 连接成功: {self.url}")
                return
            except Exception as e:
                logger.warning(f"连接失败 (第{attempt}次): {e}")
                if attempt < retry_count:
                    await asyncio.sleep(retry_interval)

        raise ConnectionError(f"OPC UA 连接失败，已重试{retry_count}次: {self.url}")

    async def disconnect(self):
        """断开连接"""
        if self._client and self._connected:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning(f"断开连接异常: {e}")
            finally:
                self._connected = False
                self._node_cache.clear()
                logger.info("OPC UA 已断开")

    def _get_node(self, node_id: str):
        """
        获取节点对象（带缓存）

        Args:
            node_id: 节点标识, 如 "ns=0;s=DPU3013.HW.AI010605.PV"
        """
        if node_id not in self._node_cache:
            self._node_cache[node_id] = self._client.get_node(node_id)
        return self._node_cache[node_id]

    async def read_value(self, node_id: str) -> Any:
        """
        读取单个节点值（容忍 UncertainInitialValue 状态）

        Args:
            node_id: 节点标识
        Returns:
            节点当前值
        """
        node = self._get_node(node_id)
        dv = await node.read_data_value(raise_on_bad_status=False)
        return dv.Value.Value

    async def read_data_value(self, node_id: str) -> Any:
        """
        读取单个节点的完整 DataValue（含源时间戳和状态码）

        Args:
            node_id: 节点标识
        Returns:
            asyncua DataValue 对象（.Value.Value 为值，.SourceTimestamp 为源时间戳）
        """
        node = self._get_node(node_id)
        return await node.read_data_value(raise_on_bad_status=False)

    async def read_values(self, node_ids: List[str]) -> List[Any]:
        """
        批量读取节点值

        Args:
            node_ids: 节点标识列表
        Returns:
            值列表, 顺序与输入一致。读取失败的返回 None
        """
        # 分批并发, 防压垮 NTVDPU
        BATCH = 100
        results = []
        for i in range(0, len(node_ids), BATCH):
            chunk = node_ids[i:i+BATCH]
            raw = await asyncio.gather(*[self.read_value(nid) for nid in chunk],
                                       return_exceptions=True)
            for nid, val in zip(chunk, raw):
                if isinstance(val, Exception):
                    logger.warning(f"读取失败 [{nid}]: {val}")
                    results.append(None)
                else:
                    results.append(val)
        return results

    # NTVDPU 怪点缓存: 某些 DI/AI 点 read 返回的 VariantType (常是 Boolean)
    # 跟 write attribute schema (实际只接受 Float) 不一致, 直写报 BadTypeMismatch.
    # 一旦命中, 该节点永久标记为"用 Float 写", 下次跳过 Boolean 直接 Float.
    _FORCE_FLOAT_NODES: set = set()

    @staticmethod
    def _adapt_value(value: Any, vt: "ua.VariantType") -> Any:
        """Python 值类型适配 NTVDPU 严格类型"""
        if vt == ua.VariantType.Boolean and not isinstance(value, bool):
            if isinstance(value, (int, float)):
                return bool(value)
        elif vt in (ua.VariantType.Float, ua.VariantType.Double):
            if isinstance(value, (bool, int)):
                return float(value)
        elif vt in (ua.VariantType.SByte, ua.VariantType.Byte,
                    ua.VariantType.Int16, ua.VariantType.UInt16,
                    ua.VariantType.Int32, ua.VariantType.UInt32,
                    ua.VariantType.Int64, ua.VariantType.UInt64):
            if isinstance(value, (bool, float)):
                return int(value)
        return value

    async def write_value(self, node_id: str, value: Any,
                          variant_type: ua.VariantType = None):
        """
        写入单个节点值(自动 VariantType 检测 + Python 值类型适配)

        遇到 BadTypeMismatch 自动 fallback 到 Float 重试 — NTVDPU 部分 DI 点的怪行为:
        read 返回 Boolean, write 期望 Float. 命中即缓存, 下次直接 Float.

        Args:
            node_id: 节点标识
            value: 要写入的值(允许 float 写到 Boolean 节点等,自动转)
            variant_type: OPC UA 数据类型。为 None 时自动检测
        """
        node = self._get_node(node_id)
        # 已知怪点 → 直接 Float, 跳过 Boolean
        if variant_type is None and node_id in self._FORCE_FLOAT_NODES:
            variant_type = ua.VariantType.Float
        if variant_type is None:
            dv = await node.read_data_value(raise_on_bad_status=False)
            variant_type = dv.Value.VariantType
        adapted = self._adapt_value(value, variant_type)
        try:
            await node.write_value(ua.DataValue(ua.Variant(adapted, variant_type)))
        except ua.uaerrors.BadTypeMismatch:
            # NTVDPU read/write VariantType 不一致 — 用 Float 重试
            float_val = self._adapt_value(value, ua.VariantType.Float)
            try:
                await node.write_value(ua.DataValue(ua.Variant(float_val, ua.VariantType.Float)))
            except Exception:
                raise   # Float 也不行就抛原异常类型
            else:
                # Float 写成功, 标记节点, 下次绕开 Boolean
                self._FORCE_FLOAT_NODES.add(node_id)
                logger.info(f"[NTVDPU quirk] {node_id} 用 Float 写成功 (Boolean 被拒), 已缓存")

    async def write_values(self, values: Dict[str, Any],
                           variant_types: Dict[str, ua.VariantType] = None):
        """
        批量写入节点值

        Args:
            values: {node_id: value} 字典
            variant_types: {node_id: VariantType} 字典, 可选
        """
        if variant_types is None:
            variant_types = {}

        # 分批并发,避免一次性 gather 上千个并发请求压垮 NTVDPU
        BATCH = 50
        out: Dict[str, bool] = {}
        node_list = list(values.keys())
        for i in range(0, len(node_list), BATCH):
            chunk = node_list[i:i+BATCH]
            tasks = [self.write_value(nid, values[nid], variant_types.get(nid))
                     for nid in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, r in zip(chunk, results):
                if isinstance(r, Exception):
                    logger.warning(f"写入失败 [{nid}]: {r}")
                    out[nid] = False
                else:
                    out[nid] = True
        return out

    async def write_ai_channel(self, channel_base: str, target_value: float,
                               variant_type: ua.VariantType = None):
        """
        写入 AI 通道 PV

        前置条件: NTVDPU 端 AI 通道已下装包含 HR/LR 行的点表
        (HR/LR 节点的存在会触发 AI 通道切换为"外部驱动模式",信号发生器停,PV 直接可写)

        Args:
            channel_base: AI 通道基础路径, 如 "ns=0;s=DPU3013.HW.AI010502"
                          (不含 .PV;函数内部自动补)
            target_value: 目标值
            variant_type: 数据类型, 为 None 时自动检测
        """
        pv_id = f"{channel_base}.PV"
        await self.write_value(pv_id, target_value, variant_type)

    async def write_ai_channels(self, channels: Dict[str, float],
                                variant_type: ua.VariantType = None) -> Dict[str, Optional[str]]:
        """
        批量写入多个 AI 通道(单个失败不影响其他)

        Args:
            channels: {通道基础路径: 目标值} 字典
        Returns:
            {channel_base: 错误信息 or None} — 成功的为 None
        """
        bases = list(channels.keys())
        tasks = [self.write_ai_channel(b, channels[b], variant_type) for b in bases]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: Dict[str, Optional[str]] = {}
        for base, r in zip(bases, results):
            if isinstance(r, Exception):
                logger.warning(f"AI 通道写入失败 [{base}]: {r}")
                out[base] = str(r)
            else:
                out[base] = None
        return out

    async def browse_children(self, node_id: str) -> List[Tuple[str, str]]:
        """
        浏览节点的子节点

        Args:
            node_id: 父节点标识
        Returns:
            [(子节点名, 子节点ID), ...]
        """
        node = self._get_node(node_id)
        children = await node.get_children()
        result = []
        for child in children:
            name = await child.read_browse_name()
            result.append((name.Name, child.nodeid.to_string()))
        return result

    async def browse_hw_points(self, dpu_name: str) -> List[dict]:
        """
        浏览指定 DPU 的所有硬件点 (HW.* 下的 PV 节点)。

        Args:
            dpu_name: DPU 节点名, 如 "DPU3013"
        Returns:
            [{"name": "AI010605", "code": "AI", "node": "ns=0;s=DPU3013.HW.AI010605.PV"}, ...]
        """
        import re as _re
        result = []
        try:
            hw_children = await self.browse_children(f"ns=0;s={dpu_name}.HW")
        except Exception as e:
            logger.warning(f"浏览 {dpu_name}.HW 失败: {e}")
            return result
        # hw_children: [("AI010605", "ns=0;s=DPU3013.HW.AI010605"), ...]
        PT = _re.compile(r"^([A-Z]+)(\d+)$")
        for name, _id in hw_children:
            m = PT.match(name)
            if not m:
                continue
            result.append({
                "name": name,
                "code": m.group(1),
                "node": f"ns=0;s={dpu_name}.HW.{name}.PV",
            })
        return result
