# DCS 协调控制逻辑仿真验证平台 — 项目规划

## 一、项目背景与目标

### 背景
在 DCS 改造工程中，需要对机组协调控制（CCS）逻辑进行仿真验证，确认逻辑修改的可靠性，减少现场调试风险和时间。

### 核心目标
构建一套轻量化的协调控制逻辑仿真验证平台，实现：
1. 仿真模型与 DCS 虚拟控制器的闭环运行
2. 可视化 SAMA 图逻辑组态界面
3. 自动化测试框架，支持批量工况验证和回归测试
4. 可复用的工程模板

### 非目标
- 不做全量仿真机（不覆盖全部辅机和系统）
- 不做操作员培训功能
- 不做硬实时系统（200-500ms 步长足够）

---

## 二、现有技术条件

| 条件 | 状态 | 说明 |
|------|------|------|
| DCS 虚拟控制器 | ✅ 已有 | 科远 NT6000 NTVDPU，exe 运行 |
| OPC UA 通信 | ✅ 已通 | `opc.tcp://localhost:9440`，DPU3013 |
| AI 硬点写入 | ✅ 已解决 | HR=LR=目标值间接控制 PV |
| DI 硬点写入 | ❌ 待解决 | 需组态层做仿真切换逻辑 |
| 闭环验证 | ✅ 已通 | 煤量扰动→功率变化→OPC 读回，全程正常 |

---

## 三、系统架构

```
┌────────────────────────────────────────────┐
│         Web UI (Flask, 端口 5001)           │
│                                            │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌───────┐  │
│  │ IO层 │  │ IL层 │  │ IB层 │  │ L3层  │  │
│  │通道配│  │预处理│  │逻辑组│  │封装库 │  │
│  │  置  │  │      │  │态+运 │  │       │  │
│  │      │  │      │  │  行  │  │       │  │
│  └──────┘  └──────┘  └──┬───┘  └───────┘  │
│                         │                  │
│  ┌──────────────────────▼───────────────┐  │
│  │        仿真引擎 (SimEngine)          │  │
│  │  固定步长循环 · 在线/离线 · 数据记录  │  │
│  └──────────┬───────────────────────────┘  │
│             │                              │
│  ┌──────────▼──────────┐  ┌─────────────┐  │
│  │  OPC Client         │  │ 功能块库     │  │
│  │  批量读写·AI写入     │  │ 93 个 R600C  │  │
│  │  信号映射           │  │ 宏命令定义   │  │
│  └──────────┬──────────┘  └─────────────┘  │
└─────────────┼──────────────────────────────┘
              │ OPC UA
              ▼
    ┌──────────────────┐
    │  NTVDPU 虚拟控制器 │
    │  科远 NT6000       │
    │  协调控制逻辑执行   │
    └──────────────────┘
```

### 数据流
1. 仿真模型计算工艺参数（汽压、功率等）
2. 通过 OPC UA 写入 AI 通道（HR=LR=目标值）
3. NTVDPU 控制逻辑执行，产生控制输出
4. 仿真模型通过 OPC UA 读取控制输出
5. 回到步骤 1，形成闭环

---

## 四、开发进度

### Phase 0：硬点问题攻关 ✅ 完成

- AI 通道：HR=LR 方案验证通过，写入后 1s 生效
- DI 通道：需组态层仿真切换（MUX 二选一）

### Phase 1：最小闭环验证 ✅ 完成（2026-03-14）

- [x] OPC UA 通信模块
- [x] 仿真功能块库（基础 10 个 → 扩展至 46 个 Python 实现）
- [x] 仿真循环引擎（在线/离线双模式）
- [x] CCS 被控对象模型（2 入 2 出）
- [x] 最小闭环跑通：煤量 200→240→200 t/h，功率跟随变化
- [x] Web UI 框架（Flask + Drawflow 组态画布）
- [x] R600C 宏命令功能块库（93 个定义，8 个分类）

### Phase 2：组态界面完善（进行中）

- [ ] SAMA 图组态画布优化（Simulink 风格）
- [ ] 离线组态 + 在线监视双模式
- [ ] 功能块库 UI 样式完善
- [ ] 逻辑封装系统完善

### Phase 3：协调控制全量闭环

- [ ] 完成所有 AI/DI 通道映射
- [ ] 扩展仿真模型至 CCS 全部输入输出
- [ ] 整体闭环调试

