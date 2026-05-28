# IO 配对生成器 P1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从科远点表自动生成模拟调节门的 AQ→AI 配对，由引擎声明式执行"指令→阀位"赋值，免去手画。

**Architecture:** 新增 `PairingRunner`（声明式 IL 运行器）与 `io_pairing_gen`（生成器模块），与现有 `GraphRunner` 并行。引擎合并两者输出。P1 只做 analog 模板（direct/inertia/scale 三种 transform）。

**Tech Stack:** Python 3.12、pyyaml、复用 `src/blocks/Inertia`。测试用 pytest（沿用现有 `sys.path.insert` 约定）。

参考设计：`docs/superpowers/specs/2026-05-28-io-pairing-generator-design.md`

---

## 文件结构

- Create `src/sim_engine/pairing_runner.py` — 声明式 IL 运行器（load/step/reset，analog 模板）
- Create `src/sim_engine/io_pairing_gen.py` — 生成器核心逻辑（CSV 解析、硬件 IO 筛选、analog 配对），可单测
- Create `tools/gen_io_pairing.py` — 生成器 CLI 薄包装
- Create `tests/test_pairing_runner.py` — PairingRunner 单测
- Create `tests/test_io_pairing_gen.py` — 生成器单测（临时 GBK CSV fixture）
- Create `tests/test_engine_pairing.py` — 引擎离线集成测试
- Modify `src/sim_engine/engine.py` — 引擎接入 PairingRunner（构造参数 + 循环合并）

---

### Task 1: PairingRunner — analog 模板

**Files:**
- Create: `src/sim_engine/pairing_runner.py`
- Test: `tests/test_pairing_runner.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pairing_runner.py
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.pairing_runner import PairingRunner


def _runner_with(analog):
    r = PairingRunner()
    r.load_dict({"analog": analog})
    return r


def test_direct_transform_assigns_command_to_feedback():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "direct"}},
    ])
    out = r.step({"HW.AQ01.PV": 42.0}, dt=0.2)
    assert out == {"HW.AI01.PV": 42.0}


def test_scale_transform_applies_gain_and_bias():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "scale", "gain": 2.0, "bias": 5.0}},
    ])
    out = r.step({"HW.AQ01.PV": 10.0}, dt=0.2)
    assert out["HW.AI01.PV"] == 25.0


def test_inertia_transform_lags_toward_command():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "inertia", "T": 2.0}},
    ])
    # 阶跃到 100，一步后应在 0 和 100 之间（未到稳态）
    out1 = r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    v1 = out1["HW.AI01.PV"]
    assert 0.0 < v1 < 100.0
    # 继续逼近，第二步应更接近 100
    out2 = r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    assert out2["HW.AI01.PV"] > v1


def test_missing_command_defaults_to_zero():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "direct"}},
    ])
    out = r.step({}, dt=0.2)
    assert out["HW.AI01.PV"] == 0.0


def test_command_and_feedback_tag_lists():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV", "transform": {"type": "direct"}},
        {"cmd": "HW.AQ02.PV", "fb": "HW.AI02.PV", "transform": {"type": "direct"}},
    ])
    assert r.get_command_tags() == ["HW.AQ01.PV", "HW.AQ02.PV"]
    assert r.get_feedback_tags() == ["HW.AI01.PV", "HW.AI02.PV"]


def test_reset_clears_inertia_state():
    r = _runner_with([
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV",
         "transform": {"type": "inertia", "T": 2.0}},
    ])
    r.step({"HW.AQ01.PV": 100.0}, dt=0.2)
    r.reset()
    out = r.step({"HW.AQ01.PV": 0.0}, dt=0.2)
    assert out["HW.AI01.PV"] == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `py -3.12 -m pytest tests/test_pairing_runner.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.sim_engine.pairing_runner'`

- [ ] **Step 3: 实现 PairingRunner**

