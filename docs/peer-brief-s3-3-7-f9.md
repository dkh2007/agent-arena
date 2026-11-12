# 同侪评审简报：S3-3-7 第 4 步 / F9 —— 「配置声明未执行」算不算 bug

> **round-37 状态（2026-11-11，维护者留档）**：Q1 已由维护者拍板 **(b)**
> （人确认无仓外消费者），Step2 已落地原仓，**F9 / TD-020 全部收口**。
> 本包及 §3 / §4 的 Q1–Q5 均为**历史快照**，不要再按「待拍板」给建议。
>
> **round-36 状态（2026-11-11，维护者留档）**：本包回件已核证，Step1
> （① 层内 id 查重 + ② `depends_on` 加载期六项校验）**已在原仓落地且验证通过**；
> §2 的九场景实测表是**修复前快照**，不可再当作当前行为。③ / Q1 当时未拍板，
> 现已由 round-37 收口。
>
> **round-35 订正（2026-11-11）**：同侪回件已收到并逐项本地核证，**我方三处表述已订正**：
> ① §2.1 的「全链路」不成立 —— S1 / S2b / S3b 只在 `build_report()` 直连复现，
> `recompute_all → build_report` 生产链路被 id 集合长度 / 结果重复 / 执行器 blocked 挡下；
> ② §2.2 第 3 点补「`uniqueItems` 碰巧能抓 S1，但抓不住同 id 不同 body」；
> ③ §3 Q1(a) 补「先撞 F1 的 audit 对账（`PipelineError`），同步 audit 后才得 `0.5`」。
> 三处都只改**暴露面与后果**的描述，不改变「缺口存在」的结论，也不改变待拍板项。
> §3 / §4 的「待拍板」与「我方倾向」是 round-34 历史快照；现行决策仍待维护者拍板。
>
> **状态（2026-11-10）**：S3-3-7 序列为 F1 → F3 → F4/F5 → **F9**（末项）。
> F1 / F3 / F4 / F5 已拍板落地并闭合。**本轮只做只读复现与证据锁，判定代码一个字节未改**：
> `src/` / `tasks/` / `fixtures/` / DTO schema 均未改动；探针用内存构造、跑完即删。
> F9 修复落地**等本包的判据拍板**。本文件是**可外发单文件**，只写仓库相对路径；
> 本机根目录由维护者在本机侧填写，不写进包内（避免破坏盲审匿名）。
>
> 请勿参考历史包：`docs/verify-brief-s3-3-1.md`、`docs/peer-brief-s3-3-6.md`、
> `docs/peer-brief-s3-3-7-f1.md`、`docs/peer-brief-s3-3-7-f3.md`、
> `docs/peer-brief-s3-3-7-f4-f5.md`（均已落地或过期，按「待拍板」给建议会答偏）。
> `docs/review-response-v1.md` 是维护者内部处置记录，**不外发、不在只读清单内**。

---

## 1. 请求什么

请对下面 **Q1–Q5** 给出**可判定意见**：每个结论请配一个**十分钟内能在本仓重跑的验证命令或最小反例**，
并写出**反证条件**（什么事实成立即推翻你的结论）。不需要夸奖、不需要重写代码、不需要泛泛谈架构。
我们只采信能用仓库内命令复现的主张；**回件自述一律先当「待核证主张」**，不当验收结论。

本轮盲区不是「现状会不会出错」（本地已用 9 个场景证明会），而是**应然该怎么定**：
本地裁判（pytest / 字节级变异 / 评审包 `--check`）能锁住现状，判不了契约选择。三处具体歧义：

1. **③ 语义歧义**：YAML 声明了 `agent_id.required=true`，却没有任何执行者。
   这算「实现漏了」还是「声明本来只是文档」？两种答案会**改分数并反转既有测试锁**。
2. **② 职责归属**：`depends_on` 引用拼错时，该由加载期/组装器挡，还是只由执行器挡？
   本仓已有静态环检查，容易误读成「已经覆盖」。
3. **③ 跨模块口径**：YAML 声明 / `_trace_audit_issues()` / 报告分数 / 既有 P9 锁 /
   DTO schema 的 `submission.agent_id`，五处口径是否一致，本地裁判无法替人决定。

