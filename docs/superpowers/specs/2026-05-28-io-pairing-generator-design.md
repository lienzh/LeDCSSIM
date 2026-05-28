# IO 配对生成器 + 声明式 IL 层设计（2026-05-28）

## 背景与目标

仿真的本质是"把 DPU 输出的指令赋值回 DPU 的输入反馈"。其中 **80% 是简单赋值**（调节门指令→阀位、开/关脉冲→开关状态），手工在画布上拖几千个块是最重、最不值得的工作。

科远 NT6000 的 KKS 设计编号**确定性地编码了设备身份 + 信号角色**，因此这部分可以从点表 CSV **自动生成**，不必手画。本设计实现：

1. **配对生成器**：点表 CSV → 筛选硬件 IO → 设备分组 → 套用模板 → 产出 `io_pairing.yaml` + 例外清单。
2. **声明式 IL 运行器**：引擎直接解释 `io_pairing.yaml` 执行三类模板，产出反馈值；与现有 Drawflow 画布并存。

**简单赋值走声明式表，复杂多变量耦合工艺逻辑留在画布。**

## 已验证的 KKS 配对规律

设备 KKS 形如 `30HNA20AA`（单元2位 + 系统3字母 + 序2位 + 设备类2字母），其后是信号位后缀：

| 类别 | 指令后缀 | 反馈后缀 | 配对主键 |
|---|---|---|---|
| 模拟调节门 | `…1010`(AQ 指令) | `…1019`(AI 阀位) | 设备码 |
| 开关阀门/挡板 | `…1`(开 DQ) `…2`(关 DQ) | `…3`(已开 DI) `…4`(已关 DI) `…5`(故障 DI) | 设备码+子序 `rem[:-1]` |
| 电机/泵/风机(AN/AT/AP/AH) | `…1`(合 DQ) `…2`(跳 DQ) | `…3`(运行 DI) `…4`(停 DI) … | 设备码+子序 |

原型脚本已在 8 个 DPU 上验证：模拟 16 组、阀门挡板 20 组、电机 48 组自动配对成功，413 条无 KKS（小机 DPU3044/3045/3052）待描述配对。产物见 `config/io_pairing_draft.yaml`。

## 硬件 IO 筛选规则

点名 `HW.<2字母><数字>.PV`。判定现场硬件 IO：

- **保留**：KKS 命中设备模式 `\d{2}[A-Z]{3}\d{2}[A-Z]{2}\d+`。
- **剔除软点**：描述含 `备用 / 模件第X通道 / TEST / 设定 / TO CCS / 来自DPU / 至PECCS / 需确认 / 描述文本 / 心跳 / SOC` 等关键词。
- **无 KKS DPU**（小机 3044/3045/3052）：KKS 字段为空，回退到描述关键词配对，全部标 `needs_review`。

IO 方向语义（闭环两端）：

| 码 | 含义 | 方向 | 模型 |
|---|---|---|---|
| AQ | 模拟指令 | DPU 出 | 读 |
| DQ | 开关指令 | DPU 出 | 读 |
| AI | 模拟反馈 | DPU 入 | 写 |
| DI | 开关反馈 | DPU 入 | 写 |
| RT/TC | 温度 | DPU 入 | 写 |

## 架构

```
点表 CSV ──┐
           │  tools/gen_io_pairing.py（生成器，离线一次性）
           ▼
   config/io_pairing.yaml  ←── 人工微调例外
           │
           ▼
   src/sim_engine/pairing_runner.py（声明式 IL 运行器）
           │   每步: 读指令值 → 套模板 → 算反馈值
           ▼
   SimEngine ── 合并 PairingRunner 输出 + GraphRunner(画布)输出 → 写 OPC
```

### 组件 1：配对生成器 `tools/gen_io_pairing.py`

- 输入：`YQ3SIM-IO/*.csv`（GBK 编码）。
- 流程：加载 → 筛选硬件 IO → 按 KKS 设备分组 → 分类（模拟/阀门/电机/无KKS）→ 套模板生成条目。
- 输出：`config/io_pairing.yaml`，结构：
  ```yaml
  analog:
    - dpu: DPU3013
      device: 30HAG21AA
      cmd: HW.AQ010101.PV          # DPU 输出（模型读）
      fb:  HW.AI010502.PV          # DPU 输入（模型写）
      template: analog
      transform: {type: inertia, T: 2.0}   # direct | inertia | scale
      desc: 启动系统暖管出口电动调整门
  rs_actuator:
    - dpu: DPU3016
      device: 30HLA20AA001
      open_cmd: HW.DQ070203.PV
      close_cmd: HW.DQ070204.PV
      opened_fb: HW.DI010609.PV
      closed_fb: HW.DI010610.PV
      fault_fb: HW.DI010611.PV     # 可空；仿真固定 0
      template: rs_actuator
      travel_time: 5.0             # 全行程秒数；0=瞬动
      desc: 送风机出口联络风门
  motor: [...]                     # 同 rs_actuator 结构，语义为 合/跳→运行/停
  needs_review: [...]              # 无 KKS，cmd/desc/code，待人工补全
  ```
