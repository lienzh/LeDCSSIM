# 模型搬家 + 工程目录化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 第 1 步把模型库(ccs_model/steam)从 viewer 迁出到 `src/models/` 并建 DSL 工厂注册表;第 2 步把"工程"落成 `projects/<name>/` 目录,viewer/cli/tools 全部按"当前激活工程"解析路径,顶栏可切换。

**Architecture:** 新建 `src/project.py` 作为"工程 = 目录"的唯一真相源(脚本/备份/状态镜像/OPC 端点/点表/generated yaml 都从它解析);`src/models/dsl_registry.py` 作为 DSL 与模型库之间唯一挂接点(加 660MW preset = 注册表加一条,runtime 不动)。runtime/app 的硬路径常量全部改为"经 project.paths() 的函数"。

**Tech Stack:** Python 3.12, Flask, pyyaml, pytest。启动命令统一 `py -3.12`。

---

## 前置说明(执行前必读)

1. **工作区有未提交改动**(CLAUDE.md、viewer、src/web 等十余个文件)。开工前先把现状提交为一个独立 commit(`wip: 现状快照(模型搬家前)`),保证本计划每个 task 的 diff 干净、可单独回滚。
2. **画布清理(CLAUDE.md 第 7 节阶段 B)不在本计划内** —— 工作区有未提交的画布改动,且删除清单大,单独一轮做。
3. **阶段 2 执行期间不要重启 viewer**:文件搬迁过程中旧进程的路径是失效的。全部 task 完成后**通知用户**由用户决定何时重启(不要替用户重启)。
4. 每个 task 结束跑一次 `py -3.12 -m pytest tests/ -v` + `py -3.12 -c "import src.viewer.app"` 冒烟,过了才 commit。

---

## 阶段 1:模型搬家(Task 1–3)

### Task 1: ccs_model / steam 物理迁移到 src/models/

**Files:**
- Move: `src/viewer/ccs_model.py` → `src/models/ccs_usc_otbt.py`
- Move: `src/viewer/steam.py` → `src/models/steam.py`
- Modify: `src/viewer/runtime.py:34-35`(import)
- Modify: `tests/test_ccs_model.py:13`(import)
- Modify: `src/models/__init__.py`(导出)

- [ ] **Step 1: git mv 两个文件**

```bash
git mv src/viewer/ccs_model.py src/models/ccs_usc_otbt.py
git mv src/viewer/steam.py src/models/steam.py
```

- [ ] **Step 2: 改 runtime.py import**

`src/viewer/runtime.py:34-35`,old:
```python
from .ccs_model import CcsUscOtbt, load_params as _load_ccs_params
from .steam import steam_T_from_ph
```
new:
```python
from src.models.ccs_usc_otbt import CcsUscOtbt, load_params as _load_ccs_params
from src.models.steam import steam_T_from_ph
```

- [ ] **Step 3: 改 tests/test_ccs_model.py:13**

old: `from src.viewer.ccs_model import CcsUscOtbt, load_params`
new: `from src.models.ccs_usc_otbt import CcsUscOtbt, load_params`

- [ ] **Step 4: src/models/__init__.py 导出 CcsUscOtbt**

整文件改为:
```python
# -*- coding: utf-8 -*-
"""原子模型库 - 所有 Block 实现统一 step/reset 接口"""
from .base import Block
from .basic import CON, DirectThrough, FirstOrder, BLOCK_REGISTRY
from .ccs_usc_otbt import CcsUscOtbt

__all__ = ["Block", "CON", "DirectThrough", "FirstOrder", "BLOCK_REGISTRY", "CcsUscOtbt"]
```

- [ ] **Step 5: 确认没有残留引用**

Run: `grep -rn "viewer.ccs_model\|viewer.steam\|from .ccs_model\|from .steam" src tools tests --include="*.py"`
Expected: 无输出(__pycache__ 除外)。

- [ ] **Step 6: 验证**

Run: `py -3.12 -m pytest tests/test_ccs_model.py -v` → 全 PASS
Run: `py -3.12 -c "import src.viewer.runtime; import src.viewer.app"` → 无报错

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(models): ccs_model/steam 从 viewer 迁到 src/models (模型库独立第一步)"
```

---

### Task 2: DSL 模型工厂注册表(加 660MW = 注册表加一条)

**Files:**
- Create: `src/models/dsl_registry.py`
- Test: `tests/test_dsl_registry.py`
- Modify: `src/viewer/runtime.py`(删 `_get_ccs_params` 块、FUNC_ARITY、`_CcsHandle`、`_eval_rhs` CCS 分支、`prune_state_to_pairs._walk`)

- [ ] **Step 1: 写失败测试 `tests/test_dsl_registry.py`**

```python
# -*- coding: utf-8 -*-
"""DSL 模型工厂注册表测试 — 注册表驱动 FUNC_ARITY / _eval_rhs"""
from src.models.dsl_registry import MODEL_FACTORIES, get_factory_params
import src.viewer.runtime as rt


