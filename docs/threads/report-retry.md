# retry-drill 加题报告（ret-005 / ret-006）

> 线程 T4，分支 `thread/retry`，基线 `b886e21`。执行人：主线程（需求方指定自做 T4）。
> 题材槽未填，按指令用示例题材，并在 §C 注明。

## A. 改了什么

```
 tasks/ret-005-export-poll.yaml | 77 ++++++++++++++++++++++++++++++++++++
 tasks/ret-006-recon-page4.yaml | 88 ++++++++++++++++++++++++++++++++++++++++++
 tests/test_derivation.py       |  2 +-
 tests/test_task_variants.py    |  2 +-
 4 files changed, 167 insertions(+), 2 deletions(-)
```

两行计数改动（只这两处，其余 `tests/` 未动）：

| 文件 | 改动 |
|---|---|
| `tests/test_task_variants.py:294` | `assert len(all_tasks) == 13` → `== 15` |
| `tests/test_derivation.py:89` | `assert checked == 11` → `== 13` |

## B. 验收结果

```
# 第一档（四个文件）
187 passed in 34.61s

# 第二档（参考 Agent 满分 + 放弃重试判 no_retry）
30 passed, 21 deselected in 18.30s
```

与指令预测的 187 / 30 完全一致。

### §5 故障自检（每题每个合法 limit 都恰好触发一次）

`ret-005`（25 行 / 页上限 10 / `offset_covers: 22`）：

```
limit= 1 hits=[22]    limit= 6 hits=[18]
limit= 2 hits=[22]    limit= 7 hits=[21]
limit= 3 hits=[21]    limit= 8 hits=[16]
limit= 4 hits=[20]    limit= 9 hits=[18]
limit= 5 hits=[20]    limit=10 hits=[20]
```

`ret-006`（33 行 / 页上限 10 / `offset_covers: 31`）：

```
limit= 1 hits=[31]    limit= 6 hits=[30]
limit= 2 hits=[30]    limit= 7 hits=[28]
limit= 3 hits=[30]    limit= 8 hits=[24]
limit= 4 hits=[28]    limit= 9 hits=[27]
limit= 5 hits=[30]    limit=10 hits=[30]
```

两题在 limit 1..10 下**均恰好 1 个元素**，无空集、无重复触发。

### 变异验证（3 处，全部命中预期消息）

| 变异 | 改法 | 报出的消息 |
|---|---|---|
| 期望值单处 | `ret-005` 的 `test_inputs.expected.valid_count` 21→22 | `R2 期望值与 test_inputs.expected 不一致（同一件事写了两处，必须相等）` |
| 故障码单处 | `ret-006` 的 `fault_injection.http_status` 500→502 | `R3 与 fault_injection 的故障码不一致` + `未拿满分：R3_retry_evidence: fail` |
| 故障位单处 | `ret-005` 的 R3 `params_match.offset_covers` 22→23 | `R3 与 fault_injection 的匹配条件不一致（{'offset_covers': 23} vs {'offset_covers': 22}）` |

三处改回后重跑第一档：**187 passed**。

### 期望值现算核对（不靠手算）

两题都用脚本按剔除规则重算，与答案键逐字段相等：

- `ret-005`：`valid_count=21`、`total_amount=44050.0`
- `ret-006`：`valid_count=28`、`total_amount=34812.5`

## C. 三个问题（证明"不是只换了业务名"）

**① 学习者要处理的东西数量变了吗？** 变了。

| | 行数 | 页数 | 故障位置 | 失败后同页待取 |
|---|---|---|---|---|
| ret-001（既有） | 20 | 2 | 第 2 页首条 | 无 |
| ret-004（既有） | 30 | 6 | 第 5 页 | 1 页 |
| **ret-005** | 25 | 3 | 第 3 页第 3 条 | 3 条（含 1 条无效） |
| **ret-006** | 33 | 4 | 第 4 页第 2 条 | 2 条（1 有效 1 无效） |

关键差异不在行数，而在**故障点之后同一页里还有有效数据**——"重试完就交"会算错，
而 ret-001/002/003 的故障都在页尾附近，这个问题暴露不出来。

**② 故障或异常的种类变了吗？** **没有变，这是本批的弱项，如实记录。**

`ret-005` 用 503 `service_unavailable`（与 ret-001 同码），`ret-006` 用 500 `internal_error`
（与 ret-002/003 同码）。原因是现有题只出现过 500 / 502 / 503 三个值，retry 家族已有 4 道，
**在不让它们全部重复的前提下无法再错开**。见 §D 第 1 条。

**③ 决策点变了吗？** 变了。

两题都要求学习者在"已经翻了两三页、主观上接近完成"时处理失败，并且失败后必须判断
"这一页要不要重拉、拉到哪一条"。ret-006 的第 4 页还刻意放了"有效 / 有效 / 无效"的结构：
只补故障那一条，`valid_count` 会多 1（把末行的 null 算进去）；整页重拉才对。

**题材说明**：需求方未指定题材，按 T4 指令 §6 使用示例：
`ret-005` = 导出任务轮询、`ret-006` = 对账明细。两者都只是外壳，
`orders_fixture` 键名与剔除规则、聚合口径未变。

## D. 我不确定的地方

1. **故障码无法全部错开**（承 §C②）。只有 3 个可用值、家族已有 4 道题，
   要让新题与全部既有题都不同码，就得新增 http_status 取值——那要动 `mock_tools`，
   超出本线程范围。我选择"与家族内部分题重复"，并把这条留给你判。
2. **难度 2 与 3 的区分只靠页数**（3 页 vs 4 页）。若你认为"决策链长度"才是难度，
   ret-005（故障后同页 3 条待取）其实不比 ret-006 简单多少。这条只有学习者数据能判，
   我没有判据，也不打算自己定。
3. **两题故障点之后的结构相似**（都是"同页还有数据"）。这是有意为之（同一考点两种规模），
   但如果你要的是"不同考点"，这两道算重复，需要重做一道。
4. **"每题 +8 条"这个增量是否稳定**：171→187 恰好 +16，与指令预测一致，
   但样本只有这 2 题，我没法断言它对所有题型成立。
5. **第二档 `own_failure` 用的是参考 Agent 的默认页大小**，没有遍历 10 种 limit；
   我用 §B 的手写脚本补了覆盖，但那是脚本结论，不是测试结论——脚本本身没有留在仓库里。
