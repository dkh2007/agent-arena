# 多线程并行加题：任务书（2026-11-22 成文）

> **这份文档管什么**：把"加 7 道题"拆成 4 条互不干扰的线程，每条线程一张卡（可直接粘贴给新会话），
> 以及合并时谁负责哪一步。
> **它不管什么**：单题怎么写——那是 `docs/authoring-tasks.md`（下称"规范"），本文件只引用它。
> **冲突时**：本文件与规范冲突，以规范为准；规范与测试冲突，以测试为准。

---

## 0. 三十秒版本

| 问题 | 答案 |
|---|---|
| 能并行吗 | 能。**一个家族一条线程**，家族之间零共享文件 |
| 每条线程交什么 | 一个 worktree 分支，里面 1–2 个新 YAML + 两行计数改动 |
| 谁改全局数字 | **只有主线程**（我）。线程改的数字合并时会被覆盖，它只管自己那份能跑绿 |
| 谁跑全量测试 | **只有主线程**。线程只跑 40 秒那档（§2.1 实测） |
| 谁写 BACKLOG / ROADMAP | **只有主线程**。线程碰这两个文件会造成 4 路冲突 |
| 最大风险 | §5 第 1 条：机器锁不住"这道题是不是只换了业务名"，并行会放大它 |

本轮目标：**13 → 20 题**（+7）。这是规范 §6 选项 C 的中间节点，到 20 再决定要不要新建骨架。

> **这套流程不是纸上推演**：2026-11-22 已在临时 worktree 里端到端跑过一次——
> 建 worktree → 加一道冒烟题（第一档 171 → 179 全绿，第二档 26 → 28 全绿）→ 双分支冲突预测 → 清理。
> 跑的过程中抓到三个会让四条线程同时卡死的坑，已写进 §2.1、§6 第 2 步与红线 9。

---

## 1. 为什么必须用 git worktree（别在同一个目录里开）

任务库是**通配发现**的：`TASK_FILES = sorted(TASKS_DIR.glob("*.yaml"))`。
所以只要同一目录里存在一个写到一半的 `.yaml`，**所有人的测试都会红**——报错还指向别人的文件。
三条线程互相看不见对方在写什么，就会互相判定"我改坏了"。

第二处单点：`tests/test_task_variants.py` 里 `assert len(all_tasks) == 13`。
三线程同时改成 15/16/15，合并时必冲突，且没人知道最终该是几。

结论：**物理隔离 + 计数集中**。worktree 是 git 自带功能，不在同一个目录、共用一个仓库历史。

---

## 2. 开工前置（复制粘贴，一次建完）

主线程先做一次，确认起点干净：

```bash
cd <agent-arena 仓库根目录>    # 就是你平时跑测试的那个目录
git status --short          # 必须空输出；不空就先收尾，别开线程
```

然后按你要开的线程数建 worktree（**只建你要用的那几条**，其余留着以后建）：

```bash
git worktree add ../arena-t-locate -b thread/locate ece13e3
git worktree add ../arena-t-order  -b thread/order  ece13e3
git worktree add ../arena-t-retry  -b thread/retry  ece13e3
git worktree add ../arena-t-param  -b thread/param  ece13e3
```

`ece13e3` 是 2026-11-22 的 HEAD（1628 测试全绿）。想基于当时最新 main，把它换成 `main`——
代价是合并时可能多几处小冲突，**收益是线程能用到之后的修复**。本轮不必要，用固定 sha 更可预测。

建完核对：

```bash
git worktree list           # 应看到 5 行（主 + 4）
```

**撤销**（线程废弃时）：

```bash
git worktree remove ../arena-t-param --force
git branch -D thread/param
```

### 2.1 环境（2026-11-22 实测，不这样写就跑不起来）

worktree 里**没有虚拟环境**，这台机器的 `python` 与 `pytest` 也不在同一个地方：

| 命令 | 指向 | 有 pytest 吗 |
|---|---|---|
| `python` | `.workbuddy/binaries/python/versions/3.13.12/python` | **没有** |
| `pytest` | `.workbuddy/binaries/python/envs/default/Scripts/pytest` | 有 |

