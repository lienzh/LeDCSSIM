# CLAUDE-ref.md — 架构决策档案与资产处置详表

> **权威关系**:本文是历史讨论档案,只展开 `CLAUDE.md` 引用但未详写的两块内容 ——
> 1. 详细资产处置表(每个文件的去留)
> 2. 决策记录(为什么放弃画布、为什么 MVP 不做某些功能)
>
> **日常工作以 `CLAUDE.md` 为准**(它是每次对话自动加载的指令源)。
> 本文未来会随阶段 B 完成被并入 CLAUDE.md 后删除。
> 内容若与 CLAUDE.md 冲突,以 CLAUDE.md 为准。

## 1. 决策记录

### 1.1 为什么放弃画布(Drawflow 组态)

历史上项目走过"Drawflow 画布 + JSON 序列化"路线。2026-05-30 决定弃用,原因:

1. **投入产出比低** — 复制粘贴、跨页 ref、多选、变量管理 UI、工况预设 UI 等大量代码,本质上只是为了让用户拖出和 SAMA 图等价的连接关系,而 YAML 几行就描述完了。
2. **调试困难** — 画布 JSON 是序列化结构,出问题难以 grep / diff / code-review;YAML 是文本,git diff 一目了然。
3. **可移植性差** — 画布带着自身坐标、缩放、节点 ID、UI 状态,跨项目复用要拖一堆和工程无关的东西。
4. **真正可视化的工作在 CCMStudio 那边已做** — 热控工程师看 SAMA 图思考时用 DCS 厂家的工具,本框架不应该重复。

→ **决策**:纯代码 Block + YAML 配置,工程师直接写 YAML,**不提供任何画布编辑/查看 UI**。
→ **未来选项**:如果哪天真要做查看器,也只做"从 YAML 单向渲染"(看图),**不做双向编辑**。

### 1.2 MVP 范围 — 为什么这么克制

2026-05-30 用户明确 MVP 只做两件事:**阀门指令回写** + **一阶惯性流量模拟**。下列功能虽然成熟仿真系统(APROS / DYNSIM / OTS 等)普遍都有,但 MVP 阶段不做:

| 功能 | 为什么不做 |
|---|---|
| 故障注入(Malfunction) | 用户后续规划,暂不需要 |
| 运行控制(暂停 / 单步 / 加速 / 回带) | OTS 卖点,DCS 逻辑验证不必需 |
| 工况快照(IC / snapshot) | YAGNI,等真需要保存现场再加 |
| scenarios 命名工况库 | MVP 只有一组工况就够,扩到多工况再说 |
| DAE 隐式求解器(SUNDIALS / IDA / BDF) | 显式 Euler + 100ms 步长对"逻辑验证 + 简单一阶系统"够用 |
| 多变量复杂耦合(汽水循环、燃烧、热平衡) | 用户自己写,不由本框架负责 |
| 工程层组装(电厂级别整套配置) | 用户自己处理 |
| 自动化测试框架 | 暂未需要 |
| FMI/FMU 协同仿真标准 | YAGNI |
| 工艺组件库(泵 / 阀 / 换热器 / 汽包等带物理方程) | MVP 只要数学块(FirstOrder / DirectThrough);工艺组件按需补 |

新会话里如果有需求点名其中任何一项,再回来补设计。

## 2. 资产处置详表

按"保留迁移 / 改造重写 / 删除"三类标注当前仓库内容的目标态。

### 2.1 保留迁移(改名/移位即可,核心逻辑沿用)

| 当前位置 | 目标位置 | 说明 |
|---|---|---|
| `src/blocks/*.py` | `src/models/*.py` | 接口签名从 `calc(input, dt)` 改为 `step(inputs: dict, dt) -> dict`;加 `reset(state=None)` 方法。MVP 只需要 `DirectThrough` + `FirstOrder` 两个,其余按需迁 |
| `src/opc_client/client.py` | `src/adapter/opc_ua.py` | 已封装批量读写、AI 通道 HR/LR 写入,基本可直接用;只去掉与画布配合的辅助函数 |
| `src/opc_client/mapping.py` | 拆 → `src/engine/tagmap.py` | 提取"YAML 加载 + 变量名 ↔ OPC 节点"逻辑,补量程换算 |
| `src/sim_engine/recorder.py` | `src/engine/recorder.py` | 现有实现已经够用,直接搬 |
| `config/opc_mapping.yaml` | `config/tagmap.yaml` | 字段补齐 `range_low / range_high / physical_unit / dtype`(MVP 阶段够用) |
| `config/sim_settings.yaml` | `config/sim_settings.yaml` | 保留,字段按 MVP 需要精简 |

### 2.2 改造重写

| 当前 | 目标 | 说明 |
|---|---|---|
| `src/sim_engine/engine.py`(画布驱动) | `src/engine/runner.py`(全新) | 从 `models.yaml + connections.yaml + tagmap.yaml` 加载;主循环严格读-算-写三段式;不再引用 GraphRunner |

### 2.3 删除(MVP 不需要,且不影响新架构)

| 路径 | 原因 |
|---|---|
| `src/web/`(全部) | 画布 UI 整体弃用(Flask app、canvas-engine.js、所有 templates、block_defs.py) |
| `src/sim_engine/graph_runner.py` | 只服务画布 JSON,无保留价值 |
| `src/sim_engine/pairing_runner.py` | 为画布配对服务;MVP 直接手写 connections.yaml |
| `src/sim_engine/io_pairing_gen.py` | 同上 |
| `src/sim_engine/ccs_model.py / demo_model.py / model.py` | 硬编码模型示例,不再需要 |
| `config/models/*.json`(Drawflow 序列化) | 画布组态;MVP 不做转换,直接手写新 YAML |
| `config/models/_manifest.json` | 画布多页清单 |
| `config/block_library.yaml` | 画布功能块面板定义 |
| `config/variables.yaml` | 画布同步产物,被 tagmap 替代 |
| `config/scenarios.yaml` | MVP 不做命名工况库 |
| `config/io_pairing_draft.yaml` / `config/io_pairing.generated.yaml` | 画布配对工具产物 |

## 3. MVP 阶段验收标准

阶段 A(MVP 跑通)的验收条件:

1. `py -3.12 -m src.cli run --duration 10` 离线可跑,Recorder 出 CSV,FirstOrder 的阶跃响应曲线符合 `τ` 参数(用 pandas 看一下时间常数对不对)。
2. `py -3.12 -m src.cli run --online --duration 10` 连 NTVDPU 可跑,在 DCS 侧改一个阀门指令值,本框架读到 → 回写;能在 DCS 趋势上看到反馈跟上来。
3. `config/models.yaml + connections.yaml + tagmap.yaml` 三个文件**手写**就能描述上述场景,不超过 30 行。
4. `src/engine/runner.py` 总代码量 < 300 行。

阶段 B(清理画布)的验收条件:
- 第 2.3 表所列文件全部删除
- 仓库 `grep -r "Drawflow\|canvas\|drawflow"` 无残留
- `CLAUDE-ref.md` 合并进 `CLAUDE.md`,本文档删除
