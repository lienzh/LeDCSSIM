# -*- coding: utf-8 -*-
"""
GraphRunner - 主仿真循环

职责:
- 从 YAML 加载 blocks + connections
- 拓扑排序(纯代数环报错退出)
- 主循环严格读-算-写三段式
- 离线模式:跳过 OPC read/write,只 step + 落 CSV
- 在线模式:由 src.cli.main 注入 Adapter,本类调用 adapter.read_batch/write_batch

不依赖任何具体 Adapter 实现(adapter 可为 None,即离线模式)。
"""
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

import yaml

from src.models import BLOCK_REGISTRY, Block
from .recorder import DataRecorder

logger = logging.getLogger(__name__)


class AlgebraicLoopError(RuntimeError):
    """纯代数环报错 - 引擎拒绝启动"""


# connection: (from_block, from_port, to_block, to_port)
Connection = Tuple[str, str, str, str]


class GraphRunner:
    def __init__(
        self,
        blocks: Dict[str, Block],
        connections: List[Connection],
        dt: float,
        recorder: Optional[DataRecorder] = None,
    ):
        self.blocks = blocks
        self.connections = list(connections)
        self.dt = float(dt)
        self.recorder = recorder

        self.t: float = 0.0
        # 每个 (block, port) 端口的当前值
        self._outputs: Dict[Tuple[str, str], float] = {}

        # 启动前校验
        self._validate_connections()
        self.order: List[str] = self._topo_sort()
        logger.info(f"GraphRunner 启动: {len(self.blocks)} 块, "
                    f"{len(self.connections)} 连接, dt={self.dt}s")
        logger.debug(f"执行顺序: {self.order}")

    # ---------- 配置加载 ----------

    @classmethod
    def from_yaml(
        cls,
        models_yaml: str,
        connections_yaml: str,
        dt: float,
        recorder: Optional[DataRecorder] = None,
    ) -> "GraphRunner":
        with open(models_yaml, "r", encoding="utf-8") as f:
            models_doc = yaml.safe_load(f) or {}
        with open(connections_yaml, "r", encoding="utf-8") as f:
            conns_doc = yaml.safe_load(f) or {}

        blocks: Dict[str, Block] = {}
        for entry in models_doc.get("blocks", []):
            name = entry["name"]
            btype = entry["type"]
            params = entry.get("params") or {}
            if btype not in BLOCK_REGISTRY:
                raise ValueError(
                    f"未知块类型 {btype!r} (块 {name});"
                    f"可用: {list(BLOCK_REGISTRY.keys())}"
                )
            if name in blocks:
                raise ValueError(f"块名重复: {name}")
            blocks[name] = BLOCK_REGISTRY[btype](name, params)

        connections: List[Connection] = []
        for entry in conns_doc.get("connections", []):
            fb, fp = _parse_endpoint(entry["from"])
            tb, tp = _parse_endpoint(entry["to"])
            connections.append((fb, fp, tb, tp))

        return cls(blocks, connections, dt, recorder)

    # ---------- 启动校验 ----------

    def _validate_connections(self) -> None:
        for fb, fp, tb, tp in self.connections:
            if fb not in self.blocks:
                raise ValueError(f"连接源块不存在: {fb} (连接 {fb}.{fp} → {tb}.{tp})")
            if tb not in self.blocks:
                raise ValueError(f"连接目标块不存在: {tb} (连接 {fb}.{fp} → {tb}.{tp})")
            if fp not in self.blocks[fb].outputs:
                raise ValueError(
                    f"块 {fb} 没有输出端口 {fp!r};"
                    f"可用: {self.blocks[fb].outputs}"
                )
            if tp not in self.blocks[tb].inputs:
                raise ValueError(
                    f"块 {tb} 没有输入端口 {tp!r};"
                    f"可用: {self.blocks[tb].inputs}"
                )

    def _topo_sort(self) -> List[str]:
        """
        拓扑排序:有状态块打破环路,纯代数环报错

        实现:在依赖图里跳过指向有状态块的边
        (有状态块的输入是上一步的输出,所以不形成本步依赖)
        """
        # deps[name] = {upstream block names that must run before name}
        deps: Dict[str, set] = {name: set() for name in self.blocks}
        for fb, _, tb, _ in self.connections:
            # 如果 tb 是有状态块,fb→tb 这条边在本步不构成依赖
            if not self.blocks[tb].STATEFUL:
                deps[tb].add(fb)

        order: List[str] = []
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {n: WHITE for n in self.blocks}

        def visit(name: str, path: List[str]) -> None:
            if color[name] == BLACK:
                return
            if color[name] == GRAY:
                cycle = path[path.index(name):] + [name]
                raise AlgebraicLoopError(
                    f"检测到纯代数环: {' → '.join(cycle)}\n"
                    f"在环路中插入有状态块(如 FirstOrder / DELAY)或重组连接以打破。"
                )
            color[name] = GRAY
            for up in deps[name]:
                visit(up, path + [name])
            color[name] = BLACK
            order.append(name)

        for name in self.blocks:
            visit(name, [])
        return order

    # ---------- 主循环 ----------

    def reset(self) -> None:
        for b in self.blocks.values():
            b.reset()
        self._outputs.clear()
        self.t = 0.0

    def step_once(self) -> Dict[str, float]:
        """
        执行一步,返回本步所有 (block.port) 输出值

        读-算-写中的"算"部分。在线模式下:
            - 在调用 step_once 之前由 adapter 把 OPC 读值塞进 _outputs
            - 在调用之后由 adapter 把指定 tag 写出 OPC
        """
        for name in self.order:
            block = self.blocks[name]
            block_inputs: Dict[str, float] = {}
            for port in block.inputs:
                # 找连到这个输入的上游
                upstream_val = 0.0
                for fb, fp, tb, tp in self.connections:
                    if tb == name and tp == port:
                        upstream_val = self._outputs.get((fb, fp), 0.0)
                        break
                block_inputs[port] = upstream_val
            outs = block.step(block_inputs, self.dt)
            for port, val in outs.items():
                self._outputs[(name, port)] = float(val)

        self.t += self.dt

        # 收集本步所有输出端口值作为记录
        snap = {f"{b}.{p}": v for (b, p), v in self._outputs.items()}
        if self.recorder is not None:
            self.recorder.record(self.t, snap)
        return snap

    def get_output(self, block_name: str, port: str = "out") -> float:
        return self._outputs.get((block_name, port), 0.0)

    def set_input_signal(self, block_name: str, port: str, value: float) -> None:
        """
        强制设置某个端口的"上游来源值"
        在线模式下,adapter 用它把 OPC 读到的值喂给指定 block 的输入端口
        (实际上是把值写进 (upstream_block, upstream_port) 端口)
        """
        # 找到连到 (block_name, port) 的上游
        for fb, fp, tb, tp in self.connections:
            if tb == block_name and tp == port:
                self._outputs[(fb, fp)] = float(value)
                return
        raise KeyError(f"未找到指向 {block_name}.{port} 的连接")


def _parse_endpoint(s: str) -> Tuple[str, str]:
    """'block.port' → ('block', 'port')。port 缺省为 'out' 或 'in'(自动判断不便,要求显式写)"""
    if "." not in s:
        raise ValueError(f"端点格式错误,应为 'block.port': {s!r}")
    block, port = s.split(".", 1)
    return block.strip(), port.strip()