def test_registry_has_ccs_1000():
    spec = MODEL_FACTORIES["CCS_1000"]
    assert spec.arity == 3
    assert spec.pins == ("PST", "HM", "NE")


def test_func_arity_merged_from_registry():
    """FUNC_ARITY 必须从注册表合并 — 加新工厂不改 runtime"""
    for name, spec in MODEL_FACTORIES.items():
        assert rt.FUNC_ARITY.get(name) == spec.arity
        assert name in rt.SUPPORTED_FUNCS


def test_eval_ccs_1000_via_registry():
    """$m = CCS_1000(uB, Dfw, ut) 返回把柄, 管脚齐全且只积分一次"""
    s = rt._State()
    s.cycle_count = 1
    rhs = ("CCS_1000", [65.5, 452.8, 0.6733])
    handle = rt._eval_rhs(rhs, {}, s, dt=0.2)
    assert set(handle.outputs) == {"PST", "HM", "NE"}
    assert handle.outputs["NE"] > 0
    # 同周期再算一次 → 不重复积分 (last_cycle 去重)
    h2 = rt._eval_rhs(rhs, {}, s, dt=0.2)
    assert h2 is handle


def test_unknown_factory_params_returns_none():
    assert get_factory_params("CCS_NOPE") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.12 -m pytest tests/test_dsl_registry.py -v`
Expected: FAIL(`ModuleNotFoundError: src.models.dsl_registry`)

- [ ] **Step 3: 写 `src/models/dsl_registry.py`**

```python
# -*- coding: utf-8 -*-
"""DSL 模型工厂注册表 — viewer DSL 与模型库之间唯一的挂接点

加新容量 preset(如 660MW)= 此处加一条 + 一份参数 yaml, runtime/脚本语法不动:
    "CCS_660": ModelFactorySpec(arity=3, pins=("PST", "HM", "NE"),
                                params_path="config/ccs_models/xxx-660mw.yaml",
                                make=lambda p: CcsUscOtbt(p)),
"""
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .ccs_usc_otbt import CcsUscOtbt, load_params


@dataclass(frozen=True)
class ModelFactorySpec:
    arity: int                # DSL 工厂函数入参个数
    pins: Tuple[str, ...]     # 输出管脚名, 顺序 = model.step() 返回元组顺序
    params_path: str          # 参数 yaml 路径
    make: Callable            # params dict → 模型实例 (实例须有 step(*inputs, dt) -> tuple)


MODEL_FACTORIES = {
    # $YQ3 = CCS_1000(uB, Dfw, ut);  读管脚: $YQ3.PST / $YQ3.HM / $YQ3.NE
    "CCS_1000": ModelFactorySpec(
        arity=3,
        pins=("PST", "HM", "NE"),
        params_path="config/ccs_models/usc-otbt-1000mw.yaml",
        make=lambda params: CcsUscOtbt(params),
    ),
}

# 参数懒加载缓存 — 失败缓存错因, 不反复读盘 (沿用原 _get_ccs_params 语义)
_params_cache: dict = {}
_params_err: dict = {}


def get_factory_params(fname: str) -> Optional[dict]:
    if fname in _params_cache:
        return _params_cache[fname]
    if fname in _params_err:
        return None
    try:
        _params_cache[fname] = load_params(MODEL_FACTORIES[fname].params_path)
        return _params_cache[fname]
    except Exception as e:          # KeyError(未注册) / FileNotFoundError / yaml 错都走这
        _params_err[fname] = str(e)
        return None


def get_factory_error(fname: str) -> Optional[str]:
    """诊断面板用: 参数加载失败的错因"""
    return _params_err.get(fname)
```

- [ ] **Step 4: runtime.py 改注册表驱动(5 处)**

(4a) import 行(Task 1 改过的)再改,old:
```python
from src.models.ccs_usc_otbt import CcsUscOtbt, load_params as _load_ccs_params
from src.models.steam import steam_T_from_ph
```
new:
```python
from src.models.dsl_registry import MODEL_FACTORIES, get_factory_params
from src.models.steam import steam_T_from_ph
```

(4b) 删除 runtime.py:37-52 整块(`_CCS_PARAMS_PATH`、`_CCS_PARAMS`、`_CCS_PARAMS_ERR`、`def _get_ccs_params`)。

(4c) `FUNC_ARITY`(原 897-920):删掉 `"CCS_1000": 3,` 一行及其上 4 行注释,并把
old:
```python
}
SUPPORTED_FUNCS = tuple(FUNC_ARITY.keys())
```
new:
```python
}
# 模型工厂 (src/models/dsl_registry.py 注册) — 加 660MW preset = 注册表加一条, 本文件不动
FUNC_ARITY.update({name: spec.arity for name, spec in MODEL_FACTORIES.items()})
SUPPORTED_FUNCS = tuple(FUNC_ARITY.keys())
```

(4d) `_CcsHandle`(原 1189-1205)泛化为带 pins,old `__slots__`/`__init__`/`step_if_needed` 整体替换:
```python
class _CcsHandle:
    """模型工厂函数返回的运行时把柄 — 装到 s.intermediates['$YQ3'] 里.
    持有: 模型对象 + 管脚名 + 上次输出 + 上次积分的 cycle (整周期只 step 一次)
    """
    __slots__ = ("model", "pins", "outputs", "last_cycle")

    def __init__(self, model, pins):
        self.model = model
        self.pins = tuple(pins)
        self.outputs: dict = {}        # {pin: float}
        self.last_cycle: int = -1

    def step_if_needed(self, vals, dt: float, cycle: int) -> None:
        if self.last_cycle != cycle:
            self.last_cycle = cycle
            outs = self.model.step(*vals, dt)
            self.outputs = dict(zip(self.pins, outs))
