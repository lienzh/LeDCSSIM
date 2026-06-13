# 任务交接 — 驱动规则外置(需求 4)

> **交接时间**:2026-06-13
> **接手方**:另一个 AI 会话
> **一句话**:正在用「子代理逐 task 执行」实施"驱动规则外置",**8 个 task 已完成 Task 1,从 Task 2 接着做**。计划文件是唯一施工依据。

---

## 0. 立刻要做的(TL;DR)

1. 读计划:`docs/superpowers/plans/2026-06-13-driver-rules-externalization.md`(8 个 task,逐字带代码)。
2. 读设计依据:`docs/superpowers/specs/2026-06-13-driver-rules-externalization-design.md`。
3. **Task 1 已提交(commit `f178cda`)但其两阶段审查被打断** —— 先补一个轻量审查(它只是抓金标准 fixture + 一个回归测试,`py -3.12 -m pytest tests/test_generator_golden.py -v` 过即可认为合格),然后**从 Task 2 开始**逐 task 实施。
4. 执行方法:`superpowers:subagent-driven-development`(每 task 派实施子代理 → 规格审查 → 质量审查 → 修 → 标记完成,再下一个)。
5. 全部 8 个 task 完成后:跑全套测试 + 通知用户**自行重启 viewer**(不要替用户重启),点【🔧 初始化 → 生成样本】验证生成脚本与之前一致。

---

## 1. 当前精确状态(交接快照)

- **分支**:`main`(本会话一直直接提交 main —— 用户单人开发仓库,已确立的模式,继续即可)。
- **工作区**:干净(无未提交改动)。唯一 untracked:`model-ref-paper.pdf`(用户的,别动)。
- **测试**:`py -3.12 -m pytest tests/ -q` → **34 passed**。
- **最近提交**(新→旧):
  - `f178cda` test(gen): YQ3 生成器输出金标准 ← **驱动规则外置 Task 1**
  - `b91bdae` docs(plan): 驱动规则外置实施计划
  - `83b83b9` docs(spec): 驱动规则外置设计稿
  - `85779b1`/`ecedc29`/`e9da42d` 画布清理(阶段 B 完成)
  - 更早:OPC DI 写 fallback 修复、工程目录化、模型库独立(见 §4)
- **驱动规则外置文件进度**:
  - ✅ 有:`tests/fixtures/yq3_generated_golden.txt`(129 行/6201 字节,YQ3 金标准)、`tests/test_generator_golden.py`
  - ⬜ 待建:`config/drivers/{vocab,devices}.yaml`、`src/viewer/gen/{rules,generator,gateway}.py`、`ProjectPaths.drivers_dir`、相关测试
- **TaskList 工具里的任务**:Task #11–#18 对应计划 Task 1–8;#11 应标 completed,#12 起 pending。

---

## 2. 剩余 task(都在计划文件里,带完整代码)

| # | task | 关键点 |
|---|---|---|
| 2 | `ProjectPaths.drivers_dir` | 工程优先 `projects/<name>/drivers/`,兜底 `config/drivers/`。TDD 加在 `tests/test_project.py` |
| 3 | `config/drivers/{vocab,devices}.yaml` | **逐字转录** `runtime.py` 当前常量(行范围在计划里)。**坑**:尾空格词 `"开 "/"关 "/"停 "` 和 `"RB-"` 必须加引号,否则 yaml 丢字 → 金标准会挂 |
| 4 | `gen/rules.py` `DriverRules` | 设备匹配 **valve 类先于 motor 类**(保持原行为);motor 排除 = `motor_exclude_common` |
| 5 | `gen/gateway.py` | 柜间段接口:`gateway.csv` 驱动(列 `目标信号,来源,描述`),不存在返 `[]`。**本轮不解析 Excel**,只留接口 |
| 6 | `gen/generator.py` | **把 `runtime.py:2179-2527` 机制整体搬入**,常量引用换成 `rules.vocab[...]`/`rules.match_device(...)`(计划给了逐处映射表)。**金标准逐字一致是唯一正确性闸门** |
| 7 | `runtime.py` 改薄壳 | `generate_script_from_tagmap` 委托 `gen.generate()`,删已搬走的 ~530 行常量/helper(删前 grep 确认无外部引用) |
| 8 | CLAUDE.md | 第 7 节"新功能去哪里"表加生成器位置 |

**安全网**:Task 4–7 全程 `tests/test_generator_golden.py` 必须绿(新引擎/薄壳对 YQ3 输出与金标准逐字一致)。Task 6 若 diff,计划里给了"首处差异定位"命令。

---

## 3. 执行方法(子代理逐 task)