**回件格式**（便于我方逐条核证）：

```text
Q1: 结论（选 a/b/c，或第三方案） | 十分钟内验证命令或最小反例 | 反证条件
Q2: ...（同上，逐条）
...
事实 / 假设 / 待拍板 分界：逐条标明
```

---

## 2. 已核证的现状（磁盘实测，可直接复现）

基线（生成时实测，隔离 venv 的 python）：

| 项 | 实测 |
|---|---|
| `pytest tests/ -q` | **610 passed** |
| `pytest tests/test_trace_pipeline.py -q -k f9_td020` | **8 passed, 155 deselected** |
| `python src/arena/trace_pipeline.py --fixture all` | `0.0 / 0.0 / 0.25 / 0.5 / 1.0` |
| `python src/arena/review_packet.py --check` | **OK (`97f30c2a0935755b`)** |
| 工作区 | HEAD `03cf8c9`，38 项未提交；本轮 F9 未改 `src/` / `tasks/` / `fixtures/` / DTO schema |

F9 = **TD-020「配置校验缺口」**，三个子项：
① `rubric_layers()` 只查 gate∩credit **跨层**重复，不查**层内**重复；
② `depends_on` 引用无加载期校验（拼错时组装器抛裸 `KeyError`）；
③ `trace_requirements.minimum.agent_id.required=true` 无任何执行器 / 派生器检查。

### 2.1 九场景复现（`build_report()` 直连；生产链路另有护栏）

> **round-35 订正**：本节九场景走的是 `build_report(task, raw)` **直连**（F9 现状锁与
> `--fixture` CLI 的路径），**不是** `recompute_all → build_report` 生产链路。
> 后者有三道偶然护栏：`trace_pipeline.py` 的 `raw.assertion_results` id 集合长度对账、
> `build_report()` 的「断言结果重复」、以及执行器先把 R2 判 blocked。
> 本地实测：S1 / S2b 在 chain 下 raw5 → `PipelineError`（id 集合不一致）、
> raw6 → `ReportError`（断言结果重复）；S3b 在 chain 下 → `KeyError: 'R1_typo'`，
> **静默只在 direct**。缺口仍在，暴露面是直连；修复点仍是两层共用的 `rubric_layers()`。

| # | 场景 | 实测 | 判读 |
|---|---|---|---|
| S1 | gate 内复制 R1 | `rubric_layers` 不拒（gate=3/credit=3）；`build_report` → **6 条断言 / 两个 R1**；`validate_report=PASS`；`by_state.pass=6`、`ratio=1.0`、`total_weight=4` | **核心缺陷**；重复还污染状态计数，不只多一条 |
| S2 | credit 内复制 R3（`total_weight` 仍 4） | `ReportError: YAML 声明的 credit.total_weight=4，但 credit 断言权重之和=5`，**错误文本不含「重复」** | 拦截是**权重对账的副产品**，不是 id 唯一性检查 |
| **S2b** | 同上 + 同步改 `total_weight=5` | 也穿透：6 条断言 / 两个 R3 / `total_weight=5` / `by_state.pass=6` / validate PASS | **新证据**：缺口覆盖**两层**，修复不能只补 gate |
| S3 | `depends_on=["R1_typo"]` + R2 `blocked` | 裸 `KeyError: 'R1_typo'` | **核心缺陷**：无可读诊断，且不是 `ReportError` |
| **S3b** | 同样拼错但 R2 非 blocked | `build_report` + `validate_report` **全 PASS**、`R2=pass`、`ratio=1.0` | **新证据**：真实缺口是**加载期引用完整性**；`KeyError` 只是偶然在 blocked 分支炸，非 blocked 路径完全静默 |
| S4 | 执行器侧两条路径 | 缺 `dependency_results` → `EvaluatorError`（可读）；前置未过 → `blocked/dependency_blocked` + `blocked_by` | 对照：同一 `depends_on` 在两层有两种行为 |
| S5 | 删 `trace.agent_id` 全链路 | `ratio=1.0`、`schema_valid=True`、`invalid_reasons=[]`，而 YAML 明写 `required=true` | **③ 复现**（既有锁 `test_f1_p9_missing_agent_id_is_not_refused` 已钉 `ratio`） |
| S6 | 跨层重复 | `ReportError: 断言同时出现在 gate 与 credit` | 对照：既有拦截有效 |
| S7 | 结果层重复 | `ReportError: 断言结果重复` | 对照：既有拦截有效 |