```python
# src/sim_engine/pairing_runner.py
# -*- coding: utf-8 -*-
"""
声明式 IL 运行器

读取 io_pairing.yaml，按设备模板逐步计算反馈值。
与 GraphRunner（画布）并行，承担"简单赋值"类 IO 配对。

P1 仅实现 analog 模板（模拟调节门：指令→阀位）。
transform: direct（直接赋值） | inertia（一阶惯性，模拟行程时间） | scale（折算 gain/bias）
"""
import logging
from pathlib import Path
from typing import Dict, List

import yaml

from ..blocks import Inertia

logger = logging.getLogger(__name__)


class PairingRunner:
    def __init__(self):
        self._analog: List[dict] = []   # [{cmd, fb, transform, _inertia}]
        self._cmd_tags: List[str] = []
        self._fb_tags: List[str] = []

    # ── 加载 ──────────────────────────────────────────────

    def load(self, pairing_yaml_path):
        """从 YAML 文件加载配对表"""
        path = Path(pairing_yaml_path)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self.load_dict(data)

    def load_dict(self, data: dict):
        """从已解析的 dict 加载（便于测试）"""
        self._analog = []
        for item in data.get("analog", []):
            transform = item.get("transform", {"type": "direct"})
            entry = {
                "cmd": item["cmd"],
                "fb": item["fb"],
                "transform": transform,
                "_inertia": None,
            }
            if transform.get("type") == "inertia":
                entry["_inertia"] = Inertia(K=1.0, T=float(transform.get("T", 1.0)))
            self._analog.append(entry)
        self._cmd_tags = [e["cmd"] for e in self._analog]
        self._fb_tags = [e["fb"] for e in self._analog]
        logger.info(f"加载 IO 配对: analog {len(self._analog)} 组")

    # ── 查询 ──────────────────────────────────────────────

    def get_command_tags(self) -> List[str]:
        """需从 DPU 读取的指令点（模型输入）"""
        return list(self._cmd_tags)

    def get_feedback_tags(self) -> List[str]:
        """需写回 DPU 的反馈点（模型输出）"""
        return list(self._fb_tags)

    # ── 执行 ──────────────────────────────────────────────

    def step(self, commands: Dict[str, float], dt: float) -> Dict[str, float]:
        """一步：读指令 → 套 transform → 产出反馈 {fb_tag: 值}"""
        out: Dict[str, float] = {}
        for e in self._analog:
            cmd_val = float(commands.get(e["cmd"], 0.0))
            t = e["transform"]
            ttype = t.get("type", "direct")
            if ttype == "inertia":
                fb = e["_inertia"].calc(cmd_val, dt)
            elif ttype == "scale":
                fb = cmd_val * float(t.get("gain", 1.0)) + float(t.get("bias", 0.0))
            else:  # direct（含未知类型兜底）
                fb = cmd_val
            out[e["fb"]] = fb
        return out

    def reset(self):
        """复位所有有状态 transform"""
        for e in self._analog:
            if e["_inertia"] is not None:
                e["_inertia"].reset(0.0)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `py -3.12 -m pytest tests/test_pairing_runner.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/sim_engine/pairing_runner.py tests/test_pairing_runner.py
git commit -m "feat: PairingRunner 声明式 IL 运行器 - analog 模板"
```

---

### Task 2: 生成器核心 io_pairing_gen — analog 提取

**Files:**
- Create: `src/sim_engine/io_pairing_gen.py`
- Test: `tests/test_io_pairing_gen.py`

- [ ] **Step 1: 写失败测试**（测试内用临时 GBK CSV 作为 fixture）

```python
# tests/test_io_pairing_gen.py
# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.io_pairing_gen import load_points, is_soft, pair_analog, generate

# 一个最小点表：表头 + 1 个调节门(AQ指令+AI反馈) + 1 个软点(备用)
_CSV = """#VERSION,1,2026/5/28,4
~索引,测点名称,描述,设计编号,数据类型
1,HW.AQ010101.PV,暖管出口电动调整门指令,30HAG21AA1010,FLOAT
2,HW.AI010502.PV,暖管出口电动调整门位置,30HAG21AA1019,FLOAT
3,HW.AQ040502.PV,备用AQ-KM236A,30HAG21AA9999,FLOAT
4,HW.AQ020101.PV,主蒸汽压力设定#1,30CJA06DU001Q05,FLOAT
"""


def _write_gbk(tmp_path, text):
    fn = tmp_path / "DPU3013.csv"
    fn.write_bytes(text.encode("gbk"))
    return fn


def test_load_points_parses_hw_points(tmp_path):
    fn = _write_gbk(tmp_path, _CSV)
    pts = load_points(str(fn))
    names = {p["name"] for p in pts}
    assert "HW.AQ010101.PV" in names
    assert len(pts) == 4


