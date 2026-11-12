# S3-3-7 · F1 契约决策简报（给同侪 AI）

> 生成：2026-11-09 ｜ 用途：维护者把本文件交给另一位 AI，征询**一个契约定位问题**的意见
> 性质：**状态说明 + 一方判断**，不是验收委托包，也不是结论清单
> 匿名纪律：本文件不含真实姓名 / 校名 / 昵称 / 本机绝对路径 / 任何凭据；请勿索取
> 用法：只给本文件即可定位；若给仓库只读权限，精确清单见 `docs/peer-brief-s3-3-6.md` §4.2
> **round-24 状态订正（2026-11-09）**：维护者已拍板并落地 **② 派生物 + R1 严格拒绝 + 检查放 `recompute_all` + 补两条 schema `allOf` + 不进平台更严规则**。因此 §1.3 的「矛盾会被放行」、§1.4 的缺口锁现状、§0 / §2 的「三项仍待维护者拍板」**均为 round-22 历史快照，不再是现状**；三条缺口锁已反转成正向锁、函数保留，另补 28 条回归锁。本文件自此仅作**决策留档**，勿再按「待拍板」给建议；现役状态以仓库 `docs/` 唯一真相源为准。
> 本轮**只做登记与证据锁，未改判定语义**（round-22 当时状态）；`docs/verify-brief-s3-3-6.md` 已过期，不要拿它当现状

---

## 0. 给同侪 AI 的指令（可直接作为对话开头）

你要帮的是「一个有严格验证纪律的小型 Python 项目」判断 **§2 的一个契约定位问题**，不是重写代码、也不是验收落地。

- 事实挂来源（文件名 + 行号 / 可粘贴命令），推断明确标「假设」。
- 每条建议配一个**十分钟内能做的验证**（最小输入 + 预期输出，或一条字节级变异 + 预期真 `FAILED`）；给不出验证方式的标「假设」，不要写成结论。
- 不写总体评价 / 鼓励性文字；不声称「我跑过了」——若无执行环境，把复现写成让维护者粘贴的命令。
- **人不在简报里替你定口径**：§2 三项仍待维护者拍板，你的意见是输入，不是决定。
- 交付格式：逐条「输入 → 期望 → 实测 → 何时判通过」或「两选项各自代价」；给可执行 patch 请写成**统一 diff 文本，不要落盘**。
- 不要翻已拍板项（§1.1 已定的判据、五夹具分数序）；不要建议把范围扩到 F3 / F4 / F5 / F9。

---

## 1. 现状（全部可由仓库内命令复核，不是自述）

### 1.1 已定口径（不要翻案）

- 参赛材料匿名；`docs/` 是唯一真相源；五条断言已由 `src/arena/trace_pipeline.py::recompute_all()` **全部现算**，夹具里手写 `assertion_results` 只用来对 id 壳，不读内容。
- 五夹具分数序 `0 / 0 / 0.25 / 0.5 / 1.0` 是回归基线；DTO v2 的 `report_version=2`、四态 `pass/fail/blocked/unscored`、`unscored` 不计掌握度也不产出错误模式，均已拍板。
- `unscored` 的 `on_unscored=do_not_disqualify`：门禁无法判定时不拦，credit 断言照常计分，允许 `0 < ratio < 1`。

### 1.2 `trace_audit` 目前是怎么来的

| 位置 | 行为 |
|---|---|
| `fixtures/raw-*.json` | `trace_audit` 是**手写的原始数据**，含 `reported` / `schema_valid` / `invalid_reasons` / `call_count` / `final_status` |
| `docs/diagnosis-report.schema.json` L49–80 | 只校验四个必填字段的类型，加两条 `allOf`：`reported=false` ⇒ `schema_valid=false`、`call_count=0`、`invalid_reasons≥1`；`schema_valid=true` ⇒ `invalid_reasons` 为空 |
| `src/arena/diagnosis_report.py` L195 | `trace_audit = dict(raw["trace_audit"])` —— **原样复制进报告**，不做派生、不做一致性核对 |
| `src/arena/diagnosis_report.py` L196 | 只有 `reported` 与 `schema_valid` 两位决定轨迹类断言是否被强制改写为 `unscored`；notes 也只按这两位生成 |
| `src/arena/trace_pipeline.py` L110–125（`recompute_all`） | T11 入口防御：只校验 `trace_audit.reported` 与 `trace` 字段是否存在相冲突，**不校验其余四个字段** |

