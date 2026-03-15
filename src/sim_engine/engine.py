# -*- coding: utf-8 -*-
"""
仿真循环引擎

驱动画布组态的图执行，支持在线（OPC）和离线两种模式。
每一步：读取输入 → 图执行 → 写入输出 → 记录数据。

在线模式：IO 层信号通过 OPC UA 与 NTVDPU 闭环
离线模式：输入使用用户设定值，不连接 OPC
"""
import asyncio
import logging
import time
from typing import Dict, Optional

from .recorder import DataRecorder

logger = logging.getLogger(__name__)


class SimEngine:
    """
    仿真循环引擎

    核心职责：
    - 固定步长循环（默认 200ms）
    - 驱动 GraphRunner 执行画布组态
    - 在线模式管理 OPC 连接和 IO 读写
    - 过程数据记录
    """

    def __init__(self, graph_runner, step_size: float = 0.2):
        """
        Args:
            graph_runner: GraphRunner 实例（已 load 完成的可执行图）
            step_size: 仿真步长, 秒（默认 200ms）
        """
        self.graph = graph_runner
        self.step_size = step_size

        self.recorder = DataRecorder(max_rows=10000)

        self._running = False
        self._sim_time = 0.0
        self._step_count = 0

        # 在线模式的 OPC 相关
        self._opc_client = None
        self._mapping = None

    # ── 公共接口 ──────────────────────────────────────────

    async def start(self, duration: float = None,
                    initial_inputs: Dict[str, float] = None,
                    opc_client=None, mapping=None):
        """
        启动在线仿真（连接 OPC UA，与 NTVDPU 闭环）

        Args:
            duration: 运行时长, 秒。None = 持续运行直到调用 stop()
            initial_inputs: 初始输入值 {信号tag: 值}
            opc_client: OPCClient 实例
            mapping: SignalMapping 实例
        """
        self._print_banner("在线仿真")
        self._opc_client = opc_client
        self._mapping = mapping

        # 连接 OPC
        await self._opc_client.connect()

        try:
            self._initialize(initial_inputs)

            # 写入初始输出到 OPC
            if initial_inputs:
                await self._write_initial_outputs()
                logger.info("等待 AI 通道 HR/LR 生效 (1.5s)...")
                await asyncio.sleep(1.5)

            self._running = True
            logger.info("─── 在线仿真开始 ───")

            while self._running:
                t_wall = time.perf_counter()

                # 1. 读 OPC 输入
                io_values = await self._read_opc_inputs()

                # 2. 图执行
                outputs = self.graph.step(io_values, self.step_size)

                # 3. 写 OPC 输出
                await self._write_opc_outputs(outputs)

                # 4. 记录（所有节点值，含中间变量）
                all_values = self.graph.get_all_node_values()
                self.recorder.record(self._sim_time, all_values)

                # 5. 步进
                self._sim_time += self.step_size
                self._step_count += 1

                # 6. 定期日志
                if self._step_count % max(1, int(10.0 / self.step_size)) == 0:
                    self._log_status({**io_values, **outputs})

                # 7. 检查时长
                if duration is not None and self._sim_time >= duration:
                    break

                # 8. 实时节拍
                elapsed = time.perf_counter() - t_wall
                sleep_time = self.step_size - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        finally:
            self._running = False
            await self._opc_client.disconnect()
            self._print_summary()

    async def run_offline(self, duration: float,
                          initial_inputs: Dict[str, float] = None):
        """
        离线运行（不连接 OPC），输入使用设定值

        Args:
            duration: 运行时长, 秒
            initial_inputs: 初始输入值 {信号tag: 值}
        """
        self._print_banner("离线运行")
        self._initialize(initial_inputs)

        # 离线模式的输入固定为 initial_inputs
        io_values = dict(initial_inputs) if initial_inputs else {}

        self._running = True
        logger.info("─── 离线仿真开始 ───")

        while self._running and self._sim_time < duration:
            t_wall = time.perf_counter()

            # 图执行
            outputs = self.graph.step(io_values, self.step_size)

            # 记录（所有节点值，含中间变量）
            all_values = self.graph.get_all_node_values()
            self.recorder.record(self._sim_time, all_values)

            # 步进
            self._sim_time += self.step_size
            self._step_count += 1

            # 定期日志
            if self._step_count % max(1, int(10.0 / self.step_size)) == 0:
                self._log_status({**io_values, **outputs})

            # 实时节拍（Web UI 用）
            elapsed = time.perf_counter() - t_wall
            sleep_time = self.step_size - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        self._running = False
        self._print_summary()

    def request_stop(self):
        """同步停止请求（可从任意线程安全调用）"""
        self._running = False
        logger.info("收到停止信号，仿真将在当前步完成后停止")

    async def stop(self):
        """异步停止仿真"""
        self.request_stop()

    # ── 状态查询 ──────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def sim_time(self) -> float:
        return self._sim_time

    @property
    def step_count(self) -> int:
        return self._step_count

    def export_data(self, filepath: str):
        """导出过程数据到 CSV"""
        self.recorder.to_csv(filepath)

    # ── 内部方法 ──────────────────────────────────────────

    def _initialize(self, initial_inputs: Dict[str, float] = None):
        """重置引擎和图状态"""
        self._sim_time = 0.0
        self._step_count = 0
        self.recorder.clear()
        self.graph.reset()

        # 用初始输入执行一步以建立稳态
        if initial_inputs:
            self.graph.step(initial_inputs, self.step_size)

    async def _read_opc_inputs(self) -> Dict[str, float]:
        """从 OPC 批量读取图的输入信号"""
        result = {}
        if not self._mapping:
            return result

        input_tags = self.graph.get_input_tags()
        names = []
        nodes = []

        for tag in input_tags:
            sig = self._mapping.get(tag)
            if sig is not None:
                names.append(tag)
                nodes.append(sig.pv_node)

        if nodes:
            values = await self._opc_client.read_values(nodes)
            for i, name in enumerate(names):
                val = values[i]
                result[name] = float(val) if val is not None else 0.0

        return result

    def _collect_ai_channels(self, values: Dict[str, float]) -> Dict[str, float]:
        """将信号名→值映射转换为 OPC AI 通道→值映射（含冗余通道）"""
        channels = {}
        for name, value in values.items():
            sig = self._mapping.get(name)
            if sig and sig.channel_type.upper() == "AI":
                channels[sig.node_base] = value
            for redundant_base in self._mapping.get_redundant_channels(name):
                channels[redundant_base] = value
        return channels

    async def _write_opc_outputs(self, outputs: Dict[str, float]):
        """将图的输出信号写入 OPC AI 通道"""
        if not self._mapping:
            return
        channels = self._collect_ai_channels(outputs)
        if channels:
            await self._opc_client.write_ai_channels(channels)

    async def _write_initial_outputs(self):
        """将图的初始输出值写入 OPC"""
        if not self._mapping:
            return
        latest_outputs = self.graph.get_latest_outputs()
        channels = self._collect_ai_channels(latest_outputs)
        if channels:
            await self._opc_client.write_ai_channels(channels)
            logger.info(f"初始值已写入 {len(channels)} 个 AI 通道")

    # ── 日志与展示 ────────────────────────────────────────

    def _log_status(self, values: dict):
        """输出当前状态"""
        parts = [f"{k}={v:.2f}" for k, v in values.items()]
        logger.info(f"[t={self._sim_time:.1f}s | step={self._step_count}] {' | '.join(parts)}")

    def _print_banner(self, mode: str):
        """打印启动信息"""
        logger.info("=" * 60)
        logger.info(f"  仿真引擎 — {mode}")
        logger.info(f"  步长: {self.step_size * 1000:.0f}ms")
        logger.info(f"  图节点数: {self.graph.node_count}")
        logger.info(f"  输入: {self.graph.get_input_tags()}")
        logger.info(f"  输出: {self.graph.get_output_tags()}")
        logger.info("=" * 60)

    def _print_summary(self):
        """打印结束摘要"""
        logger.info("─── 仿真结束 ───")
        logger.info(f"  总步数: {self._step_count}")
        logger.info(f"  仿真时长: {self._sim_time:.1f}s")
        logger.info(f"  数据行数: {self.recorder.count}")
        if self.recorder.count > 0:
            logger.info(f"\n{self.recorder.summary()}")