def test_is_soft_filters_spare_and_setpoint():
    assert is_soft({"desc": "备用AQ-KM236A"}) is True
    assert is_soft({"desc": "主蒸汽压力设定#1"}) is True
    assert is_soft({"desc": "暖管出口电动调整门指令"}) is False


def test_pair_analog_matches_command_to_feedback_by_kks(tmp_path):
    fn = _write_gbk(tmp_path, _CSV)
    pts = load_points(str(fn))
    pairs = pair_analog(pts, "DPU3013")
    # 仅 30HAG21AA 设备应配对成功；软点/设定值被剔除；DU001 非设备模式
    assert len(pairs) == 1
    p = pairs[0]
    assert p["cmd"] == "HW.AQ010101.PV"
    assert p["fb"] == "HW.AI010502.PV"
    assert p["device"] == "30HAG21AA"
    assert p["template"] == "analog"
    assert p["transform"]["type"] == "inertia"
    assert p["online_writable"] is True


def test_generate_aggregates_directory(tmp_path):
    _write_gbk(tmp_path, _CSV)
    data = generate(str(tmp_path))
    assert "analog" in data
    assert len(data["analog"]) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `py -3.12 -m pytest tests/test_io_pairing_gen.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.sim_engine.io_pairing_gen'`

- [ ] **Step 3: 实现生成器核心**

```python
# src/sim_engine/io_pairing_gen.py
# -*- coding: utf-8 -*-
"""
IO 配对生成器核心（P1：模拟调节门）

从科远 NT6000 点表 CSV（GBK 编码）筛选现场硬件 IO，
按 KKS 设备码将 AQ 指令与 AI 反馈配对。

KKS 约定（已验证）：设备码 = 单元2位+系统3字母+序2位+设备类2字母，
其后信号位 1010=模拟指令，1019=位置反馈。
"""
import csv
import glob
import re
from pathlib import Path
from typing import Dict, List

# 点名: HW.<2字母><数字>.PV
PT = re.compile(r"^HW\.([A-Z]{2})(\d+)\.PV")
# 现场设备 KKS: 30HAG21AA + 信号位
DEV = re.compile(r"^(\d{2}[A-Z]{3}\d{2}[A-Z]{2})(\d+)$")
# 软点/通讯/备用/设定 关键词（描述层面剔除）
SOFT_KW = ["备用", "模件第", "TEST", "TO CCS", "来自DPU", "至PECCS",
           "需确认", "描述文本", "心跳", "SOC", "设定"]


def load_points(fn: str) -> List[dict]:
    """加载点表 CSV（GBK），返回 HW.*.PV 点列表"""
    with open(fn, encoding="gbk", errors="replace") as f:
        lines = f.read().splitlines()
    rows = [l for l in lines if l and not l.startswith("#")]
    reader = csv.reader(rows)
    next(reader)  # 跳过 ~ 表头
    pts = []
    for r in reader:
        if len(r) < 5:
            continue
        m = PT.match(r[1].strip())
        if not m:
            continue
        pts.append({"name": r[1].strip(), "code": m.group(1),
                    "desc": r[2].strip(), "kks": r[3].strip()})
    return pts


def is_soft(p: dict) -> bool:
    """是否软点/通讯/备用（按描述关键词）"""
    return any(k in p["desc"] for k in SOFT_KW)


def pair_analog(pts: List[dict], dpu: str) -> List[dict]:
    """同一 KKS 设备码下 AQ 指令 ↔ AI 反馈配对"""
    by_root: Dict[str, list] = {}
    for p in pts:
        m = DEV.match(p["kks"])
        if not m or is_soft(p):
            continue
        by_root.setdefault(m.group(1), []).append(p)
    pairs = []
    for root, grp in by_root.items():
        aq = [p for p in grp if p["code"] == "AQ"]
        ai = [p for p in grp if p["code"] == "AI"]
        if aq and ai:
            for c in aq:
                pairs.append({
                    "dpu": dpu, "device": root,
                    "cmd": c["name"], "fb": ai[0]["name"],
                    "template": "analog",
                    "transform": {"type": "inertia", "T": 2.0},
                    "online_writable": True,
                    "desc": c["desc"],
                })
    return pairs


def generate(src_dir: str) -> dict:
    """扫描目录下所有 *.csv，聚合 analog 配对"""
    analog = []
    for fn in sorted(glob.glob(str(Path(src_dir) / "*.csv"))):
        dpu = Path(fn).stem
        analog.extend(pair_analog(load_points(fn), dpu))
    return {"analog": analog}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `py -3.12 -m pytest tests/test_io_pairing_gen.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add src/sim_engine/io_pairing_gen.py tests/test_io_pairing_gen.py
