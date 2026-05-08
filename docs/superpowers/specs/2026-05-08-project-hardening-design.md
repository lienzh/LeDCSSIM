# 项目加固设计（2026-05-08）

DCS 协调控制（CCS）逻辑仿真验证平台一次性整批修复 P0 + P1 大部分问题，目标是"日常使用顺手 + 在线闭环不再出错数据"。

## 1. 范围与"完成的定义"

### 1.1 修复清单（10 项，单批合并）

| # | 类别 | 严重 | 项目 | 决策 |
|---|---|---|---|---|
| 1 | 引擎 | P0 | 纯代数环检测 | 启动严格检测，环存在即报 `AlgebraicLoopError` 拒启动 |
| 2 | 引擎 | P0 | ref_in/ref_out 跨页/跨层校验 | 启动严格校验，缺失即 `RefMissingError`，重复即 `RefDuplicateError` |
| 3 | OPC | P0 | 在线断线重连 | 自动重连（指数退避 1/2/4/8/16s），失败期间保持上次正常值，连续 5 次失败 ≈ 30s 后停止仿真 |
| 4 | OPC | P0 | 事件循环复用 | 后台单一长生命周期 loop，所有 OPC 协程通过 `run_coroutine_threadsafe` 提交 |
| 5 | OPC | P1 | SourceTimestamp 校验 | 改为 `(now - src_ts) > 2 * dt` 阈值判陈旧；阈值参数化到 `sim_settings.yaml` |
| 6 | 前端 | P0 | 复制粘贴 ref 重命名语义 | 仅 `output` 与 `ref_out` 加 `_copy`；`ref_in` 保留原 tag |
| 7 | 前端 | P0 | 方向键作用域 | 增加 contenteditable/侧边面板/工况编辑器焦点判定，输入态不触发节点移动 |
| 8 | 前端 | P0 | 侧边面板 keydown 监听器累积 | 改为具名 handler，open 前先 remove，保证唯一；统一到 `_panelKeyHandler` / `_pasteHandler` / `_arrowHandler` |
| 9 | API | P1 | 变量同步 dry-run | 新增 `?dry_run=true`，前端弹窗确认后再二次 POST |
| 10 | 启动 | P1 | 配置一致性严格校验 | 启动时校验 `scenarios.yaml` / `_manifest.json` / `block_library.yaml`，任一不一致拒启动；可用 `LEDCS_SKIP_STARTUP_CHECK=1` 跳过 |

附带的小项（不单列编号、随上述变更一起做）：bare except 全面补 `logger.exception`；DELAY/Integrator/Inertia 等有状态块的状态从 `node.params["_prev"]` 迁出到 `GraphNode.state`；`config/sim_settings.yaml` 落地。

### 1.2 完成的定义

- **在线**：连接真实 NTVDPU（`opc.tcp://localhost:9440`，`AI010605` 通道）跑 ≥60s 闭环无错；中途人为断网/恢复一次，状态切换 `online → degraded → online` 正确。
- **离线**：跑一遍包含 ref_in/ref_out 跨页 + 含反馈环（含 DELAY）的场景，行为符合新规约。
- **前端**：手动确认四个场景（见 §5.3）。
- **启动校验**：故意把 `scenarios.yaml` 塞进一个不存在的变量，启动应明确拒绝并打印定位信息。
- **回归**：现有 `tests/test_graph_runner.py` 保持通过，新增最少必要单测全绿。

---

## 2. 引擎层改动（src/sim_engine, src/opc_client）

### 2.1 graph_runner.py

#### 纯代数环检测（#1）

拓扑排序前增加一次环检测：

1. 复制依赖图，删除所有标记为"有状态"的节点（Inertia / Integrator / PID / LeadLag / SecondOrder / RateLimiter / DELAY 等——通过 Block 基类增加 `is_stateful: bool` 属性区分）。
2. 在剩余子图上跑 SCC（Tarjan 或 networkx 等价实现），任何长度 > 1 的强连通分量或自环 → `AlgebraicLoopError`。
3. 错误消息列出环上节点 id、它们之间的连边，以及"建议在 X→Y 之间插入 DELAY"的提示。

`validate(available_signals)` 把检测合并进去；`engine.start()` 先 `validate()` 再开始步进。

#### ref 跨页/跨层校验（#2）

加载所有页面后构建：

