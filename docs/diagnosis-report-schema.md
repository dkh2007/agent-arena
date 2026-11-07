# 诊断报告 DTO v1（层间契约：评测层 → 状态层）

> **状态**：已落仓并跑通（`src/arena/diagnosis_report.py` + `docs/diagnosis-report.schema.json` + `fixtures/` 三份夹具）。
> **对应步数**：`review-response-v1.md` §4 的 **S2**。完成判据是"用 dbg-001 的假提交产出符合 schema 的 JSON"，已达成。
> **谁写**：评测层（执行器跑完 5 条断言后调用 `build_report()`）。
> **谁读**：状态层（拿 `assertions[].state` + `error_patterns` + `assertion.skill_id` 更新掌握度与错误模式）。
> **不读**：计划书、前端、复习层都不该绕过状态层直接吃这份报告。

---

## 1. 三条设计原则（每条都是踩过的坑）

| # | 原则 | 反例（真实发生过） |
|---|---|---|
| 1 | **只装可判定的观测，不装推断** | 报告若带"建议复习什么"，复习层和报告就会出现两套推荐逻辑 |
| 2 | **契约自解释** | 报告自带 `attribution_policy` / `denominator_policy`；否则状态层必须回读任务 YAML 才知道 `blocked` 算不算掌握度 |
| 3 | **宁可报错，不静默降级** | A2E 在 trace 读不到时静默降级并给 `score=1.0`（`spike/README.md` 2.2）。本 DTO 反着做：trace 不可用 → 强制改写 + 记 `reason_code` |

---

## 2. 顶层结构

| 字段 | 类型 | 说明 |
|---|---|---|
| `report_version` | `"v1"` | 常量。不兼容改动必须升版本，不许原地改语义 |
| `submission` | object | 提交标识：`submission_id` / `task_id` / `task_git_hash` / `agent_id` / `evaluated_at` / `invoke` |
| `submission.task_git_hash` | string(7–40 hex) | 产生本报告的 YAML 版本。**没有它，报告里的断言 id 无法与任务对齐** |
| `submission.invoke` | object | `duration_ms` / `timeout_seconds` / `platform_retries`（后者恒为 0，见下） |
| `trace_audit` | object | trace 前置校验结果，**必填**（即使没上报也要有这一块） |
| `score` | object | 分数聚合，见 §3 |
| `attribution_policy` | object | 从任务 YAML 的 `scoring.attribution` 抄来（去掉人类可读的 `reason`） |
| `assertions` | array | 与 rubric 一一对应，见 §4 |
| `skill_gaps` | array | 技能缺口聚合（不是推断），见 §5 |
| `notes` | array（可选） | 报告层面的显式说明，例如"未上报 trace"。**不允许用 notes 代替 `reason_code`** |

`platform_retries` 恒为 0 是硬约束（schema 里是 `const: 0`）：重试本身是被测能力，平台代劳会让 R3（失败重试策略）永远测不到。

---

## 3. `score`：分母恒为总权重

```json
{ "weighted_sum": 3, "total_weight": 6, "ratio": 0.5,
  "denominator_policy": "fixed_at_total_weight",
  "by_state": { "pass": 3, "fail": 2, "blocked": 0, "unscored": 0 } }
```

- `total_weight` 必须等于 rubric 各条权重之和，`build_report()` 会核对 YAML 里声明的 `scoring.score.total_weight`，不一致直接抛错。
- `ratio = weighted_sum / total_weight`，**分母永不缩小**。这一条同时堵两个漏洞：
  - 缺产物时若从分母剔除 → "不交文件"反而分数虚高；
  - 缺 trace 时若从分母剔除 → "不报 trace"稳拿满分（P2 想堵的那个）。
- `blocked` / `unscored` 对 `weighted_sum` 的贡献都是 0；它们与 `fail` 的区别**只体现在 `attribution_policy`**，不体现在分数。

---

