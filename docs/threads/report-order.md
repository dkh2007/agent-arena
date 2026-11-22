# order-report 加题报告（dbg-005 / dbg-006）

> 线程：T3（`docs/threads/T3-order.md`）　分支：`thread/order`　日期：2026-11-22
> 编号段：`dbg-005` / `dbg-006`（只用这两个）
> 说明：该线程由主线程代做（外部会话未启动）。

## A. 改了什么

```
 tasks/dbg-005-return-credit.yaml    | 71 ++++++++++++++++++++++++++++++++
 tasks/dbg-006-stock-diff-audit.yaml | 82 +++++++++++++++++++++++++++++++++++++
 tests/test_derivation.py            |  2 +-
 tests/test_task_variants.py         |  2 +-
 4 files changed, 155 insertions(+), 2 deletions(-)
```

（本报告 `docs/threads/report-order.md` 是另一个待提交文件，不在上面的 stat 里。）

两处测试改动（§3 允许的那两行，含断言消息同步）：

```diff
@@ tests/test_task_variants.py:294
-    assert len(all_tasks) == 13, f"任务库应为 13 个任务，实际 {len(all_tasks)}"
+    assert len(all_tasks) == 15, f"任务库应为 15 个任务，实际 {len(all_tasks)}"

@@ tests/test_derivation.py:89
-    assert checked == 11, f"只有 {checked} 个任务声明了 derivation，应为 11（母本 + 10 变体）"
+    assert checked == 13, f"只有 {checked} 个任务声明了 derivation，应为 13（母本 + 12 变体）"
```

两个新题都没有 `tools:` 块（页上限 10 从母本继承），没有 `rubric_gate` / `scoring` /
`trace_requirements` / `submission_requirements`（继承），也没有 `error_pattern_detection`
（母本 cases 已是相对谓词）。
`task_version` 写 `"2026-11-22.1"`：改了故障位与期望值，按规范抬版本；同家族四道仍是 `2026-11-15.1`。

## B. 验收结果

基线（动手前）：`171 passed in 30.64s`

第一档：

```
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
187 passed in 34.68s
```

第二档：

```
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
30 passed, 21 deselected in 18.08s
```

故障覆盖自检（合法 limit 1..10，每个都必须恰好触发一次）：

```
=== dbg-005-return-credit ===
rows=24 ceiling=10 pages=3 difficulty=2
现算=(19, 17915.5)  答案键=(19, 17915.5)  一致
故障位 offset_covers=18
  limit= 1  hits=[18]      limit= 6  hits=[18]
  limit= 2  hits=[18]      limit= 7  hits=[14]
  limit= 3  hits=[18]      limit= 8  hits=[16]
  limit= 4  hits=[16]      limit= 9  hits=[18]
  limit= 5  hits=[15]      limit=10  hits=[10]
故障覆盖: 全部恰好一次 ✅

=== dbg-006-stock-diff-audit ===
rows=34 ceiling=10 pages=4 difficulty=3
现算=(27, 52031.25)  答案键=(27, 52031.25)  一致
故障位 offset_covers=27
  limit= 1  hits=[27]      limit= 6  hits=[24]
  limit= 2  hits=[26]      limit= 7  hits=[21]
  limit= 3  hits=[27]      limit= 8  hits=[24]
  limit= 4  hits=[24]      limit= 9  hits=[27]
  limit= 5  hits=[25]      limit=10  hits=[20]
故障覆盖: 全部恰好一次 ✅
```

变异验证（改一处必须报错）：

| 改坏的地方 | 报出的消息 |
|---|---|
| dbg-005 `expected.valid_count` 19→20 | `R2 期望值与 test_inputs.expected 不一致` + `现算 {..19..} != YAML {..20..}` |
| dbg-006 题面「共 34 条」→「共 35 条」 | `文案里总条数与夹具 34 不一致` |
| dbg-006 `offset_covers` 27→28（仅故障块） | `R3 与 fault_injection 的匹配条件不一致` |
| dbg-005 `difficulty` 2→3（仍 3 页） | `标了难度 3，但只需翻 3 页（难度 3 至少要 4 页）` |

四处改回后复跑 187 全绿。

## C. 三个问题

**1. 学习者要处理的东西数量变了吗？**

**没有实质变化——这是我要先说清的一件事。** 同家族六道的实测对比：

