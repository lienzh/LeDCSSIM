# -*- coding: utf-8 -*-
"""
仿真模型基类

定义仿真模型的标准接口。所有具体模型（锅炉、汽机等）继承 SimModel，
实现 setup() 和 step() 方法即可接入仿真引擎。

设计思路（类似 Simulink 脚本模式）：
    1. setup() 中声明输入/输出信号，创建功能块
    2. step()  中用功能块组合计算逻辑
    3. 引擎自动处理 OPC 读写和数据记录

用法示例：
    class BoilerModel(SimModel):
        def setup(self):
            self.add_input("valve_position", "调节阀开度", "%")
            self.add_output("steam_pressure", "主汽压力", "MPa", default=16.7)
            self.boiler = Inertia(K=15.0, T=120.0, name="锅炉惯性")

        def step(self, inputs, dt):
            valve = inputs["valve_position"]
            pressure = self.boiler.calc(valve, dt)
            return {"steam_pressure": pressure}
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List

from ..blocks.base import Block


@dataclass
class SignalSpec:
    """模型信号规格"""
    name: str            # 信号名
    description: str     # 中文描述
    unit: str            # 工程单位
    default: float       # 默认值（用于离线运行或OPC读取失败时）


class SimModel(ABC):
    """
    仿真模型基类

    继承此类，实现 setup() 声明信号 + step() 编写计算逻辑。
    引擎会自动调用 step()，传入 OPC 读取的输入，将输出写回 OPC。
    """

    def __init__(self, name: str = ""):
        self.name = name
        self._input_specs: Dict[str, SignalSpec] = {}
        self._output_specs: Dict[str, SignalSpec] = {}
        self.setup()

    # ── 信号声明 ──────────────────────────────────────────

    def add_input(self, name: str, description: str = "", unit: str = "",
                  default: float = 0.0):
        """
        声明一个输入信号（从 OPC 读取，通常是 DCS 控制器输出）

        Args:
            name: 信号名，需与 opc_mapping.yaml 中的 name 一致
            description: 中文描述
            unit: 工程单位
            default: 默认值
        """
        self._input_specs[name] = SignalSpec(name, description, unit, default)

    def add_output(self, name: str, description: str = "", unit: str = "",
                   default: float = 0.0):
        """
        声明一个输出信号（写入 OPC，通常是工艺参数）

        Args:
            name: 信号名，需与 opc_mapping.yaml 中的 name 一致
            description: 中文描述
            unit: 工程单位
            default: 初始输出值
        """
        self._output_specs[name] = SignalSpec(name, description, unit, default)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def input_names(self) -> List[str]:
        """所有输入信号名"""
        return list(self._input_specs.keys())

    @property
    def output_names(self) -> List[str]:
        """所有输出信号名"""
        return list(self._output_specs.keys())

    # ── 子类必须实现 ──────────────────────────────────────

    @abstractmethod
    def setup(self):
        """
        配置模型：声明输入/输出信号，创建功能块

        在此方法中调用 add_input() / add_output() 声明信号，
        并创建所需的功能块作为 self 的属性。
        """
        pass

    @abstractmethod
    def step(self, inputs: Dict[str, float], dt: float) -> Dict[str, float]:
        """
        执行一步仿真计算

        Args:
            inputs: {信号名: 当前值}，从 OPC 读取的输入信号
            dt: 仿真步长, 秒
        Returns:
            {信号名: 计算值}，要写入 OPC 的输出信号
        """
        pass

    # ── 可选覆盖 ──────────────────────────────────────────

    def reset(self, initial_values: Dict[str, float] = None):
        """
        重置模型到初始工况

        默认行为：将所有 Block 属性重置为 0。
        子类可覆盖此方法实现更精确的稳态初始化。

        Args:
            initial_values: {信号名: 初始值}，可选
        """
        for attr_name in list(vars(self).keys()):
            attr = getattr(self, attr_name, None)
            if isinstance(attr, Block):
                attr.reset(0.0)

    # ── 信息查询 ──────────────────────────────────────────

    def get_info(self) -> dict:
        """获取模型信息摘要（用于UI展示和调试）"""
        blocks = []
        for attr_name in sorted(vars(self).keys()):
            attr = getattr(self, attr_name, None)
            if isinstance(attr, Block):
                blocks.append({
                    "name": attr_name,
                    "type": type(attr).__name__,
                    "block_name": attr.name,
                })
        return {
            "name": self.name,
            "inputs": [
                {"name": s.name, "description": s.description,
                 "unit": s.unit, "default": s.default}
                for s in self._input_specs.values()
            ],
            "outputs": [
                {"name": s.name, "description": s.description,
                 "unit": s.unit, "default": s.default}
                for s in self._output_specs.values()
            ],
            "blocks": blocks,
        }

    def __repr__(self):
        return (f"<{type(self).__name__} '{self.name}' "
                f"inputs={self.input_names} outputs={self.output_names}>")