## 4. `assertions[]`：四态 + 证据 + 归因

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` / `type` / `weight` | ✓ | 从 rubric 抄来，不由执行器决定 |
| `state` | ✓ | `pass` / `fail` / `blocked` / `unscored` |
| `skill_id` | ✓ | 可为 `null`（=只计分不归因）。R1/R5 目前是 `null`，见 P5 |
| `error_patterns` | ✓ | 只在 `fail` 时非空，且必须 ⊆ 该断言 YAML 声明的模式 ∩ 词表 |
| `reason_code` | 条件必填 | `blocked` / `unscored` 必须有，枚举见 §6 |
| `evidence` | ✓ | `{observed, expected, extra?}`，`observed`/`expected` 任意类型（可 null） |
| `note` | 可选 | 人类可读补充，不参与任何计算 |

### 4.1 `evidence` 按断言类型的约定（schema 只校验是对象，语义由执行器保证）

| type | `observed` | `expected` | `extra` 里放什么 |
|---|---|---|---|
| `file_exists` | `{exists, size_bytes}` | `{exists, non_empty}` | — |
| `json_field_equals` | 实际读到的字段值 | YAML 的 `expected` | `diffs[]`：逐字段差异（含 tolerance） |
| `trace_sequence` | `{offsets[], statuses[], http_statuses[]}` | YAML 的 `calls[]` | `probe`（JMESPath 表达式）、`matched_pairs`、`why` |
| `trace_param_schema_valid` | `{violations[], checked_calls}` | `{mode}` | 每条违规的 `call_index` / 参数名 |
| `trace_final_status` | 实际 `final_status` | `"completed"` | — |

把"为什么失败"写进 `extra.why` 是有意的：诊断报告会被学员看到，只给一个布尔值等于没诊断。

---

## 5. `skill_gaps[]`：只做聚合，不做推断

```json
{ "skill_id": "failure_retry", "failed_assertions": ["R3_retry_evidence"],
  "patterns": ["retry_wrong_params"], "weight_lost": 1 }