```python
ref_index = {
    tag: {
        "out": [(page_id, node_id), ...],
        "in":  [(page_id, node_id), ...],
    }
}
```

校验规则：

- `len(out) == 0` 且 `len(in) > 0` → `RefMissingError`（列出所有缺失的 ref_in 位置）。
- `len(out) > 1` → `RefDuplicateError`（列出所有重复的 ref_out 位置）。
- `len(out) == 1` 且 `len(in) == 0` → warning（不阻塞）。

错误消息形如 `RefMissingError: ref_in 'unit_power' at page='IB_master_ctrl' node='id_42' has no matching ref_out`。

#### 有状态块状态迁出 params

- `GraphNode` 新增 `state: dict` 字段（默认 `{}`）；
- DELAY 的 `_prev`、Integrator 的累积量、Inertia 的上一步值等从 `node.params` 迁到 `node.state`；
- 序列化时 `state` 不进 JSON（运行态字段）；
- 不改外部 API 与画布 JSON。

### 2.2 src/opc_client/

#### `last_good_values` 缓存（#3）

```python
class OPCClient:
    last_good_values: dict[str, Any]   # 最近一次成功读取的值快照
    
    async def read_values(self, nodes):
        try:
            values = await self._raw_read(nodes)
            self.last_good_values.update(zip(nodes, values))
            return values
        except OPCError:
            # 上抛由 engine 决定是否回退到 last_good_values
            raise
```

#### 后台事件循环（#4）

新文件 `src/opc_client/runtime.py`：

```python
class OPCRuntime:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
    
    def run(self, coro):  # 同步等待
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
    
    def shutdown(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
```

`app.py` 起一个全局 `OPC_RUNTIME = OPCRuntime()`；所有原本 `asyncio.new_event_loop()` 的地方改成 `OPC_RUNTIME.run(client.do_something())`。Flask 路由阻塞等结果。

**引擎主循环也共用此 loop**：`engine.start()` 不再自建 loop，而是把 `_step_loop` 协程通过 `OPC_RUNTIME.run()` 或 `run_coroutine_threadsafe` 提交到同一个后台 loop。这样 `_reconnect_loop` 用 `asyncio.create_task` 创建的子任务能正确调度，且 OPC client 的连接不会被多 loop 争用。

### 2.3 engine.py

#### 在线模式重连循环（#3）

```python
async def _read_opc_inputs(self):
    try:
        values = await self.client.read_values(self.input_nodes)
        self.opc_status = "online"
        return values
    except OPCError:
        self.opc_status = "degraded"
        # 触发重连协程（不阻塞当前步进）
        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        # 回退使用 last_good_values
        return [self.client.last_good_values.get(n) for n in self.input_nodes]

async def _reconnect_loop(self):
    backoffs = self.settings.opc.reconnect_backoff  # [1, 2, 4, 8, 16]
    max_failures = self.settings.opc.reconnect_max_failures  # 5
    for i, delay in enumerate(backoffs[:max_failures]):
        await asyncio.sleep(delay)
        try:
            await self.client.connect()
            self.opc_status = "online"
            return
        except OPCError:
            continue
    self.opc_status = "failed"
    self.stop()
```

`engine.opc_status` 通过 `/api/sim/status` 暴露。

#### SourceTimestamp 阈值（#5）

```python
threshold = self.settings.opc.timestamp_stale_threshold_steps * self.dt  # 默认 2*dt
if (datetime.utcnow() - src_ts).total_seconds() > threshold:
    logger.warning(f"stale value for {node}: ts age {age:.2f}s > {threshold}s")
```

`engine.dt` 在 `engine.__init__` 时从 `sim_settings.yaml` 的 `step_dt` 加载（缺省回退到现有硬编码值）。

### 2.4 异常类型

新文件 `src/sim_engine/errors.py`：

```python
class AlgebraicLoopError(RuntimeError): ...
class RefMissingError(RuntimeError): ...
class RefDuplicateError(RuntimeError): ...
class ConfigInconsistencyError(RuntimeError): ...
```

`engine.start()` 不捕获这些（让它们冒泡到调用方）；`app.py` 启动钩子捕获 → `print` + `sys.exit(1)`；API 路由捕获 → 400 + 详细 message。

---

## 3. API 与配置层（src/web/app.py + config/）

### 3.1 变量同步 dry-run（#9）

API：

