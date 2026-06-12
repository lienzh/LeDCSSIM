# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **架构方向已变更(2026-05-30)**:项目从"Drawflow 画布 + JSON 序列化"路线**转向**"纯代码 Block + YAML 配置"路线。画布相关代码、UI、序列化产物**全部计划删除**,不再投入。详见第 2、6、7 节。
>
> **MVP 范围(当前阶段唯一目标)**:
> 1. **阀门指令回写** — DCS 出阀门指令 → 读到 → 直接当反馈写回(echo)
> 2. **流量模拟** — DCS 出阀门指令 → 过一阶惯性 → 当流量反馈写回
>
> **暂不做**(明确砍掉,不要主动设计/实现):故障注入、运行控制(暂停/单步/加速/回带)、工况快照(IC/snapshot)、scenarios 工况库、DAE 隐式求解器、多变量复杂耦合(用户自己写)、工程层组装(用户自己处理)、测试框架。
>
> **工程经验保护(架构再变也不要删)**:第 8 节(OPC 通讯方案)、第 9 节(DCS 硬约束)、第 10.1 节(OPC 通信规范)、第 4 节"过渡态命令"、第 1 节里"Web UI 端口 5001""地址归一化"这类条款,都是**踩坑得来的事务性经验**,不属于架构描述,**架构演进时不要删,只能补**。
>
> 详细目标架构、资产处置见 `CLAUDE-ref.md`(讨论稿,与本文一致;本文是权威)。

## 1. 开发原则(必须遵守)

1. **最小实现优先**:只实现用户明确要求的功能,不要添加未要求的 UI 控件、额外功能或"锦上添花"。如果觉得某功能有用但未被要求,记在注释里而不是实现它。
2. **复杂功能先出方案**:涉及多文件修改或新架构时,先列出要改的文件、接口和数据流,用户确认后再写代码。
3. **每步验证**:写完代码后验证基本功能(跑 CLI、对比输出);服务/进程启动后必须确认可达;发现问题定位根因而不是盲试。
4. **地址/路径归一化**:处理 OPC 地址、信号映射时,必须统一格式(去 `ns=0;s=`、`s=` 前缀和 DPU 名前缀,大写比较),防止重复和匹配失败。
5. **配置驱动**:实例、参数、连接、点映射全部进 YAML,不写死在代码里。
6. **Web UI 端口固定 5001**(过渡期事务性约束):画布回归对比期间,`py -3.12 -m src.web.app` 默认端口 5001,不要用 5000(5000 在某些 Windows/Hyper-V 环境被系统占用)。
7. **YAGNI**:不为"未来可能换协议/换厂家/做工况库/加运行控制/故障注入"等假想需求预留抽象;真到那天再加。

## 2. 项目概述

DCS 协调控制(CCS)逻辑仿真验证平台。Python 仿真模型通过 OPC UA 与科远 NT6000 虚拟控制器(NTVDPU)闭环通信,自动化验证控制逻辑的可靠性。

**闭环流程**:模型算出工艺参数 → OPC UA 写入 NTVDPU → 控制逻辑产生控制指令 → OPC UA 读回 → 模型算下一步 → 循环。

**核心设计诉求**:
1. **可移植** — 换 DCS 厂家或换项目时,只重做适配层 + tagmap,模型库与引擎不动。
2. **不重复造轮子** — 沿用 Block-Signal-Engine 范式;**不自研图形组态编辑器**(画布路线已否决,见第 6 节)。

## 3. 环境

- **Python**:3.12(3.14 不兼容 `asyncua`,严禁使用)
- **启动命令**:统一用 `py -3.12`
- **依赖**:`pip install asyncua pyyaml pandas`
  - 旧依赖 `flask`、`matplotlib` 随画布层一起弃用,不要在新代码中引入
- **OPC UA Server**:`opc.tcp://localhost:9440`(NTVDPU)
- **DPU 节点**:`DPU3013`

## 4. 常用命令