```

(4e) `_eval_rhs` 的 CCS_1000 分支(原 1312-1327)替换为:
```python
    # 模型工厂 (dsl_registry 注册) — $YQ3 = CCS_1000(uB, Dfw, ut)
    # 返回 _CcsHandle 把柄, 后续 $YQ3.PST 等从把柄读管脚
    # 入参 hashable 作 key → 同一脚本里多次写 $YQ3 = CCS_1000(...) 用同一份模型
    if fname in MODEL_FACTORIES:
        spec = MODEL_FACTORIES[fname]
        key = (fname, _make_hashable(raw_args))
        handle = s.ccs_state.get(key)
        if handle is None:
            params = get_factory_params(fname)
            if params is None:
                raise _SkipCycle()    # 参数 yaml 没加载成功, 跳过 (诊断面板可查错因)
            handle = _CcsHandle(spec.make(params), spec.pins)
            s.ccs_state[key] = handle
        handle.step_if_needed([float(v) for v in vals], dt, s.cycle_count)
        return handle
```

(4f) `prune_state_to_pairs._walk`(原 763-764),old:
```python
        elif fname == "CCS_1000":
            live.add(("CCS_1000", _make_hashable(args)))
```
new:
```python
        elif fname in MODEL_FACTORIES:
            live.add((fname, _make_hashable(args)))
```

- [ ] **Step 5: 确认 runtime 无 CCS_1000 残留硬编码**

Run: `grep -n "CCS_1000\|_get_ccs_params\|_load_ccs_params\|CcsUscOtbt" src/viewer/runtime.py`
Expected: 无输出(注释里的示例文字允许保留,代码引用必须为 0;app.py 帮助文本里的 CCS_1000 示例不动)。

- [ ] **Step 6: 跑测试**

Run: `py -3.12 -m pytest tests/test_dsl_registry.py tests/test_ccs_model.py -v` → 全 PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(models): DSL 模型工厂注册表 — 加容量 preset 不再改 runtime"
```

---

### Task 3: 模型库隔离红线测试

**Files:**
- Test: `tests/test_models_isolation.py`

- [ ] **Step 1: 写测试(直接应通过 — 它是防回归红线)**

```python
# -*- coding: utf-8 -*-
"""红线: src/models 不得依赖 viewer / Flask / asyncua.

模型库必须可被离线脚本纯 import (将来控制优化/参数整定不经 OPC 直接调模型),
反向依赖一旦混进来, 这条路就断了. 架构再变也保留本测试.
"""
import re
from pathlib import Path

FORBIDDEN = re.compile(r"^\s*(from|import)\s+(src\.viewer|flask|asyncua)", re.M)


def test_models_package_is_standalone():
    files = list(Path("src/models").glob("*.py"))
    assert files, "src/models 下应有模型文件"
    for py in files:
        text = py.read_text(encoding="utf-8")
        m = FORBIDDEN.search(text)
        assert m is None, f"{py} 引用了禁止依赖: {m.group(0).strip()}"
```

- [ ] **Step 2: 跑测试**

Run: `py -3.12 -m pytest tests/test_models_isolation.py -v` → PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_models_isolation.py
git commit -m "test(models): 模型库不依赖 viewer/flask/asyncua 红线测试"
```

---

## 阶段 2:工程目录化(Task 4–10)

### Task 4: src/project.py 工程上下文

**Files:**
- Create: `src/project.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: 写失败测试 `tests/test_project.py`**

```python
# -*- coding: utf-8 -*-
"""src/project.py — 工程目录上下文测试 (pytest, 用 tmp_path 隔离)"""
from pathlib import Path

import src.project as prj


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(prj, "ACTIVE_PTR", tmp_path / "active.yaml")


def test_paths_layout_and_meta(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    root = tmp_path / "projects" / "demo"
    root.mkdir(parents=True)
    (root / "project.yaml").write_text(
        "display: 演示工程\nio_dir: SOME/IO\nio_fallback_globs:\n  - OLD/DPU*.csv\n",
        encoding="utf-8")
    assert prj.list_projects() == ["demo"]
    assert prj.get_active() == "demo"      # 无指针 → 取第一个
    p = prj.paths()
    assert p.script == root / "script.txt"
    assert p.endpoints == root / "opc_endpoints.yaml"
    assert p.snapshot == root / "state" / "state_snapshot.json"
    assert p.snapshot_backups == root / "state" / "snapshot_backups"
    assert p.script_backups == root / "script_backups"
    assert p.generated_dir == root / "generated"
    assert p.io_dir == Path("SOME/IO")
    assert p.io_fallback_globs == ["OLD/DPU*.csv"]
    assert p.display == "演示工程"


def test_io_dir_defaults_to_project_io(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "projects" / "bare").mkdir(parents=True)   # 无 project.yaml
    p = prj.paths("bare")
    assert p.io_dir == tmp_path / "projects" / "bare" / "io"
    assert p.display == "bare"


def test_set_active_and_underscore_excluded(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for n in ("aaa", "bbb", "_templates"):
        (tmp_path / "projects" / n).mkdir(parents=True)
    assert prj.list_projects() == ["aaa", "bbb"]   # _templates 不算工程
    prj.set_active("bbb")
    assert prj.get_active() == "bbb"
    try:
        prj.set_active("nope")
        assert False, "不存在的工程应当抛 ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.12 -m pytest tests/test_project.py -v`