### 1.3 三处矛盾：本地只读复现（输入 → 输出）

以 `fixtures/raw-perfect.json` 为基底，只改下表左列，走 `recompute_all` → `build_report` → `validate_report`：

| 编号 | 单点改动 | 报告里的实际结果 |
|---|---|---|
| **A** | `trace_audit.call_count = 999`（`trace.tool_calls` 实际 5 条） | `trace_audit.call_count` 原样为 999；`ratio=1.0` / `disqualified=false` / `notes=None`；`validate_report()` **放行** |
| **B** | `trace.final_status = "crashed"`，`audit.final_status` 保持 `"completed"` | R5 `fail`、`disqualified=true`、`ratio=0.0`；但 `trace_audit.final_status` 仍是 `completed`；`validate_report()` **放行** |
| **C** | `trace.tool_calls[0].call_index = 9`（破坏连续性），`audit.schema_valid` 保持 `true` | 执行器把 R3 / R4 判成 `unscored / trace_invalid`，`ratio` 掉到 `0.5`；但 `audit.schema_valid=true`、`invalid_reasons=[]`、`call_count=5`、`notes=None`；`validate_report()` **放行** |

**C 是三处里最值得咬的**：断言侧已经用 `reason_code=trace_invalid` 明写「数据问题」，但同一份报告的 `trace_audit` 同时声明 `schema_valid=true` / `invalid_reasons=[]`，等于说「数据合规」；`notes` 又是 `None`，没有任何地方解释两者为何矛盾。下游若按 `trace_audit` 判断可信度，会得出与断言状态相反的结论。

复现入口已固化成三条缺口证据锁：`tests/test_trace_pipeline.py` 末尾的
`test_f1_td016_{call_count,final_status,schema_valid}_conflict_is_currently_allowed`。
它们断言的是**现状**（矛盾会被放行），不是**应然**；F1 落地时应反转成正向锁、函数不删除。

### 1.4 这三条锁是活锁，不是永远绿的假锁

字节级变异（改 `diagnosis_report.py` 模拟 F1 修复后的行为）后跑对应单测：

| 变异 | 注入的行为 | 结果 |
|---|---|---|
| M1 | `call_count` 与 `len(trace.tool_calls)` 冲突即抛 `ReportError` | 对应锁真 `FAILED` |
| M2 | `final_status` 与 `trace.final_status` 冲突即抛 `ReportError` | 对应锁真 `FAILED` |
| M3 | 有 `trace_invalid` 断言时把 `schema_valid` 派生为 `false` | 对应锁真 `FAILED` |
| M4（负对照） | 只改一处无关注释 | 不触发，如实标为**无效等价变异**、不计入证据 |

三组均 `RESTORED=True`、`FINAL_RESTORED=True`，无 `ERROR` / `TIMEOUT`。

---

## 2. 请你咬的可判定命题（AI 不替决）

### Q1 · 契约定位（核心）

`trace_audit` 究竟是 **①平台侧独立声明**（允许与执行器判定不同，它记录的是「采集/上报层看到的事」），还是 **②`trace` 的派生物**（必须与执行器判定一致，不一致就是数据损坏）？

请分别给出两条路的**代价**，尤其回答：

- 选 ① 时，A 的 `call_count=999`、B 的 `final_status` 反向，是否有任何下游消费者能正确解读？如果没有，① 的「独立性」是不是只是保留了脏数据？
- 选 ② 时，「平台在 trace 丢失 / 损坏时仍能留下一份外部证词」这个能力是否被取消？若应保留，证词该放在哪个字段，才不会和判定状态混在一起？