git commit -m "feat: IO 配对生成器核心 - analog 提取与硬件IO筛选"
```

---

### Task 3: 生成器 CLI tools/gen_io_pairing.py

**Files:**
- Create: `tools/gen_io_pairing.py`

- [ ] **Step 1: 实现 CLI 薄包装**

```python
# tools/gen_io_pairing.py
# -*- coding: utf-8 -*-
"""
IO 配对生成器 CLI

用法:
    py -3.12 tools/gen_io_pairing.py [点表目录] [输出文件]
    默认: YQ3SIM-IO -> config/io_pairing.generated.yaml

产物需人工 diff 后合并到 config/io_pairing.yaml（避免覆盖人工微调）。
"""
import sys
from pathlib import Path

import yaml

# 允许从仓库根运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.sim_engine.io_pairing_gen import generate


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "YQ3SIM-IO"
    out = sys.argv[2] if len(sys.argv) > 2 else "config/io_pairing.generated.yaml"
    data = generate(src)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)
    print(f"analog 配对 {len(data['analog'])} 组 -> {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 在真实点表上冒烟运行**

Run: `py -3.12 tools/gen_io_pairing.py YQ3SIM-IO config/io_pairing.generated.yaml`
Expected: 输出 `analog 配对 16 组 -> config/io_pairing.generated.yaml`（与原型一致）

- [ ] **Step 3: 提交**

```bash
git add tools/gen_io_pairing.py config/io_pairing.generated.yaml
git commit -m "feat: IO 配对生成器 CLI + 全 DPU analog 配对产物"
```

---

### Task 4: 引擎离线集成

**Files:**
- Modify: `src/sim_engine/engine.py`（`__init__`、`_initialize`、`run_offline`）
- Test: `tests/test_engine_pairing.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_engine_pairing.py
# -*- coding: utf-8 -*-
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sim_engine.engine import SimEngine
from src.sim_engine.graph_runner import GraphRunner
from src.sim_engine.pairing_runner import PairingRunner


def test_offline_run_merges_pairing_feedback_into_recorder():
    # 空画布（仅跑配对）
    graph = GraphRunner()
    graph.load({"drawflow": {"Home": {"data": {}}}})

    pairing = PairingRunner()
    pairing.load_dict({"analog": [
        {"cmd": "HW.AQ01.PV", "fb": "HW.AI01.PV", "transform": {"type": "direct"}},
    ]})

    engine = SimEngine(graph, step_size=0.2, pairing_runner=pairing)
    asyncio.run(engine.run_offline(duration=0.4,
                                   initial_inputs={"HW.AQ01.PV": 73.0}))

    # 反馈点应出现在记录中，值等于指令（direct）
    df = engine.recorder.to_dataframe() if hasattr(engine.recorder, "to_dataframe") else None
    last = engine.recorder.latest() if hasattr(engine.recorder, "latest") else None
    # 用通用方式取最后一行：recorder 记录 {tag: value}
    assert "HW.AI01.PV" in engine.recorder.columns()
    assert engine.recorder.last_value("HW.AI01.PV") == 73.0


def test_engine_without_pairing_still_runs():
    graph = GraphRunner()
    graph.load({"drawflow": {"Home": {"data": {}}}})
    engine = SimEngine(graph, step_size=0.2)  # 不传 pairing
    asyncio.run(engine.run_offline(duration=0.4, initial_inputs={}))
    assert engine.step_count >= 1
```

> 说明：测试用到 `recorder.columns()` / `recorder.last_value()`。先运行确认 recorder 是否已有等价方法；若没有，在 Step 3 顺便加这两个只读辅助方法（见下）。

- [ ] **Step 2: 运行测试确认失败**

Run: `py -3.12 -m pytest tests/test_engine_pairing.py -v`
Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'pairing_runner'`

- [ ] **Step 3: 改 engine.py 接入 PairingRunner**

在 `SimEngine.__init__` 签名加参数并存储：

```python
    def __init__(self, graph_runner, step_size: float = 0.2, pairing_runner=None):
        self.graph = graph_runner
        self.step_size = step_size
        self._pairing = pairing_runner          # 新增：声明式 IL 运行器（可选）
        self.recorder = DataRecorder(max_rows=10000)
        # ...（其余不变）
```

在 `_initialize` 末尾复位配对运行器：

```python
    def _initialize(self, initial_inputs: Dict[str, float] = None):
        self._sim_time = 0.0
        self._step_count = 0
        self.recorder.clear()
        self.graph.reset()
        if self._pairing is not None:        # 新增
            self._pairing.reset()
        if initial_inputs:
            self.graph.step(initial_inputs, self.step_size)
```

在 `run_offline` 循环里合并配对输出（画布优先）：

```python
        while self._running and self._sim_time < duration:
            t_wall = time.perf_counter()

            # 图执行（复杂逻辑）
            canvas_out = self.graph.step(io_values, self.step_size)
            # 声明式配对（简单赋值）
            pairing_out = (self._pairing.step(io_values, self.step_size)
                           if self._pairing is not None else {})
            outputs = {**pairing_out, **canvas_out}   # 同 tag 时画布优先

            # 记录（画布全节点值 + 配对反馈）
            all_values = self.graph.get_all_node_values()
            self.recorder.record(self._sim_time, {**pairing_out, **all_values})

            self._sim_time += self.step_size
            self._step_count += 1

            if self._step_count % max(1, int(10.0 / self.step_size)) == 0:
                self._log_status({**io_values, **outputs})

            elapsed = time.perf_counter() - t_wall
            sleep_time = self.step_size - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
```

若 `DataRecorder` 没有 `columns()` / `last_value()`，在 `src/sim_engine/recorder.py` 加只读辅助（按现有内部存储结构适配字段名）：

```python
    def columns(self) -> list:
        """已记录的所有列名（tag）"""
        cols = set()
        for row in self._rows:            # 若内部用别的名字，改这里
            cols.update(row.keys())
        return sorted(cols)

    def last_value(self, tag: str):
        """某列最后一个非空值"""
        for row in reversed(self._rows):
            if tag in row:
                return row[tag]
        return None
```

> 实施提示：先 Read `src/sim_engine/recorder.py` 确认内部行存储字段名（可能是 `_rows` / `_data` / `_records`），按实际命名实现上面两个方法；若已有等价 API，改测试调用即可。

- [ ] **Step 4: 运行测试确认通过**

Run: `py -3.12 -m pytest tests/test_engine_pairing.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `py -3.12 -m pytest tests/ -v`
Expected: 全部 PASS（含原有 `test_graph_runner.py`）

- [ ] **Step 6: 提交**

```bash
git add src/sim_engine/engine.py src/sim_engine/recorder.py tests/test_engine_pairing.py
git commit -m "feat: 引擎离线集成 PairingRunner - 画布+声明式配对合并执行"
```

---

## Self-Review

**1. Spec coverage（对 P1 范围）：**
- 生成器（analog）→ Task 2 + Task 3 ✓
- analog 模板 direct/inertia/scale → Task 1 ✓
- PairingRunner → Task 1 ✓
- 引擎集成（离线）→ Task 4 ✓
- 离线验证 → Task 4 Step 1/5 ✓
- 硬件 IO 筛选（剔软点）→ Task 2 `is_soft` ✓
- 未覆盖（属 P2/P3，本计划不含）：rs_actuator/motor 模板、travel_time、DI 不可写处理、di_mux_required 清单、无 KKS 描述配对、Web UI、在线模式集成。已在 spec 标注分期。

**2. Placeholder 扫描：** 无 TBD/TODO；recorder 辅助方法给了"按实际字段名适配"的明确指示而非占位（因为内部字段名需读后确认，已提供完整可工作骨架）。

**3. 类型一致性：**
- `PairingRunner.load_dict` / `step` / `reset` / `get_command_tags` / `get_feedback_tags` 在 Task 1 定义，Task 4 测试调用一致 ✓
- 生成器 `load_points` / `is_soft` / `pair_analog` / `generate` 在 Task 2 定义，Task 3 CLI 调用 `generate` 一致 ✓
- `transform` 字段结构 `{type, T/gain/bias}` 在 spec、Task 1、Task 2 一致 ✓
- `SimEngine(graph, step_size, pairing_runner)` 在 Task 4 定义并被测试使用一致 ✓
