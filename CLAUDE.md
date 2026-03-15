# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 开发原则（必须遵守）

1. **最小实现优先**：只实现用户明确要求的功能，不要添加未要求的 UI 控件、额外功能或"锦上添花"。如果觉得某功能有用但未被要求，记在注释里而不是实现它。
2. **复杂功能先出方案**：涉及多文件修改或新架构时，先列出要改的文件、接口和数据流，用户确认后再写代码。
3. **每步验证**：服务启动后必须 curl 确认端口可达；写完代码后验证基本功能；发现问题定位根因而不是盲试。
4. **地址/路径归一化**：处理 OPC 地址、信号映射时，必须统一格式（去 `ns=0;s=`、`s=` 前缀和 DPU 名前缀，大写比较），防止重复和匹配失败。
5. **Web UI 端口固定 5001**：`py -3.12 -m src.web.app` 默认端口 5001，不要用 5000。

## 项目概述

DCS协调控制（CCS）逻辑仿真验证平台。Python仿真模型通过OPC UA与科远NT6000虚拟控制器（NTVDPU）闭环通信，自动化验证控制逻辑的可靠性。

**闭环流程**：模型算出工艺参数 → OPC UA写入NTVDPU → 控制逻辑产生控制指令 → OPC UA读回 → 模型算下一步 → 循环

详细项目规划和阶段计划见 `PROJECT_PLAN.md`。

## 环境

- **Python**: 3.12（3.14 不兼容 asyncua，不要使用）
- **启动命令**: 使用 `py -3.12` 运行所有 Python 脚本
- **依赖**: `pip install asyncua pyyaml matplotlib pandas flask`
- **OPC UA Server**: `opc.tcp://localhost:9440`（科远 NTVDPU，端口 9440）
- **DPU 节点**: `DPU3013`

## 常用命令

```bash
# 启动 Web 界面 (端口 5001)
py -3.12 -m src.web.app

# 命令行运行仿真
py -3.12 -m src.sim_engine offline --duration 60
py -3.12 -m src.sim_engine online --duration 60

# 执行测试用例（待开发）
py -3.12 -m src.test_framework.runner test_cases/
```

## 核心架构

```
OPC UA (NTVDPU) ←→ IO层(硬点) ←→ IL层(预处理) ←→ IB层(模型逻辑)
```

**组态分层**（H5000M 规范）：
- **IO**：OPC 硬件信号的直接映射（AI/DI 通道）
- **IL**：信号预处理/后处理桥梁层。输入方向：多个 IO 硬点 → 三取中/滤波 → 抽象模型量；输出方向：模型输出 → 处理 → 写回 IO
- **IB**：模型本体，用户用功能块在画布上组态搭建

**在线 vs 离线**：画布逻辑（IL+IB）相同，区别仅在于 IO 层是否连接 OPC。
- 在线：OPC 读输入 → 图执行 → OPC 写输出
- 离线：用户设定值 → 图执行 → 记录数据

**图执行引擎**：GraphRunner 解析 Drawflow 画布 JSON → 拓扑排序 → 按步执行每个功能块

**页面管理**：IL/IB 层支持多页面，通过 `_manifest.json` 管理页面清单。
- 统一画布模板 `canvas.html`，IL/IB 功能完全一致
- 侧边栏树形展开，可新建/重命名/删除页面
- 跨页变量：`ref_out`(A页) → `ref_in`(B页) 通过 tag 名匹配，支持跳转
- 多页仿真：Run 页面可多选 IB/IL 页面组合运行

**路由结构**：
- `/canvas/<layer>/<page_id>` — 统一画布（IL/IB）
- `/il`, `/ib` — 重定向到该层第一个页面
- `/api/pages` — 页面 CRUD
- `/api/pages/refs` — 扫描所有页面的 ref_out 标签

### 模块说明