S6/S7 把缺口边界钉死：**精确在「层内配置 + 加载期引用」**，不是所有重复都漏。

### 2.2 三处容易读错的地方（请在下结论前先核证）

1. **两层 `agent_id` 不是同一个字段**。
   `docs/diagnosis-report.schema.json` 的 `submission.required` 含 `agent_id`（报告层，
   夹具 `fixtures/raw-perfect.json` 里 `submission.agent_id` 有值，`build_report` 直接取 `raw["submission"]`）；
   本轮 S5 删的是 **`trace.agent_id`**，对应 `tasks/dbg-001-order-report.yaml` 的
   `trace_requirements.minimum.agent_id: {required: true}`（轨迹层）。
   **请勿以「DTO schema 已 required」直接判定 ③ 不是缺陷**——这是 Q1 要处理的核心口径。
2. **静态环检查只覆盖磁盘 YAML 文件**。
   `tests/test_task_yaml.py::test_dependencies_declared_and_acyclic` 确实检查
   `dep in rubric` 且无环，但它读的是仓库内那一份 YAML，不覆盖运行时构造 / 外部注入的 task。
   S3/S3b 用的是内存里改过的 task，故不在其覆盖范围内。
3. **`assertions` 没有 `uniqueItems`**。
   `docs/diagnosis-report.schema.json` 里 `assertions` 只有 `minItems: 1`，其 description 明写
   「不多、不少、不重复……（由 builder 保证，schema 只能保证结构）」。
   文件内确有 `uniqueItems: true`，但挂在 `invalid_reasons` / `disqualifying_assertions` 等数组上，
   且锁的是**整条数组元素相等**，不是「id 唯一」。
   **补充（round-35）**：`uniqueItems` 对逐字段相等的重复 item 确实会报错——当前 builder
   对重复 id 的两条 spec 取同一个 id-keyed 结果，S1 的两个 item 通常深度相等，故加
   `uniqueItems` 会「碰巧」抓到 S1；但「同 id、不同 state / type」的数组仍通过。
   最小复现已本地核证：`[{id:R1,state:pass},{id:R1,state:pass}]` → INVALID，
   `[{id:R1,state:pass},{id:R1,state:fail}]` → VALID。它最多是脆弱的第二道防线。

### 2.3 代码锚点（行号为生成时磁盘实测，可能随编辑漂移，以函数名为准）

```text
src/arena/diagnosis_report.py
  rubric_layers()      ~L80-100：只算 set(gate_ids) & set(credit_ids) 跨层交集，无层内查重
  _assertion_item()    ~L145-158：仅 state == "blocked" 时才读 results[d]，故 S3b 静默
  build_report()       ~L274：submission 直接取 raw["submission"]
src/arena/trace_pipeline.py
  _trace_audit_issues()~L79-122：只查 tool_calls 结构与 final_status，不查 agent_id.required
docs/diagnosis-report.schema.json  L23 / L177-180（见 §2.2 第 1、3 点）
tasks/dbg-001-order-report.yaml    L116 / L143 / L177-263（required / total_weight / depends_on）
```

### 2.4 证据锁（8 条，`tests/test_trace_pipeline.py` 的 `test_f9_td020_*`）

6 条**现状锁（断言现状、非应然，修复落地时反转成正向锁、函数名不删除）**：
`..._duplicate_gate_id_is_accepted`、`..._duplicate_credit_id_only_trips_weight_reconciliation`、
`..._duplicate_credit_id_passes_with_matched_total_weight`、`..._typo_dep_raises_bare_keyerror`、
`..._typo_dep_is_silent_when_not_blocked`、`..._missing_agent_id_keeps_clean_audit`。
2 条**边界锁（修复后仍须成立）**：
`..._executor_dependency_paths_are_structured`、`..._cross_layer_and_result_duplicates_are_rejected`。
另有既有锁 `test_f1_p9_missing_agent_id_is_not_refused`（钉 `ratio=1.0`），若 ③ 选真执行须一并反转。

