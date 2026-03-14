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

    async def read_values(self, node_ids: List[str]) -> List[Any]:
        """
        批量读取节点值

        Args:
            node_ids: 节点标识列表
        Returns:
            值列表, 顺序与输入一致。读取失败的返回 None
        """
        results = []
        tasks = [self.read_value(nid) for nid in node_ids]
        for coro in asyncio.as_completed(tasks):
            pass  # as_completed 不保序

        # 用 gather 保序
        raw = await asyncio.gather(*[self.read_value(nid) for nid in node_ids],
                                   return_exceptions=True)
        for i, val in enumerate(raw):
            if isinstance(val, Exception):
                logger.warning(f"读取失败 [{node_ids[i]}]: {val}")
                results.append(None)
            else:
                results.append(val)
        return results

    async def write_value(self, node_id: str, value: Any,
                          variant_type: ua.VariantType = None):
        """
        写入单个节点值

        Args:
            node_id: 节点标识
            value: 要写入的值
            variant_type: OPC UA 数据类型。为 None 时自动检测
        """
        node = self._get_node(node_id)
        if variant_type is None:
            dv = await node.read_data_value(raise_on_bad_status=False)
            variant_type = dv.Value.VariantType
        await node.write_value(ua.DataValue(ua.Variant(value, variant_type)))

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

        tasks = []
        for node_id, value in values.items():
            vt = variant_types.get(node_id)
            tasks.append(self.write_value(node_id, value, vt))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, (node_id, _) in enumerate(values.items()):
            if isinstance(results[i], Exception):
                logger.warning(f"写入失败 [{node_id}]: {results[i]}")

    async def write_ai_channel(self, channel_base: str, target_value: float,
                               variant_type: ua.VariantType = None):
        """
        通过 HR=LR=目标值 写入 AI 通道

        AI 硬点 PV 不可直接写入，但可以通过设置 HR 和 LR 为相同值
        使 PV 锁定在该值。

        Args:
            channel_base: AI 通道基础路径, 如 "ns=0;s=DPU3013.HW.AI010605"
            target_value: 目标值
            variant_type: 数据类型, 为 None 时自动检测
        """
        hr_id = f"{channel_base}.HR"
        lr_id = f"{channel_base}.LR"
        await asyncio.gather(
            self.write_value(hr_id, target_value, variant_type),
            self.write_value(lr_id, target_value, variant_type),
        )

    async def write_ai_channels(self, channels: Dict[str, float],
                                variant_type: ua.VariantType = None):
        """
        批量写入多个 AI 通道

        Args:
            channels: {通道基础路径: 目标值} 字典
        """
        tasks = []
        for base, value in channels.items():
            tasks.append(self.write_ai_channel(base, value, variant_type))
        await asyncio.gather(*tasks)

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
