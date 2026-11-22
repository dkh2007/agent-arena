# 执行器判定语义（M0 第 8 步实现前必读）

> 状态：v0（2026-11-06 建，round-24 补 §4，round-32 补 §5）。本页锁定已定稿、且历史上被评审指出
> "容易实现错"的规则。与任务 YAML 内注释冲突时，以本页为准并修 YAML。

## 1. trace_sequence 的子序列匹配（R3）

`ordered: true` + `allow_interleaved: true` 的组合语义是**子序列匹配**，不是相邻匹配：

1. 在 tool_calls 中找第一个满足 `name=fetch_orders && params_match{offset_covers:10} && status=error && http_status=500` 的调用；
2. 在它**之后**找第一个满足 `name=fetch_orders && params_match{offset_covers:10} && status=success` 的调用；
3. 两次命中之间允许存在任意数量的其他工具调用（包括对 fetch_orders 的其他 offset 调用）。

**`offset_covers: N` 是「页区间覆盖」，不是「offset 等于 N」（B-31，2026-11-22）**：
命中条件是 `offset <= N < offset + limit`，即这次请求的页里含第 N 条数据。理由：
`limit` 本就允许 1..上限，学习者用 `limit=3` 时偏移序列是 0/3/6/9…，`offset: 10`
的精确相等永远不成立——他把 20 条数据拉全了，故障却从不触发，R3 判「没重试」。
旧写法等于把「必须按上限分页」偷偷写进了评分标准。

实现分两处，**不能合并**：jmespath 没有算术（`params.offset + params.limit` 直接
抛 `LexerError`），所以 `_filter_expr()` 只按 name 过滤、把 `offset_covers` 的数值
作为第二个返回值交给 Python 侧的 `page_covers()` 过滤；过滤发生在**匹配步骤之前**
（投影下标是 `_match_step` 游标的基准，事后再筛会错位）。

配套的一条硬约束：**非法请求不注入故障**。`offset=0, limit=11` 的区间也覆盖第 10 条，
若先注入故障，错误码会从 `400 invalid_limit` 变成 `500`。所以 `MockToolService.call()`
把 `_pagination_problem()` 前置到 `_take_fault()` 之前——真实服务端同样是先校验再处理。

**禁止实现成**：两条调用在数组中必须相邻；或中间不得有任何其他调用——那会误伤合理的失败恢复路径
（如 `offset=10 失败 → offset=5 补拉成功 → offset=10 重试成功`）。

**条件必填的层归属（round-59，层错位改述）**：任务 YAML `trace_requirements.minimum.tool_calls.note`
中「`http_status` 仅 `status=error` 必填」**由执行器层承担，不由结构层校验**：R3 的
`_ambiguous_step()` 在「失败步 spec 声明了 `http_status`、候选调用缺该字段」时判
`unscored / trace_invalid`；`_trace_audit_issues()` 不读条件必填，`audit.schema_valid`
不因此变 false（§28.6 D2「仅执行器层，audit 不同步」）。注意 note 的条件式表述与执行器
实际口径不等价：执行器按**失败步 spec 声明的字段**取输入，不按 `status=error` 做全量
条件必填。

**`error` / `result_summary` 不做条件必填（B-22，2026-11-18 关闭）**：原先 note 里那句
「仅对应 `status` 必填」是一条**没有执行者**的声明（TD-026），两个选项里选了**删声明**：

- 判定链不读这两个字段——`_ambiguous_step()` 只把 `params_match` 的键与 `http_status`
  当匹配输入；这两个字段只进 `llm_judge` 的投影和 `reconciliation` 对账，属**诊断线索**。
- 若改成条件必填，缺文案的失败步会被判 `unscored / trace_invalid`。那是「没写说明」，
  不是「判据缺输入」——`unscored` 的定义是有判据没数据，强行借用会让这个状态码失真。
- 顺带纠正一处记错的代价：**补执行者并不推翻 D2**（D2 要的正是校验放在执行器层、
  audit 不同步）。真正的代价是它会改变「缺 `error` 的失败步」的判定——那才是要人拍的东西。

重开条件：若将来某条断言真的需要读 `error` 文案（例如判错误信息是否含关键线索），
应当在**断言的 expected 里声明**，而不是回到 trace 结构层做条件必填。

## 2. trace_param_schema_valid 的校验范围（R4）

`check_schema: true` 的语义**同时覆盖四项**，缺一即该次调用违规（叠加 `mode: all_calls_must_be_valid`）：