---

## 3. 可判定命题（请逐条回答，附验证方式与反证条件）

| 编号 | 问题 | 选项（不穷尽，可提第三方案） |
|---|---|---|
| **Q1** | ③ `agent_id.required` 声明无执行者，算什么？ | **(a) 实现漏了** → 真执行：`_trace_audit_issues()` 按 `required` 检查 `trace.agent_id`，缺失判 `unscored/trace_invalid`（`ratio` **1.0 → 0.5**，R2 非 trace 类仍 pass 拿 2 分，R3/R4 被强制改写），并反转既有 P9 锁；**(b) 声明只是文档/采集侧契约** → 不改判定，删或降级该声明；**(c) 第三方案**（请写明，例如只在报告层标 `schema_valid=false` 而不改分数）。**请一并回答**：轨迹层与报告层两个 `agent_id` 是否该合并处理；若选 (a)，「改分数 + 反转既有锁」是否可接受，判据是什么 |
| **Q2** | ② `depends_on` 引用完整性归谁负责？ | **(a) 加载期/组装器**：每个 dep 必须指向本任务存在的断言 id，任意 state 下拼错都在组装期抛含 typo id 的可读 `ReportError`；**(b) 只由执行器负责**，组装器不管（仅补 blocked 分支可读性）；**(c) 别的**。**请判断** `test_dependencies_declared_and_acyclic` 能否替代运行时校验（见 §2.2 第 2 点） |
| **Q3** | ① 层内重复 id 的校验范围与手段？ | **(a) 只补 gate 层**；**(b) gate + credit 两层都查**，抛 `ReportError` 含重复 id，且**不加 schema `uniqueItems`**；**(c) 用 schema `uniqueItems` 解决**。**请核证**：「uniqueItems 锁整条对象相等、不是 id 唯一」是否成立；S2b 是否已证明只补 gate 不够 |
| **Q4** | 落地顺序（一次一小步，每步可独立验证）？ | **(a) ①②③ 一次落地**；**(b) 拆两步**：Step1 = ①+②（都在加载期、**不改任何分数**），Step2 = ③（动结构派生器 + 改分数），每步独立变异 + 评审包两遍重生成；**(c) 别的拆法**。若选 (b)，Step2 除「五夹具分数序仍 `0/0/0.25/0.5/1.0`」还需补哪条**可判定**判据 |
| **Q5** | 盲区与波及面 | 三个子项之外，是否还有第 4 个「配置声明未执行 / 加载期校验缺失」？修复会波及哪些文件与测试？请给**十分钟内可复现的反例或最小验证**，不要泛泛列风险 |

**round-35 订正（Q1(a) 的实际后果）**：(a) 不是「只改判定器」的最小步。本地模拟给
`_trace_audit_issues()` 加 `agent_id` 检查后，raw 里旧的手写 audit
（`schema_valid=true` / `invalid_reasons=[]`）会先被 F1 的 `_refuse_audit_conflict()`
拒绝 → `PipelineError`，不产出报告；只有同步声明 `schema_valid=false` /
`invalid_reasons=['missing_required_field']` 才得到 `ratio=0.5`
（`by_state={pass:2, unscored:3}`、`disqualified=false`）。故 (a) 还须同时决定
平台侧 audit 是否同步、F1 对账是否放行 —— 这支持 Q4 把 ③ 单独拆一步。

---

## 4. 我方倾向（**假设，不是结论**；请给反证条件）

