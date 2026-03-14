# -*- coding: utf-8 -*-
"""
仿真循环引擎

驱动仿真模型与科远 NTVDPU 的闭环运行。
每一步：读取 OPC 输入 → 模型计算 → 写入 OPC 输出 → 记录数据。

支持两种运行模式：
    1. 在线模式 (start)：连接 OPC UA，与 NTVDPU 闭环
    2. 离线模式 (run_offline)：不连 OPC，用自定义输入函数驱动，调试用

用法：
    model = MyModel("测试模型")
    mapping = SignalMapping.from_yaml("config/opc_mapping.yaml")
    engine = SimEngine(model, mapping, step_size=0.2)

    # 在线闭环
    await engine.start(duration=60)

    # 离线调试
    await engine.run_offline(duration=10, input_func=lambda t: {"valve": 50 + t})

命令行运行：
    py -3.12 -m src.sim_engine.engine
"""
import asyncio
import logging
import time
from typing import Callable, Dict, Optional

from .model import SimModel
from .recorder import DataRecorder
from ..opc_client import OPCClient, SignalMapping

logger = logging.getLogger(__name__)


class SimEngine:
    """
    仿真循环引擎

    核心职责：
    - 管理 OPC 连接生命周期
    - 固定步长循环（默认 200ms）
    - 模型信号与 OPC 节点的自动映射
    - 过程数据记录
    """

    def __init__(self, model: SimModel, mapping: SignalMapping,
                 opc_url: str = "opc.tcp://localhost:9440",
                 step_size: float = 0.2):
        """
        Args:
            model: 仿真模型实例
            mapping: 信号映射（模型变量名 ↔ OPC 节点）
            opc_url: OPC UA Server 地址
            step_size: 仿真步长, 秒（默认 200ms）
        """
        self.model = model
        self.mapping = mapping
        self.opc_url = opc_url
        self.step_size = step_size

        self.recorder = DataRecorder()

        self._client: Optional[OPCClient] = None
        self._running = False
        self._sim_time = 0.0
        self._step_count = 0
        self._input_overrides: Optional[Callable] = None
        self._readback_nodes: Dict[str, str] = {}

        # 启动时校验映射
        self._validate_mapping()

    # ── 公共接口 ──────────────────────────────────────────

    async def start(self, duration: float = None,
                    initial_values: Dict[str, float] = None,
                    input_overrides: Callable[[float], Dict[str, float]] = None,
                    readback_nodes: Dict[str, str] = None):
        """
        启动在线仿真（连接 OPC UA，与 NTVDPU 闭环）

        Args:
            duration: 运行时长, 秒。None = 持续运行直到调用 stop()
            initial_values: 初始工况 {信号名: 值}，写入 OPC 并初始化模型
            input_overrides: 输入覆盖函数 f(t) -> {信号名: 值}，
                             用于开环输入（如煤量调度），返回的信号不从 OPC 读取
            readback_nodes: OPC 读回节点 {显示名: 节点ID}，写入后读回验证
        """
        self._print_banner("在线仿真")
        self._input_overrides = input_overrides
        self._readback_nodes = readback_nodes or {}

        # 1. 连接 OPC
        self._client = OPCClient(self.opc_url)
        await self._client.connect()

        try:
            # 2. 初始化
            await self._initialize(initial_values)

            # 3. 写入输出初始值到 OPC
            if initial_values:
                await self._write_initial_outputs(initial_values)
                logger.info("等待 AI 通道 HR/LR 生效 (1.5s)...")
                await asyncio.sleep(1.5)

            # 4. 主循环
            self._running = True
            logger.info("─── 仿真开始 ───")
            await self._loop(duration)

        finally:
            self._running = False
            self._input_overrides = None
            await self._client.disconnect()
            self._print_summary()

    async def run_offline(self, duration: float,
                          input_func: Callable[[float], Dict[str, float]] = None,
                          realtime: bool = False):
        """
        离线运行（不连接 OPC），用于模型调试

        Args:
            duration: 运行时长, 秒
            input_func: 输入函数 f(t) -> {信号名: 值}
                        None 时使用各输入信号的默认值
            realtime: True 时按实际步长节拍运行（Web UI 用），False 时全速运行
        """
        self._print_banner("离线调试" + (" (实时)" if realtime else " (全速)"))
        self._initialize_state()
        self.model.reset()

        self._running = True
        logger.info("─── 离线仿真开始 ───")

        while self._running and self._sim_time < duration:
            t_wall = time.perf_counter()

            # 生成输入
            if input_func:
                inputs = input_func(self._sim_time)
            else:
                inputs = {name: spec.default
                          for name, spec in self.model._input_specs.items()}

            # 模型计算
            outputs = self.model.step(inputs, self.step_size)

            # 记录
            self.recorder.record(self._sim_time, {**inputs, **outputs})

            # 步进
            self._sim_time += self.step_size
            self._step_count += 1

            # 定期输出状态
            if self._step_count % max(1, int(10.0 / self.step_size)) == 0:
                self._log_status(inputs, outputs)

            # 实时节拍
            if realtime:
                elapsed = time.perf_counter() - t_wall
                sleep_time = self.step_size - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

        self._running = False
        self._print_summary()

    async def stop(self):
        """停止仿真"""
        self._running = False
        logger.info("收到停止信号，仿真将在当前步完成后停止")

    # ── 状态查询 ──────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def sim_time(self) -> float:
        """当前仿真时间, 秒"""
        return self._sim_time

    @property
    def step_count(self) -> int:
        return self._step_count

    def export_data(self, filepath: str):
        """导出过程数据到 CSV"""
        self.recorder.to_csv(filepath)

    # ── 内部方法 ──────────────────────────────────────────

    def _validate_mapping(self):
        """校验模型信号与 OPC 映射的一致性"""
        # 输出信号必须全部映射（否则无法写入 OPC）
        missing_out = [n for n in self.model.output_names
                       if self.mapping.get(n) is None]
        if missing_out:
            msg = (f"以下模型输出信号在 OPC 映射中未找到: {missing_out}\n"
                   f"请在 opc_mapping.yaml 中配置这些信号的 AI 通道。")
            raise ValueError(msg)

        # 输入信号允许缺失（使用模型默认值）
        mapped_in = [n for n in self.model.input_names
                     if self.mapping.get(n) is not None]
        unmapped_in = [n for n in self.model.input_names
                       if self.mapping.get(n) is None]
        if unmapped_in:
            for name in unmapped_in:
                default = self.model._input_specs[name].default
                logger.warning(f"输入 '{name}' 未映射 OPC 节点，将使用固定值 {default}")

        logger.info(f"信号映射校验: {len(mapped_in)} 输入已映射, "
                    f"{len(unmapped_in)} 输入使用默认值, "
                    f"{len(self.model.output_names)} 输出")

    def _initialize_state(self):
        """重置引擎内部状态"""
        self._sim_time = 0.0
        self._step_count = 0
        self.recorder.clear()

    async def _initialize(self, initial_values: Dict[str, float] = None):
        """初始化模型和引擎状态"""
        self._initialize_state()
        self.model.reset(initial_values)
        logger.info(f"模型已初始化: {self.model.name}")

    async def _loop(self, duration: float = None):
        """固定步长主循环"""
        while self._running:
            t_wall_start = time.perf_counter()

            # 1. 读取 OPC 输入
            inputs = await self._read_inputs()

            # 1.5 应用输入覆盖（开环输入）
            if self._input_overrides:
                overrides = self._input_overrides(self._sim_time)
                inputs.update(overrides)

            # 2. 模型计算
            outputs = self.model.step(inputs, self.step_size)

            # 3. 写入 OPC 输出
            await self._write_outputs(outputs)

            # 3.5 OPC 读回验证
            readback = {}
            if self._readback_nodes:
                readback = await self._read_back()

            # 4. 记录数据
            self.recorder.record(self._sim_time,
                                 {**inputs, **outputs, **readback})

            # 5. 步进
            self._sim_time += self.step_size
            self._step_count += 1

            # 6. 定期输出状态 (每 10 秒)
            if self._step_count % max(1, int(10.0 / self.step_size)) == 0:
                self._log_status(inputs, outputs, readback)

            # 7. 检查时长
            if duration is not None and self._sim_time >= duration:
                break

            # 8. 精确等待到下一步
            elapsed = time.perf_counter() - t_wall_start
            sleep_time = self.step_size - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            elif elapsed > self.step_size * 2:
                logger.warning(
                    f"步长超时: 耗时 {elapsed*1000:.0f}ms > 预算 {self.step_size*1000:.0f}ms")

    async def _read_inputs(self) -> Dict[str, float]:
        """从 OPC 批量读取所有模型输入信号"""
        result = {}
        mapped_names = []
        mapped_nodes = []

        for name in self.model.input_names:
            sig = self.mapping.get(name)
            if sig is not None:
                mapped_names.append(name)
                mapped_nodes.append(sig.pv_node)
            else:
                # 未映射的输入使用默认值
                result[name] = self.model._input_specs[name].default

        # 批量读取已映射的信号
        if mapped_nodes:
            values = await self._client.read_values(mapped_nodes)
            for i, name in enumerate(mapped_names):
                val = values[i]
                if val is not None:
                    result[name] = float(val)
                else:
                    result[name] = self.model._input_specs[name].default
                    logger.debug(f"输入 {name} 读取失败，使用默认值 {result[name]}")

        return result

    async def _write_outputs(self, outputs: Dict[str, float]):
        """将模型输出批量写入 OPC AI 通道（含冗余通道）"""
        channels = {}
        for name, value in outputs.items():
            sig = self.mapping.get(name)
            if sig and sig.channel_type.upper() == "AI":
                channels[sig.node_base] = value
            # 冗余通道：同一值写入多个 AI 通道
            for redundant_base in self.mapping.get_redundant_channels(name):
                channels[redundant_base] = value
        if channels:
            await self._client.write_ai_channels(channels)

    async def _write_initial_outputs(self, values: Dict[str, float]):
        """写入输出信号的初始值到 OPC（含冗余通道）"""
        channels = {}
        for name in self.model.output_names:
            sig = self.mapping.get(name)
            init_val = values.get(name, self.model._output_specs[name].default)
            if sig and sig.channel_type.upper() == "AI":
                channels[sig.node_base] = init_val
            for redundant_base in self.mapping.get_redundant_channels(name):
                channels[redundant_base] = init_val
        if channels:
            await self._client.write_ai_channels(channels)
            logger.info(f"初始值已写入 {len(channels)} 个 AI 通道")

    async def _read_back(self) -> Dict[str, float]:
        """读回 OPC PV 值，用于验证写入是否生效"""
        result = {}
        names = list(self._readback_nodes.keys())
        nodes = list(self._readback_nodes.values())
        if not nodes:
            return result
        try:
            values = await self._client.read_values(nodes)
            for i, name in enumerate(names):
                val = values[i]
                result[name] = float(val) if val is not None else 0.0
        except Exception as e:
            logger.debug(f"OPC 读回异常: {e}")
        return result

    # ── 日志与展示 ────────────────────────────────────────

    def _log_status(self, inputs: dict, outputs: dict, readback: dict = None):
        """输出当前状态"""
        parts = []
        for k, v in {**inputs, **outputs}.items():
            sig = self.mapping.get(k)
            unit = sig.unit if sig else ""
            parts.append(f"{k}={v:.2f}{unit}")
        if readback:
            for k, v in readback.items():
                parts.append(f"{k}={v:.2f}")
        logger.info(f"[t={self._sim_time:.1f}s | step={self._step_count}] {' | '.join(parts)}")

    def _print_banner(self, mode: str):
        """打印启动信息"""
        logger.info("═" * 60)
        logger.info(f"  仿真引擎 — {mode}")
        logger.info(f"  模型: {self.model.name}")
        logger.info(f"  步长: {self.step_size * 1000:.0f}ms")
        logger.info(f"  输入: {self.model.input_names}")
        logger.info(f"  输出: {self.model.output_names}")
        if mode == "在线仿真":
            logger.info(f"  OPC:  {self.opc_url}")
        logger.info("═" * 60)

    def _print_summary(self):
        """打印结束摘要"""
        logger.info("─── 仿真结束 ───")
        logger.info(f"  总步数: {self._step_count}")
        logger.info(f"  仿真时长: {self._sim_time:.1f}s")
        logger.info(f"  数据行数: {self.recorder.count}")
        if self.recorder.count > 0:
            logger.info(f"\n{self.recorder.summary()}")


# ── 命令行入口 ────────────────────────────────────────────

async def _main():
    """命令行运行入口"""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 默认运行离线演示
    from .demo_model import PressureLoopDemo

    model = PressureLoopDemo()
    mapping = SignalMapping.from_yaml("config/opc_mapping.yaml")

    engine = SimEngine(model, mapping, step_size=0.2)

    mode = sys.argv[1] if len(sys.argv) > 1 else "offline"

    if mode == "online":
        logger.info("在线模式：连接 OPC UA...")
        await engine.start(duration=60)
    else:
        logger.info("离线模式：使用模拟输入...")

        def demo_input(t):
            """模拟 DCS 控制器输出：阀位在 50% 附近阶跃"""
            valve = 50.0 if t < 5.0 else 60.0
            return {"valve_position": valve}

        await engine.run_offline(duration=30, input_func=demo_input)

    # 导出数据
    engine.export_data("data/demo_run.csv")
    logger.info("数据已保存到 data/demo_run.csv")


if __name__ == "__main__":
    asyncio.run(_main())