- **src/opc_client/** — OPC UA 异步通信（已完成）
  - `client.py`: OPC UA Client 封装。连接重试、批量读写、AI 通道写入（HR/LR 方案）
  - `mapping.py`: 信号映射管理。YAML 加载，变量名 ↔ OPC 节点路径转换
- **src/blocks/** — 仿真功能块库（已完成）
  - `base.py`: 功能块抽象基类，统一接口 `output = block.calc(input, dt)`
  - `basic.py`: Inertia（一阶惯性）、Integrator（积分器）、DeadZone、RateLimiter、Limiter
  - `transfer.py`: LeadLag（超前滞后）、SecondOrder（二阶惯性）
  - `select.py`: HighSelect、LowSelect、Switch（二选一）
  - `function.py`: LinearInterp（折线插值）、Polynomial（多项式）
- **src/sim_engine/** — 仿真循环引擎（已完成）
  - `graph_runner.py`: 图执行引擎。解析 Drawflow JSON → 拓扑排序 → 逐步执行功能块。支持多页加载（`load()` 接受 list）
  - `engine.py`: 固定步长循环，驱动 GraphRunner，在线/离线双模式
  - `model.py`: SimModel 基类（保留，供硬编码模型使用）
  - `recorder.py`: 数据记录器，CSV 导出 + pandas
  - `ccs_model.py`: CCS 被控对象模型（保留作为预设参考）
- **src/web/** — Web 界面（已完成）
  - `app.py`: Flask 应用，页面管理 + 仿真 API + OPC API
  - `templates/canvas.html`: IL/IB 统一画布模板
  - `templates/base.html`: 侧边栏树形页面导航
  - `static/js/canvas-engine.js`: Drawflow 增强引擎，含跨页 ref 选择器
  - 端口 5001，启动命令 `py -3.12 -m src.web.app`
- **src/test_framework/** — 自动化测试（待开发）
- **tools/** — 辅助脚本（待开发）

### 配置文件

- `config/opc_mapping.yaml` — OPC UA 节点映射（模型变量名 ↔ 通道号，含 Server 地址）
- `config/models/_manifest.json` — 页面清单（自动生成，记录页面 id/layer/name/order）
- `config/models/*.json` — 各页面的 Drawflow 画布组态数据
- `config/model_params.yaml` — 仿真模型参数（待创建）
- `config/sim_settings.yaml` — 运行设置（待创建）

## OPC UA 通讯方案（已验证）

### OPC 节点结构

```
DPU3013
├── HW                          # 硬件通道
│   ├── AI010605                # AI 通道（机组功率, 0-990MW）
│   │   ├── PV                  # 过程值（只读，由 HR/LR 决定）
│   │   ├── HR                  # 量程上限（可写, Float）
│   │   └── LR                  # 量程下限（可写, Float）
│   ├── DI030401                # DI 通道
│   │   ├── PV                  # 开关量（只读, Boolean）
│   │   ├── ALM, ACK, ACFG      # 报警相关参数
│   │   ├── SCI, K, B, RSET, EN, SETB, CPV, RPV  # DI 配置参数
│   │   └── ...                 # 均无法间接控制 PV
│   └── ...
├── SH0015, SH0021, ...         # 组态图号（约100个）
│   └── {功能块名}
│       └── PV                  # 功能块输出
└── ...
```

### AI 通道写入方案 ✅

**原理**：NTVDPU 对无卡件 AI 通道内置正弦波信号发生器，PV 在 LR~HR 范围内波动。设置 HR=LR=目标值，PV 即锁定为该值。

**已验证结论**：
- 写入 HR/LR 后约 **1 秒**生效，之后 PV 完全稳定
- 数据类型为 Float（VariantType=10）
- 128 个 AI 通道，HR/LR 需在 CCMStudio 中配置暴露（目前仅 AI010605 已开通，后续可批量开通）
- 单通道读写耗时约 1.6ms，远低于 200ms 步长预算

**代码使用**：
```python
# 通过 OPCClient 写 AI 通道
await client.write_ai_channel("ns=0;s=DPU3013.HW.AI010605", 600.0)
```

### DI 通道写入方案 ❌

**已验证结论**：
- DI 通道 PV 为 Boolean，直接写入不生效
- DI 暴露的所有参数（ALM, ACK, SCI, K, B, RSET, EN, SETB, CPV, RPV）均无法间接控制 PV
- **DI 通道必须在 CCMStudio 组态层做仿真切换（MUX 二选一）**

### 读取注意事项

- PV 读取必须使用 `raise_on_bad_status=False`，因为无卡件通道状态为 `UncertainInitialValue`
- 代码中已封装在 `OPCClient.read_value()` 中

## 编码规范

- **中文注释**：面向热控工程师，代码注释用中文，变量命名用英文但关键变量加中文注释
- **物理量标注单位**：`main_steam_pressure  # 主汽压力, MPa`
- **配置与代码分离**：OPC 节点地址、模型参数、测试工况全部放 config/ 或 test_cases/，不硬编码
- **依赖轻量**：不引入重型框架
- **Python 版本**：必须使用 3.12，不要用 3.14（asyncua 不兼容）

### OPC UA 通信规范

- 使用 opcua-asyncio 异步 API
- 连接失败自动重试（间隔 3s，最多 10 次）
- 批量读写用 `read_values`/`write_values`，不逐个读写
- 读写异常捕获记录日志，单个点异常不中断整个仿真循环
- PV 读取使用 `read_data_value(raise_on_bad_status=False)`

### 功能块规范

- 所有功能块继承 `Block` 基类
- 统一接口：`output = block.calc(input, dt)`
- `reset(value)` 用于初始化工况
- 每个块独立无状态依赖，可自由组合

## DCS 环境关键约束

- **NTVDPU 是黑盒**：exe 形式，不可修改内部，只能通过 CCMStudio 下装组态
- **AI 硬点可通过 HR/LR 间接写入**（已验证）
- **DI 硬点不可写**，必须组态层做仿真切换逻辑（MUX 二选一模块）
- **在线组态**：CCMStudio 支持在线组态，一个控制周期内生效，不需要停控制器
- **精度定位**：目标是逻辑正确性验证（控制方向、联锁动作、模式切换），不苛求绝对数值精度
- **趋势监控**：使用科远 DCS 自带趋势软件，不需要自行开发
