# param-surface 加题报告（prm-004）

> 线程：T2（`docs/threads/T2-param.md`）　分支：`thread/param`　日期：2026-11-22
> 编号段：`prm-004`（只用这一个）
> 说明：该线程由主线程代做（外部会话未启动）。

## A. 改了什么

```
 tasks/prm-004-return-batch-6.yaml | 81 ++++++++++++++++++++++++++++++++++++++++
 tests/test_derivation.py          |  2 +-
 tests/test_task_variants.py       |  2 +-
 3 files changed, 83 insertions(+), 2 deletions(-)
```

（本报告 `docs/threads/report-param.md` 是另一个待提交文件，不在上面的 stat 里。）

两处测试改动（§3 允许的那两行，含断言消息同步）：

```diff
@@ tests/test_task_variants.py:294
-    assert len(all_tasks) == 13, f"任务库应为 13 个任务，实际 {len(all_tasks)}"
+    assert len(all_tasks) == 14, f"任务库应为 14 个任务，实际 {len(all_tasks)}"

@@ tests/test_derivation.py:89
-    assert checked == 11, f"只有 {checked} 个任务声明了 derivation，应为 11（母本 + 10 变体）"
+    assert checked == 12, f"只有 {checked} 个任务声明了 derivation，应为 12（母本 + 11 变体）"
```

`task_version` 写成 `"2026-11-22.1"`：本题改了工具 schema（`maximum` 4/5 → 6）与期望值，
按 `docs/authoring-tasks.md` 第 2 步「改断言、改期望值、改工具 schema 必须抬版本号」。
同家族前三道一律继承母本的 `2026-11-15.1`，是历史写法；如果约定是「变体一律继承」，删掉这一行即可。

## B. 验收结果

基线（动手前）：`171 passed in 30.11s`

第一档：

```
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
179 passed in 32.95s
```

第二档：

```
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
28 passed, 21 deselected in 17.10s
```

故障覆盖自检（合法 limit 1..6，每个都必须恰好触发一次）：

```
rows=18 ceiling=6 pages=3
现算=(14, 13140.5)  答案键=(14, 13140.5)  一致
故障位 offset_covers=13
  limit= 1  hits=[13]
  limit= 2  hits=[12]
  limit= 3  hits=[12]
  limit= 4  hits=[12]
  limit= 5  hits=[10]
  limit= 6  hits=[12]
故障覆盖: 全部恰好一次 ✅
```

变异验证（改一处必须报错，否则等于没有判据）：

| 改坏的地方 | 报出的消息 |
|---|---|
| `expected.valid_count` 14→15（单处） | `prm-004-return-batch-6: R2 期望值与 test_inputs.expected 不一致` |
| `tools.limit.maximum` 6→5（题面仍写 6） | `分页上限 6 与 limit.maximum 5 不一致` + `文案没写清页大小上限 5` |
| `fault_injection.match.offset_covers` 13→14 | `R3 与 fault_injection 的匹配条件不一致` |

三处改回后复跑 179 全绿。

## C. 三个问题

**1. 学习者要处理的东西数量变了吗？**

**变了，但方向是「退」——这是本题最该被质疑的地方。**

| 题 | 行数 | 上限 | 页数 | 难度 |
|---|---|---|---|---|
| prm-001 | 20 | 5 | 4 | 1 |
| prm-002 | 30 | 5 | 6 | 2 |
| prm-003 | 20 | 4 | 5 | 3 |
| **prm-004（本题）** | **18** | **6** | **3** | **2** |

行数与页数都是家族最少。难度 2 的判据是「至少 3 页」（`test_difficulty_is_not_decorative`），
本题刚好踩在下限，而同为难度 2 的 prm-002 要翻 6 页。**难度字段在这道题上几乎没有区分度。**

一处真实的增量：剔除原因里 `null` 出现两次（`rt-005` 第二页、`rt-016` 第三页），
这是家族内首次「同一剔除原因跨页重复」。写成「遇到 null 就 break」的学习者会漏掉 `rt-017` / `rt-018`，
算出的 `valid_count` 是 12 而不是 14。这一条算数量维度的唯一贡献。

**2. 故障或异常的种类变了吗？**

- 故障码 `502` 是 param-surface 家族首次（prm-001 `500` / prm-002 `503` / prm-003 `500`）。
  全库范围内 `502` 已被 `ret-004` 用过，所以这不是全新的异常种类，只是本家族首次。
- 故障位 `offset_covers: 13` 是全库首次（已用 5 / 8 / 10 / 15 / 20 / 30）。

**3. 决策点变了吗？**

变了，但**变弱了**，原因是本题的取舍：

- 上限 6 能整除 18（恰好 3 页），所以不存在「最后一页不满」这种碎片页。
  而 prm-003（20 / 4）与 prm-002（30 / 5）都会留碎片页，学习者必须处理「offset 越界返回空」。
  本题把这个坑主动去掉了 —— 这是为了满足指令 §2「≥13 行且上限 6」的硬指标而付的代价。
- 保留的决策点：第一次调用前就要从题面读出「1 到 6」；故障落在第三页第 2 条，
  重试若只补那一条而不重拉整页 `[12,18)`，`rt-016`（null）之后的两条会被漏掉。

## D. 我不确定的地方

1. **本题是家族里最浅的一道，标难度 2 是靠判据下限过关的**（3 页 vs prm-002 的 6 页）。
   如果要让它真有难度梯度，两个改法：行数提到 24（4 页）并标难度 3；或保持 3 页但把难度降成 1
   ——后者会和 prm-001 撞。**我按指令原文（难度 2 + 上限 6 + ≥13 行）做的，没有自作主张改指标**，
   但这一条需要人确认是否接受。
2. **上限 6 整除总行数**，使得本题没有碎片页。若「参数校验」这一技能的核心坑是碎片页，
   本题的考查力度会低于 prm-002/003。
3. **`task_version` 抬到了 `2026-11-22.1`**，与同家族三道的 `2026-11-15.1` 不一致。
   理由是规范要求改 schema 就抬版本；如果约定是继承母本，删这一行即可（不影响任何测试）。
4. **题材用了「退货入库」，字段仍是 `order_id` / `amount`**。指令 §6 的示例是物流轨迹
   （`tracking_id` + 签收时间），但那样剔除规则要跟着改字段，而 §6 明令「剔除规则不能改」。
   为不破坏 R2 断言类型，我保留了 `order_id` / `amount`，只换了业务说法。
5. **没跑全量**（`pytest tests -q`）。按 §5 禁止。