**目标态(MVP 完成后)**:
```bash
# 离线运行(纯本地,不连 OPC,看模型自洽)
py -3.12 -m src.cli run --duration 60

# 在线运行(连 NTVDPU 闭环)
py -3.12 -m src.cli run --online --duration 60
```

**配套工具**:
```bash
# Web 仪表板 + DSL 脚本编辑器 (端口 5002)
#   - /script 页面: DSL 赋值脚本编辑 + OPC 实时桥接(MVP 主入口)
#   - 编辑器: @ 自动补全(连选 Ctrl+Enter) / 18 个算法块 (RS RS_NOT NOT AND OR
#            ADD SUB MUL DIV POW SQRT ABS MAX MIN LIMIT SEL LAG CHAR) /
#            中间变量 $xxx (LHS/RHS 都可带 (描述) 注释) /
#            中缀运算符 + - * / ^ + 嵌套 / 多目标赋值 /
#            行号 / 语法高亮 / 鼠标悬停看变量实时值 /
#            错误标注 (红波浪+底色+悬停看错因) + 全角字符警告 (橙) /
#            帮助 F1
#   - 工作流 (运行前 3 步): 📤 下载 (磁盘镜像→内存) → 📥 上载 (工程现状→内存,
#                              锚定 LAG/RS 状态) → 🔍 预演 (算一周期看风险) → ▶ 运行
#   - 状态管理: 📤 下载 面板含 💾 保存镜像 / 📤 下载到内存 / 🗑 删除 /
#                ⏮ 重置初值 (清 LAG/$var, 跟踪脚本输入) / 🔥 清空状态 (核弹级)
#   - 实时值面板: 右侧 1Hz 刷新, $var 行带 ✏ 改描述同步整脚本所有出现位置
#   - 工具栏 2 行布局: [加载/保存 | 下载/上载/预演 | 运行/停止]
#                        [诊断/事件/备份/帮助 | 🔧 初始化 ▾ | OPC: 本地/VM ✎]
#   - 状态/诊断: 📜 事件 (时间线: 启停/重连/镜像/清状态/端点切换) /
#                🩺 诊断 (状态/失败统计/日志, 一键复制) / OPC 自动重连 /
#                热重启 / 错误持久面板 errBox (📋 复制按钮)
#   - 配置: OPC 端点切换 [本地]/[VM] (持久化, 顶栏 ✎ 改 VM IP)
py -3.12 -m src.viewer

# 从 YQ3SIM-IO/*.csv 自动勾选 OPC 通讯(按 KKS 配对规则,改前自动备份)
py -3.12 -m tools.mark_opc_communication

# 从配对结果生成 ref 架构 yaml (models.generated.yaml 等)
py -3.12 -m tools.generate_yaml_from_pairs
```

**点表目录约定** (viewer 自动扫描):
- 简化版主点表:`YQ3SIM-IO/SIMPLE/简化/<dpu>_S.csv` (优先)
- 老路径回退:`YQ3SIM-IO/DPU<num>.csv`
- 文件名后缀 `_S`/`-S` 自动剥离为 DPU 名(如 `3013_S` → `DPU3013`)

**DSL 脚本语法概要** (完整说明按 F1 弹帮助):
```
DPU3013.AI010502 = DPU3013.AQ010101           # OPC 直通
DPU3013.AI010502(反馈) = DPU3013.AQ010101(指令) # 节点带信号名注释(可读性,解析忽略)
DPU3013.AI010502 = 50.0                        # 写常数
DPU3013.DI = RS(DPU3013.DQ开, DPU3013.DQ关)    # SR 锁存
$tmp(中间量) = MUL(DPU3013.AQ_tph, 0.2778)     # 中间变量也支持 (描述) 信号名
DPU3013.AI = LIMIT($tmp(中间量), 0, 100)        # 引用中间变量, 同样可带描述
DPU3013.SH0500.PRO21120.IN = 100.0             # SH 组态段 (无 HW. / .PV)
```
函数库:`RS/RS_NOT/NOT/AND/OR/ADD/SUB/MUL/DIV/MAX/MIN/LIMIT/SEL/LAG`

