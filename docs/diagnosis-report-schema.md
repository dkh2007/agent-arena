# 诊断报告 DTO v2（层间契约：评测层 → 状态层）

> **状态**：已落仓并跑通（`src/arena/diagnosis_report.py` + `docs/diagnosis-report.schema.json` + `fixtures/` 五份夹具）。
> **对应步数**：`review-response-v1.md` §4 的 **S2**，v2 改造对应 §6（P5/P6 拍板）。
> **谁写**：评测层（执行器跑完断言后调用 `build_report()`）。
> **谁读**：状态层（拿 `assertions[].state` + `error_patterns` + `assertion.skill_id` 更新掌握度与错误模式）。
> **不读**：计划书、前端、复习层都不该绕过状态层直接吃这份报告。
>
> 本文写**为什么**，schema 写**长什么样**，`src/arena/diagnosis_report.py` 写**怎么保证**。
> 三者不一致时以 schema 为准，然后回来改本文。
>
> **v2 是破坏性变更**（`report_version: "v1"` → `"v2"`）：rubric 拆成 gate / credit 两层，
> 分母从"全部断言权重和 6"改为"credit 层权重和 4"，新增 `disqualified` / `disqualifying_assertions` /
> `unattributed_patterns` 三个字段。原因见 §3 与 §8。

---

## 1. 四条设计原则（每条都是踩过的坑）

1. **只装可判定的观测，不装推断。** 复习建议、掌握度增量都在状态层产生。一旦在报告里也放一份，就有两套口径。
2. **契约自解释。** 报告自带 `attribution_policy` 与 `denominator_policy`，状态层不用回读 YAML 才知道 `blocked` 算不算掌握度。
3. **宁可报错，也不静默降级。** A2E 的教训是"读不到 trace 就静默给满分"。所以 trace 不可用时**强制**把轨迹断言改写为 `unscored`（调用方在 raw 里填 `pass` 也改），任何不自洽直接抛 `ReportError`，不产半成品。
4. **门禁只管分数，不管观测。** gate 失败时 credit 断言**照常判定并记录**，只是不计入分数。反过来（门禁失败就把 credit 全标 `blocked`）等于用评分逻辑吞掉诊断数据——这是 v1 遗留的隐患，v2 明确禁止。

---

## 2. 顶层结构

```
report_version       "v2"
submission           提交元信息（含 task_git_hash：没有它，断言 id 无法与 YAML 对齐）
trace_audit          trace 前置校验结果（**trace 的派生值**，见 §2.1；A2E 反面教材的产物）
score                两段式分数：gate 决定资格、credit 决定分数（含 voided：本份是否作废）
attribution_policy   四态 × {是否计入掌握度, 是否产出错误模式}
report_attribution   整份开关（派生值）：本次报告能否计入掌握度 / 产出错误模式
process_fact         流程事实：这道题要不要过程、这次交没交（round-70 / P2，中性记录）
assertions[]         逐条断言：layer / weight / state / skill_id / error_patterns / evidence
skill_gaps[]         技能缺口聚合（fail 且能归到技能的断言）
unattributed_patterns[]  有错误模式但两条归因路都走不通的失败——不进 skill_gaps，但不许丢
notes[]              可选。报告出现"未覆盖情况"时必须在这里说明，不许静默
judge_scores[]       可选（B-04b）。跑了 judge 才有：每次评价的 {rating 1–4, suspected_patterns, rationale, model, latency_ms}
judge_agreement      可选（B-04b）。跑了 judge 才有：两两比较中 rating 相同的比例，[0,1]
```

> **两个 judge 字段"缺省即无"，不是 `null`**：缺字段 = 这份提交没跑双评；`null` 会让
> "没评"与"评了但没拿到值"混成一件事。两者都不参与 `score` / `assertions` /
> `report_attribution` / `detected_patterns`（`judge_pipeline.attach_judgement()` 机械核对），
> 低一致率只影响「进不进人工仲裁队列」（`arbitration_queue`，v3），不改这份成绩。
> 详见 `docs/data-model.md` §5.1 末段与 §5.4。

