# locate-drill 加题报告（fix-003 / fix-004）

> 线程：T1（`docs/threads/T1-locate.md`）　分支：`thread/locate`　日期：2026-11-22
> 编号段：`fix-003` / `fix-004`（只用这两个）

## A. 改了什么

```
 tasks/fix-003-tz-window-mismatch.yaml | 109 +++++++++++++++++++++++++++++++
 tasks/fix-004-cache-lost-update.yaml  | 117 ++++++++++++++++++++++++++++++++++
 tests/test_task_variants.py           |   2 +-
 3 files changed, 227 insertions(+), 1 deletion(-)
```

（本报告 `docs/threads/report-locate.md` 是同一 commit 的第四个文件，故不在上面的 stat 里。）

测试文件唯一改动（§3 允许的那一行）：

```diff
@@ -294 +294 @@ def test_every_skill_is_primary_somewhere(all_tasks):
-    assert len(all_tasks) == 13, f"任务库应为 13 个任务，实际 {len(all_tasks)}"
+    assert len(all_tasks) == 15, f"任务库应为 15 个任务，实际 {len(all_tasks)}"
```

| 文件 | 难度 | 根因 | 出错行 | 日志 ERROR 条数 | 故障位 |
|---|---|---|---|---|---|
| `fix-003-tz-window-mismatch.yaml` | 2 | `type_mismatch` | 22 | 3 | **读日志**第一次 503 |
| `fix-004-cache-lost-update.yaml` | 3 | `other` | 27 | 4 | **读源码**第一次 503 |

两题都照 `fix-002` 的结构写：只覆盖 `scenario` / `variant_meta` / `fault_injection` /
`locate` / `test_inputs` / `rubric_credit` 里 R2、R3 的 `expected`，`rubric_gate`、`scoring`、
`trace_requirements`、`submission_requirements` 一个字没写（继承母本）。
`error_pattern_detection` 按 §4 的说明**没有重写**（B-31 之后母本的 cases 已与页大小/故障位置无关，
继承即可；`fix-002` 里那一段是历史遗留）。

行号不是估的：两题的 `_diagnose(log, src)` 都用参考 Agent 的同一段代码现算过（`test_expected_values_match_the_fixture`
每次跑都会再算一遍），本报告里的 22 / 27 与现算值一致。

## B. 验收结果

**先说一件影响读法的事**：主工作区 `arena-t-locate` 里此刻有**四个** fix-003/004 文件——
我写的两个，加另外两个同编号文件（`fix-003-tz-mixed-reconcile.yaml`、`fix-004-lockless-overwrite.yaml`，
写入时间 13:54:39 与 13:54:42，和我的两次写入交错）。四条线程里只有 T1 用 fix-003/004，
所以这是**同一条线程被并发跑了第二遍**，不是别的编号段串味。详见 §D-1。

我没有删除、也没有移动那两份文件。因此下面两档是在**同内容的隔离副本**里跑的
（`%TEMP%/locate-verify-t1`，从本工作区整份复制后只去掉那两份外来文件，任务数 = 15）：

第一档：

```
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
187 passed in 34.22s
```

第二档：

```
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
30 passed, 21 deselected in 18.13s
```

两档全绿，条数与线程预期一致（187 / 30）。对照基线（加题前，本工作区）：

```
171 passed in 29.74s
```

额外一条可核事实：在**未清理外来文件**的主工作区跑第一档，结果是 `202 passed, 1 failed`，
唯一失败是 `test_every_skill_is_primary_somewhere` 的 `17 == 15`（任务库实际 17 个）。
换句话说，外来那两份文件本身是能跑通的，冲突出在"编号重复 + 计数行只有一个值"，
不在题目质量。

## C. 三个问题

**1. 学习者要处理的东西数量变了吗？**

- `fix-004`：**变了**。4 条 ERROR（vs `fix-002` 的 3 条），分属 **4 条不同 request_id**、
  落在 4 个不同函数里（`put_raw` / `load_entry` / `merge_shard` / `read_tier`），
  要逐个否掉 3 个才能确定第 4 个。出错函数体内 `.get` 出现**两次**（第 27、28 行），
  按"第一处"规则只能选 27，而 28 行（`profile.get(...)`）是看起来更"像根因"的诱饵。
- `fix-003`：**没变**，与 `fix-002` 同为 3 条 ERROR、2 份文件。这题不是靠数量拉开的（见第 3 问）。

**2. 故障或异常的种类变了吗？**

- 故障位：`fix-003` 把 503 放回**读日志**（母本同侧、`fix-002` 反面）；`fix-004` 放在**读源码**
  （`fix-002` 同侧）。线程 §4⑥ 要的正是"两种时机各来一次"，现在齐了。