Expected: FAIL(`ModuleNotFoundError: src.project`)

- [ ] **Step 3: 写 `src/project.py`**

```python
# -*- coding: utf-8 -*-
"""工程上下文 — "工程 = projects/<name>/ 目录" 的唯一真相源

所有组件 (viewer / src.cli / tools) 都从这里解析"当前工程"的文件路径:
    脚本 / 脚本备份 / 状态镜像 / OPC 端点 / 点表目录 / generated yaml

激活指针存 config/active_project.yaml (机器相关, gitignore):
    active: yq3
缺指针时取 projects/ 下第一个目录 (按名排序); `_` 开头的目录 (如 _templates) 不算工程.
"""
from pathlib import Path
from typing import List, Optional

import yaml

PROJECTS_ROOT = Path("projects")
ACTIVE_PTR = Path("config/active_project.yaml")

# 点表文件名默认模式 (project.yaml 可用 io_glob 覆盖)
DEFAULT_IO_GLOB = "*[_-]S.csv"


class ProjectPaths:
    """单个工程的全部文件路径"""

    def __init__(self, name: str):
        self.name = name
        self.root = PROJECTS_ROOT / name
        self.project_yaml = self.root / "project.yaml"
        self.script = self.root / "script.txt"
        self.script_backups = self.root / "script_backups"
        self.endpoints = self.root / "opc_endpoints.yaml"
        self.state_dir = self.root / "state"
        self.snapshot = self.state_dir / "state_snapshot.json"
        self.snapshot_backups = self.state_dir / "snapshot_backups"
        self.generated_dir = self.root / "generated"
        meta = {}
        if self.project_yaml.exists():
            try:
                meta = yaml.safe_load(self.project_yaml.read_text(encoding="utf-8")) or {}
            except Exception:
                meta = {}
        self.display = str(meta.get("display") or name)
        # 点表目录: 默认 projects/<name>/io, 可用 io_dir 指到仓库其它位置 (如 YQ3SIM-IO)
        self.io_dir = Path(meta.get("io_dir")) if meta.get("io_dir") else (self.root / "io")
        self.io_glob = str(meta.get("io_glob") or DEFAULT_IO_GLOB)
        # 简化点表找不到时的回退 glob (仓库根相对), 如老命名 YQ3SIM-IO/DPU*.csv
        self.io_fallback_globs = [str(g) for g in (meta.get("io_fallback_globs") or [])]
        # 全量点表目录 (tools/generate_yaml_from_pairs 用), 默认与 io_dir 相同
        self.io_full_dir = Path(meta.get("io_full_dir")) if meta.get("io_full_dir") else self.io_dir


def list_projects() -> List[str]:
    if not PROJECTS_ROOT.exists():
        return []
    return sorted(p.name for p in PROJECTS_ROOT.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def get_active() -> str:
    names = list_projects()
    if not names:
        raise RuntimeError("projects/ 下没有任何工程目录 — 至少建一个 projects/<name>/")
    if ACTIVE_PTR.exists():
        try:
            doc = yaml.safe_load(ACTIVE_PTR.read_text(encoding="utf-8")) or {}
            if doc.get("active") in names:
                return doc["active"]
        except Exception:
            pass
    return names[0]


def set_active(name: str) -> None:
    if name not in list_projects():
        raise ValueError(f"工程不存在: {name!r} (现有: {list_projects()})")
    ACTIVE_PTR.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PTR.write_text(
        f"# 当前激活工程 — viewer 顶栏切换写这里\nactive: {name}\n", encoding="utf-8")


def paths(name: Optional[str] = None) -> ProjectPaths:
    return ProjectPaths(name or get_active())
```

- [ ] **Step 4: 跑测试**