**viewer OPC 端点切换**(顶栏 `OPC: [本地] [VM] ✎`):
- 配置文件:`config/opc_endpoints.yaml`(`.gitignore` 已排除,机器相关)
  ```yaml
  mode: local                          # local | vm  (上次选择)
  local: opc.tcp://127.0.0.1:9440      # NTVDPU 跑在本机
  vm:    opc.tcp://192.168.31.39:9440  # NTVDPU 跑在虚拟机,LAN IP 视实际改
  ```
- 点 [本地] / [VM] → 立即持久化 mode 到 yaml + 触发一次探活,下次点【▶ 运行】用新地址
- 点 ✎ → 弹 prompt 改 VM URL(IP 变了不用编辑器,直接 UI 改)
- **运行中不允许切换**,会提示先点 [■ 停止]
- yaml 不存在时,首次启动 viewer 会写入默认 `mode=local`
- API:`GET/POST /api/opc/endpoint`(切换),`GET/POST /api/opc/probe`(探活)
- **唯一真相源**:所有组件(viewer / `src/cli` / `tools/verify_opc`)都从 `opc_endpoints.yaml` 读端点,改一处全局生效;CLI 还可 `--opc-url` 显式覆盖
- **顶栏 🟢/🔴 实时状态点**:不点【▶ 运行】也能看到当前端点是否可达
  - 后台 5s 一次 OPC UA HELLO/ACK 协议级探活(不开 session,~10ms)
  - 🟢 + 延迟 ms = 端口可达且对端是 OPC Server
  - 🔴 + 错因 = 不通(超时 / 拒绝 / 非 OPC 协议响应)
  - 点状态点 → 立即重新探一次
  - **为啥不用纯 TCP**:Tailscale / Meta(198.18/15)等代理会劫持任意 IP 的 TCP connect 让握手成功,必须看应用层 HELLO/ACK 才能戳穿假成功

**viewer 运行前工作流**(📤 下载 / 📥 上载 / 🔍 预演):

| 按钮 | 方向 | 用途 | 何时用 |
|---|---|---|---|
| **📤 下载** | 磁盘镜像 → 本项目内存 → (运行后) → 工程 | 把保存的算法状态还原 | 本项目重启 / NTVDPU 重启后 |
| **📥 上载** | 工程 → 本项目内存 | 锚定 LAG/RS state 到工程现状,第 1 周期写出 = 工程现状,无扰起步 | 工程状态变了 (VM 镜像还原 / CCMStudio 重下组态 / DCS 端被人改) |
| **🔍 预演** | (无写,仅读+算) | 干运行一周期, 显示每个 OPC LHS 的 (算出 vs DCS 现状 vs 差值 vs 风险) | 上载后, 运行前, 验证脚本逻辑跟工程现状是否一致 |

**上载的 4 步流程**(`runtime.py:reinit_lag_from_dcs`):
1. 检查 OPC 状态(单独探活,~10ms)
2. 读 DCS 当前值 — 所有 LHS (LAG/RS 用于锚定) + 所有 RHS 引用的 OPC 节点 (用于面板"读取"列显示)
3. 无扰跟踪同步:
   - LAG → `lag_state[key] = DCS 值`(不跨 `$var` 边界,避免温度值塞给煤量 LAG)
   - RS → `Q = DCS 值`
   - RS_NOT → `Q = NOT DCS 值`(反算)
   - `last_written = {}`(强制下周期重写)
   - 读到的 DCS 值灌进 `last_read`(面板"读取"列立刻反映)
4. 待用户点【▶ 运行】

**LAG 跟踪初始化**(`_eval_rhs` 默认行为):
- `y_prev = s.lag_state.get(key, x)` — 首次评估默认 `y_prev = 当前输入 x`
- 全新启动 LAG 立即稳态(不再从 0 爬升 4-5τ)
- 已锚定的 LAG(上载后 / 镜像下载后)用锚定值

