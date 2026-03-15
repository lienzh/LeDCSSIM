# -*- coding: utf-8 -*-
"""
图执行引擎

将 Drawflow 画布组态 JSON 转为可执行的仿真图。
解析节点和连线 → IL-IB 层间对接 → 拓扑排序 → 按步执行。

数据流：IO输入 → IL预处理 → IB计算 → IL后处理 → IO输出

用法：
    runner = GraphRunner()
    runner.load(ib_json, il_json)
    outputs = runner.step({"steam_pressure": 16.7}, dt=0.2)
"""
import json
import logging
import math
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..blocks import (
    Block, Inertia, Integrator, DeadZone, RateLimiter, Limiter,
    LeadLag, SecondOrder,
    HighSelect, LowSelect, Switch,
    LinearInterp, Polynomial,
    PIController, PIDController, PDController,
    ANDGate, ORGate, NOTGate, XORGate, FlipFlopSR, FlipFlopRS, Comparator,
    TimerOn, TimerOff, TimerPulse, Counter,
    SampleHold, RampGenerator, Gradient, ScaleConvert,
    BiasGain, Deviation, AbsValue, Divider, SquareRoot,
    MaxValue, MinValue,
)

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "models"


class GraphNode:
    """图中的一个节点（对应画布上的一个功能块）"""

    def __init__(self, node_id: str, block_type: str, params: dict):
        self.id = node_id
        self.block_type = block_type  # 如 'ADD', 'Inertia', 'input', 'output'
        self.params = params
        self.input_connections: List[tuple] = []  # [(源节点id, 源输出端口号), ...]
        self.output_value: float = 0.0  # 当前输出值
        self.block: Optional[Block] = None  # 有状态的功能块实例
        # 记录哪些下游节点从此节点取值
        self.downstream: List[tuple] = []  # [(目标节点id, 目标输入端口索引), ...]

    @property
    def tag(self) -> str:
        return self.params.get("tag", self.params.get("name", ""))

    def __repr__(self):
        return f"<Node {self.id} type={self.block_type}>"