### Phase 4：自动化测试框架

- [ ] 测试用例格式定义（YAML）
- [ ] 测试执行引擎
- [ ] 核心工况覆盖

---

## 五、目录结构

```
LeDCSsim/
├── CLAUDE.md                       # Claude Code 项目指令
├── PROJECT_PLAN.md                 # 本文档
├── 1-3-R600C宏命令.pdf             # 科远 R600C 宏命令参考手册
│
├── config/
│   ├── opc_mapping.yaml            # OPC UA 节点映射（信号 ↔ 通道）
│   ├── block_library.yaml          # R600C 功能块库定义（93 个块）
│   ├── sim_settings.yaml           # 仿真运行参数
│   └── models/                     # 组态模型存储
│       ├── CCS_model.json          # CCS 被控对象 IB 层组态
│       ├── L3_boiler_thermal.json  # 锅炉蓄热封装块
│       └── L3_turbine_power.json   # 汽机功率封装块
│
├── src/
│   ├── opc_client/                 # OPC UA 通信
│   │   ├── client.py               #   Client 封装（连接/读写/AI写入）
│   │   └── mapping.py              #   信号映射管理（YAML↔节点路径）
│   │
│   ├── blocks/                     # 仿真功能块库（46 个 Python 实现）
│   │   ├── base.py                 #   Block 抽象基类
│   │   ├── basic.py                #   Inertia/Integrator/DeadZone/RateLimiter/Limiter
│   │   ├── transfer.py             #   LeadLag/SecondOrder
│   │   ├── select.py               #   HighSelect/LowSelect/Switch
│   │   ├── function.py             #   LinearInterp/Polynomial
│   │   ├── pid.py                  #   PIController/PIDController/PDController
│   │   ├── logic.py                #   AND/OR/NOT/XOR/FlipFlop/Comparator
│   │   ├── timer.py                #   TimerOn/TimerOff/TimerPulse/Counter
│   │   └── signal.py               #   SampleHold/Ramp/Gradient/Scale/Bias/Abs/Div/Sqrt
│   │
│   ├── sim_engine/                 # 仿真循环引擎
│   │   ├── model.py                #   SimModel 基类
│   │   ├── engine.py               #   SimEngine（在线/离线/输入覆盖/OPC读回）
│   │   ├── recorder.py             #   DataRecorder（CSV/pandas）
│   │   ├── ccs_model.py            #   CCS 被控对象模型（2入2出）
│   │   └── demo_model.py           #   单回路演示模型
│   │
│   └── web/                        # Web UI
│       ├── app.py                  #   Flask 应用（API + 页面路由）
│       ├── block_defs.py           #   功能块库 YAML 加载器
│       ├── static/
│       │   ├── css/main.css        #   全局样式
│       │   ├── css/canvas.css      #   SAMA/Simulink 画布样式
│       │   └── js/canvas-engine.js #   Drawflow 增强引擎
│       └── templates/
│           ├── base.html           #   布局模板（侧边栏导航）
│           ├── index.html          #   首页（总览）
│           ├── io.html             #   IO 层（硬件通道配置）
│           ├── il.html             #   IL 层（信号预处理）
│           ├── ib.html             #   IB 层（逻辑组态 + 仿真运行）
│           ├── l3.html             #   L3 层（封装库管理）
│           └── run.html            #   独立运行页（备用）
│
└── tools/
    ├── min_loop_demo.py            # 最小闭环命令行演示
    └── create_ccs_preset.py        # CCS 预置模型创建脚本
```

---

## 六、技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.12（不可用 3.14） |
| OPC UA | opcua-asyncio | asyncua |
| Web | Flask | 3.x |
| 画布 | Drawflow.js | 0.0.59 (CDN) |
| 图表 | Chart.js | 4.4.0 (CDN) |
| 数据 | pandas, PyYAML | - |
| DCS | 科远 NT6000 | NTVDPU + CCMStudio |

---

## 七、风险与应对

| 风险 | 应对 | 状态 |
|------|------|------|
| AI 硬点无法写入 | HR=LR 间接控制 | ✅ 已解决 |
| DI 硬点无法写入 | 组态层仿真切换 | 待实施 |
| Python 3.14 不兼容 asyncua | 固定 3.12 | ✅ 已解决 |
| 闭环震荡/发散 | 步长可配置 | 待验证 |