**当前过渡态(画布代码尚未删除,仅用于回归对比)**:
```bash
# 旧画布 Web,即将弃用 — 不要再加功能(默认端口 5001)
py -3.12 -m src.web.app

# 旧引擎 CLI,新 src.cli 跑通前可继续用作回归基线
py -3.12 -m src.sim_engine offline --duration 60
py -3.12 -m src.sim_engine online --duration 60
```

## 5. 目标架构(4 层)

```
配置层(Config, 数据驱动)
  models.yaml       — 实例化哪些块、各自参数
  connections.yaml  — 块间信号连接
  tagmap.yaml       — 仿真变量 ↔ OPC 节点 + 量程/单位换算
  sim_settings.yaml — 步长、OPC 连接参数、记录器配置
        │
        ▼
引擎层(Engine)
  - 加载配置 → 实例化 Block → 拓扑排序
  - 固定周期主循环:批量读 OPC → step 所有块 → 批量写 OPC
  - 数据写 CSV(Recorder 是 engine 内的小模块,不独立成层)
        │
   ┌────┴────┐
   ▼         ▼
模型库(Lib) 适配层(Adapter)
原子模型类   封装 asyncua;对上暴露与协议无关的批量读写接口
```

**可移植性来源**:适配层 + tagmap。**复用性来源**:模型库。

**仿真逻辑分两类**(对应 MVP 两个目标):
1. **直通(DirectThrough)**:阀门指令读到 → 直接当反馈写回。对应 Block:`DirectThrough`。
2. **建模(FirstOrder)**:阀门指令 → 一阶惯性 → 当流量反馈写回。对应 Block:`FirstOrder`。

新增第三种(PID/限幅/选择)只是再加一个 Block 类,引擎和配置不改。

### 5.1 目录结构(目标态)

```
src/
  adapter/          # 协议适配层
    base.py         # 协议无关接口(read_batch / write_batch)
    opc_ua.py       # asyncua 实现(由 src/opc_client/client.py 迁移)
  models/           # 原子模型库(由 src/blocks/ 迁移并改接口)
    base.py         # Block 基类:step(inputs, dt) -> dict / reset(state=None)
    basic.py        # MVP 必需:DirectThrough, FirstOrder。后续按需加 PID/Limiter/选择等
  engine/           # 仿真引擎
    runner.py       # 加载 YAML → 实例化 → 拓扑 → 主循环(读-算-写三段)
    tagmap.py       # 加载 tagmap.yaml + 物理量/工程量换算(MVP 阶段就一个薄文件)
    recorder.py     # 数据记录器 → CSV(沿用 src/sim_engine/recorder.py)
  cli/
    main.py         # `py -3.12 -m src.cli run [--online] --duration N`
  viewer/           # 只读 Web 仪表板(端口 5002)
    app.py          # Flask 单页 — 展示 blocks/connections/tagmap + 最新 CSV
    __main__.py     # `py -3.12 -m src.viewer` 启动

tools/              # 工程辅助脚本(独立可跑,与核心代码解耦)
  mark_opc_communication.py    # YQ3SIM-IO/*.csv 批量勾选 OPC 通讯
  generate_yaml_from_pairs.py  # 配对结果 → ref 架构 yaml
                    # 后续按需加: OPC 节点批量扫描、AI 通道开通辅助、变量名归一化检查

config/
  models.yaml  connections.yaml  tagmap.yaml  sim_settings.yaml

tests/  docs/
```

> **注**:`tagmap` 放在 `src/engine/` 内而非独立 `src/mapping/` 目录 — MVP 阶段它就是个"读 YAML + 做量程换算"的薄文件,独立成层属于过度抽象。未来如果换算逻辑复杂到需要独立模块再升格。

### 5.2 各层职责要点