1. **required**：必填参数缺失（如 `fetch_orders(offset=10)` 漏 `limit`）；
2. **type**：参数值类型（integer/string）；
3. **range**：minimum/maximum（offset≥0，1≤limit≤10）；
4. **additional_properties**：schema 外参数一律违规。

## 3. 结果断言的容差（R2）

`json_field_equals` + `tolerance: {total_amount: 0.01}`：数值字段按 `abs(实际-期望) ≤ tolerance` 判定，
非数值字段按 `compare: subset` 精确匹配。禁止对浮点做严格 `==`。

---

## 4. 结构校验与语义 `trace_invalid` 的分界（round-24 / F1）

`trace_audit.schema_valid` 只表示 **trace 结构是否可用**，不表示任务判定是否通过。
两者可能共用同一个 `unscored / trace_invalid` 结果码，但来源不同，**不能互相推导**：

| | 结构校验（`_trace_audit_issues`） | 执行器语义判定 |
|---|---|---|
| 范围 | `trace_requirements.minimum` 已声明：`tool_calls` 数组、`call_index` 从 1 连续、`status ∈ status_enum`、`params` 是对象、`final_status` 必填且在枚举内 | R3 子序列投影、R4 工具参数 schema、未知工具名、probe 投影丢失 |
| 影响 | `schema_valid` / `invalid_reasons` | 单条断言的 `state` / `reason_code` |
| 例子 | `call_index=9` → `invalid_reasons=["call_index_not_sequential"]` | 未知工具 → R4 `unscored/trace_invalid`（**无已观测违规时**，见 §5.1），但结构合法、`schema_valid=true` |

**硬规则**：派生 `schema_valid` 时**不得**扫描断言结果里的 `trace_invalid` 字面值。
否则未知工具（P-B 已拍板的 `unscored` 口径）会被误判成结构损坏，
`test_unknown_tool_stays_unscored_with_evidence` 会真 `FAILED`（回件 §5.4 M5 实测）。

**`agent_id` 不在结构校验范围内**（TD-020 ③ 拍板 b）：agent 身份以报告层
`submission.agent_id` 为准（DTO 必填），`trace.agent_id` 只是可选元数据，
没有任何断言消费它。缺失或与 `submission.agent_id` 不一致都不改变
`schema_valid` / `invalid_reasons` / 分数；因此 `trace_requirements.minimum`
不再声明 `agent_id.required`，避免留下没有执行者的声明。

同理，R4 的参数 schema 违规属于任务语义，不进 `invalid_reasons`；平台若将来要加
时间戳单调性 / `status=error` 条件必填等更严规则，须先写进 `trace_requirements`，
由 `call_sequence_problems()` 单一内核供执行器与 F1 派生器共用（TD-024 已收敛，
§55 / §57），否则 R1 严格对账会误伤合法提交。

---

## 5. 未知工具、目标工具与受检口径（round-32 / F4-F5）

### 5.1 未知工具不屏蔽已观测违规（F4）

`trace_param_schema_valid`（R4）判定时：

1. **已知工具的违规照判 `fail`**，分类照旧（`param_out_of_range` 等，按 YAML cases 首命中）；
2. `unknown_tools` **降为 evidence 注记**，不再短路整个断言、不再丢弃 `violations`；
3. **仅当 `violations` 为空且存在未知工具** → `unscored / trace_invalid`（P-B 口径的
   精确边界：「无已观测违规」的未知工具才免判）。

禁止实现成「有未知工具就整体 unscored」——那会把已观测的能力失败一起屏蔽掉
（TD-019a，round-29 F4-S2 复现的缺陷）。

### 5.2 `target_tools` 与受检口径（F5）

1. `expected.target_tools` **必须是非空字符串数组**，且每个名字须在
   `expected.schema_ref` 指向的工具表覆盖范围内；缺失/空/越界一律加载期 + 判定期
   fail-fast（`EvaluatorError`），禁止静默回落。
2. `checked_calls` 的语义是**受检调用数** = 总调用数 − 未知工具数（不是总调用数）。
3. **无目标工具受检调用**（`target_calls == 0`，含空集）→ `unscored / trace_invalid`，
   不计掌握度、不产出错误模式。空集不再平凡 `pass`（TD-019b）。
4. 目标工具的违规照判 `fail`；目标工具集合外的已知工具调用不参与 R4 判定。

「没调目标工具」目前复用 `trace_invalid`（语义借码，TD-030）；若将来需要独立信号
（如 `no_target_call`），走 DTO 枚举扩展惯例（TD-008）单开一步，不在本页改。