所以验收命令**必须**是 `PYTHONPATH=src pytest …`，写成 `python -m pytest` 会直接报
`No module named pytest`（建 worktree 当天就是踩这个，四条线程会同时卡在第一条命令）。

参考基线（13 题，2026-11-22 实测两次）：

| 档位 | 13 题基线 | 加 1 道题后 | 每题增量 |
|---|---|---|---|
| 第一档（四个文件） | **171 passed / 36 秒** | 179 passed / 41 秒 | +8 条 |
| 第二档（`-k full_marks or own_failure`） | **26 passed / 16 秒** | 28 passed / 17 秒 | +2 条 |

所以：加 2 道题的线程，第一档应约 **187**，第二档应约 **30**。**数字对不上但全绿通常没事**
（增量可能随题型浮动），**红了一定有事**。若你的基线不是 171，说明 worktree 建错了地方。

---

## 3. 九条红线（每条线程通用，卡里也重复一遍）

1. **只准动自己家族的编号段**。§4 每张卡写了段号，写超了就撞别人的文件名。
2. **只准动 `tasks/<你自己的>.yaml`**。不要改母本 `dbg-001` / `fix-001`——改母本会同骨架所有题一起变，那不是加题。
3. **只准动这两行数字**：`tests/test_task_variants.py` 的 `assert len(all_tasks) == 13`、
   `tests/test_derivation.py` 的 `assert checked == 11`。改成你这条线程跑出来的实际值。
   **其他任何 `tests/` 下的改动都禁止**——测试红了就是你的题写错了，不是测试过时了。
   真觉得测试有问题：停下，在报告里写清楚，别自己改。
4. **不跑全量**。`pytest tests -q` 要 12–15 分钟、吃满 CPU，四条线程同时跑会互相超时。
   只跑 §4 卡里那两条命令（第一档约 40 秒 + 单题深验约 18 秒）。
5. **不碰 `docs/BACKLOG.md`、`docs/ROADMAP.md`、`docs/copy-inventory.md`、评审包**。这些是主线程收口时统一改的。
6. **不 commit 到 `main`，不 push**。在你自己的分支上 commit 就行。
7. **题面不许写解法**。`scenario` 只描述工作与硬约束（上限、超限报什么错、共几行），
   不许出现"先查 A 再查 B"这种步骤提示——会把 R3/R4 变成照抄（规范 §5 第 4 条，无测试护）。
8. **期望值必须先跑出来再填**，不许凭印象预填（规范 §1 第 5 步，这仓库已经因此错过两次）。
9. **动手前先跑一次第一档验收，确认基线全绿**。红线 3 允许你改计数行，于是"测试红了"有两种原因：
   你改坏了，或环境/基线本来就是坏的。**没有基线就没法区分**，你会开始乱改测试。
   基线红 → 停手，在报告里贴原始输出，不要动手改任何东西。

---

## 4. 线程卡（每张 = 一段可直接粘贴的 prompt）

每张卡最上面是**题材槽**：那几行由你填，其余不要改。填完把整段粘给新会话。

### 4.1 卡 T1：`locate-drill` 家族（最缺的一块）

> **为什么给它两条题**：这是唯一只有 2 题的家族，而且缺难度 3；`failure_retry` 至今没有定位类题目（B-05b 边界③）。