- **Adapter**:只此一层出现 `asyncua` / `ns=…;s=…`。批量读写,禁止逐点同步。PV 读取用 `read_data_value(raise_on_bad_status=False)`(NTVDPU 无卡件状态是 `UncertainInitialValue`)。
- **Models**:统一接口 `step(inputs: dict, dt: float) -> dict` + `reset(state=None)`。新增模型 = 加一个类,引擎和配置机制不改。
  - **MVP 必需块**(两个):`DirectThrough`(直通,支持可配置一阶滞后)、`FirstOrder`(一阶惯性)。
  - **常用辅助块**(按需实现,不超前):
    - `CON`(常数):0 输入 1 输出,提供初值或固定信号源
    - `DELAY`(一拍延迟):1 输入 1 输出,输出延迟一个仿真周期 — **打破纯代数环的唯一工程手段**,引擎检测到代数环时用户应该插这个
  - **不再需要的画布专用块**:`comment`(文本注释)— 仅画布渲染用途,新架构里 YAML 注释即可
- **Engine**:
  - 加载 4 个 YAML → 实例化 Block → 拓扑排序(检测到纯代数环则报错退出,提示用户在环路中插入 `DELAY` 块或重组连接)
  - 主循环严格**读-算-写三段式**:`adapter.read_batch(in_tags) → for block in topo_order: block.step() → adapter.write_batch(out_tags)`
  - 离线模式跳过 read/write,只 step 模型 + 写 CSV,用于本地自洽测试
- **tagmap**:单条目字段 MVP 够用即可:`tag / opc_node / direction(in|out) / dtype / range_low / range_high`。可选:`redundant_nodes`(冗余通道列表,三取中/二取一时用)、`scale`(非线性换算)。物理量/工程量换算在本层做,模型层只见物理量。
  - OPC Server 地址(`opc.tcp://localhost:9440`)和 DPU 节点(`DPU3013`)放在 `sim_settings.yaml`,不在 tagmap 重复。
- **Recorder**:每步追加被关注 tag 的值,落 CSV。MVP 不需要 Parquet/滚动文件/降频。

## 6. 为什么放弃画布(已决策,不要建议恢复)

历史上项目走过"Drawflow + JSON"可视化组态路线,现已弃用。原因:

1. **投入产出比低** — 复制粘贴、跨页 ref、多选、变量管理 UI 等大量代码,仅是为了拖出 SAMA 等价连接关系,YAML 几行就描述完。
2. **调试困难** — 画布 JSON 难 grep/diff/code-review;YAML 文本一目了然。
3. **可移植性差** — 画布带坐标、UI 状态,跨项目复用要拖一堆和工程无关的东西。
4. **真正的可视化看图工作在 CCMStudio 那边已做**,本框架不重复。

→ **不要建议引入任何形式的画布、组态编辑器、画布查看器、SAMA 渲染器**,除非用户在新会话中明确要求重新讨论这个决策。

## 7. 过渡期约束(关键 — 当前代码现状)

代码库当前**仍是画布架构**,新架构(第 5 节)尚未实现。`src/web/`、`src/sim_engine/graph_runner.py`、`config/models/*.json` 等画布资产**都存在但计划删除**。

**禁止区**(下列代码上不要添加新功能、不要修复非阻塞 bug):
- `src/web/`(全部 — Flask 应用、画布模板、canvas-engine.js、block_defs.py)
- `src/sim_engine/graph_runner.py`(只服务画布 JSON)
- `src/sim_engine/engine.py`(画布驱动版,将被 `src/engine/runner.py` 取代)
- `src/sim_engine/pairing_runner.py`(画布版的配对运行器;新架构用 `src/engine/runner.py` 跑生成的 yaml,不要它)
- `src/sim_engine/ccs_model.py / demo_model.py / model.py`(硬编码示例)
- `config/models/*.json`(Drawflow 序列化)
- `config/models/_manifest.json`
- `config/block_library.yaml`(画布面板定义)
- `config/variables.yaml`(画布同步产物,被 tagmap 替代)
- `config/scenarios.yaml` / `config/io_pairing*.yaml`(scenarios 暂不做;io_pairing 是画布配对工具的旧产物)