- 根因种类：`fix-004` 是本族**第一道 `other`**（`AttributeError` 但消息里没有 NoneType）。
  此前是 `type_mismatch`（fix-001/003）与 `null_deref`（fix-002）。
  四个取值里现在有三个被真题覆盖，只剩 `off_by_one` 没题——`IndexError` 的消息
  （`list index out of range`）不含定位特征，`_diagnose` 会拒答，要出这类题得先改规则（属方式 C，需人拍）。

**3. 决策点变了吗？**

- `fix-003`：第一处判断发生在**还没有任何证据时**——日志第一次读就 503。学习者必须先决定
  "重试日志"还是"先去读源码"。选后者会走进一条看似合理、但拿不到函数名/消息的死路
  （源码里没有函数名到行号的映射，规则是把日志的函数名当索引用）。第二处判断才是三条 ERROR 挑最后一条，
  且第 2 条干扰项同样是 `AttributeError`，长得像"本次失败"。
- `fix-004`：第一处判断是四条 ERROR 挑第四条，而**第 1 条本身就是 NoneType 的 AttributeError**——
  误把它当"最后一条"会得到 `null_deref`，词表里合法、判分判错，属于错得很难发现的那种。
  第二处判断是在 `read_tier` 体内两处 `.get` 里选第一处。

## D. 我不确定的地方

1. **同编号撞车（最重要）**。`arena-t-locate` 里同时存在：
   `fix-003-tz-window-mismatch`（我）/ `fix-003-tz-mixed-reconcile`（另一实例）、
   `fix-004-cache-lost-update`（我）/ `fix-004-lockless-overwrite`（另一实例）。
   两份外来文件的场景槽与本线程 §6 的示例一致（时区混用 / 并发覆盖），说明它也是按 T1 指令写的。
   **我没有动它们**（未删、未移、未进我的 commit）。需要你拍一件事：
   - 若保留我的两份 → 删掉另两份，计数行保持 15（我的 commit 就是这个状态）；
   - 若保留另两份 → 把我这两个文件和本报告一起丢掉即可，计数行仍是 15，不冲突。
   两种都不需要改别的文件。如果不拍，我这套是自洽的（隔离副本 187/30 全绿），可以先合。
   顺带：`git log` 里看不到第二实例的提交，它可能还在跑或已中断；它若继续，会覆盖
   `docs/threads/report-locate.md`（本文件）和计数行，合并时以哪一份为准要你看一眼时间戳。

2. **继承来的多余文件名**。变体的 `test_inputs` 是深合并，`files` 这个 dict 不会整体替换，
   所以我的两题加载后会多出母本的两个键（`/workspace/logs/app.log`、`/workspace/service/token.py`）。
   `fix-002` 也是这样（加载后有 4 个键），所以我按既定行为没处理。
   影响面：好奇的学习者读 `/workspace/logs/app.log` 能读到 fix-001 的日志；不判分（R2 只看我的
   `locate.log_path`），但严格说是一份"读得到的无关文件"。要清掉得改合并规则（方式 C）。

3. **`task_version` 我写了 `"2026-11-22.1"`**。`fix-002` 没写、继承母本的 `2026-11-15.1`；
   而 `docs/authoring-tasks.md` §1 第 2 步说 `task_version` 必须写全、改期望值要抬版本。
   没有测试卡这条，我选了后者。如果约定是"变体一律继承母本版本号"，把这两行删掉即可。

4. **`fix-004` 的根因只能记 `other`**。并发覆盖在词表里没有独立取值（`off_by_one` / `type_mismatch` /
   `null_deref` 都不贴），按题面规则归"其余"。如果希望"竞态/覆盖"能被复习层单独推题，
   得先扩词表再建题（方式 B/C，需人拍），不在本线程范围。这也是 `fix-004` 标 3 分的代价：
   它同时考"从四条里挑第四条"和"接受词表里没有 race 这个标签"。

5. **没跑全量**。按线程 §5，`pytest tests -q` 没跑（12–15 分钟，四线程并行会互相超时）。
   `test_locate_skeleton.py` 也没改、没跑（它只参数化 fix-001/fix-002；我的两题由
   `test_task_variants.py` 的 `full_marks` / `own_failure` 全覆盖到）。

6. **§6 题材槽是空的**，我按文件里给的示例写（fix-003 = 时区混用 / `type_mismatch`；
   fix-004 = 并发后写覆盖 / `other`）。若需求方原本有别的场景，换 `scenario` + `files` 即可，
   其余结构不用动。