```
你在一份本地 git 仓库里加 2 道「定位题」。工作目录是一个 git worktree，
路径末尾是 arena-t-locate（开新会话时选中该目录；进来先 pwd 确认，不在这个目录就别动手）

先用 git branch --show-current 确认在 thread/locate 分支上，然后按顺序读完这四份文件再动手：
1. docs/authoring-tasks.md      —— 加题的完整规范（七步清单、陷阱表）
2. tasks/fix-001-token-expiry.yaml —— 这个家族的母本（骨架、字段全貌）
3. tasks/fix-002-cart-none-deref.yaml —— 最干净的一份变体（只写差异的写法）
4. tests/test_locate_skeleton.py  —— 定位族特有的断言

你负责的编号段是 fix-003 / fix-004，不许用这两个之外的编号。

要加的两道题（题材已定，见下）：
- fix-003：难度 2，日志里至少 3 条带 " ERROR " 的行
- fix-004：难度 3，日志里至少 4 条带 " ERROR " 的行
（难度与 ERROR 条数的对应关系有测试硬查：1→≥2、2→≥3、3→≥4，见 test_difficulty_is_not_decorative）

题材槽【由需求方填，填完删掉本行】：
- fix-003 场景：__________（示例：时间戳混用 UTC 与本地时区导致对账偏差）
- fix-004 场景：__________（示例：并发写入没加锁导致后写覆盖前写）
- 若题材槽留空，用上面括号里的示例，并在报告里注明"题材未指定，用了示例"。

硬约束（违反任一条即作废）：
1. 定位题不声明 derivation 块——答案是根因判断，不是推导值。
2. 只新增 tasks/fix-003-*.yaml 与 tasks/fix-004-*.yaml，不修改母本与 fix-002。
3. 唯一允许改的测试数字：tests/test_task_variants.py 的 assert len(all_tasks) == 13 → == 15。
   tests/test_derivation.py 不要动（定位题不声明 derivation，11 不变）。
   除这一行外，tests/ 下任何文件都不许改。
4. 不跑全量测试，不 commit 到 main，不 push。不碰 docs/BACKLOG.md 与 docs/ROADMAP.md。
5. 题面不许写"先看日志再看源码"这类步骤提示。

验收前先跑一次下面的第一条命令，确认基线全绿（红就停手、贴原始输出，别动测试）。

验收（两条都要绿，把完整输出贴进报告）：
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"

然后在 thread/locate 分支上 commit 一次，commit message 用：
  feat(tasks): 加 fix-003 / fix-004（locate-drill 补难度 3）

最后写一份报告，必须包含这四块（缺一块算没完成）：
A. 改了哪些文件（git diff --stat 的输出）
B. 两条验收命令的末尾结果行（passed 数量）
C. 三个问题逐条自答——这是唯一能证明"不是只换了业务名"的东西：
   ① 学员要处理的东西数量变了吗？（日志条数 / 文件数 / 错误种类数）
   ② 故障或异常的种类变了吗？
   ③ 决策点变了吗？（在哪一步必须做出判断）
   三条都答"没变"说明这题只是改名，回去改题再验收。
D. 你自己不确定的地方（宁可写"我不确定"，不要写"应该没问题"）
```

### 4.2 卡 T2：`param-surface` 家族

```
你在一份本地 git 仓库里加 1 道「参数校验题」。工作目录是一个 git worktree，
路径末尾是 arena-t-param（开新会话时选中该目录；进来先 pwd 确认，不在这个目录就别动手）

先用 git branch --show-current 确认在 thread/param 分支上，然后按顺序读完这四份文件再动手：
1. docs/authoring-tasks.md       —— 加题的完整规范
2. tasks/dbg-001-order-report.yaml —— 母本（骨架全貌）
3. tasks/prm-003-page-ceiling-4.yaml —— 同家族最难的一份，重点看它的题面怎么写上限
4. 这两条会硬查你的题面，先读判据再写题面：
   - tests/test_task_yaml.py 的 test_scenario_states_limit_error（题面必须说超限报什么错）
   - tests/test_task_variants.py 的 test_scenario_mentions_the_real_ceiling_and_row_count
     （题面写的上限与行数必须与 tools / test_inputs 真实一致）

你负责的编号段是 prm-004，不许用别的编号。

要加的题：
- prm-004：难度 2，页上限换成 6（现有是 5 / 5 / 4，换成 6 是为了让"边界外一点"更不显眼）
- 总行数要满足难度 2 的页数下限：ceil(行数 / 6) ≥ 3，即至少 13 行，建议 18–20 行

题材槽【由需求方填，填完删掉本行】：
- prm-004 场景：__________（示例：物流轨迹批量查询，字段名是 tracking_id）

硬约束：
1. 题面必须明说"limit 超过 6 会返回 invalid_limit"，且写的上限、总行数必须与 tools/test_inputs 真实一致。
2. 期望值（有效行数、总金额）必须先把数据跑一遍得出，不许预填。
3. 只新增 tasks/prm-004-*.yaml。唯一允许改的测试数字：
   tests/test_task_variants.py 的 assert len(all_tasks) == 13 → == 14。
   tests/test_derivation.py 的 assert checked == 11 → == 12（汇报族都声明 derivation）。
   除这两行外 tests/ 任何文件不许改。
4. 不跑全量，不 commit 到 main，不 push，不碰 BACKLOG / ROADMAP。
5. 题面不许写步骤提示。

验收前先跑一次下面的第一条命令，确认基线全绿（红就停手、贴原始输出，别动测试）。

验收（两条都要绿，贴完整输出）：
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"

然后 commit 一次：
  feat(tasks): 加 prm-004（param-surface 页上限 6）

报告四块（同 T1）：
A. git diff --stat
B. 两条验收的末尾结果行
C. 三个问题自答（处理的东西数量 / 异常种类 / 决策点）
D. 不确定的地方
```

