# 驱动规则外置(需求 4)设计

> **状态**:设计稿,待用户复核 → writing-plans 出实施计划
> **范围**:把 `runtime.py` 里硬编码的设备驱动知识抽成 `drivers/*.yaml` 规则库,生成器改为规则驱动。容量模板(需求 3)是**下一轮**,本轮只把 drivers 规则稳定下来供其消费。

## 1. 目标

`py -3.12 -m src.viewer` 的【🔧 初始化 → 生成样本】调用 `generate_script_from_tagmap`,按工艺规则从点表生成 DSL 脚本草稿(人审后入库)。当前这套"看到什么点 → 生成什么赋值"的领域知识(约 530 行,`runtime.py:1994-2527`)全部硬编码。本设计把**会随工程/机组变化的知识**搬到 `drivers/*.yaml`,让换工程 = 换 yaml,不改 Python——这是需求 3(容量模板)能装下"可复用又可微调的驱动知识"的前提。

## 2. 现状盘点(哪些是领域知识、哪些是机制)

**领域知识(外置到 yaml)**:
- 关键词词表(~15 组):`_FAULT_WORDS / _REMOTE_WORDS / _RUN_WORDS / _START_WORDS / _STOP_WORDS / OPEN_FB_WORDS / CLOSE_FB_WORDS / LOCAL_WORDS / CMD_EXCLUDE_WORDS / _GATEWAY_WORDS` 等
- 设备白名单:`MOTOR_DEVICES`(7 类电机,各带 include/exclude)、`VALVE_DEVICES`(5 类阀门)、`_COMMON_MOTOR_EXCLUDE`
- 设备类型 → 默认 DSL 模式:电机类(DI/DQ → RS 闭环)、阀门类(AI/AQ → 直通)

**机制(留在代码)**:KKS 设备根分组、实例号提取(`_device_instance`)、设备名公共前缀提取(`_device_name`)、白名单匹配主循环、分段输出与统计、`_fmt_node` 格式化。这些是"怎么把规则用起来",换工程不变。

## 3. 外置力度决策:混合(偏简单一侧)

**进 yaml**:词表 + 设备白名单 + 设备类型(motor/valve)。
**留代码**:每个类型的 DSL 模式原语(motor → `RS(启,停)`/`RS_NOT`/单边/`NOT`;valve → AI 配对 AQ 直通)。这些是"电机回写""阀门直通"概念本身,稳定。
**逃生阀**:设备项可选 `pattern:` 字段覆盖默认模式(罕见的"反着来"设备用),不必为此扩 yaml 规则语言。

理由:真正随工程变的是"哪些设备、用什么词、是电机还是阀门";"电机/阀门各自怎么生成"是稳定机制。这样躲开"换工程改 Python",又不在 yaml 里重造图灵完备规则语言(YAGNI)。

## 4. drivers 文件结构与 schema

位置解析(工程优先、仓库兜底,沿用 `src/project.py` 既有路径模式):
- `projects/<工程>/drivers/` 存在 → 用它
- 否则用 `config/drivers/`(仓库默认基线,本轮把 YQ3 现有知识原样落这里)
- `ProjectPaths` 新增 `drivers_dir` 字段做这个解析

`drivers/vocab.yaml` —— 跨设备关键词词表:
```yaml
fault:       [故障, 告警, 报警, 异常, 失败, 跳闸, ...]   # → 反馈 = 0
remote:      [远方, 远控, 允许投运, ...]                  # → 反馈 = 1
local:       [在就地, 就地控制, 就地操作]                 # → 跳过不写
run:         [运行, 在运行, 运转]
open_fb:     [运行, 在运行, 开到位, 已开, 开反馈, 合位, ...]
close_fb:    [关到位, 已关, 关反馈, 跳位, 已停, 停止, ...]
start_cmd:   [启动, 开启, 合闸, 投入, 启泵, 开阀, ...]
stop_cmd:    [停止, 停机, 关闭, 分闸, 切除, 跳闸, ...]
cmd_exclude: [FSSS, MFT, 保护跳闸, 联锁跳闸, RB, 来自DPU, ...]   # 不能当 RS 启/停源
gateway:     [MEH, DEH, 至CCS, 来自DPU, 至PECCS, 柜间, 跨DPU, ...]
```

`drivers/devices.yaml` —— 设备白名单 + 类型:
```yaml
motor_exclude_common: [动叶, 调节阀, 调门, 风门, 润滑油, 液压油, 油泵, 油站,
                       电加热, 加热器, 冷却风, 失速, 联络, 出口, 进口, ...]
devices:
  - { name: 送风机,     type: motor, include: [送风机] }
  - { name: 引风机,     type: motor, include: [引风机] }
  - { name: 一次风机,   type: motor, include: [一次风机] }
  - { name: 给煤机,     type: motor, include: [给煤机] }
  - { name: 磨煤机,     type: motor, include: [磨煤机] }
  - { name: 前置泵,     type: motor, include: [前置泵] }
  - { name: 凝结水泵,   type: motor, include: [凝结水泵, 凝水泵] }
  - { name: 除氧器主调节阀, type: valve, include: [除氧器主调节阀, 除氧主调节阀, ...] }
  - { name: 除氧器副调节阀, type: valve, include: [除氧器副调节阀, ...] }
  - { name: 送风机动叶,     type: valve, include: [送风机动叶] }
  - { name: 引风机动叶,     type: valve, include: [引风机动叶] }
  - { name: 一次风机动叶,   type: valve, include: [一次风机动叶] }
# 字段:
#   name           设备显示名
#   type           motor(DI/DQ→RS闭环) | valve(AI/AQ→直通)
#   include        描述命中词(任一命中即归类)
#   exclude_extra  在 motor_exclude_common 之外再排除的词(可选)
#   pattern        可选,覆盖 type 的默认模式(逃生阀,本轮可不填)
```

