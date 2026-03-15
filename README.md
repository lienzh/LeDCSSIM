# LeDCSsim

DCS 协调控制（CCS）逻辑仿真验证平台。Python 仿真模型通过 OPC UA 与科远 NT6000 虚拟控制器（NTVDPU）闭环通信，自动化验证控制逻辑的可靠性。

## 快速开始

### 环境要求

- Python 3.12（3.14 不兼容 asyncua）
- 依赖：`pip install asyncua pyyaml matplotlib pandas flask pymupdf`

### 启动 Web 界面

```bash
py -3.12 -m src.web.app
```

打开 http://127.0.0.1:5001

### 命令行仿真

```bash
# 离线仿真
py -3.12 -m src.sim_engine offline --duration 60

# 最小闭环演示（需启动 NTVDPU）
py -3.12 tools/min_loop_demo.py
```

## 架构概览

```
Python 仿真模型  ←── OPC UA ──→  科远 NTVDPU 虚拟控制器
      │                                │
 功能块库(46个)                    协调控制逻辑执行
 + 仿真引擎                       控制输出(阀位/挡板)
 + Web 组态 UI                          │
      │                                │
      └────── 闭环仿真验证 ─────────────┘
```

### Web UI 层级（H5000M 架构）

| 层级 | 路由 | 功能 |
|------|------|------|
| IO 层 | `/io` | OPC UA 硬件通道配置、信号映射 |
| IL 层 | `/il` | 信号预处理（缩放/滤波/冗余选择） |
| IB 层 | `/ib` | **逻辑组态 + 仿真运行**（主工作区） |


IB 层集成了组态和运行：
- **停止时**：拖拽功能块组态、编辑参数、连线
- **运行时**：自动切换监视模式，节点显示实时值

### 功能块库

基于科远 R600C 宏命令，93 个功能块定义，8 个分类：

| 分类 | 块数 | 示例 |
|------|------|------|
| 信号 | 4 | input, output, CON |
| 算术运算 | 9 | ADD, SUB, ML, DIV, SUM |
| 比较选择 | 12 | HS, LS, CMP, SEL |
| 动态环节 | 21 | G, I, LDL, FLT, DB, LIM |
| 控制 | 16 | PI, PID, PD, AM, MODE |
| 逻辑 | 12 | A, OR, NOT, FFR, ASW |
| 定时计数 | 12 | TB, TD, TP, CNT |
| 信号传输 | 7 | CPL, BTR, SG |

其中 46 个已有 Python 仿真实现（含 PID、逻辑门、定时器等）。

## OPC UA 通信

- Server: `opc.tcp://localhost:9440`（科远 NTVDPU）
- AI 通道写入：设置 HR=LR=目标值，PV 自动锁定
- DI 通道：需组态层仿真切换（MUX 二选一）

## 项目状态

- [x] Phase 0: AI/DI 硬点验证
- [x] Phase 1: 最小闭环跑通
- [ ] Phase 2: 组态界面完善
- [ ] Phase 3: 全量闭环
- [ ] Phase 4: 自动化测试

详见 [PROJECT_PLAN.md](PROJECT_PLAN.md)