| 题 | 行数 | 页数 | 难度 | 剔除行 | 故障位 | 故障码 |
|---|---|---|---|---|---|---|
| dbg-001（母本） | 20 | 2 | 1 | 3 | 10 | 500 |
| dbg-002 | 20 | 2 | 1 | 4 | 10 | 503 |
| dbg-003 | 30 | 3 | 2 | 5 | 10 | 500 |
| dbg-004 | 40 | 4 | 3 | 6 | 10 | 500 |
| **dbg-005（本题）** | 24 | 3 | 2 | 5 | **18** | 500 |
| **dbg-006（本题）** | 34 | 4 | 3 | 7 | **27** | 500 |

- dbg-005（24 行 / 3 页 / 剔除 5）与 dbg-003（30 行 / 3 页 / 剔除 5）**几乎持平**，还少了 6 行。
- dbg-006（34 行 / 4 页 / 剔除 7）与 dbg-004（40 行 / 4 页 / 剔除 6）也基本持平，少 6 行但多 1 行剔除。
- 唯一的新增是 dbg-006 的剔除行数（7，家族最多），以及两题都让三类无效原因同时出现（null / 缺字段 / 负数各至少一次）。

按「页数 → 难度」这条唯一被测试承认的口径，这两题只是把 dbg-003 / dbg-004 的位置占了第二遍。

**2. 故障或异常的种类变了吗？**

**故障码没变，故障位置是真增量。**

- 故障码仍是 `500`（家族里 dbg-001/003/004 都是 500，只有 dbg-002 用 503）。
- **但故障位是全家族第一次离开第 10 条**：此前 dbg-001/002/003/004 全部卡在 `offset_covers: 10`，
  也就是「第二页第一条」。这意味着此前四条汇报题里，学习者只要形成「故障总在第 2 页开头」的定势，
  就能在完全没读题的情况下按同一套流程走完。
  dbg-005 挪到 18（第三页第 9 条）、dbg-006 挪到 27（第四页第 8 条），这个定势失效了。

这是这两题**唯一**真正的增量，我认为也是它们值得存在的主要理由。

**3. 决策点变了吗？**

变了，而且正是由故障位变化带来的：

- **dbg-005**：故障在第三页第 9 条（`limit=10` 时只剩第 10 条未取）。学习者要决定：重试这一页、
  还是重试单条。选「重试单条」的话，`offset` 会写成 18 而不是 10 —— 这不是错题，但会掉进
  `fault_then_moved_on` 之外的路径，且第三页剩下的数据要靠额外一次请求补齐。
- **dbg-006**：故障在第四页第 8 条，后面还有 6 条，其中 `sku-a32`（缺字段）与 `sku-a33`（null）
  是无效行。若重试后只补故障那一条、不重拉整页，`valid_count` 会多算 —— 少剔除 2 行，得 29 而不是 27。
  这个错误的特征是**分数只差 2**，不像整页缺失那样一眼可见。
- 对照 dbg-003/004：故障在第二页首条，重试策略选错的代价小得多（后续页还没开始拉）。

## D. 我不确定的地方

1. **数量维度基本没有增量**（见 C-1）。dbg-005 与 dbg-003 同难度同页数、行数还更少；
   dbg-006 与 dbg-004 同难度同页数。如果「加题」的标准是「必须带来新的处理规模」，
   这两道都不合格，需要把它们拉到 45 / 55 行（5 / 6 页）才行 —— 但那会突破家族现有的难度上限 3
   （测试只认 1/2/3），得先扩难度口径，属方式 C。
2. **故障码没能错开**。家族里 500 已经出现三次（母本 / dbg-003 / dbg-004），我这两道又各加一次。
   要真正错开需要 `mock_tools` 支持新状态码（现有只有 500 / 502 / 503），超出加题范围。
3. **两题的考点高度相近**。都是「故障发生在非首条、需要重拉整页」，只是位置不同。
   如果按「一道题一个考点」的标准，dbg-005 与 dbg-006 可视为同一考点的两个规模。
   我按「同考点两规模」保留；要换考点需重做一道。
4. **`task_version` 抬到 `2026-11-22.1`**，与家族四道的 `2026-11-15.1` 不一致。
   理由见 §A；若约定是继承母本，删掉这两行即可（不影响任何测试）。
5. **剔除以 `amount` 单字段为准，业务对象换了但字段名没换**。指令 §6 的示例提到
   `return_id` / `sku_id`，但母本 `derivation` 的 `distinct_by` 是 `order_id`，
   改键名会让派生口径对不上。所以我把 `rtn-*` / `sku-*` 的值放进 `order_id` 键里，
   业务说法换了、字段名保住。严格说这有点别扭（`order_id: sku-a01`），
   如果之后要真支持自定义键名，得改 `derivation` 口径（方式 C）。
6. **没跑全量**（`pytest tests -q`）。按 §5 禁止。