motor 默认排除 = `motor_exclude_common` + 该设备 `exclude_extra`。valve 默认不排除。

## 5. 代码重构:抽出 `src/viewer/gen/`

`generate_script_from_tagmap` 约 530 行从 `runtime.py` 拆出,成独立可单测模块:

```
src/viewer/gen/
  rules.py       # DriverRules:读 drivers/*.yaml(工程优先兜底)→ 结构化规则对象
                 #   load_rules(project_paths) -> DriverRules
                 #   DriverRules.vocab / .devices / .match_device(desc) / .device_type(...)
  generator.py   # 通用生成引擎:消费 DriverRules + 点表 → 脚本文本
                 #   generate(project_paths) -> str   (替代旧 generate_script_from_tagmap)
                 #   机制:KKS 分组 / 实例提取 / 设备名 / 主循环 / 分段输出 / 统计
  gateway.py     # 柜间段提供器(见第 6 节)
```

`runtime.py` 的 `generate_script_from_tagmap` 改为薄壳:`from .gen.generator import generate; return generate(proj.paths())`。viewer 的 `/api/script/generate` 调用点不变。
保留 `src/sim_engine/io_pairing_gen` 依赖(load_points/pair_analog/pair_digital/is_soft)——它是已升格的配对算法,generator 仍调用。

## 6. 柜间通讯接口(留 seam,Excel 清册驱动)

柜间段做成**可插拔段提供器**,输入是一张归一化的"柜间点对点表",来源是用户的柜间线清册(Excel)。本轮**只定义接口 + 兜底**,不写 Excel 解析。

接口契约(`gen/gateway.py`):
```python
def gateway_section(project_paths) -> list[str]:
    """生成柜间通讯段的脚本行。
    读 projects/<工程>/gateway.csv(若存在)——归一化柜间点对点表:
        列: 目标信号(短码或全节点), 来源(信号短码 / 常数), 描述(可选)
        每行 → "目标 = 来源"  (直通 / 写常数)
    文件不存在 → 退回当前注释占位:按 vocab.gateway 关键词扫出潜在柜间点,
        以 '# DPU.xxx(描述) = ???' 列出供人工补全。
    """
```
- **Excel → gateway.csv** 的转换是用户/后续小工具的事,不在本轮(避免引入 Excel 解析库 + 各家清册格式差异)。本轮交付的是这张 csv 的**列定义**和 generator 对它的消费,Excel 一到接上即用。
- generator 把 `gateway_section()` 的返回插进"柜间通讯"分段,替换现在的硬编码注释块。

## 7. 不改坏:金标准回归

验收硬指标:用 YQ3 现有点表,新 yaml 驱动引擎 `gen.generator.generate()` 的输出,与重构前 `generate_script_from_tagmap` 的输出**逐字一致**(把 YQ3 当前硬编码知识原样翻译进 `config/drivers/*.yaml`,行为零变化)。
实现:重构前先抓一份当前输出存为 golden fixture(`tests/fixtures/yq3_generated_golden.txt`),新引擎产出与之 `assertEqual`。任何差异必须是有意的并在测试里显式标注。

## 8. 范围边界

- **本轮做**:vocab + devices 外置 + 生成引擎重构 + drivers 路径解析 + 柜间段接口(seam + 兜底)+ 金标准回归。
- **本轮不做**:柜间 Excel 解析(留接口);容量模板(需求 3,下一轮);per-device `pattern` 逃生阀只留字段、不必有真实用例。
- **下一轮(需求 3 模板)**:`projects/_templates/<容量>/drivers/` + 参数 yaml + 符号脚本段实例化;消费本轮的 drivers schema。

## 9. 单元划分与测试

| 单元 | 职责 | 测试 |
|---|---|---|
| `gen/rules.py` | 读 drivers/*.yaml → DriverRules;工程优先兜底解析 | yaml 缺字段/缺文件兜底;match_device include/exclude;工程 override |
| `gen/generator.py` | 规则 + 点表 → 脚本文本 | 金标准回归(YQ3 逐字一致);motor RS/RS_NOT/单边;valve 直通 |
| `gen/gateway.py` | 柜间段:csv 驱动 / 兜底注释 | 有 gateway.csv → 直通行;无 → 注释占位 |
| `src/project.py` | `drivers_dir` 解析 | 工程有 drivers/ 用之,无则 config/drivers/ |

## 10. 数据流

```
drivers/vocab.yaml + devices.yaml ──┐
                                     ├─→ rules.load_rules() ─→ DriverRules
projects/<工程>/(或 config/)默认 ───┘                            │
点表 (io_pairing_gen.load_points) ──────────────────────────────┤
                                                                 ▼
                          generator.generate() ── 机制(KKS分组/匹配/分段)
                                                                 │
        ┌──────────────────────────┬─────────────────┬──────────┴────────┐
        ▼                          ▼                 ▼                   ▼
   电机设备层(RS)          阀门设备层(直通)   柜间段(gateway.py)    模型层(占位)
        └──────────────────────────┴─────────────────┴───────────────────┘
                                     ▼
                              脚本文本 → /api/script/generate → editor
```
