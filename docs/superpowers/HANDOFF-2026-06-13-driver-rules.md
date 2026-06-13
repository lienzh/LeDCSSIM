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