```

- 只收 `state=fail` 且 `skill_id != null` 的断言；`weight_lost` = 这些断言的权重之和（可核验，不是打分公式）。
- `skill_id` 为 `null` 的断言（R1/R5）**故意不进本数组**：它们的失败仍扣分，但不在技能层留痕。
- 掌握度、复习建议都不在这里产生——那是状态层与复习层的事。

---

## 6. `reason_code` 枚举（状态层按它分支，不解析中文）

| code | 含义 | 谁写 |
|---|---|---|
| `trace_not_reported` | Agent 未上报 trace | 组装器强制改写 |
| `trace_invalid` | 上报了但没通过前置校验（call_index 不连续等） | 组装器强制改写 |
| `dependency_blocked` | 前置断言未通过，本断言无法判定 | 执行器 |
| `tool_no_response` | 工具无响应，无法判定该断言 | 执行器 |
| `invoke_timeout` | 整轮调用超时 | 执行器 |

`trace_audit.invalid_reasons` 是另一套枚举（`not_reported` / `call_index_not_sequential` / `timestamp_not_increasing` / `missing_required_field` / `field_type_invalid` / `status_error_without_http_status`），描述"trace 哪里坏了"。

---

## 7. 由 schema 和组装器分别强制的规则

**schema 管结构**（`jsonschema.Draft202012Validator`）：

- S1 顶层字段不多不少（`additionalProperties: false`，逐层都关）。
- S2 `state=fail` ⇒ `error_patterns` 至少 1 条；`state∈{pass,blocked,unscored}` ⇒ 必须为空。
- S3 `state∈{blocked,unscored}` ⇒ 必须有 `reason_code`。
- S4 `trace_audit.reported=false` ⇒ `schema_valid=false` 且 `call_count=0` 且 `invalid_reasons` 非空。
- S5 `denominator_policy` 只能是 `fixed_at_total_weight`。

**组装器管语义**（`build_report()` 抛 `ReportError`，**不产出半成品**）：

| # | 不变量 | 为什么必须在这里 |
|---|---|---|
| I1 | 断言 id 与 rubric 完全一致（不多、不少、不重复） | schema 无法表达"等于另一份文件的集合" |
| I2 | trace 不可用 ⇒ 所有 `trace_*` 断言强制 `unscored` | 调用方可能"忘了"标记；A2E 的坑就是替调用方兜底太宽松 |
| I3 | `fail` 的错误模式 ⊆ 该断言 YAML 声明 ∩ 词表 | 否则会出现"词表里没有的错误码"流进状态层 |
| I4 | `blocked` 必须至少有一个前置断言非 `pass` | 防止用它当"看不清就不判"的逃生口 |
| I5 | `total_weight` 与 rubric 权重之和一致 | 权重被改而没同步声明是静默错账 |
| I6 | `weighted_sum` 只统计 `pass` | 四态里只有 pass 加分，写在代码里而不是文档里 |

---

## 8. 三份夹具的实测结果（这是本步的证据）

| 夹具 | 场景 | ratio | 关键行为 |
|---|---|---|---|
| `fixtures/raw-fake-retry.json` | 游标没推进的假重试 | **0.5**（3/6） | R2 `fail`→`wrong_total_amount`+`wrong_valid_count`；R3 `fail`→`retry_wrong_params`；两个技能缺口各记 `weight_lost` 2 和 1 |
| `fixtures/raw-no-trace.json` | 未上报 trace，raw 里**故意把轨迹断言写成 pass** | **0.5**（3/6） | 组装器把 R3/R4/R5 强制改写为 `unscored` + `trace_not_reported`，`notes` 说明原因。**这就是 A2E 的反面** |
| `fixtures/raw-no-file.json` | 没写 report.json | **0.5**（3/6） | R1 `fail`→`no_output_file`；R2 因 `depends_on` 记 `blocked`；`skill_gaps` **为空** |

复现：`python src/arena/diagnosis_report.py --fixture fixtures/raw-fake-retry.json`

### 8.1 这一步暴露的问题（必须记下来，不许当没看见）

**三种质量完全不同的提交拿到同一个 0.5 分。** 这是 DTO 忠实实现后的必然结果，不是 bug：

| 提交 | 实际质量 | 分数 |
|---|---|---|
| 假重试（拉了一半数据就交） | 差：结果错 + 过程错 | 0.5 |
| 无 trace（结果对，但过程不可核验） | 中：结果对 | 0.5 |
| 无文件（过程全对，产物没交） | 差：任务未完成 | 0.5 |

分数分辨率不够，说明**权重与门禁需要单独设计**（S4），不能靠改 DTO 解决。见 **P6**。

同时 `raw-no-file` 的 `skill_gaps` 是空数组——"没交产物"这件事在技能层完全不留痕，因为 R1 的 `skill_id` 是 `null`。这条路让 **P5** 从"措辞问题"升级成"诊断盲区"。

---

## 9. 待拍板（接 `review-response-v1.md` §3 / §5.6）

| # | 判断题 | 我的建议 | 不拍板的后果 |
|---|---|---|---|
| P4 | 分数分母恒为 6（缺 trace 最多 50%） | 恒定 | 有博弈空间 |
| P5 | R1/R5 无技能归属 | **新增第 4 个技能"任务交付纪律"**（R1/R5 都归它）。理由见 §8.1：留 null 会让"不交产物"在技能层看不见 | 诊断有盲区，学员反复漏交却推不出复习项 |
| **P6（新）** | 分数分辨率不足（三种提交同为 0.5） | **加完成门禁**：R1 未过 ⇒ `ratio` 记 0（断言仍逐条记录供诊断）。或把 R2 权重提到 5 | 学员会学到"过程好看就行" |

---

## 10. v1 故意不做的事

- 不做 `review_items`（复习建议）：跨层推断，属复习层。
- 不做 `mastery_delta`：掌握度公式还没拍板（P1）。
- 不做多语言文案：`note` 里的中文是给人看的，机器只读 `state` / `error_patterns` / `reason_code`。
- 不做报告签名/防篡改：M1 再说。