**已升格为正式工具(非禁止区,可继续维护)**:
- `src/sim_engine/io_pairing_gen.py` — KKS 配对算法(`pair_analog` AQ↔AI、`pair_digital` DQ↔DI)。`tools/mark_opc_communication.py` 和 `tools/generate_yaml_from_pairs.py` 都依赖它。后续如需扩展(放宽配对规则、加新信号位)在这里改。

**新功能/修改应去的地方**:
| 类别 | 位置 |
|---|---|
| OPC 通信改动 | `src/opc_client/client.py` 现状可用;迁移期间直接复用 |
| 新增模型块 | 直接写在 `src/blocks/` 也行,但**必须用新接口 `step(inputs:dict, dt) -> dict`**,不要再用 `calc(input, dt) -> output` |
| 配置 schema 改动 | 直接按目标态写 `config/models.yaml / connections.yaml / tagmap.yaml`,不要再扩展 `config/models/*.json` |
| 仿真主循环 | 直接新建 `src/engine/runner.py`,不要修改 `src/sim_engine/engine.py` |

**资产对照**(详见 CLAUDE-ref.md 第 9 节):
- **保留迁移**:`src/blocks/`(改接口后迁 `src/models/`)、`src/opc_client/client.py`(迁 `src/adapter/opc_ua.py`)、`src/sim_engine/recorder.py`(迁 `src/engine/recorder.py`)、`config/opc_mapping.yaml`(改写为 `config/tagmap.yaml`)、`config/sim_settings.yaml`
- **删除**:`src/web/`、`graph_runner.py`、`engine.py`(旧)、`pairing_runner.py`、`io_pairing_gen.py`、`config/models/*.json`、`config/variables.yaml`、`config/scenarios.yaml`、`config/block_library.yaml`、`config/io_pairing*.yaml`、画布相关一切

**迁移分两阶段**(简化为 MVP 节奏):
- **阶段 A:新写跑通 MVP** — 在 `src/adapter/ engine/ models/ cli/` 下新写最小骨架,实现 `DirectThrough + FirstOrder` 两个 Block,跑通"阀门指令回写 + 一阶惯性流量"闭环。旧画布代码不动。
- **阶段 B:清理画布** — 阶段 A 验收通过后,删除上面"删除"列表里的所有内容,合并 CLAUDE-ref.md 进 CLAUDE.md。

## 8. OPC UA 通讯方案(已验证,DCS 硬约束)

### 8.1 OPC 节点结构

```
DPU3013
├── HW                          # 硬件通道
│   ├── AI010605                # AI 通道(机组功率, 0-990MW)
│   │   ├── PV                  # 过程值(可直接写,前提:CCMStudio 已暴露)
│   │   ├── HR                  # 量程上限(也可写, Float)
│   │   └── LR                  # 量程下限(也可写, Float)
│   ├── DI030401                # DI 通道
│   │   ├── PV                  # 开关量(可直接写, Boolean)
│   │   ├── ALM, ACK, ACFG      # 报警相关
│   │   └── SCI, K, B, ...      # 配置参数(一般不动)
│   └── ...
├── SH0015, SH0021, ...         # 组态图号(约 100 个)
│   └── {功能块名}
│       └── IN / OUT / PV       # 块端子(IN 一般被组态上游驱动, 写无效)
└── ...
```

### 8.2 AI 通道写入 ✅(可直接写 PV)

**结论**(2026-06-02 实测验证):
- 直接写 `AI.PV` 即生效(NTVDPU 端 CCMStudio 暴露后)
- **NTVDPU 内部刷新有 ~1 秒延迟** — 写后立即读还是旧值,1 秒后才稳定为新值
- 数据类型 Float(VariantType=10);Boolean / Int 会被 `OPCClient.write_value` 自动适配
- 早期的 HR/LR 双写方案(`write_ai_channel`)在某些场景下仍可用作锁定,但**不是必须**

```python
# 直接写 PV (推荐)
await client.write_value("ns=0;s=DPU3013.HW.AI010605.PV", 600.0)
```

### 8.3 DI 通道写入 ✅(可直接写 PV)