Run: `py -3.12 -m pytest tests/test_project.py -v` → 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/project.py tests/test_project.py
git commit -m "feat(project): 工程上下文 src/project.py — 工程=目录的唯一真相源"
```

---

### Task 5: 现工程资产迁入 projects/yq3/

**Files:**
- Create: `projects/yq3/project.yaml`
- Move: `config/script.txt` → `projects/yq3/script.txt`(git mv,已跟踪)
- Move: `config/script_backups/` → `projects/yq3/script_backups/`(未跟踪,普通 mv)
- Move: `config/opc_endpoints.yaml` → `projects/yq3/opc_endpoints.yaml`(gitignored,普通 mv)
- Move: `data/state_snapshot.json`、`data/snapshot_backups/` → `projects/yq3/state/`(gitignored)
- Move: `config/*.generated.yaml` → `projects/yq3/generated/`(gitignored)
- Modify: `.gitignore`

- [ ] **Step 1: 建目录 + 迁移(Bash 工具执行)**

```bash
mkdir -p projects/yq3/state projects/yq3/generated
git mv config/script.txt projects/yq3/script.txt
[ -d config/script_backups ] && mv config/script_backups projects/yq3/script_backups
[ -f config/opc_endpoints.yaml ] && mv config/opc_endpoints.yaml projects/yq3/opc_endpoints.yaml
[ -f config/script.txt.bak ] && mv config/script.txt.bak projects/yq3/script.txt.bak
[ -f data/state_snapshot.json ] && mv data/state_snapshot.json projects/yq3/state/
[ -d data/snapshot_backups ] && mv data/snapshot_backups projects/yq3/state/snapshot_backups
for f in config/models.generated.yaml config/connections.generated.yaml config/tagmap.generated.yaml; do
  [ -f "$f" ] && mv "$f" projects/yq3/generated/
done
true
```

- [ ] **Step 2: 写 `projects/yq3/project.yaml`**

```yaml
# YQ3 工程 — 1000MW USC, 现网首个工程
# io_dir: 简化点表目录 (viewer @ 补全 / 脚本生成扫这里)
# io_full_dir: 全量点表目录 (tools/generate_yaml_from_pairs 配对用)
display: YQ3 (1000MW)
io_dir: YQ3SIM-IO/SIMPLE/简化
io_full_dir: YQ3SIM-IO
io_fallback_globs:
  - YQ3SIM-IO/DPU*.csv
```

- [ ] **Step 3: .gitignore 更新**

old(18-33 行附近的相关条目):
```
config/opc_endpoints.yaml
```
和
```
config/script.txt.bak
config/*.generated.yaml
```
new(同位置替换 + 追加):
```
config/active_project.yaml
projects/*/opc_endpoints.yaml
projects/*/script_backups/
projects/*/state/
projects/*/generated/
projects/*/script.txt.bak
```
(YQ3SIM-IO 的 18-25 行放行规则不动。)

- [ ] **Step 4: 验证**

Run: `git status --short` → 应看到 `R config/script.txt -> projects/yq3/script.txt`、`.gitignore` 修改、`projects/yq3/project.yaml` 新增;`projects/yq3/opc_endpoints.yaml`、`state/`、`generated/`、`script_backups/` **不**出现在 untracked 里。
Run: `py -3.12 -c "from src import project; p=project.paths(); print(p.name, p.script, p.io_dir)"`
Expected: `yq3 projects\yq3\script.txt YQ3SIM-IO\SIMPLE\简化`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(project): 现工程资产迁入 projects/yq3/ (脚本/备份/端点/状态/generated)"
```

---

### Task 6: runtime.py 全部路径走工程上下文 + switch_project

**Files:**
- Modify: `src/viewer/runtime.py`(端点路径、脚本路径 ×2、镜像路径、点表扫描、新增 switch_project)

- [ ] **Step 1: 加 import(runtime.py:33 `from src.opc_client...` 之后)**

```python
from src import project as proj
```

- [ ] **Step 2: 端点路径改函数**

删除 L106 `_ENDPOINT_PATH = Path("config/opc_endpoints.yaml")`,原位置加:
```python
def _endpoint_path() -> Path:
    """当前工程的 opc_endpoints.yaml (跟着 projects/<active>/ 走)"""
    return proj.paths().endpoints
```
然后对整个文件做 replace_all:`_ENDPOINT_PATH` → `_endpoint_path()`(命中 `_load_endpoint_config` 内 5 处、`set_endpoint_mode` 内 2 处)。

- [ ] **Step 3: 脚本路径 2 处**

`reinit_lag_from_dcs`(原 413):old `script_path = Path("config/script.txt")` → new `script_path = proj.paths().script`;
同函数错误文案 old `"config/script.txt 不存在, 先编辑器里点【💾 保存】"` → new `f"{script_path} 不存在, 先编辑器里点【💾 保存】"`。
`dryrun_preview`(原 622):old `sp = Path("config/script.txt")` → new `sp = proj.paths().script`;错误文案 old `"config/script.txt 不存在"` → new `f"{sp} 不存在"`。

- [ ] **Step 4: 镜像路径改函数**

删除原 1341-1342 两个常量,原位置加:
```python
def _snapshot_path() -> Path:
    return proj.paths().snapshot

def _snapshot_bak_dir() -> Path:
    return proj.paths().snapshot_backups
```
replace_all:`_SNAPSHOT_PATH` → `_snapshot_path()`、`_SNAPSHOT_BAK_DIR` → `_snapshot_bak_dir()`(命中 `_migrate_legacy_bak` / `list_snapshot_backups` / `save_state_snapshot` / `restore_state_snapshot` / `get_snapshot_info` / `reset_persistent_state`:801)。
注意 `_migrate_legacy_bak` 里 `.with_suffix` 链照常工作;app.py:1022 的 `from src.viewer.runtime import _SNAPSHOT_PATH` 在 Task 7 改。