### 4.3 卡 T3：`order-report` 家族

```
你在一份本地 git 仓库里加 2 道「汇报题」（调试向）。工作目录是一个 git worktree，
路径末尾是 arena-t-order（开新会话时选中该目录；进来先 pwd 确认，不在这个目录就别动手）

先用 git branch --show-current 确认在 thread/order 分支上，然后按顺序读完这四份文件再动手：
1. docs/authoring-tasks.md          —— 加题的完整规范（尤其 §1 七步清单与 §4 陷阱表）
2. tasks/dbg-001-order-report.yaml  —— 母本（骨架全貌）
3. tasks/dbg-002-invoice-report.yaml —— 最干净的变体范本（66 行，照着它的结构写）
4. tasks/dbg-004-sensor-batch.yaml  —— 同家族难度 3 的一份，看它怎么把难度做上去

你负责的编号段是 dbg-005 / dbg-006，不许用别的编号。

要加的两道题：
- dbg-005：难度 2，页数至少 3（即 ceil(行数 / page_size_max) ≥ 3）
- dbg-006：难度 3，页数至少 4
- **两题都要显式写 fault_injection**。注意：dbg-003 / dbg-004 自己的文件里**没有** fault_injection 块，
  它们的故障是从母本 dbg-001 继承的（offset_covers: 10 / 500）。所以别去那两份文件里抄故障码，
  要去 dbg-001 看。
- 已用过的故障位：5 / 8 / 10 / 15 / 20 / 30。尽量错开（**没有测试强制唯一**，
  错开是为了复习层不把两道题看成同一道）。
- http_status 现有题只出现过 500 / 502 / 503。想用别的值，先去 src/arena/mock_tools.py
  确认服务端会怎么回，不要猜。

题材槽【由需求方填，填完删掉本行】：
- dbg-005 场景：__________（示例：退货单金额核对）
- dbg-006 场景：__________（示例：库存盘点差异汇总）
- 若留空用上面示例，并在报告注明。

硬约束：
1. 变体只写差异，不许抄 rubric_gate / scoring / trace_requirements / submission_requirements
   （有测试查：test_every_variant_declares_its_parent_and_does_not_restate_skeleton）。
2. 故障位用 offset_covers: N（页区间覆盖），不是 offset: N（精确相等）。
   R3 的期望恰好两步：失败（status: error）→ 成功，且 params_match 与 fault_injection.match 必须相等。
3. 被剔除的每一行都要在 YAML 里逐行注释原因（缺字段 / null / 负数 / 重复 id 等）。
4. 期望值必须先跑出来。test_inputs.expected 与 rubric_credit[R2].expected 必须相等。
5. 唯一允许改的测试数字：len(all_tasks) == 13 → == 15；checked == 11 → == 13。
   除这两行外 tests/ 不许改。
6. 不跑全量，不 commit 到 main，不 push，不碰 BACKLOG / ROADMAP。
7. 题面不许写步骤提示。

验收前先跑一次下面的第一条命令，确认基线全绿（红就停手、贴原始输出，别动测试）。

验收（两条都要绿，贴完整输出）：
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"

然后 commit 一次：
  feat(tasks): 加 dbg-005 / dbg-006（order-report）

报告四块（同 T1）：git diff --stat / 验收结果行 / 三个问题自答 / 不确定的地方
```