**结论**(2026-06-02 实测验证,推翻早期"DI 不可写"结论):
- 直接写 `DI.PV` 即生效,Boolean 类型
- 跟 AI 一样有 **~1 秒**生效延迟
- 写后立即读 → 仍是旧值;1 秒后稳定为新值
- 之前误判"DI 不可写"的原因:对比逻辑没考虑延迟 → viewer 已修正为"持续 ≥ 5 周期(1 秒)不一致才报未生效"

```python
# 直接写 DI (Boolean)
await client.write_value("ns=0;s=DPU3044.HW.DI010204.PV", True)
```

**踩坑事务性经验**(2026-06-10):部分 DI 点 **read 返回 Boolean 但 write 只接受 Float**,直写 Boolean 报 `BadTypeMismatch`。已知实例:`DPU3005.HW.DI010402.PV`。`OPCClient.write_value` 已加 fallback:Boolean 失败时自动用 Float 重试 + 节点加入 `_FORCE_FLOAT_NODES` 缓存,下次直接走 Float 跳过 Boolean。**架构再变也保留这个 fallback** — NTVDPU 端的 read/write attribute schema 不一致是已知怪行为,不是我们的 bug。

### 8.3a SH 段(组态块端子)写入 ⚠️

- `DPU.SH0xxx.<块名>.IN` / `.OUT` / `.PV` 是组态块端子,不是硬件通道
- `.IN` **通常被组态上游驱动** — 写入会被下一个扫描周期覆盖,且**当前 NTVDPU 不让外部读**(返回 None)→ viewer 会以 ⛔ "被跳过的赋值" 报出
- 解决:CCMStudio 端把 `.IN` 上游断线(成为外部可驱动),或改用 `.OUT` 端子,或改写仿真量软点

### 8.4 读取注意事项

- PV 读取必须 `raise_on_bad_status=False`(无卡件通道状态为 `UncertainInitialValue`)
- 已封装在 `OPCClient.read_value()` 中
- **写入有 ~1 秒生效延迟**:写后立即读还是旧值。viewer 的"写后未生效"判定已加 5 周期(1 秒)宽限期,避免误报。SourceTimestamp 校验可选(实测延迟稳定在 1 秒左右)

### 8.5 连接建立与重试

- 启动时:`asyncua.Client(url=...)` → `await client.connect()`,连接失败按第 10.1 节规则重试
- 连接对象长期持有,**不要每步都重连**
- 优雅退出:`await client.disconnect()`(否则 NTVDPU 端 session 会残留,影响下次连接)

### 8.6 NTVDPU 跑在虚拟机里(Bridged / NAT 都可)

viewer 顶栏支持 `[本地] / [VM]` 切换(见第 4 节"viewer OPC 端点切换")。VM 跑 NTVDPU 时网络模式两种都能通,各有取舍:

| 模式 | VM IP 段 | 宿主机连得到 | LAN 上其他机器连得到 | 备注 |
|---|---|---|---|---|
| **Bridged**(推荐) | 跟宿主机同 LAN 段(如 `192.168.31.x`) | ✅ | ✅ | VMware: VM Settings → Network Adapter → "Bridged"。注意要在 VM 个体设置里改,不只是 Virtual Network Editor |
| **NAT** | VMware 内部 `192.168.135.x`(VMnet8) | ✅(经 VMnet8 虚拟网卡) | ❌ 除非配端口转发 | 宿主机 `ipconfig` 看 VMware Network Adapter VMnet8 = `192.168.135.1` |

**踩坑事务性经验**(架构再变也别删):
- VM 内 `ipconfig` 看 `网关.2 + DHCP.254 + DNS 后缀 localdomain` = VMware NAT 标志,**没真正桥接**;Bridged 的网关/DHCP/DNS 应该是用户真实路由器
- VM 内防火墙默认拦入站 ICMP(`ping` 不通),但 9440 TCP 通就够 OPC 用
- VM 重启 / 切网络后 IP 可能变,在 viewer 顶栏 ✎ 改 yaml 里的 `vm:` URL 即可,不用动代码
- 宿主机 `Test-NetConnection <vm_ip> -Port 9440`,`TcpTestSucceeded = True` 才算通