- [ ] **Step 5: generate_script_from_tagmap 点表扫描(原 2191-2197)**

old:
```python
    SIMPLE_DIR = Path("YQ3SIM-IO/SIMPLE/简化")
    csv_files = sorted(SIMPLE_DIR.glob("*[_-]S.csv"))
```
及其后的 `Path("YQ3SIM-IO").glob("DPU*.csv")` 回退,整段 new:
```python
    pp = proj.paths()
    csv_files = sorted(pp.io_dir.glob(pp.io_glob))
    if not csv_files:
        import glob as _glob
        for pat in pp.io_fallback_globs:
            csv_files = sorted(Path(f) for f in _glob.glob(pat))
            if csv_files:
                break
```
(保留原回退分支里对文件名的其它处理逻辑不变。)

- [ ] **Step 6: 新增 switch_project(放在 `get_status` 定义之前)**

```python
def switch_project(name: str) -> dict:
    """切换激活工程 — 仅停止态允许. 全量重建运行内存状态 + 立即探活新工程端点."""
    global _STATE
    if _STATE.running:
        return {"ok": False, "error": "OPC 循环运行中, 先点【■ 停止】再切工程"}
    try:
        proj.set_active(name)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    _STATE = _State()      # RS/LAG/$var/模型实例全清 — 不能把 A 工程状态带进 B 工程
    log_event("project", f"📂 切换工程 → {name}")
    try:
        _probe_once_and_store()
    except Exception:
        pass
    return {"ok": True, "active": name}
```

- [ ] **Step 7: 验证**

Run: `grep -n "config/script.txt\|config/opc_endpoints\|data/state_snapshot\|data/snapshot_backups\|YQ3SIM-IO" src/viewer/runtime.py`
Expected: 无代码命中(模块 docstring/注释允许)。
Run: `py -3.12 -m pytest tests/ -v` → 全 PASS
Run: `py -3.12 -c "import src.viewer.runtime as rt; print(rt.get_endpoint_config()['url'])"` → 打印 yq3 工程端点 URL

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(viewer): runtime 路径全部走工程上下文 + switch_project"
```

---

### Task 7: app.py 路径走工程上下文

**Files:**
- Modify: `src/viewer/app.py`(CONFIG、SCRIPT_PATH/BACKUP_DIR、POINT_TABLE、_SNAPSHOT_PATH import)

- [ ] **Step 1: 加 import(app.py:12 `from . import runtime as rt` 之后)**

```python
from src import project as proj
```

- [ ] **Step 2: CONFIG 改"None = 当前工程 generated 目录"**

old(19-28):
```python
CONFIG = {
    "models": "config/models.generated.yaml",
    "connections": "config/connections.generated.yaml",
    "tagmap": "config/tagmap.generated.yaml",
    "csv": "data/run.csv",
}

# 点表目录 + 文件名模式 — 当前用"简化"版
POINT_TABLE_DIR = "YQ3SIM-IO/SIMPLE/简化"
POINT_TABLE_GLOB = "*[_-]S.csv"   # 3001_S.csv / 3038-S.csv 都匹配
```
new:
```python
CONFIG = {
    "models": None,        # None = projects/<active>/generated/models.generated.yaml
    "connections": None,   # CLI --models/--connections/--tagmap 仍可显式覆盖
    "tagmap": None,
    "csv": "data/run.csv",
}


def _cfg_path(kind: str) -> Path:
    """models/connections/tagmap 的 generated yaml — CLI 覆盖优先, 否则当前工程目录"""
    v = CONFIG.get(kind)
    if v:
        return Path(v)
    return proj.paths().generated_dir / f"{kind}.generated.yaml"
```
然后:L50 `Path(CONFIG["models"])` → `_cfg_path("models")`;L71 → `_cfg_path("connections")`;L80 → `_cfg_path("tagmap")`;L559 `rt.generate_script_from_tagmap(CONFIG["tagmap"])` → `rt.generate_script_from_tagmap(str(_cfg_path("tagmap")))`;`run()` 里 4 行 print 的 `CONFIG['models']` 等 → `_cfg_path('models')` 等(csv 保留 `CONFIG['csv']`)。

- [ ] **Step 3: 脚本/备份路径改函数**

old(480-481):
```python
SCRIPT_PATH = CONFIG_DIR / "script.txt"
BACKUP_DIR = CONFIG_DIR / "script_backups"   # 时间戳备份目录
```
new:
```python
def _script_path() -> Path:
    return proj.paths().script


def _backup_dir() -> Path:
    return proj.paths().script_backups
