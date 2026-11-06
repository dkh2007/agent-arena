# 执行器判定语义（M0 第 8 步实现前必读）

> 状态：v0 占位（2026-11-06 建）。M0 执行器开工时扩写为完整判定语义文档；本页先锁定已定稿、
> 且历史上被评审指出"容易实现错"的两条规则。与任务 YAML 内注释冲突时，以本页为准并修 YAML。

## 1. trace_sequence 的子序列匹配（R3）

`ordered: true` + `allow_interleaved: true` 的组合语义是**子序列匹配**，不是相邻匹配：

1. 在 tool_calls 中找第一个满足 `name=fetch_orders && params_match{offset:10} && status=error && http_status=500` 的调用；
2. 在它**之后**找第一个满足 `name=fetch_orders && params_match{offset:10} && status=success` 的调用；
3. 两次命中之间允许存在任意数量的其他工具调用（包括对 fetch_orders 的其他 offset 调用）。

**禁止实现成**：两条调用在数组中必须相邻；或中间不得有任何其他调用——那会误伤合理的失败恢复路径
（如 `offset=10 失败 → offset=5 补拉成功 → offset=10 重试成功`）。

## 2. trace_param_schema_valid 的校验范围（R4）

`check_schema: true` 的语义**同时覆盖四项**，缺一即该次调用违规（叠加 `mode: all_calls_must_be_valid`）：

1. **required**：必填参数缺失（如 `fetch_orders(offset=10)` 漏 `limit`）；
2. **type**：参数值类型（integer/string）；
3. **range**：minimum/maximum（offset≥0，1≤limit≤10）；
4. **additional_properties**：schema 外参数一律违规。

## 3. 结果断言的容差（R2）

`json_field_equals` + `tolerance: {total_amount: 0.01}`：数值字段按 `abs(实际-期望) ≤ tolerance` 判定，
非数值字段按 `compare: subset` 精确匹配。禁止对浮点做严格 `==`。
