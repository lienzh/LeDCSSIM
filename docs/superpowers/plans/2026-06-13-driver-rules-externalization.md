# 驱动规则外置(需求 4)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `runtime.py` 里硬编码的设备驱动知识(词表/白名单/类型,约 530 行)抽到 `drivers/*.yaml`,生成器重构为规则驱动的 `src/viewer/gen/` 模块,YQ3 输出逐字不变。

**Architecture:** 先抓 YQ3 当前生成器输出做金标准 fixture;再旁路搭新引擎(`gen/rules.py` 读 yaml + `gen/generator.py` 消费规则 + `gen/gateway.py` 柜间段接口),用金标准证明新引擎逐字一致;最后把 `runtime.py` 的 `generate_script_from_tagmap` 改成薄壳委托新引擎、删除已搬走的常量。drivers 路径工程优先、`config/drivers/` 兜底。

**Tech Stack:** Python 3.12(`py -3.12`)、pyyaml、pytest。依赖现有 `src/sim_engine/io_pairing_gen`(load_points/pair_analog/pair_digital/is_soft)和 `src/project.py`。

---

## 前置说明(执行前必读)

1. **金标准是安全网**:Task 1 先抓 YQ3 当前输出。此后 Task 4–6 在旁边新建引擎、不动旧函数,旧函数的金标准测试始终绿;Task 7 切薄壳后该测试仍须绿——这就是"零行为变化"的证明。
2. **drivers yaml 必须逐字转录当前常量**。Task 3 从 `runtime.py` 指定行范围**原样**拷词表/白名单。任何细微差异由金标准 diff 暴露。不要"优化""补全"词表。
3. **YQ3 点表须在位**:`YQ3SIM-IO/SIMPLE/简化/*_S.csv`(仓库已跟踪),金标准可复现。
4. **运行中的 viewer 不重启**:生成器是离线脚手架,不在 OPC 热路径。全部完成后通知用户自行重启验证 UI。
5. 每个 task 结束跑 `py -3.12 -m pytest tests/ -q` 全绿 + 相关冒烟才提交。
6. commit message 末尾加空行 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

## 文件结构

| 文件 | 职责 |
|---|---|
| `tests/fixtures/yq3_generated_golden.txt`(新) | YQ3 当前生成器输出快照,回归基线 |
| `tests/test_generator_golden.py`(新) | 断言生成器输出 == 金标准 |
| `src/project.py`(改) | 新增 `ProjectPaths.drivers_dir`(工程优先、`config/drivers/` 兜底) |
| `config/drivers/vocab.yaml`(新) | 跨设备关键词词表(转录自 runtime.py) |
| `config/drivers/devices.yaml`(新) | 设备白名单 + 类型(转录自 runtime.py) |
| `src/viewer/gen/__init__.py`(新) | 包导出 `generate` |
| `src/viewer/gen/rules.py`(新) | `DriverRules` + `load_rules(project_paths)` |
| `src/viewer/gen/gateway.py`(新) | `gateway_section(project_paths)` 柜间段提供器 |
| `src/viewer/gen/generator.py`(新) | 生成引擎 `generate(project_paths)`,消费规则 + 点表 |
| `src/viewer/runtime.py`(改) | `generate_script_from_tagmap` 改薄壳;删已搬走的常量 |
| `tests/test_driver_rules.py`(新) | `DriverRules` 加载/匹配/兜底测试 |
| `tests/test_gateway_section.py`(新) | 柜间段 csv 驱动 / 兜底测试 |
| `CLAUDE.md`(改) | 第 7 节"新功能去哪里"表更新生成器位置 |

---

## Task 1: 金标准 fixture + 回归测试

**Files:**
- Create: `tests/fixtures/yq3_generated_golden.txt`
- Create: `tests/test_generator_golden.py`

- [ ] **Step 1: 抓当前输出存为金标准**

Run:
```bash
py -3.12 -c "import src.viewer.runtime as rt; open('tests/fixtures/yq3_generated_golden.txt','w',encoding='utf-8').write(rt.generate_script_from_tagmap(''))"
```
(`generate_script_from_tagmap` 内部走 `proj.paths()` 取 YQ3 点表,入参被忽略,传空串即可。)
Expected: 文件生成,`wc -l tests/fixtures/yq3_generated_golden.txt` 数百行,含"白名单严格模式""电机设备层""阀门设备层"等段。