```
replace_all:`SCRIPT_PATH` → `_script_path()`(约 14 处:490/495/506/509/511/524-529/571-573/692-694/706-710/725-727),`BACKUP_DIR` → `_backup_dir()`(约 9 处:491/494/497/1035/1038/1055/1059/1085/1087/1090)。
1056-1060 的路径校验逻辑里 `bdir = BACKUP_DIR.resolve(...)` 同样变 `_backup_dir().resolve(...)`。

- [ ] **Step 4: 点表扫描 2 处**

`api_script_symbols`(1116-1120)old:
```python
        candidates = sorted(_g.glob(f"{POINT_TABLE_DIR}/{POINT_TABLE_GLOB}"))
        if not candidates:
            candidates = sorted(_g.glob("YQ3SIM-IO/DPU*.csv"))
            candidates = [c for c in candidates if "_" not in Path(c).stem
                          or Path(c).stem.startswith("DPU")]
```
new:
```python
        pp = proj.paths()
        candidates = sorted(_g.glob(f"{pp.io_dir}/{pp.io_glob}"))
        if not candidates:
            for pat in pp.io_fallback_globs:
                candidates = sorted(_g.glob(pat))
                if candidates:
                    break
            candidates = [c for c in candidates if "_" not in Path(c).stem
                          or Path(c).stem.startswith("DPU")]
```
`api_script_symbols_from_opc`(1183-1190)同样把 `POINT_TABLE_DIR/POINT_TABLE_GLOB` 换成 `pp.io_dir/pp.io_glob`、把 `YQ3SIM-IO/DPU*.csv` 回退换成 `pp.io_fallback_globs` 循环。

- [ ] **Step 5: 镜像路径 import 修正**

`api_script_state_delete`(1019-1028)old:
```python
    from src.viewer.runtime import _SNAPSHOT_PATH
    try:
        if _SNAPSHOT_PATH.exists():
            _SNAPSHOT_PATH.unlink()
```
new:
```python
    try:
        sp = rt._snapshot_path()
        if sp.exists():
            sp.unlink()
```
另:`api_script_state_*` 里所有 `rt._SNAPSHOT_BAK_DIR`(如 1001)→ `rt._snapshot_bak_dir()`。

- [ ] **Step 6: 验证**

Run: `grep -n "SCRIPT_PATH\|BACKUP_DIR\|POINT_TABLE_DIR\|_SNAPSHOT_PATH\|_SNAPSHOT_BAK_DIR\|YQ3SIM-IO" src/viewer/app.py`
Expected: 仅 HTML 帮助文本/注释命中,代码 0 命中。
Run: `py -3.12 -c "import src.viewer.app"` → 无报错;`py -3.12 -m pytest tests/ -v` → 全 PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(viewer): app.py 脚本/备份/点表/generated 路径走工程上下文"
```

---

### Task 8: /api/project + 顶栏工程切换器

**Files:**
- Modify: `src/viewer/app.py`(新 API ×2、工具栏第 2 行 HTML、JS)

- [ ] **Step 1: 新增 API(放在 `api_script_backups_list` 之前)**

```python
# ---------- 工程切换 ----------

@app.route("/api/project")
def api_project_get():
    """工程清单 + 当前激活"""
    items = [{"name": n, "display": proj.paths(n).display} for n in proj.list_projects()]
    return jsonify({"active": proj.get_active(), "projects": items})


@app.route("/api/project", methods=["POST"])
def api_project_switch():
    """切换工程 — 运行中拒绝; 成功后前端整页 reload"""
    global _SYMBOLS_CACHE
    name = (request.get_json(force=True, silent=True) or {}).get("name", "")
    r = rt.switch_project(name)
    if not r.get("ok"):
        return jsonify(r), 409
    _SYMBOLS_CACHE = None     # 点表跟工程走, 切换后重扫
    return jsonify(r)
```

- [ ] **Step 2: 工具栏第 2 行 HTML(1438-1449)**

把现有 `<span class="grp-label">工程</span>`(🔧 初始化那组)改为 `<span class="grp-label">工程辅助</span>`,并在该行**最前**(`<span class="grp-label">查看</span>` 之前)插入:
```html
  <span class="grp-label">工程</span>
  <select id="projSel" onchange="switchProject()" title="切换工程 (运行中禁止; 切换后整页重载)"></select>
  <span class="sep"></span>
```

- [ ] **Step 3: JS(加在 `setInterval(refreshProbe, 3000);` 即原 1783 行之前)**

```javascript
async function loadProjects(){
  try{
    const d = await (await fetch('/api/project')).json();
    const sel = document.getElementById('projSel');
    sel.innerHTML = '';
    for (const p of d.projects){
      const o = document.createElement('option');
      o.value = p.name; o.textContent = p.display;
      if (p.name === d.active) o.selected = true;
      sel.appendChild(o);
    }
  }catch(e){ /* 工程 API 不可用时下拉留空, 不影响其它功能 */ }
}
async function switchProject(){
  const sel = document.getElementById('projSel');
  const name = sel.value;
  if (!confirm(`切换到工程 [${name}] ?\n编辑器/状态/端点将切到该工程 (内存 RS/LAG 状态清空)。`)){
    await loadProjects(); return;
  }
  const r = await fetch('/api/project', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
  const d = await r.json();
  if (!d.ok){ alert(d.error || '切换失败'); await loadProjects(); return; }
  location.reload();
}
loadProjects();
```

