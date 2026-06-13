# LeDCSsim

DCS 协调控制(CCS)逻辑仿真验证平台。当前项目路线是 **DSL 脚本 + viewer runtime + 工程目录**:Python 仿真/脚本运行时通过 OPC UA 与科远 NT6000 虚拟控制器(NTVDPU)闭环通信,用于验证控制逻辑方向、联锁动作和模式切换。

> 旧 Drawflow 画布 Web、旧 `src.sim_engine` CLI 和画布 JSON 配置已删除。不要再使用 `src.web.app`、`src.sim_engine offline` 或端口 5001。

## 环境

- Python: 3.12(`py -3.12`),不要使用 3.14(`asyncua` 不兼容)
- 依赖: `py -3.12 -m pip install -r requirements.txt`
- OPC UA Server: `opc.tcp://localhost:9440` 或当前工程 `opc_endpoints.yaml` 指向的 VM 地址
- 默认 viewer 端口: 5002

## 快速开始

启动 viewer:

```bash
py -3.12 -m src.viewer
```

打开:

```text
http://127.0.0.1:5002/script
```

常用 CLI:

```bash
# 离线运行(纯本地,不连 OPC)
py -3.12 -m src.cli run --duration 60

# 在线运行(连 NTVDPU 闭环)
py -3.12 -m src.cli run --online --duration 60

# 从 YQ3SIM-IO/*.csv 自动勾选 OPC 通讯
py -3.12 -m tools.mark_opc_communication

# 从配对结果生成 ref 架构 yaml
py -3.12 -m tools.generate_yaml_from_pairs
```

## 当前主入口

- `py -3.12 -m src.viewer`:Web 仪表板 + `/script` DSL 脚本编辑器
- `projects/<name>/`:工程目录,包含 `project.yaml`、`script.txt`、备份、状态镜像和机器本地 OPC 端点配置
- `config/active_project.yaml`:当前激活工程指针(本地文件,gitignored)
- `src/project.py`:工程路径解析唯一真相源

## DSL 脚本示例

```text
DPU3013.AI010502 = DPU3013.AQ010101
DPU3013.AI010502(反馈) = DPU3013.AQ010101(指令)
DPU3013.DI010101(运行反馈) = RS(DPU3013.DQ010101(启动), DPU3013.DQ010102(停止))
$tmp(中间量) = LIMIT(DPU3013.AQ010101, 0, 100)
```

短码如 `DPU3013.AI010502` 会自动展开为 `ns=0;s=DPU3013.HW.AI010502.PV`。括号内信号名只用于可读性,解析时忽略。

## 驱动生成器

viewer 的【初始化 → 生成样本】从点表生成 DSL 脚本草稿:

- 规则词表: `config/drivers/vocab.yaml`
- 设备白名单: `config/drivers/devices.yaml`
- 工程级覆盖: `projects/<工程>/drivers/`
- 生成引擎: `src/viewer/gen/`
- 柜间通讯归一化表: `projects/<工程>/gateway.csv`(列: `目标信号,来源,描述`)

生成器有金标准回归测试,要求 YQ3 输出在重构前后逐字一致。

## 测试

```bash
py -3.12 -m pytest tests/ -q
```

关键测试:

- `tests/test_generator_golden.py`:生成器输出金标准
- `tests/test_opc_write_fallback.py`:NTVDPU DI 写类型双向 fallback
- `tests/test_models_isolation.py`:模型库不得依赖 viewer/OPC/asyncua

## 架构边界

- OPC 通信改动: `src/opc_client/client.py`
- 新增模型: `src/models/` + `src/models/dsl_registry.py`
- 新增 DSL 算法块: `src/viewer/runtime.py` 的 `FUNC_ARITY` + `_eval_rhs`
- 工程/路径相关: `src/project.py`
- 点表配对工具: `src/sim_engine/io_pairing_gen.py` + `tools/`

详细约束和踩坑记录见 `CLAUDE.md`。