> **`report_attribution` 不是第二真相源**：它可由 `attribution_policy` + `score.by_state` + `score.voided` + `process_fact.missing` 重算（`tests/test_diagnosis_report.py::test_td038_report_attribution_is_derived` 锁住）。存在的唯一理由是：作废/缺过程时「不记能力短板」这句话必须有**执行者**，只写在 `notes` 文案里等于没有校验。
>
> **`process_fact` 与 `score` 分开的理由**：缺过程是**流程事实**，不是能力判断。若把它写进 `score`，下游很容易把它当成分数原因；写在顶层则只回答"这次交没交过程"。

顺序约定：`assertions[]` **先 gate 后 credit**，与执行顺序一致（执行器先跑门禁，不过就不必跑昂贵的 credit 判定）。`R5` 排在 `R2` 之前不是笔误。

**加载期硬约束（round-46 / TD-035 ② 案 A）**：`depends_on` 的被依赖项，必须在上面这个
**全局 `gate + credit` 序列**里排在引用方之前；引用后置断言由
`src/arena/diagnosis_report.py::rubric_layers()` 在加载期直接拒绝（`ReportError`，
消息点名引用方、被依赖项与双方序号）。跨层引用合法：现役
`R2_report_correct → R1_file_exists` 是 credit 引用 gate，必须保留。

**本条是契约声明，不是对未来实现的预测**。依据是**当前可核证的事实**：执行器按 rubric
数组顺序累积 `dependency_results`（`trace_pipeline.py`「gate 在前，R1 一定先于 R2 算出」），
后置断言尚未算出 ⇒ 报告层 `ratio=1.0` 与执行器 `blocked` 结论相反。若日后把执行器改为
拓扑排序或允许乱序声明，即构成**契约变更**：必须同时反转本条与
`docs/review-response-v1.md` §46 的加载期顺序校验及其测试锁，否则会误拒合法配置。
环检查仍由 `tests/test_task_yaml.py::test_dependencies_declared_and_acyclic` 静态负责，
不在加载期重复实现（§45.4 同源口径）。

### 2.1 `trace_audit`：trace 的派生物（round-24 拍板，F1 / TD-016）

**它不是平台侧独立声明**。五个字段全部由 `src/arena/trace_pipeline.py::recompute_all()`
从 `raw.trace` + `tasks/dbg-001-order-report.yaml::trace_requirements.minimum` 算出：

| 字段 | 派生规则 |
|---|---|
| `reported` | `trace is not None` |
| `call_count` | `len(trace.tool_calls)`（非数组时为 0） |
| `schema_valid` | 结构校验无违规 |
| `invalid_reasons` | 结构违规原因码（`traceInvalidReason` 枚举内，集合比较） |
| `final_status` | `trace.final_status`，仅当值在 `final_status.enum` 内才写入；`trace=None` 时整项缺失 |

**结构校验**只覆盖 YAML 已声明的规则：`tool_calls` 是数组、每条 `call_index` 从 1 连续、
`status` 在 `status_enum` 内、`params` 是对象、`final_status` 必填且在枚举内。
执行器的**语义** `trace_invalid`（未知工具、probe 投影丢失、工具参数 schema 违规）
**不算结构损坏**，否则会推翻已拍板的 P-B 口径；分界见 `docs/executor-semantics.md` §4。

`agent_id` 有意不在上表内（TD-020 ③ 拍板 b）：它只是可选元数据，agent 身份以报告层
`submission.agent_id` 为准（DTO 必填）。`trace.agent_id` 缺失或与 `submission.agent_id`
不一致都不改变 `schema_valid` / 分数；任务 YAML 也不再声明 `agent_id.required`。