| 路径 | 行为 |
|---|---|
| `POST /api/variables/sync?dry_run=true` | 只计算 diff，不写盘，返回 JSON |
| `POST /api/variables/sync` | 真正写入；可选 `?delete_orphans=true` 同时清理孤儿 |

返回结构：

```json
{
  "added":    [{"tag": "...", "type": "AI", "source": "opc_mapping"}],
  "updated":  [{"tag": "...", "field": "type", "old": "CALC", "new": "AI", "source": "opc_mapping"}],
  "orphaned": [{"tag": "...", "reason": "画布与 mapping 都不再引用"}],
  "summary":  {"add": 3, "update": 2, "orphan": 1}
}
```

前端 `/variables` 页面"一键同步"按钮改为两步：先 `dry_run=true` → 弹窗显示 added/updated/orphaned 表格 → 用户点"确认"才发送真正写入请求。orphaned 条目给单独 checkbox"同时删除孤儿变量"，默认不勾。

### 3.2 启动严格校验（#10）

新文件 `src/web/startup_check.py`，导出 `run_all_checks()`：

```python
def run_all_checks(config_dir: Path) -> None:
    """启动时调用；任一失败抛 ConfigInconsistencyError。"""
    _check_scenarios(config_dir)
    _check_manifest(config_dir)
    _check_block_library(config_dir)
```

#### 校验 ① — scenarios.yaml

读 `scenarios.yaml`，遍历每个工况的 `inputs` 字典，每个 key 必须在 `variables.yaml` OR `opc_mapping.yaml` 中存在。失败示例：

```
ConfigInconsistencyError: scenarios.yaml: scenario 'full_load' references unknown variable 'coal_flow'
```

#### 校验 ② — _manifest.json vs 磁盘

- 清单中 page_id 列出的 `<page_id>.json` 必须存在；缺失 → 报错。
- `config/models/*.json` 中文件名（去 `.json` 后缀）不在清单里 → warning（不阻塞）。

#### 校验 ③ — block_library.yaml vs Python 实现

- 实施前先检查 `block_defs.py` 是否已暴露 block 类型注册表；若已有同等结构（命名可能不同），复用；若没有则本次新增 `BLOCK_REGISTRY: dict[str, type[Block]]`。
- YAML 里每个 block 的 `type` 字段必须在注册表中存在。
- Python 注册了 YAML 没有的 → warning（不阻塞，方便 Python 端先行开发）。

`app.py` 启动钩子：

```python
if not os.environ.get("LEDCS_SKIP_STARTUP_CHECK"):
    try:
        run_all_checks(Path("config"))
    except ConfigInconsistencyError as e:
        print(f"[STARTUP CHECK FAILED] {e}", file=sys.stderr)
        sys.exit(1)
```

### 3.3 bare except 全面修补

不做无差别"全部改写"，只改"吞错"的：

- 全局搜 `except Exception:`、`except:`，逐处判断：
  - 无日志、无 raise、无返回 → 改为 `except Exception as e: logger.exception(...); raise`（或合适的回退 + return error）。
  - 已有 `raise` / `return error_json` 的保留。
- API 路由统一捕获 `ConfigInconsistencyError` / `AlgebraicLoopError` / `RefMissingError` / `RefDuplicateError` 返回 400 + 详细 message。

### 3.4 配置文件落地

新增 `config/sim_settings.yaml`（最小字段集，按需扩展）：

```yaml
step_dt: 0.2          # 仿真步长 s

opc:
  server_url: "opc.tcp://localhost:9440"
  reconnect_max_failures: 5
  reconnect_backoff: [1, 2, 4, 8, 16]
  timestamp_stale_threshold_steps: 2
```

`engine.py` 加载时如果缺失 `sim_settings.yaml`，使用 hard-coded 默认（防止首次升级即崩）。CLAUDE.md 同步更新该文件状态。

`config/model_params.yaml` 本次不创建——无明确需求，避免空配置。

---

## 4. 前端层（src/web/static/js/canvas-engine.js）

### 4.1 复制粘贴 ref 重命名（#6）

定位 paste 处理函数（关键字搜索：`_copy` 后缀添加逻辑，或 `_pasteHandler` / `paste`）：

- 现状：对 `output` / `ref_out` / `ref_in` 都加 `_copy` 后缀。
- 改为：仅当 `node.name === 'output' || node.name === 'ref_out'` 时改 tag；`ref_in` 跳过。
- 粘贴完成后扫描所有新粘贴的 ref_in，若引用的 ref_out tag 在画布所有页面中都不存在，`console.warn` 提示（不阻塞）。