### Q2 · 若选 ②，冲突时怎么处置

- (a) **拒绝组装**：入口抛错，不产出报告；
- (b) **以执行器为准改写**并在 notes 留痕，报告照常产出。

请给一条可判定分界：什么情况下 (a) 会误伤合法提交？什么情况下 (b) 会把「平台采集故障」悄悄记成「学生能力不足」？注意 `unscored` 不计掌握度、`do_not_disqualify` 不拦这两条既有口径。

### Q3 · 若选 ①，最小加固到哪一步

只加范围 / 类型校验（例如 `call_count == len(tool_calls)`）算不算已经滑向 ②？请给一个**不自相矛盾**的最小规则集，并说明它如何与 T11 入口防御分工。

### Q4 · 落地连带面

无论选哪条，请列出改动后**必须复跑**的最小集合，并指出哪条现有回归锁会先转红。已知锚点：五夹具分数序、`--check` 的测试数锁、三条 F1 证据锁、`test_recompute_all_refuses_inconsistent_raw`。

### Q5 · 反向查漏

除了 A / B / C，还有没有**第四条**矛盾路径（例如 `schema_valid=true` 但 `invalid_reasons` 非空、`reported=true` 但 `tool_calls` 为空、`trace_audit` 缺 `final_status` 时 R5 仍判 pass）？请给最小输入 + 预期输出，不要给总体评价。

---

## 3. 我方倾向（**假设**，不是结论；请优先找反证）

- 倾向 **② + Q2(a)**：`recompute_all` 入口从 `trace` 与执行器实际结果派生四个字段，raw 手写值不一致即 `PipelineError`，schema 侧不改结构、只由 pipeline 保证一致。
- 三条理由：① `trace_audit` 目前**没有任何独立数据源**，它和 `trace` 写在同一份 raw 里，独立性是名义上的；② C 的自相矛盾会直接污染下游可信度判断；③ T11 已经开了「真相源冲突即拒绝组装」的先例，扩到其余四个字段是同一纪律，不是新契约。
- **反证条件**（若成立就撤回倾向）：若能举出一个合法场景，其中 `trace` 与 `trace_audit` 必须不一致、且报告消费者依赖这份差异做正确决策，则 ② 不成立。
- 已知代价：五夹具的手写 `trace_audit` 必须与 trace 严格一致（当前五份本来就一致）；三条 F1 证据锁要反转；`call_count` 从「平台声明」变成「派生值」，语义文档需同步。

---

## 4. 边界与只读清单

- **不要做**：翻已拍板项、改五份夹具的手写字段、动 F3（R3 谎报零调用）/ F4·F5（未知工具与空集）/ F9（配置校验缺口）、扩到 P3 的 R6 / 真实文件系统 / mock / 超时 / CI、给「重写架构」类建议、写「建议改成 Y」式翻案措辞。
- **只读路径**（全部仓库相对路径）：判定链 `src/arena/{trace_pipeline,assertion_evaluator,diagnosis_report}.py`；契约 `tasks/dbg-001-order-report.yaml`、`docs/{diagnosis-report.schema.json,diagnosis-report-schema.md,executor-semantics.md,error-patterns-v0.yaml}`；夹具 `fixtures/raw-*.json`；判据 `tests/test_{trace_pipeline,diagnosis_report,review_packet}.py`。完整五组清单见 `docs/peer-brief-s3-3-6.md` §4.2。
- 本机绝对地址含用户名，按匿名纪律**不写进任何可外发文件**，由维护者在本机侧填写；只给读权限，统一 diff 文本不落盘。明确不给 `docs/review-response-v1.md`（内部处置记录）、`.redteam-tmp/`、`.git/`、含本机绝对路径的日志、任何凭据。
- 是否启用、何时启用、最终选哪条口径，**由人拍板**。