**冲突即拒绝（R1）**：手写 `raw.trace_audit` 逐字段与派生值对账，任一不一致抛
`PipelineError`，不改写、不产出半真报告。五份夹具的手写值实测与派生值逐字相等，
因此落地不改夹具、不改分数序。这是 T11「真相源冲突即拒绝组装」的同一纪律。

**schema 侧四条 `allOf`（报告出口的第二道防线）**：

| 编号 | 规则 | 关闭的缺口 |
|---|---|---|
| A1 | `reported=false ⇒ schema_valid=false / call_count=0 / invalid_reasons≥1` | 未上报却声称有效 |
| A2 | `schema_valid=true ⇒ invalid_reasons` 为空 | 有效却列理由 |
| **A3** | `schema_valid=false ⇒ invalid_reasons≥1` | P1「说坏却给不出理由」 |
| **A4** | `reported=false ⇒` 不允许 `final_status` | TD-023 / P4「无 trace 却声称退出状态」 |

A3 / A4 为 round-24 新增（拍板 P-F1-4）。即使将来出现绕过 `recompute_all()` 的
组装路径，报告也不会带上自相矛盾的 audit。**已收敛（round-65 §55 / round-67 §57）**：
调用序列规则由 `call_sequence_problems()` 单一内核供派生器与执行器共用，两入口防御由
`_check_raw_consistency()` 共用；F1 派生对账由 `_reconcile_audit()` 单一实现供
`recompute_all()` 与兼容入口 `recompute_r3()` 共用，**兼容入口不再有旁路**。
原「两处独立实现、未来须收敛」与「已知旁路」两处描述均已过时。

---

## 3. `score`：两段式（v2 核心变更）

```json
{
  "weighted_sum": 1,
  "counterfactual_credit": 1,
  "total_weight": 4,
  "ratio": 0.25,
  "denominator_policy": "fixed_at_credit_total_weight",
  "disqualified": false,
  "disqualifying_assertions": [],
  "by_state": {"pass": 3, "fail": 2, "blocked": 0, "unscored": 0},
  "voided": false
}
```

### 3.1 两段的职责

| 段 | 断言 | 权重 | 作用 |
|---|---|---|---|
| **gate（门禁）** | R1 交产物、R5 正常退出 | **无**（报告里 `weight: 0`，只为结构统一） | 只决定"这份提交有没有资格被评分" |
| **credit（积分）** | R2 结果正确(2)、R3 重试证据(1)、R4 无参数幻觉(1) | 分母 = **4** | 决定"评多少分" |

```
suppressed = disqualified || voided || process_fact.missing
ratio = suppressed ? 0 : counterfactual_credit / 4
```

三个压制条件分开记，因为它们的**含义不同**：`disqualified` 是没交东西/没跑完（学生侧），`voided` 是版本对不上这次不算数（round-70 / TD-038），`process_fact.missing` 是题目要求过程却没交（round-70 / P2）。三者都不改断言状态。

> **计入层的执行情况（round-89 / 2026-11-16 订正）**：本节写的「作废 ⇒ 不计掌握度」此前只有
> `report_attribution` 这个派生的布尔开关在表达，计入层仍按 A 口径把证据照记——口径与实现
> 曾不一致（round-88 外审回件 F1 指出）。人已拍板「甲」，`mastery.skill_evidence()` 现在对
> `score.voided` 的提交**整份排除**（提交行与报告照留），细节见 `data-model.md` §5.2。
> `disqualified` **不受影响**，仍是 A 口径：报告压制分数，但不压制观测。

### 3.2 四条硬规则