## 9. DCS 环境关键约束

- **NTVDPU 是黑盒**:exe 形式,不可修改,只能通过 CCMStudio 下装组态
- **在线组态**:CCMStudio 支持在线组态,一个控制周期内生效,不需要停控制器
- **精度定位**:目标是逻辑正确性验证(控制方向、联锁动作、模式切换),不苛求绝对数值精度
- **趋势监控**:使用科远 DCS 自带趋势软件;本框架的 Recorder 只出 CSV 供事后分析,不做实时可视化

## 10. 编码规范

- **中文注释**:面向热控工程师,代码注释用中文,变量名用英文但关键变量加中文注释
- **物理量标注单位**:`main_steam_pressure  # 主汽压力, MPa`
- **配置与代码分离**:OPC 节点、模型参数、工况全部进 `config/`,不硬编码
- **依赖轻量**:不引入重型框架
- **Python 版本**:3.12,严禁 3.14

### 10.1 OPC UA 通信规范

- 使用 `opcua-asyncio` 异步 API(包名 `asyncua`)
- 连接失败自动重试:**间隔 3s,最多 10 次**;10 次后抛错给上层而不是无限重试
- 批量读写用 `read_values` / `write_values`,不逐点同步读写
- 单点异常捕获记录日志,**不中断整个仿真循环**(一个测点坏不能让整个模型停)
- PV 读取必须 `read_data_value(raise_on_bad_status=False)`
- AI / DI 通道直接 `write_value(PV)` 即可(见第 8.2/8.3);老的 `write_ai_channel`(HR/LR 双写)仍可用,非必须
- 在线模式校验 `SourceTimestamp`(见第 8.4)
- OPC 地址在代码内统一格式:**去 `ns=0;s=`、`s=` 前缀和 DPU 名前缀,大写比较**(防重复和匹配失败,见开发原则第 4 条)

### 10.2 功能块规范(目标接口)

- 所有功能块继承 `Block` 基类(`src/models/base.py`)
- 统一接口(MVP 阶段就这两个方法,不要加 snapshot/序列化等):
  ```python
  class Block:
      def __init__(self, name: str, params: dict): ...
      def step(self, inputs: dict, dt: float) -> dict: ...
      def reset(self, state: dict | None = None) -> None: ...
  ```
- 旧接口 `calc(input, dt) -> output` 单值版本**仅过渡期内** `src/blocks/` 还在用;迁到 `src/models/` 时必须改新接口
- 每个块独立无状态依赖,可自由组合

## 11. 给 Claude 的工作指引

- 动手前对照第 5 节分层和第 7 节过渡约束审视改动:它属于哪一层?是否动到禁止区?是否影响可移植性?
- 提改动时,说明它在新架构中的位置(配置/适配/模型/引擎)。
- **不要建议引入图形化组态编辑器、画布查看器、SAMA 渲染器或重型仿真框架**(第 6 节已否决)。如果未来真要做查看器,从 YAML 单向渲染,不做双向编辑。
- **不要主动设计**:故障注入、运行控制(暂停/单步/加速/回带)、工况快照(IC/snapshot)、scenarios 工况库、DAE 隐式求解、测试框架 — 这些都暂不在 MVP 范围内,只有用户明确点名才做。
- 新增模型块时,用新接口 `step(inputs:dict, dt:float) -> dict`,并同步给出 `models.yaml` + `connections.yaml` 配置示例和单元测试。
- 修改 OPC 相关代码时,务必检查第 8 节硬约束(AI HR/LR、DI 不可写、批量读写、`raise_on_bad_status=False`)。
- 涉及 ≥ 2 个文件或新增配置 schema 时,先列改动清单 + 数据流给用户过目,再写代码。
- 看到画布相关代码出现 bug:不要主动修;告诉用户该文件在删除清单里,问是否需要修(可能因为还在跑回归对比所以暂时需要)。