| 编号 | 倾向 | 理由 | 反证条件（成立即推翻） |
|---|---|---|---|
| Q1 | **暂不拍**（本轮发起外评的原因） | 会改分数并反转既有锁，属本地裁判判不了的契约应然；需先分清「实现漏了」还是「声明只是文档」 | 若能证明 `trace.agent_id` 在平台侧**另有采集保证**（不在本仓可见），(a) 不该在本仓执行；若能证明声明纯属注释性质，(b) 成立 |
| Q2 | **(a) 加载期校验** | S3b 非 blocked 完全静默、S4 执行器两路径结构化；静态环检查只覆盖磁盘 YAML，与 F1 的「入口严格拒绝」同构，且不碰分数 | 若能证明 `depends_on` 解析只由执行器负责、组装器不该管，改 (b) |
| Q3 | **(b) 两层都查 + 不用 `uniqueItems`** | S2b 证明只补 gate 不够；`uniqueItems` 语义是整条对象相等 | 若 `uniqueItems` 被证明能锁 id 唯一（给出最小复现），或 credit 层已另有等价拦截 |
| Q4 | **(b) 拆两步** | 一步 = 一个可独立验证的最小单元；Step1 不改分数可先闭合，Step2 单独承担分数变化 | 若 ①② 与 ③ 共享同一处代码路径且拆开会造出中间态矛盾，(a) 更好 |
| Q5 | **缺口精确在「层内配置 + 加载期引用」**（标假设） | S6/S7 已正确拒绝跨层与结果层重复 | 找到第 4 个可复现的配置校验缺口 |

---

## 5. 只读清单（**只给相对路径**，勿给整个仓库）

```text
判定/组装  src/arena/diagnosis_report.py       （rubric_layers / _assertion_item / build_report）
           src/arena/trace_pipeline.py         （_trace_audit_issues / recompute_all）
           src/arena/assertion_evaluator.py    （执行器侧依赖两路径，S4 对照）
契约       tasks/dbg-001-order-report.yaml     （agent_id.required / total_weight=4 / depends_on）
           docs/diagnosis-report.schema.json  （submission.required / assertions minItems）
           docs/diagnosis-report-schema.md
           docs/executor-semantics.md
           docs/data-model.md
夹具       fixtures/raw-perfect.json、raw-empty.json、raw-no-file.json、
           raw-no-trace.json、raw-fake-retry.json
判据       tests/test_trace_pipeline.py        （8 条 test_f9_td020_* + 既有 test_f1_p9_*）
           tests/test_task_yaml.py            （test_dependencies_declared_and_acyclic）
           tests/test_dependencies.py、tests/test_diagnosis_report.py
治理件     HANDOFF.md、docs/review-packet.md
```

**明确不给**：`docs/review-response-v1.md`（内部处置记录）、各历史 `peer-brief` / `verify-brief` /
`redteam-brief-round8.md`、`src/arena/review_packet.py`（内含匿名守卫词表字面量）、
`spike/`（内含本机绝对路径）、`.redteam-tmp/`、`.isofix-f4f5/`、`.git/`、
任何本机绝对路径日志、任何 API key / PAT。
**请只给清单内的文件级只读权限，不要给整个仓库目录**——目录级权限会带出上述文件。

可重放的最小验证（在仓库根，用本仓隔离 venv 的 python）：

```text
pytest tests/ -q                                   # 610 passed
pytest tests/test_trace_pipeline.py -q -k f9_td020  # 8 passed, 155 deselected
python src/arena/trace_pipeline.py --fixture all    # 0 / 0 / 0.25 / 0.5 / 1.0
python src/arena/review_packet.py --check           # OK（维护者侧自报；该脚本不在只读清单内，见上）
```

### 5.1 关于测试数与指纹

本包写出的测试数与指纹是生成时实测。若你看到的数字不同，请先在本地跑一次
`git status --porcelain | wc -l` 与全量测试再下结论（可能是包生成后的正常漂移）。
评审包 `--check` 的指纹同理，**不要把包内写死的指纹当唯一真相**；
另：`_snapshot()` 不含产品源码字节，故 `--check OK` ≠ 源码字节未漂移。

---

## 6. 我们不要什么

- 不要「整体设计合理」这类无判据评价；
- 不要一次性给出全套补丁：本仓纪律是**一步 = 一个可独立验证的最小单元**；
- 不要改动任何文件（只读评审）；若要给补丁，请给**统一 diff 文本**，由维护者在隔离副本里验证；
- 不要按历史包的「待拍板」状态给建议（F1 / F3 / F4 / F5 均已落地）；
- 不要引用仓库外的规则、版本号或竞赛/政策条款——硬事实必须给可核验来源，否则标「假设」；
- 不要把回件自述当验收结论：我方会按「核证 → 登记 → 处置 → 落仓 → 人拍板」处理，
  **AI 不替人拍板**。