- `disqualified = any(gate.state == "fail")`。**门禁 `unscored` 不拦**（`on_unscored: do_not_disqualify`）：无法证明不合格就不判不合格。这不会放水——同一份提交的 credit 层 R3/R4 也因 trace 缺失而 `unscored`，分数上不去。
- 不合格的分数是 **0**，不接受"封顶 0.3"这类做法：封顶值没有依据，评委会直接问"为什么是 0.3"。
- `counterfactual_credit` **与 `disqualified` / `voided` 无关**，永远等于"credit 层 pass 的权重和"。它只用于诊断，不参与 `ratio`。这个字段让报告保持内部自洽：断言列表里写着 `pass`，就有一个数与它对应。**唯一分数是 `ratio`**；字段原名 `credit_pass_weight`，round-70 因 Q1「两个分数并列会被误读」改名为带 `counterfactual` 的语义名，schema 里保留 `x-previous-name` 便于对接旧读取方。
- `voided` 与 `disqualified` **不合并**：一个是"这次不算数"，一个是"没交东西"。合并会把版本错位写成冤案（人拍板原话：「判不合格 = 冤案」）。

### 3.3 为什么不用"全部断言权重和"当分母（P4 修正）

v1 的分母是 6，语义是"缺数据也不缩小分母"，方向正确。但它把**门禁型**和**否定型**断言一起算进了分母：

- R4 的判定是 `all_calls_must_be_valid`，一个**全称命题**。零次调用时它平凡为真 → `pass` → 得 1 分。
- R5 只要不崩溃就是 `completed` → 得 1 分。

于是"什么都不做"自动拿 2/6 = **33%**。而"假重试"（交了产物但结果和过程都错）拿 3/6 = 50%。**3 个"没做错事"恰好等于 1 个"做对了核心事"**，分数失去分辨力。详见 §8。

---

## 4. `assertions[]`：四态 + 分层 + 归因

```json
{
  "id": "R3_retry_evidence",
  "layer": "credit",
  "type": "trace_sequence",
  "weight": 1,
  "state": "fail",
  "skill_id": "failure_retry",
  "error_patterns": ["retry_wrong_params"],
  "evidence": {
    "observed": {"offsets": [0, 10, 0], "statuses": ["success", "error", "success"]},
    "expected": {"ordered": true, "calls": ["..."]},
    "extra": {"probe": "calls[?name=='fetch_orders'].params.offset"}
  }
}
```

四态语义（与 YAML 的 `scoring.attribution` 一致）：

| state | 含义 | 分数贡献 | 计入掌握度 | 产出错误模式 |
|---|---|---|---|---|
| `pass` | 断言成立 | 满权重（gate 为 0） | 是 | 否 |
| `fail` | 有证据地不成立 | 0 | 是 | **是**（必须给出模式） |
| `blocked` | 前置未通过，无法判定 | 0 | 否 | 否 |
| `unscored` | 依赖的数据缺失（trace 未报） | 0 | 否 | 否 |

`layer=gate` ⇒ `weight` 必须为 0；`layer=credit` ⇒ `weight` 必须 > 0。schema 和 builder 各拦一次。

### 4.1 `evidence` 的约定

schema 只校验"是对象且含 observed/expected"，语义要求写在 YAML 的 `error_pattern_detection.probe` 里。理由：同一种断言的证据形状会随任务演化，把它写死在 schema 里会让 schema 三个月后没法改。

---

## 5. `skill_gaps[]` 与 `unattributed_patterns[]`

`skill_gaps` 只做聚合：`fail` 且能归到技能的断言按技能归类，`weight_lost` 是权重和，按它降序。

**归因有两条路，优先级固定（B-06 / ROADMAP R-2，round-78）：**

| 顺序 | 来源 | 生效条件 | `attributed_by` |
|---|---|---|---|
| 1 | 断言自己声明的 `skill_id` | 一直生效；**有它就只认它** | `assertion` |
| 2 | 词表 `docs/error-patterns-v0.yaml` 的 `mapped_skill_ids` | 仅当断言**没写** `skill_id` | `mapping` |

第二条是**兜底不是主路**：Q10=A 拍板过「不改 `build_report()` 按 `spec.skill_id` 的归因」（`docs/verify-brief-s3-3-1.md` §Q10），跨技能归因仍登记在 TD-001 未销项。所以有 `skill_id` 时词表一次都不查，既有的逐例归因一位不变。