- 幂等：重跑覆盖自动段，但保留人工标记的字段（`io_pairing.yaml` 顶部分 `# --- AUTO ---` / `# --- MANUAL ---` 两段，生成器只重写 AUTO 段）。**首版简化**：生成器写 `io_pairing.generated.yaml`，人工 diff 后合并到 `io_pairing.yaml`，避免覆盖逻辑复杂化。

### 组件 2：声明式 IL 运行器 `src/sim_engine/pairing_runner.py`

```python
class PairingRunner:
    def load(self, pairing_yaml_path): ...
    def get_command_tags(self) -> list[str]:   # 需要从 DPU 读的指令点
    def get_feedback_tags(self) -> list[str]:  # 需要写回 DPU 的反馈点
    def step(self, commands: dict, dt: float) -> dict:  # {fb_tag: value}
    def reset(self): ...
```

三类模板每步计算：

- **analog**：`fb = transform(cmd)`
  - `direct`：`fb = cmd`
  - `inertia(T)`：复用现有 `Inertia` 块，一阶惯性逼近（模拟执行机构行程时间）
  - `scale(gain,bias)`：`fb = cmd*gain + bias`（指令域与反馈域不一致时折算）
- **rs_actuator / motor**：复用现有 `FlipFlopRS` 块
  - `RS(S=open_cmd, R=close_cmd)` → `q`
  - `travel_time=0`：`opened_fb=q, closed_fb=not q`
  - `travel_time>0`：用 `TimerOn` 延迟，过渡期 `opened_fb=closed_fb=0`，到时置位（更真实，首版可选）
  - `fault_fb=0`，`local_fb=远方`（固定值）

状态（RS 锁存、惯性中间值）随块实例维护，`reset()` 清零。

### 组件 3：引擎集成

`SimEngine` 同时持有 `GraphRunner`（画布）和 `PairingRunner`（声明表），二者可独立存在：

```python
# 每步
commands = read_inputs(graph.get_input_tags() + pairing.get_command_tags())
canvas_out  = graph.step(commands, dt)        # 复杂逻辑
pairing_out = pairing.step(commands, dt)       # 简单赋值
outputs = {**pairing_out, **canvas_out}        # 画布优先（同 tag 时覆盖）
write_outputs(outputs)
```

- 离线模式：`commands` 来自工况设定，全程内部闭环，**全部模板可验证**。
- 在线模式：`commands` 来自 OPC 读 AQ/DQ；`outputs` 写回 OPC。

## 关键约束（必须知晓）

**DI 不可经 OPC 写回 NTVDPU**（已验证，CLAUDE.md 记载，`engine._write_opc_outputs` 仅写 AI 通道）。因此：

- **模拟量（AI）反馈**：在线可写（HR/LR 方案），受限于 CCMStudio 中已开通的通道（目前仅 AI010605，批量开通属代码范围外）。
- **开关量（DI）反馈**：在线**写不进** NTVDPU，需 CCMStudio 组态层 MUX 二选一（属代码范围外）。
- 因此 `rs_actuator/motor` 模板的在线价值取决于 CCMStudio 侧配合；但**离线逻辑验证完全可用**，且生成器可额外产出"DI 仿真切换需求清单"（`config/di_mux_required.yaml`）供 CCMStudio 侧参考。
- 生成器对每条配对标注 `online_writable: true|false`，引擎在线模式跳过不可写的反馈点并记一次性提示，不报错。

## 测试与验证

- `tests/test_gen_io_pairing.py`：用 DPU3013/3016 子集 CSV，断言模拟/阀门/电机分类数量与字段正确，软点被剔除。
- `tests/test_pairing_runner.py`：
  - analog direct/inertia：阶跃指令，反馈逼近正确。
  - rs_actuator：开脉冲→opened=1/closed=0；关脉冲→反转；travel_time 过渡期双 0。
  - motor：合/跳同理。
- 离线集成：构造含 3 个设备的 `io_pairing.yaml`，引擎离线跑 20 步，断言反馈值符合预期。
- 在线（NTVDPU 可用）：模拟量回路（AI010605 开通通道）跑通 AQ→AI 闭环。

## 回归边界

- 不改 GraphRunner 行为；PairingRunner 是新增并行组件。
- `io_pairing.yaml` 不存在时引擎照常仅跑画布（向后兼容）。
- 不动 OPC 写机制（AI HR/LR），不触碰 CCMStudio 范围。

## 分期

1. **P1（核心价值）**：生成器 + analog 模板 + PairingRunner + 引擎集成 + 离线验证。
2. **P2**：rs_actuator / motor 模板 + travel_time 过渡 + DI 不可写处理 + di_mux_required 清单。
3. **P3**：无 KKS 小机的描述配对辅助 + Web UI 查看/编辑配对表。