每个 task:
1. **派实施子代理**(`Agent` 工具,general-purpose):把该 task 的**完整文本 + scene-setting 上下文**贴进 prompt(别让子代理读计划文件,直接喂)。机械任务用 `sonnet`,Task 6(搬 530 行机制)可用 `sonnet` 但要强调"机制一字不改、金标准是闸门",必要时升 `opus`。
2. 子代理回 DONE 后 **派规格审查子代理**(独立核查 git diff 对照 spec,别信实施者自述)。
3. 规格过了 **派质量审查子代理**(opus 适合)。
4. 审查发现问题 → 让**同一类**修复子代理改 → 重审,直到过。
5. `TaskUpdate` 标 completed,下一个。

prompt 模板在 `C:\Users\lienz\.claude\plugins\cache\claude-plugins-official\superpowers\5.1.0\skills\subagent-driven-development\` 下(implementer/spec-reviewer/code-quality-reviewer)。
**连续执行,task 间不要停下来问用户**(用户已授权"子代理逐 task 执行")。只有 BLOCKED 或全部完成才停。

---

## 4. 本会话已完成的大背景(理解项目演进)

按时间顺序,本会话(2026-06-12~13)做了:
1. **架构重构前两步**(plan `docs/superpowers/plans/2026-06-12-models-move-and-project-dirs.md`):
   - 模型库独立:`src/models/`(`ccs_usc_otbt.py`/`steam.py`/`dsl_registry.py` 工厂注册表)。加容量 preset = 注册表加一条。
   - 工程目录化:工程 = `projects/<name>/`,`src/project.py` 唯一真相源,viewer 顶栏 `工程 ▾` 切换器。
2. **OPC DI 写 fallback 修复**(`c7223b0`):NTVDPU 的 DI 写类型 schema 会随通道状态翻转,fallback 改双向 + `_FORCE_FLOAT_NODES` 缓存可逆。
3. **画布清理(CLAUDE.md 阶段 B)**:删 `src/web/`、旧 `src/sim_engine/` 画布模块、画布 config、`CLAUDE-ref.md`,共 ~30700 行。`src/sim_engine/` 仅留 `recorder.py` + `io_pairing_gen.py`(升格工具)。
4. **驱动规则外置(需求 4)**= 当前进行中。

**需求全景**(用户 6 项):1/2(多工程切换+点表隔离)✅、5(模型模块化)✅、画布清理 ✅、**4(驱动自动化)进行中**、3(容量模板)= 下一轮、6(控制优化空间)留好不建框架。

---

## 5. 关键约定与坑(务必遵守)

**项目硬约束**(CLAUDE.md):
- Python **3.12**(`py -3.12`),严禁 3.14(asyncua 不兼容)。
- 中文注释(面向热控工程师),物理量标单位。
- viewer 端口 5002(别用 5000,Windows/Hyper-V 占用)。
- OPC 地址归一化、AI/DI 直写 PV + DI 写**双向** fallback、批量读写、`raise_on_bad_status=False`。
- commit message 末尾空行 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

**用户偏好**(来自记忆,务必遵守):
- **不主动重启 viewer / 停 OPC** —— 改完代码只通知用户,由其决定重启时机。
- **设计/规划阶段不要逐项让用户拍板** —— 自己合理决策、一次性交付(`feedback_decision_style`)。
- 无视觉能力,UI 调整靠用户反馈 + 主动验证。
- 简洁黑白紧凑 UI 风格。

**本任务专属坑**:
- 金标准 fixture 依赖 YQ3 点表 `YQ3SIM-IO/SIMPLE/简化/*_S.csv`(已跟踪)。
- `generate_script_from_tagmap` 入参被忽略(走 `proj.paths()`)。
- `gen/generator.py` 仍依赖 `src/sim_engine/io_pairing_gen`(load_points/pair_analog/pair_digital/is_soft)——这是保留的升格工具,正常 import。
- 运行中的 viewer 不受文件删改影响(已加载到内存),但改完需用户重启才生效。

---

## 6. 关键文件地图

| 用途 | 文件 |
|---|---|
| 本次施工计划 | `docs/superpowers/plans/2026-06-13-driver-rules-externalization.md` |
| 设计依据 | `docs/superpowers/specs/2026-06-13-driver-rules-externalization-design.md` |
| 待重构的生成器 | `src/viewer/runtime.py:1994-2527`(`generate_script_from_tagmap` 及其常量/helper) |
| 工程上下文 | `src/project.py`(加 `drivers_dir`) |
| OPC 客户端 | `src/opc_client/client.py`(写 fallback 在此) |
| 模型库 | `src/models/`(`dsl_registry.py` 工厂注册表) |
| 配对算法(依赖) | `src/sim_engine/io_pairing_gen.py` |
| 项目指令 | `CLAUDE.md`(权威,每会话自动加载) |
| 用户记忆 | `C:\Users\lienz\.claude\projects\C--Users-lienz-MyPlanet-ClaudeCode-LeDCSsim-LeDCSSIM\memory\`(MEMORY.md 是索引) |

---

## 7. 下一轮(本任务完成后)

需求 3 **容量模板**:`projects/_templates/<容量>/`(参数 yaml + 驱动规则 + 符号脚本段),经 tagmap 文本实例化生成工程 `script.d`。消费本轮的 drivers schema。`src/project.py` 的 `list_projects()` 已排除 `_` 前缀目录,为模板预留。详见记忆 `project_arch_roadmap.md`。

柜间通讯:用户有独立的柜间线清册(Excel),后续做 Excel → `gateway.csv` 的转换(本轮已留 `gen/gateway.py` 接口)。

---

# 附录(深度技术上下文)

## 附录 A:生成器内部详解(Task 6 搬运重点)

`generate_script_from_tagmap`(`runtime.py:2179-2527`)的执行流程,Task 6 要把这整套**机制原样搬进** `gen/generator.py`,只把常量引用换成 `rules.*`:

1. **取点表**:`pp = proj.paths()`;`csv_files = sorted(pp.io_dir.glob(pp.io_glob))`,空则遍历 `pp.io_fallback_globs`。`_dpu_of(p)`:简化路径 `"DPU"+stem.replace("_S","").replace("-S","")`,回退路径 `p.stem`。
2. **建索引**:`all_points = {dpu: load_points(csv)}`(点对象见附录 B);`desc_map = {(dpu, name): desc}`(直接读 csv 第 2 列,GBK 解码)。
3. **配对**:每 dpu 跑 `pair_analog`/`pair_digital` → `auto_pair_cmd = {(dpu, fb_name): cmd_name}`(AI↔AQ / DI↔DQ 同 KKS 配对)。
4. **白名单分组**:遍历所有点,对每个点 `desc` 调 `_match_device`(**先试 VALVE_DEVICES 再 MOTOR_DEVICES** —— 新代码用 `rules.match_device` 按 `type` 分流,顺序必须 valve 先);按 `(dpu, kks_root[:9])` 累进 `motor_groups`/`valve_groups`,每组存该设备的 DQ/DI/AQ/AI 点 + 命中的 spec。**无 KKS 的点跳过**。`_should_skip_pt`:`is_soft` 或空描述 → 跳。
5. **段 1 电机**(`sorted(motor_groups.keys())`):
   - 在该组 DQ 里找 `open_cmd`/`close_cmd`(`_is_open_cmd`/`_is_close_cmd`:首字 启/开 或 停/关 且第二字非空格,或 start/stop 词命中且不互含;先过 `_is_real_cmd` 即不含 `cmd_exclude`)。
   - 对每个 DI 反馈:命中 `fault`→`stats["skip_fault"]`跳;`local`→跳;`remote`→跳;命中 `close_fb` 且不含 `open_fb` → `RS_NOT(开,关)`/单关`=关`/`NOT(开)`;命中 `open_fb` 或 `run` → `RS(开,关)`/单开`=开`/`NOT(关)`;都不命中 → `skip_other`。
   - 标题 = `_device_instance` 提取的实例(A/B/3A)+ `dev["spec"]["name"]`,行 `# --- {标题} @ {dpu} (KKS:{kks_root}) ---`。
6. **段 2 阀门**(`sorted(valve_groups.keys())`):每个 AI 查 `auto_pair_cmd` → `AI = AQ  # AI 直通`;无配对但组内**唯一 AQ** → 用之(注"单 AQ 假设")。
7. **段 3 柜间**:扫所有点(单独读 csv,绕过 `is_soft`),描述含 `gateway` 词且未被 `consumed_fb` 消费、非空非"备用" → 收集;每 dpu 列前 5 个为 `# DPU.x(desc) = ???` 注释占位。**Task 6 在此插 `gateway_lines_from_csv(pp.root/"gateway.csv")`:非空用其行替换占位,空则保留占位**。
8. **段 4 模型**:仅一行 `# (模型层 — 待实现)`。
9. **拼装**:`SECTION_ORDER` 四段,每段加 `═`×64 分隔 + 段标题;头部插白名单严格模式统计块(`lines[3:3] = header_stats`,用 `stats` 计数)。

**逐字一致的敏感点**:`sorted()` 的分组键顺序、`all_points` 迭代顺序(`DPU_SCOPE = sorted(...)`)、valve 先于 motor 的优先级、`_fmt_node` 的 `(`→`[` 转义、各段空行、header 统计数字。任一处偏差金标准 diff 立刻报。

## 附录 B:数据契约(跨 task 类型必须一致)

```
点对象 (load_points 返回):
  {"name": "HW.AI010502.PV", "code": "AI"|"DI"|"AQ"|"DQ", "desc": str, "kks": str(完整KKS)}
  code = 点名 HW.<2字母><数字>.PV 里的 2 字母

配对对象 (pair_analog/pair_digital 返回):
  {"dpu": str, "device": KKS根, "cmd": 指令点name, "fb": 反馈点name,
   "template": "analog"|"digital", "transform": {...}, "online_writable": bool, "desc": str}

设备 spec (devices.yaml 元素 / match_device 返回):
  {"name": str, "type": "motor"|"valve", "include": [str], "exclude_extra"?: [str], "pattern"?: str}

DriverRules (gen/rules.py):
  .vocab: {str: [str]}        # fault/remote/run/start_cmd/stop_cmd/open_fb/close_fb/local/cmd_exclude/gateway
  .devices: [设备spec]         # 已 valve 在前
  .motor_exclude_common: [str]
  .match_device(desc) -> 设备spec | None    # valve 类先于 motor 类;motor 排除=motor_exclude_common+exclude_extra

函数签名 (全计划统一):
  load_rules(project_paths) -> DriverRules
  generate(project_paths=None) -> str       # gen/generator.py, 也是 gen.__init__ 导出的入口
  gateway_lines_from_csv(csv_path) -> list[str]
  ProjectPaths.drivers_dir -> Path          # 工程级 projects/<name>/drivers/ 存在则用, 否则 Path("config/drivers")
```

`is_soft(p)`:`desc` 含 `SOFT_KW`(备用/模件第/TEST/TO CCS/来自DPU/至PECCS/需确认/描述文本/心跳/SOC/设定)。`DEV` 正则取 KKS 根(2数+3字母+2数+2字母 共 9 位)。

## 附录 C:runtime.py 文件地图(Task 7 防误伤)

`runtime.py` 约 2530 行,**Task 7 只动生成器块(1994-2527)**,其余全部保留:

| 行范围(约) | 内容 | Task 7 |
|---|---|---|
| 40-100 | 事件日志 `log_event`/`get_events` | 不动 |
| 104-177 | OPC 端点配置(`_endpoint_path` 等) | 不动 |
| 180-317 | TCP/HELLO 探活 | 不动 |
| 319-365 | 短码展开 `short_to_full`、`is_intermediate` | 不动 |
| 369-737 | reinit_lag/dryrun_preview(上载/预演) | 不动 |
| 739-847 | prune/reset/debug | 不动 |
| 865-1151 | DSL parser(`_tokenize`/`_parse_*`/`parse_script`) | 不动 |
| 1141-1336 | `_resolve`/`_make_hashable`/`_CcsHandle`/`_eval_rhs` | 不动 |
| 1339-1575 | 状态镜像 snapshot | 不动 |
| 1576-1730 | `_State`/`get_status` | 不动 |
| 1897-1991 | `swap_pairs`/`start`/`stop` | 不动 |
| **1994-2527** | **生成器(`_recommend_cmd`/词表常量/白名单/`_classify_*`/`_match_device`/`_device_instance`/`_fmt_node`/`generate_script_from_tagmap`)** | **改薄壳 + 删搬走的常量/helper** |

Task 7 删除前**必须 grep** 每个 helper/常量的引用范围:只在 1994-2527 内用到的才删;`app.py` 或诊断面板若引用则保留。计划 Task 7 Step 1 已给 grep 命令。

## 附录 D:生成的 DSL 长什么样(理解"正确输出")

金标准 `tests/fixtures/yq3_generated_golden.txt` 是范本。生成的脚本语法(完整说明在 viewer F1 帮助 / CLAUDE.md 第 4 节):
```
DPU3002.DI060301(A给煤机运行) = RS(DPU3002.DQ060202(启A给煤机), DPU3002.DQ060201(停A给煤机))   # 开反馈 = RS
DPU3002.DI060502(A磨煤机跳位1) = NOT($Mill_A)
DPU3013.AI010502(送风机动叶反馈) = DPU3013.AQ010101(送风机动叶指令)   # AI 直通
DPU3002.DI060305(A给煤机故障) = 0
```
- 短码 `DPU3013.AI010502` 自动展开为 `ns=0;s=DPU3013.HW.AI010502.PV`。
- `(描述)` 仅可读性,解析忽略。函数库:`RS/RS_NOT/NOT/AND/OR/ADD/SUB/MUL/DIV/MAX/MIN/LIMIT/SEL/LAG/CHAR` + 模型工厂 `CCS_1000` + `STEAM_T`。
- 段结构:`# ═══...` 分隔 + `# 【电机设备层 (开关量, RS 触发器)】` 等四段。

## 附录 E:环境与工具

- **OS**:Windows 11;Bash 工具是 Git Bash(POSIX `sh`),PowerShell 也可用。**避免 `cd`,用绝对路径**;`LF→CRLF` warning 正常忽略。
- **Python**:统一 `py -3.12`。依赖:`asyncua pyyaml pandas iapws`(`iapws` 给 STEAM_T 用,已装 1.5.5)。
- **跑测试**:`py -3.12 -m pytest tests/ -q`。**跑单测试**:`py -3.12 -m pytest tests/test_xxx.py -v`。
- **启 viewer**:`py -3.12 -m src.viewer`(端口 5002)。**AI 不主动启/停/重启**。
- **查运行中 viewer**(若用户开着):`curl -s http://127.0.0.1:5002/api/script/status`、`/api/script/debug`(诊断)、`/api/script/generate`(POST,生成样本)、`/api/project`(工程)。
- **生成金标准的等价命令**:`py -3.12 -c "import src.viewer.runtime as rt; print(rt.generate_script_from_tagmap(''))"`。

## 附录 F:本会话调试地雷(运行 viewer 见到异常时对照)

- **DI 写类型双向 fallback**(`c7223b0`):NTVDPU 的 DI 写 schema 会随通道状态翻转(同点先只收 Float、下装后只收 Boolean)。`OPCClient.write_value` 已双向 + `_FORCE_FLOAT_NODES` 缓存可逆。诊断面板"写失败 N 节点"若复现先查是不是又翻转了。
- **SH 段 `.IN` 端子读垃圾**:`DPU.SHxxxx.xxx.IN` 被组态上游驱动,NTVDPU 外部读返回未初始化值(denormal `1e-40` / 负值)。用户脚本里给水流量 `$Model_DFW` 曾接 SH `.IN` 读到 `1.03e-40` 致 CCS 模型发散,已用 `MAX(...,500)` 兜底(`e451690`)。
- **CCS 模型无输入钳位**(待补防御):`ccs_usc_otbt.py` 的 `step` 没用 yaml `limits` 段钳 uB/Dfw/ut。坏输入(给水≈0、ut 越下限)→ `hm` 发散到 6380 kJ/kg → `STEAM_T` 越界 [10,4500] 返 None → 引用它的赋值每周期被跳(诊断面板"被跳过的赋值",源节点显示 `?` 因为是模型管脚而非 OPC 点,该提示会误导)。记在 `project_arch_roadmap.md`。

## 附录 G:子代理派发配方(怎么派、怎么审)

**实施子代理**(`Agent`,`general-purpose`):
- prompt 里贴**该 task 的完整文本 + scene-setting**(这 task 在整个重构里的位置、依赖什么、产出什么),**别让子代理读计划文件**,直接喂全文。
- 模型:Task 2/3/5/8 机械 → `sonnet`;Task 4 → `sonnet`;**Task 6(搬 530 行)→ `sonnet` 但强调"机制一字不改、金标准是唯一闸门、diff 用计划给的定位命令";若反复对不上金标准升 `opus`**;Task 7 → `sonnet`。
- 必带约束:`py -3.12`、中文注释、commit co-author trailer、**不重启进程**、行号以唯一字符串匹配为准。
- 要求回 Status(DONE/BLOCKED/NEEDS_CONTEXT/DONE_WITH_CONCERNS)+ 文件清单 + 测试结果 + Commit SHA。

**规格审查子代理**:独立核查 `git show <sha>` / `git diff`,**别信实施者自述**;逐条对 spec;自己跑测试;查"多做/少做"。返 ✅ 或 ❌+具体。

**质量审查子代理**(`opus`):给 base/head SHA,审设计/边界/坑;返 Strengths/Issues(Critical/Important/Minor)/Assessment。

**循环**:规格 ❌ → 同实施者修 → 重审;规格 ✅ 才进质量审;质量有 Important+ → 修 → 重审。两审都过 → `TaskUpdate` 标 completed → 下一 task。**task 间不停问用户,连续执行**(用户已授权)。每个 task 是独立 commit。

**全部完成后**:派一个 final code-reviewer 通审整个 `f178cda..HEAD` 区间,然后通知用户重启 viewer 验证。