兜底路径允许一对多（词表现在 9 条填了映射，全是一对一）：分摊权重，且 **`Σ weight_lost ≤ score.total_weight`** 有硬校验（I12），不会把 1 分的失败记成 2 分。词表里查不到该模式时**直接 `ReportError`**，不返回空——静默返回空会让兜底悄悄失效而报告仍"看着正常"。

> **反证条件已实测触发（务必读）。** 本任务的四条断言全写了 `skill_id`，所以映射表**一次都没参与**：五份夹具的 `skill_gaps` 与改动前逐例相同，`attributed_by` 全是 `assertion`（`tests/test_diagnosis_report.py::test_b06_five_fixtures_unchanged_declared_path_wins` 就是这条对账锁）。结论要如实写：**在今天的词表与任务上，映射表不带来任何新信息，它的价值只有"断言漏写 skill_id 时仍能归因"这一条**。计划书里「自研核心」的说法据此降级为「兜底归因 + 词表集中管理」，见 `docs/review-response-v1.md` §78。要让映射表真正干活，得先改一条断言的声明方式（例如让 R3 不再写 `skill_id`），那属于 `docs/BACKLOG.md` B-06b，不是现在顺手做的事。

`unattributed_patterns` 收容另一类：`fail` 且**两条归因路都产不出技能**的断言（当前是 R1/R5 这类门禁，它们的模式 `mapped_skill_ids` 为空）。

> **为什么必须有第二类（P5）。** v1 里"没交产物"的那个提交，`skill_gaps` 是**空数组**——R1 没有技能归属，于是这件事在技能层完全不留痕，状态层推不出任何复习项。极端对比：什么活都没干的 `raw-empty` 反而有诊断（它的 R3 记了 `no_retry`），**做得更多、诊断更少**。
>
> 但把它们塞进某个技能（比如 `multi_step_planning`）会让那个技能的错误模式统计失真。所以第三条路：**不进技能缺口，但独立留痕**。

P5 的最终处置是**不加第 4 技能**，走 gate 通道：`score.disqualifying_assertions` 告诉状态层"这次是没交产物"，`unattributed_patterns` 给出具体错误码。若后续数据表明"交付纪律"确实是可训练目标，再加技能节点，届时只需在 `skill-taxonomy-v0.yaml` 增节点并给 R1/R5 填 `skill_id`，报告结构不用改。

---

## 6. `reason_code` 枚举（状态层按它分支，不解析中文）

| code | 用于 | 含义 |
|---|---|---|
| `trace_not_reported` | `unscored` | Agent 完全没上报 trace |
| `trace_invalid` | `unscored` | 报了但没通过前置校验（call_index 不连续、时间戳倒退等） |
| `dependency_blocked` | `blocked` | 前置断言未通过，本断言无从判定 |
| `tool_no_response` | `unscored`（原写 `blocked`，见下方订正） | 工具无响应，产物不可得 |
| `invoke_timeout` | `unscored`（同上） | 调用超时 |
| `artifact_invalid` | `unscored` | 产物本体（Q13(a) 的 `raw.artifacts`）缺失或结构非法：**平台采集故障**，不判成「学生没交」、不归因；S3-3-6 按 §20.2 第 1 项扩入 |

> **M1（2026-11-13）订正：`tool_no_response` / `invoke_timeout` 的状态是 `unscored`，不是 `blocked`。**
>
> 原文把这两条标成 `blocked`，与下表 I4「`blocked` 必须有真失败的前置」直接冲突。
> 实测（M1 接 HTTP 入口时）：把 R1 标成 `blocked + tool_no_response`，builder 当场拒绝——
> `ReportError: R1_file_exists: 标了 blocked，但前置 [] 全部通过——blocked 无依据`。
> 端点不可达时**没有任何断言失败过**（连跑都没跑起来），所以它永远够不到 `blocked` 的前提。
>
> 它们与 `artifact_invalid` 属同一类「平台采集故障」：分数贡献同为 0、同样不计掌握度、
> 同样不产出错误模式，区别只在原因归属。原文「`blocked`」作废。
> 实现见 `src/arena/submission_api.py:unreachable_report()`，锁见
> `tests/test_submission_api.py::test_unreachable_is_unscored_not_fail`。