- [ ] **Step 2: 写回归测试 `tests/test_generator_golden.py`**

```python
# -*- coding: utf-8 -*-
"""生成器金标准回归 — 重构前后 YQ3 输出必须逐字一致.

抓取见计划 Task 1 Step 1. 重构 (drivers 外置 + gen/ 引擎) 期间此测试始终绿,
即证明零行为变化. 若需有意改输出, 必须同步重抓金标准并在 commit 说明.
"""
from pathlib import Path

import src.viewer.runtime as rt

GOLDEN = Path("tests/fixtures/yq3_generated_golden.txt")


def test_generator_matches_golden():
    expected = GOLDEN.read_text(encoding="utf-8")
    actual = rt.generate_script_from_tagmap("")
    assert actual == expected, "生成器输出与金标准不一致 — 重构改变了行为"
```

- [ ] **Step 3: 跑测试确认通过(此刻旧函数原样,必过)**

Run: `py -3.12 -m pytest tests/test_generator_golden.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/yq3_generated_golden.txt tests/test_generator_golden.py
git commit -m "test(gen): YQ3 生成器输出金标准 — 驱动规则外置的回归基线"
```

---

## Task 2: ProjectPaths.drivers_dir(工程优先、config/drivers 兜底)

**Files:**
- Modify: `src/project.py`(`ProjectPaths.__init__` 末尾加字段)
- Modify: `tests/test_project.py`(加测试)

- [ ] **Step 1: 写失败测试(加到 `tests/test_project.py` 末尾)**

```python
def test_drivers_dir_project_first_then_config(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    root = tmp_path / "projects" / "demo"
    (root).mkdir(parents=True)
    # 无工程级 drivers/ → 兜底 config/drivers
    p = prj.paths("demo")
    assert p.drivers_dir == Path("config/drivers")
    # 有工程级 drivers/ → 用工程的
    (root / "drivers").mkdir()
    p2 = prj.paths("demo")
    assert p2.drivers_dir == root / "drivers"
```
(`_setup` 已在文件里,monkeypatch `PROJECTS_ROOT`/`ACTIVE_PTR`。)

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.12 -m pytest tests/test_project.py::test_drivers_dir_project_first_then_config -v`
Expected: FAIL(`AttributeError: 'ProjectPaths' object has no attribute 'drivers_dir'`)

- [ ] **Step 3: 加字段** — `src/project.py` 的 `ProjectPaths.__init__` 里,在 `self.io_full_dir = ...` 之后加:

```python
        # 驱动规则目录: 工程级 projects/<name>/drivers/ 优先, 否则仓库默认 config/drivers/
        _proj_drivers = self.root / "drivers"
        self.drivers_dir = _proj_drivers if _proj_drivers.is_dir() else Path("config/drivers")