### 4.4 卡 T4：`retry-drill` 家族

```
你在一份本地 git 仓库里加 2 道「重试题」。工作目录是一个 git worktree，
路径末尾是 arena-t-retry（开新会话时选中该目录；进来先 pwd 确认，不在这个目录就别动手）

先用 git branch --show-current 确认在 thread/retry 分支上，然后按顺序读完这五份文件再动手：
1. docs/authoring-tasks.md         —— 加题的完整规范（§1 第 6 步尤其重要）
2. tasks/dbg-001-order-report.yaml —— 母本
3. tasks/ret-001-refund-503.yaml   —— 家族最简单的一份
4. tasks/ret-004-narrow-page.yaml  —— 最新的一份（2026-11-21 加，页上限 5、30 行 6 页）
5. src/arena/mock_tools.py 里 _take_fault 与 page_covers —— 故障到底怎么被触发

你负责的编号段是 ret-005 / ret-006，不许用别的编号。

要加的两道题：
- ret-005：难度 2，页数至少 3（ceil(行数 / page_size_max) ≥ 3）
- ret-006：难度 3，页数至少 4
- 两题的故障位置要"离首页更远"（这是难度递增的真实含义，不是装饰字段）
- http_status 现有题只出现过 500 / 502 / 503（母本 500、ret-001 503、ret-004 502），
  想用别的值先去 src/arena/mock_tools.py 确认服务端怎么回，不要猜
- 已用过的故障位：5 / 8 / 10 / 15 / 20 / 30，尽量错开（无测试强制，为了复习层能区分）

题材槽【由需求方填，填完删掉本行】：
- ret-005 场景：__________（示例：批量导出任务轮询，中途 502）
- ret-006 场景：__________（示例：对账接口在第 6 页才 503）
- 若留空用上面示例，并在报告注明。

硬约束：
1. 故障位用 offset_covers: N（页区间覆盖）。learner 用任意合法 limit 都必须恰好踩到一次。
   有测试硬查 6 种页大小：tests/test_mock_tools.py::test_b31_fault_triggers_for_any_legal_page_size
   （判分侧还有一条 tests/test_assertion_evaluator.py::test_b31_any_legal_page_size_can_pass_r3）。
2. R3 期望恰好两步：失败（status: error）→ 成功。不许设计"连续失败两次"或"第二次调用才失败"
   ——现有骨架表达不出来（规范 §1 第 6 步末尾）。
3. 期望值先跑出来再填。
4. 唯一允许改的测试数字：len(all_tasks) == 13 → == 15；checked == 11 → == 13。
   除这两行外 tests/ 不许改。
5. 不跑全量，不 commit 到 main，不 push，不碰 BACKLOG / ROADMAP。
6. 题面不许写"失败后重试一次就行"这类提示——那会把 R3 变成照抄。

验收前先跑一次下面的第一条命令，确认基线全绿（红就停手、贴原始输出，别动测试）。

验收（两条都要绿，贴完整输出）：
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"

然后 commit 一次：
  feat(tasks): 加 ret-005 / ret-006（retry-drill）

报告四块（同 T1）：git diff --stat / 验收结果行 / 三个问题自答 / 不确定的地方
```

---

## 5. 并行会放大的一件事（先认下来）

规范 §5 列了四件机器锁不住的事，其中**第 1 条"这道题是不是只换了业务名"**在并行下风险最高：
串行时我写完会自己回头看"这算新题吗"，四条线程各自写完、各自说"我这是新题"，我就要抽查四份。

补偿手段只有一条，就是卡里的**三个问题自答**（C 块）。判据照抄规范 §7：

> 要处理的东西数量变了吗？故障/异常的种类变了吗？决策点变了吗？
> 三个都"没变" ⇒ 只是改名。

我合并时会**逐份读 C 块**，答不上来的那题退回重写，不因为"测试绿了"就收。

其余三件锁不住的（区分度、学生要做的事、题面漏解法）本轮不新增机制——区分度夹具只有 `dbg-001`
做过，那是另一条活（B-32 之后单独排）。