---

## 7. 由 schema 和组装器分别强制的规则

schema 管结构（能表达的），builder 管语义（schema 表达不了的）。

| 编号 | 规则 | 由谁保证 | 为什么 schema 做不到 |
|---|---|---|---|
| S1 | 四态枚举、`weight >= 0`、`layer` 枚举 | schema | — |
| S2 | `layer=gate ⇒ weight=0`；`layer=credit ⇒ weight>0` | schema（allOf） | — |
| S3 | `state=fail ⇒ error_patterns` 非空；其余三态必须为空 | schema | — |
| S4 | `state ∈ {blocked, unscored} ⇒ reason_code` 必填 | schema | — |
| S5 | `denominator_policy` 只能是 `fixed_at_credit_total_weight` | schema | — |
| S6 | `disqualified=true ⇒ ratio=0 且 weighted_sum=0 且 disqualifying 非空` | schema | — |
| S7 | `schema_valid=false ⇒ invalid_reasons≥1`（round-24 / §2.1 A3） | schema（allOf） | — |
| S8 | `reported=false ⇒` 不允许 `final_status`（round-24 / §2.1 A4，TD-023） | schema（allOf） | — |
| I1 | 断言 id 集合 = `rubric_gate ∪ rubric_credit`，不重不漏、顺序一致 | builder | schema 无法表达"等于另一份文件的集合" |
| I2 | trace 不可用 ⇒ 所有 `type` 以 `trace_` 开头的断言强制 `unscored` | builder | 需要读外部状态 |
| I3 | `fail` 的模式 ⊆ 本断言声明 ∩ 词表 | builder | 需要读词表文件 |
| I4 | `blocked` 必须有真失败的前置 | builder | 需要跨条目引用 |
| I5 | `total_weight` = credit 权重和（本任务 4），与 YAML 声明一致 | builder | 跨文件一致性 |
| I6 | `weighted_sum = suppressed ? 0 : counterfactual_credit`，其中 `suppressed = disqualified ∨ voided ∨ process_fact.missing` | builder | 算术 |
| I7 | `counterfactual_credit` = credit 层 pass 的权重和（与断言列表逐条自洽） | builder | 跨条目汇总 |
| I8 | 门禁 `unscored` 不导致 `disqualified` | builder | 策略判断 |
| I9 | R1/R5 失败时其模式进 `unattributed_patterns`，不进 `skill_gaps` | builder | 归因策略 |
| I10 | `voided` / `process_fact.missing` **不改写断言状态**，只压 `ratio` 与 `report_attribution`（round-70；决策 5 的延伸：门禁只管分数，不管观测） | builder | 策略判断 |
| I11 | `voided=true ⇒ void_reason` 必填且 `ratio=0`、`weighted_sum=0`（TD-038） | schema（allOf）+ builder | — |
| I12 | 归因守恒：每条 `fail` 恰好落在 `skill_gaps` 或 `unattributed_patterns` 之一（不重不漏）；`Σ weight_lost ≤ total_weight`；兜底路径查不到映射即报错（B-06 / round-78） | builder | 算术 + 跨条目汇总 |

---

## 8. 五份夹具的实测结果（本步的证据）