### 4.2 方向键作用域（#7）

定位方向键处理函数（关键字：`ArrowLeft` / `ArrowRight` / `arrow` keydown）：

```js
_arrowHandler(e) {
    const t = e.target;
    if (
        t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' ||
        t.isContentEditable ||
        t.closest?.('.drawflow-node-properties, .scenario-editor, [data-edit-mode="true"]')
    ) return;
    // ... existing move logic
}
```

监听绑到 `document` 而非 canvas 容器，覆盖所有焦点态。

### 4.3 侧边面板监听器累积（#8）

CanvasEngine 实例新增三个具名 handler 引用：

```js
class CanvasEngine {
    constructor() {
        this._panelKeyHandler = this._onPanelKey.bind(this);
        this._pasteHandler = this._onPaste.bind(this);
        this._arrowHandler = this._onArrow.bind(this);
    }
    
    openPanel() {
        document.removeEventListener('keydown', this._panelKeyHandler);  // 防双绑
        document.addEventListener('keydown', this._panelKeyHandler);
    }
    
    closePanel() {
        document.removeEventListener('keydown', this._panelKeyHandler);
    }
}
```

paste / arrow 监听器同样用 remove-then-add 模式。

### 4.4 在线状态指示

Run 页面状态条增加一个 dot：

| `engine.opc_status` | 颜色 | 文案 |
|---|---|---|
| `online`   | 绿 | 在线 |
| `degraded` | 黄 | 重连中（保持上次值） |
| `failed`   | 红 | 连接失败，仿真已停止 |
| `offline`  | 灰 | 未连接 |

不引入新组件，复用现有状态条 DOM。

---

## 5. 测试与验证

### 5.1 单测（最少必要）

`tests/` 下新增：

| 文件 | 覆盖 |
|---|---|
| `test_algebraic_loop.py` | 纯加法器环 → `AlgebraicLoopError`；环加 DELAY → 通过 |
| `test_ref_validation.py` | 跨页 ref 配对正常 → 通过；缺失 / 重复 → 对应异常 |
| `test_variable_sync_dryrun.py` | dry-run 返回 added/updated/orphaned 内容正确，且不写盘 |
| `test_startup_check.py` | scenarios.yaml 引用不存在变量 → `ConfigInconsistencyError` |

### 5.2 在线 NTVDPU 验证脚本

`tools/verify_online_loop.py`：

- 连接 `opc.tcp://localhost:9440`。
- 加载 minimal 画布跑 60s。
- 用户提示"现在请断网 5s"，倒计时；脚本记录 `engine.opc_status` 时间序列。
- 判定标准：检测到 `online → degraded → online`，且无错误数据写入。

### 5.3 手动验证清单

| # | 场景 | 预期 |
|---|---|---|
| 1 | 复制带 ref_in 的子图 → 粘贴 | ref_in 仍指向原 ref_out tag |
| 2 | 节点参数输入框聚焦后按 ←→ | 输入框内光标移动；节点不动 |
| 3 | 反复开关侧边面板 10 次后按 Esc | 仅触发一次关闭事件 |
| 4 | 在线模式 60s + 拔网 5s + 恢复 | 状态条切换 green→yellow→green，仿真不崩 |

### 5.4 回归边界

不动以下，控制 blast radius：

- `block_library.yaml` 与 `block_defs.py` 已注册的功能块行为
- 现有画布 JSON 兼容（除非画布里就有纯代数环——这种本来就是错的，符合"严格"决策）
- 现有 API URL 路径

---

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 现有画布有纯代数环导致升级后启动失败 | 错误消息明确指出环上节点；用户在哪条边插 DELAY 可定位到位 |
| `LEDCS_SKIP_STARTUP_CHECK=1` 被滥用 | 仅作为应急出口；CLAUDE.md 文档化"不应在生产/正式验证时使用" |
| 后台 OPC loop 关闭时机错误导致 Flask 进程不退 | `OPCRuntime.shutdown()` 用 daemon thread + `join(timeout=2)`；进程退出时 daemon 会强制终止 |
| 前端 ref_in 不改名导致用户克隆子图时引用关系不符合期望 | 已与用户确认这是有意为之的语义；console.warn 提示孤儿 ref_in |

回滚：本次单分支单 PR，回滚即 `git revert` 整个合并提交。