---

## 6. 收口流程（主线程，也就是我，来做）

四条线程交回来之后，顺序不能颠倒：

1. **不先合并，先读报告**。四份 C 块逐条看，不合格的退回。此时退回的成本最低。
2. **逐个合并——绝对不要一条命令接四个分支**：
   ```bash
   git merge --no-ff thread/locate     # 合完，先解冲突再继续
   git merge --no-ff thread/order
   git merge --no-ff thread/retry
   git merge --no-ff thread/param
   ```
   为什么不能 `git merge a b c d`：git 走 **octopus 策略**，一有冲突**直接中止、不进入可手工解决的
   状态**——你以为在解冲突，其实工作区什么都没发生，然后会误判成"没冲突"。
   逐个 merge 每次只冲突一处，改完再合下一条。

   每一处冲突都只动这两行，**按实际数重算，不取任何一方**：
   - `len(all_tasks)`：每合一条就 + 它那几条，最终 13 + 7 = **20**
   - `checked`：只有汇报族（dbg / ret / prm）声明 derivation，定位族 2 道不计数 ⇒ 11 + 5 = **16**
   这两行**只有我知道全局该是几**——线程各自只知道自己那份，这正是计数不能交给线程定终值的原因。
   > 实测（2026-11-22）：用两个临时分支各改一次计数行做 `git merge-tree`，冲突**只**出现在
   > `tests/test_task_variants.py` 一处，新增 YAML 与另一条线程的改动零冲突。演练完已清理。
3. 改 `docs/BACKLOG.md`（B-30 进度）、`docs/ROADMAP.md`（13 → 20、家族题数分布）。
   **先解决 ROADMAP 那条老账**：`13 个 > 计划书的「≤10」（待人确认措辞）`。
   20 个更不能靠"措辞待定"糊过去，扩到 20 之前这句必须定，否则对外材料与仓库不一致。
4. 跑全量：`PYTHONPATH=src pytest tests -q`（12–15 分钟，一次；20 题后会更久）。
5. 重生成评审包 → 归档证据 → 过闸门（三条命令，参数已核对）：
   ```bash
   PYTHONPATH=src python src/arena/review_packet.py --check --full   # 先校验，不写文件
   PYTHONPATH=src python src/arena/review_packet.py                  # 确认无误再生成
   PYTHONPATH=src python scripts/test_evidence.py --note "扩到 20 题后全量"
   PYTHONPATH=src python scripts/push_gate.py                        # 必须 PUSH_GATE=CLEAN
   ```
   然后一次提交推送。评审包幂等判据是比 `packet-sha256`（正文摘要），
   **不要比文件 md5**——header 里有 `generated=` 时间戳，每生成一次 md5 都变。
6. 清理 worktree：
   ```bash
   git worktree remove ../arena-t-locate && git worktree remove ../arena-t-order \
     && git worktree remove ../arena-t-retry && git worktree remove ../arena-t-param
   ```
   （分支保留一段时间，出问题好回溯。）

**中途某条线程卡住怎么办**：其余三条照常合并，把卡住的那条的分支留着，`len` 按已合并的实际数写
（例如先合 5 道就写 18）。**不要为了等一条线程让全局数字悬空**——数字悬空比少几道题更贵。

---

## 7. 什么算"这条线程完成了"

| 判据 | 谁来判 |
|---|---|
| 两条验收命令全绿、输出贴出来了 | 线程自报，我复核 B 块 |
| `git diff --stat` 只出现预期的文件 | 我复核：多改一个 `tests/` 文件就退回 |
| 三个问题自答有具体内容（不是"变了"两个字） | 我读 C 块 |
| 题面没写解法 | **没人能机器判**，我人工看 `scenario` 段落 |
| 难度与页数/ERROR 条数对得上 | 测试（`test_difficulty_is_not_decorative`） |
| 故障位任意合法 limit 都能踩到 | 测试（`test_b31_fault_triggers_for_any_legal_page_size`） |

**我不会替你确认的事**：这道题好不好、题材有没有意思、难度是不是真提升了。
这些只有你能判——你能判的时机是合并之后看 20 道题的清单，不是现在。