| 夹具 | 提交形态 | v1 ratio | **v2 ratio** | round-70 起 | 关键判定 |
|---|---|---|---|---|---|
| `raw-empty` | 0 次工具调用就正常退出 | **0.333** | 0（DQ） | **0（DQ）** | R1 门禁拦下。R4 空集平凡 pass 的漏洞已堵（round-32 F5）：无目标工具受检调用 → `unscored`，不再计掌握度 |
| `raw-no-file` | 过程全对，产物没写 | 0.500 | 0（DQ） | **0（DQ）** | R1 门禁拦下；`counterfactual_credit=2` 仍照算，`unattributed_patterns=[no_output_file]` |
| `raw-fake-retry` | 游标退回 offset=0 的假重试 | 0.500 | 0.25 | **0.25** | credit 只拿 R4 的 1 分；`skill_gaps` 两个（R2 的 2 分 + R3 的 1 分） |
| `raw-no-trace` | 结果对，但没报 trace | 0.500 | 0.500 | **0** | **本题要求过程（决策 9）**：`process_fact.missing=true` ⇒ 不计能力分、不计掌握度；断言照旧（R2 仍 `pass`，`counterfactual_credit=2`），门禁 R5 `unscored` 不拦 |
| `raw-perfect` | 全对 | — | 1.000 | **1.000** | 上界锚点 |

**分数序（round-70）`0 / 0 / 0.25 / 0 / 1.00`**：不再是全序单调——`raw-no-trace` 落回 0，与 `raw-empty`、`raw-no-file` 并列。这是决策 9 的直接后果，不是回归：**分数不区分"没做"与"做了没交过程"，过程门槛本身由 `process_fact` 区分**。`raw-perfect` 仍是上端锚点，`raw-fake-retry` 仍证明"过门槛后按正确性排序"（0.25 < 1.00）。

`test_score_orders_by_process_gate_then_accuracy` 锁住这张表；`test_process_missing_suppresses_score_but_keeps_observations` 锁住"断言不被改写"。

### 8.1 v2 保留的两个已知取舍

1. **"过程全对但忘交文件"记 0 分**，惩罚偏重。这是 `disqualified_if: any_gate_failed` 的直接后果。接受的理由：任务没完成就是没完成，给 0.5 会让"不交产物"变成可行策略。若后续认为过重，可改 `on_fail` 为 `cap` 并给出**有依据的**封顶值，而不是随手写 0.3。
2. **门禁 `unscored` 不拦**，理论上"崩溃了但不报 trace"能绕过 R5。实际绕不过：同一份提交的 credit 层也全 `unscored`，分数不会因此变高。

---

## 9. 已拍板的决策（v2 关闭 v1 的三条待定项）

| # | 议题 | 结论 |
|---|---|---|
| **P4** | 分数分母口径 | 分母恒为 **credit 层权重和 4**，不因断言缺失而缩小。gate 没有权重，不进分母 |
| **P5** | R1/R5 在分类学里无归属，怎么办 | **不加第 4 技能**，走 gate 通道：`disqualifying_assertions` + `unattributed_patterns` 留痕。理由见 §5 |
| **P6** | 分数分辨率不足（三种提交同为 0.5） | 改为 **gate / credit 两段式**，门禁未过记 0 |
| P1 | 掌握度口径（FSRS D/S/R vs EWMA + S 作衰减） | **round-70 拍板**：掌握度与调度分开记，只用一个时间轴；**不把 S 当衰减系数**（量纲不成立），S 只负责调度。见 `data-model.md` §5.2 |
| P2 / Q5-D1 | 缺过程怎么算 | **round-70 拍板**：按题目类型分流——要过程的题缺过程 ⇒ 不计能力分、不计掌握度、记 `process_fact`（`submission_requirements.requires_process`）；只交答案的题照常算分。**不改写断言状态** |
| P3 | 防作弊优先项 | **round-70 拍板**：保「答案可对账」（答案数值须能由过程记录推出），随机化只作补充；对账不通过 ≠ 答错，落「未验证」 |

---

## 10. v2 故意不做的事

- **不放复习建议、不放掌握度增量**：那是状态层的事，放进来就是第二套口径。
- **不放 variant / FSRS 字段**：DTO 只描述"这次提交怎么样"，不描述"下次该做什么"。
- **不做分数平滑或曲线映射**：0 / 0.25 / 0.5 / 1 是四档粗粒度，够把质量序分开。更细的刻度等真实数据再定。
- **不把 `evidence` 结构写死进 schema**：见 §4.1。