- [ ] **Step 4: 顺手改 2 处过时文案**

保存按钮 title(原 1417)`config/script.txt` → `当前工程的 script.txt`;帮助文本 2453 行 `config/script_backups/` → `工程目录的 script_backups/`。

- [ ] **Step 5: 验证 + Commit**

Run: `py -3.12 -c "import src.viewer.app"`;`py -3.12 -m pytest tests/ -v` → PASS
(浏览器验证留给用户重启 viewer 后:下拉应显示 `YQ3 (1000MW)`。)

```bash
git add -A
git commit -m "feat(viewer): 顶栏工程切换器 + GET/POST /api/project"
```

---

### Task 9: tools 走工程路径

**Files:**
- Modify: `tools/generate_yaml_from_pairs.py`(SRC_DIR/输出目录)

- [ ] **Step 1: 路径改工程上下文(保留 argv 覆盖)**

old(约 25 行 `SRC_DIR = "YQ3SIM-IO"` 与约 195-199 的 `CONFIG_DIR / "*.generated.yaml"`):
```python
SRC_DIR = "YQ3SIM-IO"
```
new:
```python
from src import project as _prj
SRC_DIR = str(_prj.paths().io_full_dir)    # 全量点表目录 (project.yaml: io_full_dir)
```
输出 3 个 yaml 的目录:old `CONFIG_DIR`(config/)→ new `_prj.paths().generated_dir`(写前 `mkdir(parents=True, exist_ok=True)`)。文件内引导注释里的 `config/xxx.generated.yaml` 路径文案同步替换为 `projects/<工程>/generated/xxx.generated.yaml`。
`tools/mark_opc_communication.py` 不改(一次性工程工具,argv 已可指定目录,YAGNI)。

- [ ] **Step 2: 验证 + Commit**

Run: `py -3.12 -c "import tools.generate_yaml_from_pairs as g; print(g.SRC_DIR)"` → `YQ3SIM-IO`

```bash
git add -A
git commit -m "refactor(tools): generate_yaml_from_pairs 输入/输出走工程目录"
```

---

### Task 10: 文档同步 + 收尾验证

**Files:**
- Modify: `CLAUDE.md`(第 4 节点表目录约定 / opc_endpoints 路径 / 新增工程切换说明)

- [ ] **Step 1: CLAUDE.md 更新要点**

1. 第 4 节"点表目录约定"改为:`viewer 按当前工程 projects/<name>/project.yaml 的 io_dir 扫描(yq3 = YQ3SIM-IO/SIMPLE/简化);回退 io_fallback_globs`。
2. "viewer OPC 端点切换"小节:配置文件路径 `config/opc_endpoints.yaml` → `projects/<工程>/opc_endpoints.yaml`(仍 gitignore);"唯一真相源"条目同步改。
3. 第 4 节新增一小段"**工程切换**(顶栏 工程 ▾)":工程 = `projects/<name>/` 目录(script.txt / script_backups / opc_endpoints.yaml / state / generated);切换仅停止态允许,切换清空内存 RS/LAG 状态;激活指针 `config/active_project.yaml`(gitignore);新建工程 = 建目录 + project.yaml。
4. 模型库:注明 `src/models/`(ccs_usc_otbt / steam / dsl_registry),加容量 preset = dsl_registry 加一条 + 参数 yaml。

- [ ] **Step 2: 全量验证**

Run: `py -3.12 -m pytest tests/ -v` → 全 PASS
Run: `py -3.12 -c "import src.viewer.app; import src.cli.main; import tools.generate_yaml_from_pairs"` → 无报错
Run: `grep -rn "config/script.txt\|config/opc_endpoints" src tools --include="*.py"` → 代码 0 命中

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: CLAUDE.md 同步工程目录化 (projects/<name>/ + 模型注册表)"
```

- [ ] **Step 4: 通知用户**

告知:全部完成,**请用户自行重启 viewer**(`py -3.12 -m src.viewer`)验证:① 顶栏出现 `工程: [YQ3 (1000MW)]`;② 脚本/备份/镜像/端点行为与迁移前一致;③ 运行中切工程被拒绝。不要替用户重启。

---

## 自检记录

- 覆盖:需求 1/2(Task 4-8)、需求 5 的"接口稳定"前半(Task 1-3);需求 3/4/6 属后续轮次(模板机制/驱动规则外置),本计划不含。
- 类型一致性:`proj.paths()` 返回 `ProjectPaths`;`_endpoint_path()/_snapshot_path()/_snapshot_bak_dir()/_script_path()/_backup_dir()` 均返回 `Path`;`MODEL_FACTORIES[name].pins` 为 tuple,与 `_CcsHandle.pins` 一致;`switch_project` 返回 dict 与 `/api/project` POST 透传一致。
- 已知风险:runtime/app 行号基于 2026-06-12 现状,执行时以唯一字符串匹配为准,不要盲按行号。