class GraphRunner:
    """
    将 Drawflow JSON 转为可执行的仿真图

    核心流程：
    1. load(): 解析 JSON → 创建 GraphNode → IL-IB 对接 → 拓扑排序
    2. step(): 按拓扑序逐节点执行 → 返回输出值
    3. reset(): 重置所有有状态功能块
    """

    def __init__(self):
        self._nodes: Dict[str, GraphNode] = {}
        self._sorted_ids: List[str] = []  # 拓扑排序后的执行顺序
        self._input_nodes: List[GraphNode] = []   # 外部输入端子
        self._output_nodes: List[GraphNode] = []  # 外部输出端子
        self._latest_outputs: Dict[str, float] = {}
        self._ref_values: Dict[str, float] = {}   # 引出点变量值

    # ── 加载与解析 ────────────────────────────────────────

    def load(self, ib_json, il_json=None):
        """
        加载 IB（+ 可选 IL）层的 Drawflow 组态 JSON

        Args:
            ib_json: IB 层组态 JSON 或 JSON 列表（必须）
            il_json: IL 层组态 JSON 或 JSON 列表（可选）
        """
        # 兼容旧接口：单个 dict → 包装成 list
        if isinstance(ib_json, dict):
            ib_json = [ib_json]
        if il_json is not None and isinstance(il_json, dict):
            il_json = [il_json]

        self._nodes.clear()
        self._input_nodes.clear()
        self._output_nodes.clear()

        # 解析 IB 层（主逻辑，支持多页）
        for i, ib in enumerate(ib_json):
            page_name = ib.get("name", f"ib{i}")
            self._parse_drawflow(ib, prefix=f"IB_{page_name}_")

        # 解析 IL 层（预处理，可选，支持多页）
        if il_json:
            for i, il in enumerate(il_json):
                page_name = il.get("name", f"il{i}")
                self._parse_drawflow(il, prefix=f"IL_{page_name}_")
            self._connect_layers()

        # 引入/引出点虚拟依赖（确保 ref_out 在 ref_in 之前执行）
        self._link_refs()

        # 建立下游索引（拓扑排序依赖此索引，必须在 _link_refs 之后）
        self._build_downstream_index()

        # 拓扑排序（支持反馈环路）
        self._sorted_ids = self._topological_sort()

        # 刷新输入/输出端子列表
        self._refresh_io_nodes()

        logger.info(f"图加载完成: {len(self._nodes)} 个节点, "
                    f"{len(self._input_nodes)} 输入, "
                    f"{len(self._output_nodes)} 输出, "
                    f"执行顺序: {len(self._sorted_ids)} 步")

    def _parse_drawflow(self, model_json: dict, prefix: str = ""):
        """解析 Drawflow 导出的 JSON 格式"""
        # 支持两种格式：
        # 1. CanvasEngine 导出: {drawflow: {version, drawflow: {...}, meta: {...}}}
        # 2. 原生 Drawflow: {drawflow: {Home: {data: {...}}}}
        drawflow_data = model_json.get("drawflow", model_json)

        # 提取 meta（必须在嵌套剥离前，meta 在最外层 drawflow 对象上）
        meta = {}
        if isinstance(drawflow_data, dict):
            meta = drawflow_data.get("meta", {})
            # 兼容不同嵌套深度
            if not meta and "drawflow" in drawflow_data:
                inner = drawflow_data["drawflow"]
                if isinstance(inner, dict):
                    meta = inner.get("meta", {})

        # CanvasEngine 格式：剥离嵌套
        if isinstance(drawflow_data, dict) and "drawflow" in drawflow_data:
            inner = drawflow_data["drawflow"]
            if isinstance(inner, dict) and "drawflow" in inner:
                drawflow_data = inner
            else:
                drawflow_data = {"drawflow": inner}

        node_block_map = meta.get("nodeBlockMap", {})
        node_data_map = meta.get("nodeDataMap", {})

        # 获取节点数据
        nodes_data = {}
        df = drawflow_data.get("drawflow", drawflow_data)
        if isinstance(df, dict):
            for module_name, module in df.items():
                if isinstance(module, dict) and "data" in module:
                    nodes_data.update(module["data"])

        if not nodes_data:
            logger.warning(f"组态 {prefix} 中无节点数据")
            return

        # 第一遍：创建节点
        id_map = {}  # 原始节点ID → 带前缀的ID
        for node_id_str, node_data in nodes_data.items():
            node_id = str(node_id_str)
            prefixed_id = f"{prefix}{node_id}"
            id_map[node_id] = prefixed_id

            # 确定功能块类型（优先级：nodeBlockMap > _blockId > name > class）
            block_type = (node_block_map.get(node_id)
                         or (node_data.get("data", {}) or {}).get("_blockId")
                         or node_data.get("name")
                         or node_data.get("class", "unknown"))

            # 提取参数
            params = {}
            if isinstance(node_data.get("data"), dict):
                params.update(node_data["data"])
            if node_id in node_data_map and isinstance(node_data_map[node_id], dict):
                params.update(node_data_map[node_id])

            node = GraphNode(prefixed_id, block_type, params)
            node.block = self._create_block(block_type, params)
            self._nodes[prefixed_id] = node

            # 分类输入/输出端子
            if block_type == "input":
                self._input_nodes.append(node)
            elif block_type == "output":
                self._output_nodes.append(node)

        # 第二遍：建立连接
        for node_id_str, node_data in nodes_data.items():
            node_id = str(node_id_str)
            prefixed_id = id_map[node_id]
            target_node = self._nodes[prefixed_id]

            inputs_dict = node_data.get("inputs", {})
            for input_port_name in sorted(inputs_dict.keys()):
                connections = inputs_dict[input_port_name].get("connections", [])
                if connections:
                    conn = connections[0]
                    src_node_id = str(conn["node"])
                    src_output = conn.get("output", conn.get("input", "output_1"))
                    src_port = int(src_output.replace("output_", "")) - 1 if "output_" in src_output else 0
                    src_prefixed_id = id_map.get(src_node_id, f"{prefix}{src_node_id}")
                    target_node.input_connections.append((src_prefixed_id, src_port))
                else:
                    target_node.input_connections.append(None)  # 未连接

    # ── IL-IB 层间对接 ────────────────────────────────────

    def _connect_layers(self):
        """
        自动对接 IL 和 IB 层：按 tag 匹配。

        正向（IO → 模型）：IL output(tag=T) → IB input(tag=T)
        反向（模型 → IO）：IB output(tag=T) → IL input(tag=T)

        对接后，被消费的 IL output / IB input 不再作为外部端子。
        """
        # 按 tag 索引各层的 input/output 节点
        il_outputs = {}  # tag → node
        il_inputs = {}
        ib_outputs = {}
        ib_inputs = {}

        for node in self._input_nodes:
            if node.id.startswith("IL_"):
                tag = node.tag
                if tag:
                    il_inputs[tag] = node
            elif node.id.startswith("IB_"):
                tag = node.tag
                if tag:
                    ib_inputs[tag] = node

        for node in self._output_nodes:
            if node.id.startswith("IL_"):
                tag = node.tag
                if tag:
                    il_outputs[tag] = node
            elif node.id.startswith("IB_"):
                tag = node.tag
                if tag:
                    ib_outputs[tag] = node

        connected_tags = []

        # 正向：IL output → IB input（同 tag）
        for tag, ib_in_node in ib_inputs.items():
            il_out_node = il_outputs.get(tag)
            if il_out_node:
                # IB input 节点变成一个"中继"：从 IL output 取值
                # 将 IB input 的类型改为 _relay，并设置其输入连接为 IL output
                ib_in_node.block_type = "_relay"
                ib_in_node.input_connections = [(il_out_node.id, 0)]
                connected_tags.append(("IL→IB", tag))

        # 反向：IB output → IL input（同 tag）
        for tag, il_in_node in il_inputs.items():
            ib_out_node = ib_outputs.get(tag)
            if ib_out_node:
                il_in_node.block_type = "_relay"
                il_in_node.input_connections = [(ib_out_node.id, 0)]
                connected_tags.append(("IB→IL", tag))

        if connected_tags:
            logger.info(f"IL-IB 层间对接: {len(connected_tags)} 个信号 — "
                        + ", ".join(f"{d}:{t}" for d, t in connected_tags))

        # 刷新外部端子列表（去掉已对接的节点）
        self._refresh_io_nodes()


    def _build_downstream_index(self):
        """为每个节点建立下游索引"""
        for node in self._nodes.values():
            node.downstream.clear()

        for nid, node in self._nodes.items():
            for port_idx, conn in enumerate(node.input_connections):
                if conn is not None:
                    src_id, src_port = conn
                    src_node = self._nodes.get(src_id)
                    if src_node is not None:
                        src_node.downstream.append((nid, port_idx))

    def _refresh_io_nodes(self):
        """刷新外部输入/输出端子列表"""
        self._input_nodes = [n for n in self._nodes.values()
                             if n.block_type == "input"]
        self._output_nodes = [n for n in self._nodes.values()
                              if n.block_type == "output"]

    # ── 功能块创建 ────────────────────────────────────────

    # 功能块创建器（类级别，避免每次调用重建）
    _BLOCK_CREATORS = {
        # 基础
        "FLT": lambda p: Inertia(K=1.0, T=p.get("T", 1.0)),
        "Inertia": lambda p: Inertia(K=p.get("K", 1.0), T=p.get("T", 1.0)),
        "I": lambda p: Integrator(K=p.get("K", 1.0),
                                   low=p.get("low", -1e6), high=p.get("high", 1e6)),
        "Integrator": lambda p: Integrator(K=p.get("K", 1.0),
                                            low=p.get("low", -1e6), high=p.get("high", 1e6)),
        "DB": lambda p: DeadZone(zone=p.get("zone", 0.5)),
        "DeadZone": lambda p: DeadZone(zone=p.get("zone", 0.5)),
        "RL": lambda p: RateLimiter(rate_up=p.get("rate_up", 10.0),
                                     rate_down=p.get("rate_down", -10.0)),
        "RateLimiter": lambda p: RateLimiter(rate_up=p.get("rate_up", 10.0),
                                              rate_down=p.get("rate_down", -10.0)),
        "LIM": lambda p: Limiter(low=p.get("low", 0.0), high=p.get("high", 100.0)),
        "Limiter": lambda p: Limiter(low=p.get("low", 0.0), high=p.get("high", 100.0)),
        # 传递函数
        "LDL": lambda p: LeadLag(K=p.get("K", 1.0), T1=p.get("T1", 1.0), T2=p.get("T2", 1.0)),
        "LeadLag": lambda p: LeadLag(K=p.get("K", 1.0), T1=p.get("T1", 1.0), T2=p.get("T2", 1.0)),
        "SO": lambda p: SecondOrder(K=p.get("K", 1.0), T1=p.get("T1", 1.0), T2=p.get("T2", 0.5)),
        # PID
        "PI": lambda p: PIController(Kp=p.get("Kp", 1.0), Ti=p.get("Ti", 10.0),
                                       out_low=p.get("out_low", 0.0), out_high=p.get("out_high", 100.0)),
        "PID": lambda p: PIDController(Kp=p.get("Kp", 1.0), Ti=p.get("Ti", 10.0), Td=p.get("Td", 0.0),
                                        out_low=p.get("out_low", 0.0), out_high=p.get("out_high", 100.0)),
        "PD": lambda p: PDController(Kp=p.get("Kp", 1.0), Td=p.get("Td", 0.0)),
        # 逻辑
        "AND": lambda p: ANDGate(),
        "OR": lambda p: ORGate(),
        "NOT": lambda p: NOTGate(),
        "XOR": lambda p: XORGate(),
        "SR": lambda p: FlipFlopSR(),
        "RS": lambda p: FlipFlopRS(),
        # 定时器
        "TON": lambda p: TimerOn(delay=p.get("delay", 1.0)),
        "TOFF": lambda p: TimerOff(delay=p.get("delay", 1.0)),
        "TP": lambda p: TimerPulse(duration=p.get("duration", 1.0)),
        "CTR": lambda p: Counter(target=p.get("target", 10)),
        # 信号处理
        "SH": lambda p: SampleHold(),
        "RAMP": lambda p: RampGenerator(rate=p.get("rate", 1.0)),
        "GRAD": lambda p: Gradient(),
        "SC": lambda p: ScaleConvert(in_low=p.get("in_low", 0.0), in_high=p.get("in_high", 100.0),
                                      out_low=p.get("out_low", 0.0), out_high=p.get("out_high", 100.0)),
        "BG": lambda p: BiasGain(K=p.get("K", 1.0), B=p.get("B", 0.0)),
        "DEV": lambda p: Deviation(setpoint=p.get("setpoint", 0.0)),
    }

    def _create_block(self, block_type: str, params: dict) -> Optional[Block]:
        """根据功能块类型创建对应的 Python Block 实例"""
        if block_type in self._BLOCK_CREATORS:
            try:
                return self._BLOCK_CREATORS[block_type](params)
            except Exception as e:
                logger.warning(f"创建功能块 {block_type} 失败: {e}")
                return None
        return None

    # ── 引入/引出点链接 ──────────────────────────────────

    def _link_refs(self):
        """
        为同名的 ref_in/ref_out 建立虚拟依赖，
        确保 ref_out 在对应 ref_in 之前执行。
        ref_in 的 input_connections 添加指向 ref_out 的连接。
        """
        # 按 tag 索引 ref_out 节点
        ref_outs = {}
        for node in self._nodes.values():
            if node.block_type == "ref_out":
                tag = node.tag
                if tag:
                    ref_outs[tag] = node

        # 为 ref_in 添加虚拟连接
        for node in self._nodes.values():
            if node.block_type == "ref_in":
                tag = node.tag
                if tag and tag in ref_outs:
                    # 添加虚拟输入连接（端口 0 指向 ref_out 的输出 0）
                    node.input_connections = [(ref_outs[tag].id, 0)]
                    logger.debug(f"ref 链接: {tag} ({ref_outs[tag].id} → {node.id})")

    # ── 拓扑排序 ──────────────────────────────────────────

    def _topological_sort(self) -> List[str]:
        """
        对图进行拓扑排序，确定执行顺序。
        支持反馈环路：有状态块（Integrator 等）天然打断代数环，
        环路中的节点追加到排序末尾，使用上一步的 output_value。
        """
        # 计算入度 + 利用 downstream 索引实现 O(V+E)
        in_degree = {nid: 0 for nid in self._nodes}
        for nid, node in self._nodes.items():
            for conn in node.input_connections:
                if conn is not None:
                    in_degree[nid] += 1

        # BFS（Kahn 算法）
        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        sorted_ids = []

        while queue:
            nid = queue.popleft()
            sorted_ids.append(nid)

            for target_id, target_port_idx in self._nodes[nid].downstream:
                in_degree[target_id] -= 1
                if in_degree[target_id] == 0:
                    queue.append(target_id)

        if len(sorted_ids) != len(self._nodes):
            # 环路中的节点：优先放置有状态块（它们能打断代数环）
            remaining = [nid for nid in self._nodes if nid not in sorted_ids]
            # 有状态块（Integrator/Inertia 等）排在环路前面
            stateful = [nid for nid in remaining
                        if self._nodes[nid].block is not None]
            stateless = [nid for nid in remaining
                         if self._nodes[nid].block is None]
            remaining_sorted = stateful + stateless
            logger.info(f"图中存在反馈环路（{len(remaining)} 个节点），"
                        f"有状态块自动打断代数环")
            sorted_ids.extend(remaining_sorted)

        return sorted_ids

    # ── 执行 ──────────────────────────────────────────────

    def step(self, io_values: Dict[str, float], dt: float) -> Dict[str, float]:
        """
        执行一步：按拓扑序逐节点计算

        Args:
            io_values: 外部输入值 {信号tag: 值}
            dt: 仿真步长, 秒

        Returns:
            输出信号 {信号tag: 值}
        """
        outputs = {}

        for nid in self._sorted_ids:
            node = self._nodes[nid]

            if node.block_type == "input":
                # 输入端子：从 io_values 取值（兼容 name 字段）
                tag = node.tag
                node.output_value = io_values.get(tag, node.params.get("default", 0.0))

            elif node.block_type == "output":
                # 输出端子：收集输入值作为最终输出（兼容 name 字段）
                tag = node.tag
                input_val = self._get_input_value(node, 0)
                node.output_value = input_val
                if tag:
                    outputs[tag] = input_val

            elif node.block_type == "ref_out":
                # 引出点：接收输入值，记录到 ref 字典供引入点使用
                tag = node.tag
                input_val = self._get_input_value(node, 0)
                node.output_value = input_val
                if tag:
                    self._ref_values[tag] = input_val

            elif node.block_type == "ref_in":
                # 引入点：从 ref 字典中按 tag 取值
                tag = node.tag
                node.output_value = self._ref_values.get(tag, 0.0)

            elif node.block_type == "_relay":
                # 中继节点（IL-IB 对接产生）
                node.output_value = self._get_input_value(node, 0)

            elif node.block_type in ("constant", "CON"):
                node.output_value = float(node.params.get("value", 0.0))

            elif node.block is not None:
                # 有对应 Python Block 的功能块
                input_val = self._get_input_value(node, 0)
                node.output_value = node.block.calc(input_val, dt)

            else:
                # 无 Block 实例，用内联计算
                node.output_value = self._inline_calc(node, dt)

        self._latest_outputs = outputs
        return outputs

    def _get_input_value(self, node: GraphNode, port: int) -> float:
        """获取节点指定输入端口的值"""
        if port < len(node.input_connections):
            conn = node.input_connections[port]
            if conn is not None:
                src_id, src_port = conn
                src_node = self._nodes.get(src_id)
                if src_node is not None:
                    return src_node.output_value
        return 0.0

    def _get_all_inputs(self, node: GraphNode) -> List[float]:
        """获取节点所有输入端口的值"""
        values = []
        for i in range(len(node.input_connections)):
            values.append(self._get_input_value(node, i))
        return values

    def _inline_calc(self, node: GraphNode, dt: float) -> float:
        """无 Python Block 时的内联计算（基础运算等无状态块）"""
        bt = node.block_type
        inputs = self._get_all_inputs(node)

        in1 = inputs[0] if len(inputs) > 0 else 0.0
        in2 = inputs[1] if len(inputs) > 1 else 0.0

        # 算术运算
        if bt in ("ADD", "SUM"):
            if bt == "SUM":
                total = 0.0
                for i, val in enumerate(inputs):
                    k = float(node.params.get(f"K{i+1}", 1.0))
                    total += k * val
                return total
            return in1 + in2
        if bt == "SUB":
            return in1 - in2
        if bt in ("MLT", "ML", "multiply"):
            return in1 * in2
        if bt == "DIV":
            if abs(in2) < 1e-12:
                return float(node.params.get("zero_out", 0.0))
            return in1 / in2
        if bt == "ABS":
            return abs(in1)
        if bt == "POW":
            try:
                return math.pow(in1, in2)
            except (ValueError, OverflowError):
                return 0.0
        if bt == "SQRT":
            return math.sqrt(max(0.0, in1))
        if bt == "AVE":
            n = int(node.params.get("n", len(inputs)))
            vals = inputs[:n] if n <= len(inputs) else inputs
            return sum(vals) / max(1, len(vals))

        # 比较选择
        if bt == "HS":
            return max(inputs) if inputs else 0.0
        if bt == "LS":
            return min(inputs) if inputs else 0.0
        if bt in ("CMP", "AC", "AC1"):
            db = float(node.params.get("db", 0.0))
            return 1.0 if in1 > in2 + db else 0.0
        if bt == "NTH":
            nth = int(node.params.get("nth", 2))
            sorted_vals = sorted(inputs, reverse=True)
            idx = min(nth - 1, len(sorted_vals) - 1)
            return sorted_vals[idx] if sorted_vals else 0.0
        if bt == "SEL":
            sel = int(in1) if in1 >= 0 else 0
            sel = min(sel, len(inputs) - 2)
            return inputs[sel + 1] if sel + 1 < len(inputs) else 0.0

        # 信号增益
        if bt in ("G", "gain"):
            k = float(node.params.get("K", 1.0))
            return k * in1

        # 求和（带符号，create_ccs_preset 用）
        if bt == "sum":
            signs = node.params.get("signs", "++")
            total = 0.0
            for i, val in enumerate(inputs):
                sign = signs[i] if i < len(signs) else "+"
                total += val if sign == "+" else -val
            return total

        # 开关
        if bt in ("ASW", "SW"):
            in3 = inputs[2] if len(inputs) > 2 else 0.0
            threshold = float(node.params.get("threshold", 0.5))
            return in2 if in1 > threshold else in3

        # 未知类型，直接透传
        logger.debug(f"未实现的功能块 {bt}，透传第一输入")
        return in1

    # ── 重置 ──────────────────────────────────────────────

    def reset(self):
        """重置所有功能块状态"""
        for node in self._nodes.values():
            node.output_value = 0.0
            if node.block is not None:
                node.block.reset(0.0)
        self._latest_outputs.clear()

    # ── 查询接口 ──────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    def get_input_tags(self) -> List[str]:
        """获取所有外部输入端子的 tag 列表"""
        tags = []
        for n in self._input_nodes:
            tag = n.tag
            if tag:
                tags.append(tag)
        return tags

    def get_output_tags(self) -> List[str]:
        """获取所有外部输出端子的 tag 列表"""
        tags = []
        for n in self._output_nodes:
            tag = n.tag
            if tag:
                tags.append(tag)
        return tags

    def get_latest_outputs(self) -> Dict[str, float]:
        """获取最近一步的输出值"""
        return dict(self._latest_outputs)

    def get_all_node_values(self) -> Dict[str, float]:
        """获取所有节点的当前输出值（仅返回有意义 tag 的节点，跳过无 tag 的中间节点）"""
        result = {}
        for nid, node in self._nodes.items():
            tag = node.tag
            if tag:
                result[tag] = node.output_value
        return result

    def get_info(self) -> dict:
        """获取图的元信息（用于 UI 展示）"""
        return {
            "node_count": len(self._nodes),
            "inputs": [
                {"id": n.id, "tag": n.params.get("tag", ""),
                 "default": n.params.get("default", 0.0)}
                for n in self._input_nodes
            ],
            "outputs": [
                {"id": n.id, "tag": n.params.get("tag", "")}
                for n in self._output_nodes
            ],
            "execution_order": self._sorted_ids,
        }