```

- [ ] **Step 4: 跑测试**

Run: `py -3.12 -m pytest tests/test_project.py -v`
Expected: 全 PASS(含新增)

- [ ] **Step 5: Commit**

```bash
git add src/project.py tests/test_project.py
git commit -m "feat(project): ProjectPaths.drivers_dir — 工程优先 config/drivers 兜底"
```

---

## Task 3: config/drivers/{vocab,devices}.yaml(逐字转录当前常量)

**Files:**
- Create: `config/drivers/vocab.yaml`
- Create: `config/drivers/devices.yaml`

> **转录规则**:从 `src/viewer/runtime.py` 指定行**原样**拷词进 yaml 列表,顺序、用词、空格一字不改。金标准会校验。下面给出键名 ↔ 源常量映射;每个列表的元素直接从源行拷贝。

- [ ] **Step 1: 写 `config/drivers/vocab.yaml`**

键 ↔ 源(`runtime.py`):`fault`←`_FAULT_WORDS`(2031-2032)、`remote`←`_REMOTE_WORDS`(2033-2034)、`run`←`_RUN_WORDS`(2035)、`start_cmd`←`_START_WORDS`(2036-2037)、`stop_cmd`←`_STOP_WORDS`(2038-2041)、`gateway`←`_GATEWAY_WORDS`(2047-2048)、`open_fb`←`OPEN_FB_WORDS`(2294-2295)、`close_fb`←`CLOSE_FB_WORDS`(2296-2297)、`local`←`LOCAL_WORDS`(2298)、`cmd_exclude`←`CMD_EXCLUDE_WORDS`(2301-2303)。结构:

```yaml
# 跨设备关键词词表 — 逐字转录自 runtime.py(原硬编码常量)
# 改这里 = 改生成器的工艺判断词, 不碰 Python
fault:       [故障, 告警, 报警, 异常, 失败, 失效, 损坏, 保护, 跳闸, 越限, 停电, 断线, 误操作]
remote:      [远方, 远控, 就地远方, 投运允许, 允许投运, 可用, 就绪, 准备好]
run:         [运行, 在运行, 运转]
start_cmd:   [启动, 开启, 合闸, 投入, 启泵, 开阀, "开 ", 开机, "启 "]
stop_cmd:    [停止, 停机, 停车, 停泵, 关闭, 分闸, 停运, 切除, 跳闸, 跳机, 跳泵, "关 ", 关阀, "停 ", 停E, 停F, 停A, 停B, 停C, 停D, 停#]
open_fb:     [运行, 在运行, 开到位, 已开, 在开, 开反馈, 全开, 开位, 运转, 合位, 开关合, 投运]
close_fb:    [关到位, 已关, 关反馈, 全关, 关位, 停止反馈, 停运, 停止, 停机, 已停, 停泵, 停车, 跳位, 开关跳]
local:       [在就地, 就地控制, 就地操作]
cmd_exclude: [FSSS, MFT, 保护跳闸, 联锁跳闸, SOE, 来自DPU, DCS送出, RB, "RB-", 保护动作, 保护输出, 事故]
gateway:     [MEH, DEH, TO CCS, 来自DPU, 至PECCS, 来自PECCS, 至CCS, 至DCS, 来自DCS, 柜间, 对侧, 跨DPU]
```
**注意**:含尾空格的词(`"开 "`/`"关 "`/`"停 "`)和含 `-` 的词(`"RB-"`)必须加引号,否则 yaml 丢空格/解析错。转录后**立即**与源行逐元素核对。

- [ ] **Step 2: 写 `config/drivers/devices.yaml`**

`motor_exclude_common`←`_COMMON_MOTOR_EXCLUDE`(2052-2060);`devices`←`MOTOR_DEVICES`(2063-2071)+`VALVE_DEVICES`(2073-2079),valve 在前(保持当前"先匹配阀门再匹配电机"的优先级):

```yaml
# 设备白名单 + 类型 — 逐字转录自 runtime.py
# motor: DI/DQ → RS 闭环;  valve: AI/AQ → 直通
# 新工程加设备 = 这里加一行
motor_exclude_common: [动叶, 调节阀, 调门, 风门, 出口, 进口, 气动门, 电动门, 插板,
                       出口风门, 进口风门, 润滑油, 液压油, 油泵, 油箱, 油站,
                       电加热, 加热器, 冷却风, 循环冷却, 失速, 联络]
devices:
  # —— 阀门(先匹配,优先级高)——
  - { name: 除氧器主调节阀, type: valve, include: [除氧器主调节阀, 除氧主调节阀, 除氧器主调阀, 除氧主调阀] }
  - { name: 除氧器副调节阀, type: valve, include: [除氧器副调节阀, 除氧副调节阀, 除氧器副调阀, 除氧副调阀] }
  - { name: 送风机动叶,     type: valve, include: [送风机动叶] }
  - { name: 引风机动叶,     type: valve, include: [引风机动叶] }
  - { name: 一次风机动叶,   type: valve, include: [一次风机动叶] }
  # —— 电机 ——
  - { name: 送风机,   type: motor, include: [送风机] }
  - { name: 引风机,   type: motor, include: [引风机] }
  - { name: 一次风机, type: motor, include: [一次风机] }
  - { name: 给煤机,   type: motor, include: [给煤机] }
  - { name: 磨煤机,   type: motor, include: [磨煤机] }
  - { name: 前置泵,   type: motor, include: [前置泵] }
  - { name: 凝结水泵, type: motor, include: [凝结水泵, 凝水泵] }
```
(motor 设备的 exclude 在引擎里统一 = `motor_exclude_common`,当前所有 motor 共用此表、无 per-device 额外项,故不写 `exclude_extra`。)

- [ ] **Step 3: yaml 可加载性自检**

Run:
```bash
py -3.12 -c "import yaml; v=yaml.safe_load(open('config/drivers/vocab.yaml',encoding='utf-8')); d=yaml.safe_load(open('config/drivers/devices.yaml',encoding='utf-8')); print('vocab keys', sorted(v)); print('devices', len(d['devices']), 'exclude', len(d['motor_exclude_common'])); assert '开 ' in v['start_cmd'], '尾空格丢失!'; assert 'RB-' in v['cmd_exclude']"
```
Expected: 打印 9 个 vocab 键、12 个 devices、22 个 exclude;无 AssertionError(尾空格/特殊词保住)。

- [ ] **Step 4: Commit**

```bash
git add config/drivers/vocab.yaml config/drivers/devices.yaml
git commit -m "feat(drivers): config/drivers/{vocab,devices}.yaml — YQ3 设备知识外置(逐字转录)"
```

---

## Task 4: gen/rules.py — DriverRules 加载器

**Files:**
- Create: `src/viewer/gen/__init__.py`
- Create: `src/viewer/gen/rules.py`
- Create: `tests/test_driver_rules.py`

- [ ] **Step 1: 写失败测试 `tests/test_driver_rules.py`**

```python
# -*- coding: utf-8 -*-
"""DriverRules — 读 drivers/*.yaml + 设备匹配测试"""
import src.project as prj
from src.viewer.gen.rules import load_rules


def test_load_yq3_rules():
    rules = load_rules(prj.paths("yq3"))     # yq3 无工程级 drivers → 兜底 config/drivers
    assert "故障" in rules.vocab["fault"]
    assert "开 " in rules.vocab["start_cmd"]          # 尾空格词保住
    assert len(rules.devices) == 12
    assert "动叶" in rules.motor_exclude_common


def test_match_device_valve_before_motor():
    rules = load_rules(prj.paths("yq3"))
    # "送风机动叶" 含 "送风机"(motor)也含 "送风机动叶"(valve) → 必须先判 valve
    d = rules.match_device("A送风机动叶位置反馈")
    assert d["type"] == "valve" and d["name"] == "送风机动叶"
    # 纯电机
    m = rules.match_device("A给煤机运行")
    assert m["type"] == "motor" and m["name"] == "给煤机"


def test_match_device_motor_exclude():
    rules = load_rules(prj.paths("yq3"))
    # "给煤机润滑油泵" 命中 give煤机 include 但 "润滑油"/"油泵" 在共用排除 → 不算电机本体
    assert rules.match_device("A给煤机润滑油泵运行") is None


def test_match_device_none_for_unknown():
    rules = load_rules(prj.paths("yq3"))
    assert rules.match_device("主蒸汽压力") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.12 -m pytest tests/test_driver_rules.py -v`
Expected: FAIL(`ModuleNotFoundError: src.viewer.gen.rules`)

- [ ] **Step 3: 写 `src/viewer/gen/__init__.py`**

```python
# -*- coding: utf-8 -*-
"""脚本生成器 — 规则驱动 (drivers/*.yaml). 入口 generate(project_paths)."""
from .generator import generate

__all__ = ["generate"]
```

- [ ] **Step 4: 写 `src/viewer/gen/rules.py`**

```python
# -*- coding: utf-8 -*-
"""驱动规则加载 — 读 drivers/{vocab,devices}.yaml → DriverRules

设备匹配优先级: valve 类先于 motor 类 (保持原 _match_device(VALVE) 先于 MOTOR 的行为).
匹配语义: 描述含任一 include 词, 且不含任何排除词. motor 排除 = motor_exclude_common.
"""
from pathlib import Path
from typing import List, Optional

import yaml


class DriverRules:
    def __init__(self, vocab: dict, devices: list, motor_exclude_common: list):
        self.vocab = vocab
        self.devices = devices                       # 已按 valve→motor 排序
        self.motor_exclude_common = motor_exclude_common

    def _excludes_for(self, dev: dict) -> List[str]:
        ex = list(dev.get("exclude_extra") or [])
        if dev.get("type") == "motor":
            ex = list(self.motor_exclude_common) + ex
        return ex

    def match_device(self, desc: str) -> Optional[dict]:
        if not desc:
            return None
        # valve 类先匹配, 再 motor 类 (与原代码先 VALVE_DEVICES 后 MOTOR_DEVICES 一致)
        for want_type in ("valve", "motor"):
            for dev in self.devices:
                if dev.get("type") != want_type:
                    continue
                if any(w in desc for w in dev["include"]):
                    if not any(w in desc for w in self._excludes_for(dev)):
                        return dev
        return None


def load_rules(project_paths) -> DriverRules:
    d = project_paths.drivers_dir
    vocab = yaml.safe_load((Path(d) / "vocab.yaml").read_text(encoding="utf-8")) or {}
    dev_doc = yaml.safe_load((Path(d) / "devices.yaml").read_text(encoding="utf-8")) or {}
    devices = dev_doc.get("devices") or []
    motor_exclude = dev_doc.get("motor_exclude_common") or []
    return DriverRules(vocab, devices, motor_exclude)
```

- [ ] **Step 5: 跑测试**

Run: `py -3.12 -m pytest tests/test_driver_rules.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add src/viewer/gen/__init__.py src/viewer/gen/rules.py tests/test_driver_rules.py
git commit -m "feat(gen): DriverRules 加载器 — 读 drivers/*.yaml + 设备匹配(valve先于motor)"
```

---

## Task 5: gen/gateway.py — 柜间段提供器(csv 驱动 / 兜底)

**Files:**
- Create: `src/viewer/gen/gateway.py`
- Create: `tests/test_gateway_section.py`

> 柜间归一化表 `projects/<工程>/gateway.csv`:UTF-8,表头 `目标信号,来源,描述`。每行 → `目标 = 来源`(来源是信号短码则直通,是数字则写常数)。文件不存在 → 退回注释占位(按 `vocab.gateway` 关键词扫潜在点,列 `# DPU.xxx(描述) = ???`)。本轮**不解析 Excel**,只消费这张 csv。

- [ ] **Step 1: 写失败测试 `tests/test_gateway_section.py`**

```python
# -*- coding: utf-8 -*-
"""柜间段提供器 — csv 驱动 / 兜底注释"""
from pathlib import Path

from src.viewer.gen.gateway import gateway_lines_from_csv


def test_gateway_lines_from_csv(tmp_path):
    csv = tmp_path / "gateway.csv"
    csv.write_text("目标信号,来源,描述\n"
                   "DPU3013.AI010101,DPU3044.AQ020202,MEH转速\n"
                   "DPU3013.AI010102,50.0,固定偏置\n",
                   encoding="utf-8")
    lines = gateway_lines_from_csv(csv)
    assert "DPU3013.AI010101(MEH转速) = DPU3044.AQ020202" in lines
    assert "DPU3013.AI010102(固定偏置) = 50.0" in lines


def test_gateway_csv_missing_returns_empty(tmp_path):
    assert gateway_lines_from_csv(tmp_path / "nope.csv") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.12 -m pytest tests/test_gateway_section.py -v`
Expected: FAIL(`ModuleNotFoundError: src.viewer.gen.gateway`)

- [ ] **Step 3: 写 `src/viewer/gen/gateway.py`**

```python
# -*- coding: utf-8 -*-
"""柜间通讯段 — 可插拔接口.

来源是用户的柜间线清册 (Excel). 本轮只消费一张归一化 csv:
    projects/<工程>/gateway.csv   表头: 目标信号,来源,描述
    每行 → "目标(描述) = 来源"  (来源是短码则直通, 是数字则写常数)
Excel → 此 csv 的转换是后续的事 (避免引入 Excel 解析 + 各家格式差异).
csv 不在 → gateway_lines_from_csv 返 [], 主引擎退回关键词扫描的注释占位.
"""
import csv as _csv
from pathlib import Path
from typing import List


def gateway_lines_from_csv(csv_path) -> List[str]:
    p = Path(csv_path)
    if not p.exists():
        return []
    out: List[str] = []
    rows = list(_csv.reader(p.read_text(encoding="utf-8").splitlines()))
    for r in rows[1:]:                       # 跳表头
        if len(r) < 2:
            continue
        target = r[0].strip()
        source = r[1].strip()
        desc = r[2].strip() if len(r) > 2 else ""
        if not target or not source:
            continue
        lhs = f"{target}({desc})" if desc else target
        out.append(f"{lhs} = {source}")
    return out
```

- [ ] **Step 4: 跑测试**

Run: `py -3.12 -m pytest tests/test_gateway_section.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/viewer/gen/gateway.py tests/test_gateway_section.py
git commit -m "feat(gen): 柜间段接口 — gateway.csv 驱动直通段(Excel 清册→csv 留接口)"
```

---

## Task 6: gen/generator.py — 生成引擎(消费规则,金标准逐字一致)

**Files:**
- Create: `src/viewer/gen/generator.py`
- Modify: `tests/test_generator_golden.py`(加新引擎对金标准的断言)

> **本 task 是把 `runtime.py:2179-2527` 的 `generate_script_from_tagmap` 函数体整体搬进 `generator.generate(project_paths)`,并把对模块常量的引用换成 `rules` 访问。** 机制逻辑(KKS 分组、`_device_instance`、`_device_name`、主循环、分段输出、统计、`_fmt_node`/`fmt`/`short`)原样搬入本文件。**禁止改动机制逻辑**——金标准会逐字校验。

**常量 → rules 访问映射(搬运时逐处替换)**:

| 原模块常量(runtime.py) | 替换为 |
|---|---|
| `_FAULT_WORDS` | `rules.vocab["fault"]` |
| `_REMOTE_WORDS` | `rules.vocab["remote"]` |
| `_RUN_WORDS` | `rules.vocab["run"]` |
| `_START_WORDS` | `rules.vocab["start_cmd"]` |
| `_STOP_WORDS` | `rules.vocab["stop_cmd"]` |
| `OPEN_FB_WORDS`(函数内局部) | `rules.vocab["open_fb"]` |
| `CLOSE_FB_WORDS`(局部) | `rules.vocab["close_fb"]` |
| `LOCAL_WORDS`(局部) | `rules.vocab["local"]` |
| `CMD_EXCLUDE_WORDS`(局部) | `rules.vocab["cmd_exclude"]` |
| `_GATEWAY_WORDS` | `rules.vocab["gateway"]` |
| `_match_device(desc, VALVE_DEVICES)`/`(…, MOTOR_DEVICES)` | `rules.match_device(desc)`(返回的 dict 带 `type`,按 `d["type"]=="valve"/"motor"` 分流到 valve_groups/motor_groups) |
| `MOTOR_DEVICES`/`VALVE_DEVICES`/`_COMMON_MOTOR_EXCLUDE` | 不再需要(由 rules 承载) |
| `dev["spec"]["name"]` | `dev["spec"]["name"]`(rules.match_device 返回的 dict 同样有 `name`,不变) |

`_device_instance(desc, spec)` 用 `spec["include"]` —— rules 返回的 dict 有 `include`,签名不变,直接搬。

- [ ] **Step 1: 写 `src/viewer/gen/generator.py`**

骨架(把 runtime.py 的函数体填入 `generate` 并按上表替换常量;`_recommend_cmd`/`_match_device`/`_device_instance` 等 helper 如生成器内部用到则一并搬为本模块函数):

```python
# -*- coding: utf-8 -*-
"""脚本生成引擎 — 规则驱动. 机制搬自 runtime.generate_script_from_tagmap,
设备知识改由 DriverRules 提供. 输出与原函数逐字一致 (tests/test_generator_golden.py).
"""
import csv as _csv
import glob as _glob
from collections import defaultdict
from pathlib import Path

from src import project as proj
from src.sim_engine.io_pairing_gen import load_points, pair_analog, pair_digital, is_soft
from .rules import load_rules
from .gateway import gateway_lines_from_csv


def _fmt_node(dpu, pt):
    short = pt["name"].replace("HW.", "").replace(".PV", "")
    desc = (pt.get("desc", "") or "").replace("(", "[").replace(")", "]")
    return f"{dpu}.{short}({desc})" if desc else f"{dpu}.{short}"


def _device_instance(desc, spec):
    import re
    if not desc:
        return ""
    for inc_word in spec["include"]:
        idx = desc.find(inc_word)
        if idx >= 0:
            m = re.search(r'([A-Z#0-9]{1,4})$', desc[:idx].rstrip())
            return m.group(1) if m else ""
    return ""


def generate(project_paths=None) -> str:
    pp = project_paths or proj.paths()
    rules = load_rules(pp)
    # —— 以下整体搬自 runtime.generate_script_from_tagmap(2198-2526),
    #    常量引用按计划 Task 6 映射表替换为 rules.vocab[...] / rules.match_device(...);
    #    柜间段调用 gateway_lines_from_csv(pp.root / "gateway.csv"),非空则用其行,
    #    为空退回原关键词扫描注释占位. 机制其余部分一字不改. ——
    ...
```
**实施要点**:
1. 把 runtime.py 2198–2526 的全部机制代码搬进 `generate` 体内;`csv_files`/`DPU_SCOPE`/分组/主循环/`SECTION_ORDER`/统计/header 原样。
2. 词表引用全部换成 `rules.vocab[...]`;设备白名单匹配换成 `rules.match_device(desc)` 并按返回 `type` 分流。
3. 柜间段:`gw = gateway_lines_from_csv(pp.root / "gateway.csv")`;`if gw: sections["gateway"] = gw` 否则保留原"关键词扫描 + `# … = ???`"注释逻辑。
4. `OPEN_FB_WORDS` 等原函数内局部常量删除,改引 `rules.vocab`。

- [ ] **Step 2: 加新引擎对金标准的断言(`tests/test_generator_golden.py` 末尾)**

```python
def test_new_engine_matches_golden():
    """gen.generator.generate() 输出 == 金标准 (与旧函数逐字一致)"""
    from src.viewer.gen import generate
    import src.project as prj
    actual = generate(prj.paths("yq3"))
    assert actual == GOLDEN.read_text(encoding="utf-8"), "新引擎输出偏离金标准"
```

- [ ] **Step 3: 跑测试,逐字 diff 调到一致**

Run: `py -3.12 -m pytest tests/test_generator_golden.py -v`
Expected: 两个测试都 PASS。若 `test_new_engine_matches_golden` FAIL,用以下命令看首处差异并修(差异必是某常量转录漏字或机制搬运笔误):
```bash
py -3.12 -c "
from src.viewer.gen import generate; import src.project as prj
a=generate(prj.paths('yq3')).splitlines(); g=open('tests/fixtures/yq3_generated_golden.txt',encoding='utf-8').read().splitlines()
for i,(x,y) in enumerate(zip(a,g)):
    if x!=y: print('首处差异 行',i+1); print('新:',repr(x)); print('金:',repr(y)); break
else: print('前缀一致, 行数', len(a), len(g))
"
```

- [ ] **Step 4: Commit**

```bash
git add src/viewer/gen/generator.py tests/test_generator_golden.py
git commit -m "feat(gen): 生成引擎 generator.generate() — 规则驱动, YQ3 输出金标准逐字一致"
```

---

## Task 7: runtime.py 改薄壳 + 删已搬走的常量

**Files:**
- Modify: `src/viewer/runtime.py`(`generate_script_from_tagmap` 改薄壳;删 2031-2079 常量块 + 2096-2122 的 `_match_device`/`_device_instance`/`_classify_*`/`_recommend_cmd` 等已无用 helper)

> **删除前先确认无其它引用**。`_classify_feedback`/`_classify_device_section`/`_recommend_cmd`/`_match_device`/`_device_instance`/`_fmt_node` 这些仅服务旧生成器。删前 grep 各自调用点,只在旧函数内部用到的才删;若 viewer 别处也用(如诊断面板)则保留。

- [ ] **Step 1: 查删除候选的外部引用**

Run:
```bash
grep -nE "_classify_feedback|_classify_device_section|_recommend_cmd|_match_device|_device_instance\b|MOTOR_DEVICES|VALVE_DEVICES|_FAULT_WORDS|_REMOTE_WORDS|_GATEWAY_WORDS|_fmt_node" src/viewer/app.py src/viewer/runtime.py | grep -v "def "
```
记录:仅在 `generate_script_from_tagmap` 体内(2179-2527)出现的 → 可删;`_fmt_node` 等若仅此处用也删。app.py 0 命中则全部可安全删。

- [ ] **Step 2: `generate_script_from_tagmap` 改薄壳**

`runtime.py` 的 `def generate_script_from_tagmap(...)` 整个函数体(2179-2527)替换为:
```python
def generate_script_from_tagmap(tagmap_yaml_path: str = "") -> str:
    """按工艺规则从点表生成 DSL 脚本草稿. 规则在 drivers/*.yaml,引擎在 src/viewer/gen/.
    入参 tagmap_yaml_path 保留兼容(被忽略;引擎走 proj.paths())."""
    from .gen import generate
    return generate(proj.paths())
```

- [ ] **Step 3: 删已搬走的常量与 helper**

删 `runtime.py` 中 Step 1 确认仅旧函数用到的:词表常量块(`_FAULT_WORDS`…`_GATEWAY_WORDS`、`_COMMON_MOTOR_EXCLUDE`、`MOTOR_DEVICES`、`VALVE_DEVICES`,约 2031-2079)+ helper(`_match_device`、`_device_instance`、`_classify_device_section`、`_classify_feedback`、`_recommend_cmd`、`_fmt_node`,约 2082-2169)。**Step 1 显示有外部引用的不删**。

- [ ] **Step 4: 金标准 + viewer 冒烟**

Run: `py -3.12 -m pytest tests/ -q`
Expected: 全 PASS(`test_generator_golden.py` 的 `test_generator_matches_golden` 现在经薄壳→新引擎,仍逐字一致)。
Run:
```bash
py -3.12 -c "
import src.viewer.app as a; c=a.app.test_client()
r=c.post('/api/script/generate'); d=r.get_json()
print('status', r.status_code, 'ok', d.get('ok'), '行数', len(d.get('content','').splitlines()))
"
```
Expected: status 200,ok True,行数与金标准一致。

- [ ] **Step 5: Commit**

```bash
git add src/viewer/runtime.py
git commit -m "refactor(viewer): generate_script_from_tagmap 改薄壳委托 gen/ + 删已外置常量(~530行)"
```

---

## Task 8: CLAUDE.md 文档更新

**Files:**
- Modify: `CLAUDE.md`(第 7 节"新功能去哪里"表)

- [ ] **Step 1: 更新表格** — `CLAUDE.md` 第 7 节"新功能/修改去哪里"表里,加/改行:

把"配对/点表工具"行下方加一行:
```
| 生成器规则(设备词表/白名单) | `config/drivers/{vocab,devices}.yaml`;引擎在 `src/viewer/gen/`(rules/generator/gateway) |
| 柜间通讯生成 | `projects/<工程>/gateway.csv`(归一化柜间点对点表,Excel 清册转入);引擎 `gen/gateway.py` |
```

- [ ] **Step 2: 验证 + Commit**

Run: `py -3.12 -m pytest tests/ -q`
Expected: 全 PASS

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 记录生成器规则外置位置(drivers/ + gen/ + gateway.csv)"
```

- [ ] **Step 3: 通知用户**

告知:驱动规则外置完成。drivers 知识在 `config/drivers/*.yaml`,生成器引擎在 `src/viewer/gen/`,柜间留了 `gateway.csv` 接口待 Excel 清册接入。**请用户自行重启 viewer**,点【🔧 初始化 → 生成样本】验证生成的脚本与之前一致。容量模板(需求 3)为下一轮。

---

## 自检记录

- **Spec 覆盖**:§3 力度→Task 3 schema;§4 drivers 路径→Task 2;§4 vocab/devices→Task 3;§5 gen/ 拆分→Task 4/6;§6 柜间接口→Task 5 + Task 6 Step 3;§7 金标准→Task 1 + Task 6;§8 边界(柜间 Excel 不做/模板下一轮)→计划未含,符合。
- **类型/命名一致**:`load_rules(project_paths) -> DriverRules`、`DriverRules.match_device(desc)/.vocab/.devices/.motor_exclude_common`、`generate(project_paths) -> str`、`gateway_lines_from_csv(csv_path) -> list[str]`、`ProjectPaths.drivers_dir` —— 全计划一致引用。
- **已知风险**:runtime.py 行号为 2026-06-13 现状,执行以唯一字符串匹配为准;Task 6 是"搬运 + 常量替换"非重写,金标准是唯一正确性闸门——首处 diff 命令已给出。
