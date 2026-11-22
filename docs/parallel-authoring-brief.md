# 多线程并行加题：任务书（2026-11-22 成文）

> **这份文档管什么**：把"加 7 道题"拆成 4 条互不干扰的线程，每条线程一张卡（可直接粘贴给新会话），
> 以及合并时谁负责哪一步。
> **它不管什么**：单题怎么写——那是 `docs/authoring-tasks.md`（下称"规范"），本文件只引用它。
> **冲突时**：本文件与规范冲突，以规范为准；规范与测试冲突，以测试为准。

> ## ⚠ 2026-11-22 晚更新：下面有三处已不适用
>
> 本文写于 13 → 20 那一批（当天下午），其中"线程改两行计数"的做法**当天晚上就被废掉了**。
> 现在开线程前先看这三条，其余（worktree 隔离、红线、octopus 陷阱、收口顺序）仍然有效：
>
> | 原来 | 现在 |
> |---|---|
> | 线程改 `len(all_tasks) == 13` / `checked == 11` 两行 | 两处都改成从磁盘推导了，**没有计数行可改** |
> | 冲突只落在计数行，合并时由主线程重算 | 冲突只可能落在**台账 `docs/task-authoring-log.yaml` 的相邻两行**，语义冲突（两条线程都插在同一家族段尾）——逐个 merge 照旧 |
> | 手算 `expected`、手抄骨架 | `python scripts/new_task.py --new` 生成、`--check` 3 秒自检（见规范 §0） |
>
> **线程现在只交两样**：`tasks/<id>.yaml` + 台账里属于自己家族的那一行 `why`。
> 台账那行是**唯一可能冲突的地方**，而它冲突时两个人写的 why 不同、必须都由人来判——这正是它该冲突。

---

## 0. 三十秒版本

| 问题 | 答案 |
|---|---|
| 能并行吗 | 能。**一个家族一条线程**，家族之间零共享文件 |
| 每条线程交什么 | 一个 worktree 分支，里面 1–2 个新 YAML + 台账里属于自己家族的那一行 `why` |
| 谁改全局数字 | **没有全局数字了**（2026-11-22 起数量由台账行数决定）。线程只对自己那几道负责 |
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

## 3. 十一条红线（每条线程通用；四个指令文件里各有一份精简版）

1. **只准动自己家族的编号段**。§4 每张卡写了段号，写超了就撞别人的文件名。
2. **只准动 `tasks/<你自己的>.yaml`**。不要改母本 `dbg-001` / `fix-001`——改母本会同骨架所有题一起变，那不是加题。
3. **`tests/` 下一行都不许改**（2026-11-22 起计数行已不存在，这条从"只准改两行"收紧成"一行都不许改"）。
   测试红了就是你的题写错了，不是测试过时了。真觉得测试有问题：停下，在报告里写清楚，别自己改。
   你要动的地方只有两个：自己的 `tasks/<id>.yaml`，和台账 `docs/task-authoring-log.yaml`
   里**自己家族段**的那一行 `why`。
4. **不跑全量**。`pytest tests -q` 要 12–15 分钟、吃满 CPU，四条线程同时跑会互相超时。
   只跑 §4 卡里那两条命令（第一档约 40 秒 + 单题深验约 18 秒）。
5. **不碰 `docs/BACKLOG.md`、`docs/ROADMAP.md`、`docs/copy-inventory.md`、评审包**。这些是主线程收口时统一改的。
6. **不 commit 到 `main`，不 push**。在你自己的分支上 commit 就行。
7. **题面不许写解法**。`scenario` 只描述工作与硬约束（上限、超限报什么错、共几行），
   不许出现"先查 A 再查 B"这种步骤提示——会把 R3/R4 变成照抄（规范 §5 第 4 条，无测试护）。
8. **期望值不手抄**：用 `python scripts/new_task.py --new` 生成（它按 `derivation` 现算），
   或手写后跑 `--check` 让它把正确值打出来。凭印象预填这仓库已经错过两次（规范 §1 第 5 步）。
9. **动手前先跑一次第一档验收，确认基线全绿**。红线 3 允许你改计数行，于是"测试红了"有两种原因：
   你改坏了，或环境/基线本来就是坏的。**没有基线就没法区分**，你会开始乱改测试。
   基线红 → 停手，在报告里贴原始输出，不要动手改任何东西。
10. **同一个编号段只留一份文件**。改题意就**原地改**那个 YAML，不要另存成
   `fix-003-xxx.yaml` 再写一份 `fix-003-yyy.yaml`。任务库按 `tasks/*.yaml` 通配扫描，
   多一个文件就让**所有人的**测试红（`len(all_tasks)` 对不上），而且报错指向别人。
   > 实测（2026-11-22，T1 真实翻车）：那条线程重写三轮、留下 6 个文件（2 提交 + 4 未跟踪），
   > 第一档报 `19 == 15`。它在报告里把这 4 个文件**误判成「另一条线程并发跑同一编号」**，
   > 进而建备份目录、去 `%TEMP%` 跑"隔离副本"自证——全部是白做的。
   > **判据**：`git status` 里是 `??`、且 slug 不是你这一版 ⇒ 就是你前几轮的稿，删掉。
   > 提交前跑一次 `ls tasks/<你的段号>-*.yaml`，多于预期就清。
11. **收尾清掉自己建的临时目录与 worktree**。同一条线程那次还留下一个 detached worktree
   （`.tmp-xxx-verify`），`git worktree list` 里会一直挂着，下次同路径 `add` 直接失败。
   收尾三查：`git worktree list`（有没有多余的）、`git status`（有没有 `??` 残留）、
   `ls tasks/<你的段号>-*.yaml`（有没有旧稿）。三样都干净才算完。

---

## 4. 四条线程的指令文件（每个文件 = 一条线程的全部指令）

四个文件各自自包含：位置自检、要读什么、在哪改、改完放哪、测试命令与通过标准、报告格式。
**把对应文件整份交给那条线程即可**，它不需要读这份文档。

| 线程 | 指令文件 | 家族 | 加几道 | 编号段 | 分支 / worktree |
|---|---|---|---|---|---|
| T1 | `docs/threads/T1-locate.md` | `locate-drill` | 2 | fix-003 / fix-004 | `thread/locate` · `arena-t-locate` |
| T2 | `docs/threads/T2-param.md` | `param-surface` | 1 | prm-004 | `thread/param` · `arena-t-param` |
| T3 | `docs/threads/T3-order.md` | `order-report` | 2 | dbg-005 / dbg-006 | `thread/order` · `arena-t-order` |
| T4 | `docs/threads/T4-retry.md` | `retry-drill` | 2 | ret-005 / ret-006 | `thread/retry` · `arena-t-retry` |

合计 +7：13 → **20**。

**线程只交这三样**（都在它自己的 worktree 里 commit 到自己的分支，不 push）：

1. `tasks/<编号>-<短名>.yaml` —— 新题（`--new` 生成，`--check` 通过）
2. `docs/task-authoring-log.yaml` 里自己家族段的那一行 `why` —— 说清这题变的是什么
3. `docs/threads/report-<家族>.md` —— 报告，作为加题证据留档

其余一律不改：母本、别人的编号段、`docs/BACKLOG.md`、`docs/ROADMAP.md`、评审包。

> 四个文件里的测试命令、基线数字、故障自检脚本都**实测过**（脚本拿 `ret-004` 跑出
> `limit=1..5 → hits=[20]`，与预期一致；也由此发现超限 limit 的 400 会被误记成故障命中）。

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

   冲突**只可能落在台账**（`docs/task-authoring-log.yaml`）：两条线程在同一家族段尾各插一行，
   git 会判冲突。处理方式：两行**都留**，按 id 排序，然后 `--check` 各自的题确认都还在。
   （2026-11-22 之前这里还有两行计数要重算，现在没有了——数量由台账行数决定。）
   > 实测（2026-11-22）：用两个临时分支各改一次计数行做 `git merge-tree`，冲突**只**出现在
   > `tests/test_task_variants.py` 一处，新增 YAML 与另一条线程的改动零冲突。演练完已清理。
3. 改 `docs/BACKLOG.md`（B-30 进度）、`docs/ROADMAP.md`（13 → 20、家族题数分布）。
   **先解决 ROADMAP 那条老账**：`13 个 > 计划书的「≤10」（待人确认措辞）`。
   20 个更不能靠"措辞待定"糊过去，扩到 20 之前这句必须定，否则对外材料与仓库不一致。
4. 跑全量：`PYTHONPATH=src pytest tests -q`（12–15 分钟，一次；20 题后会更久）。
5. 重生成评审包 → 归档证据 → 过闸门（三条命令，参数已核对）：

   **前两条必须用「装了 pytest 的解释器」启动**：`review_packet.py` 与 `test_evidence.py` 内部都跑
   `sys.executable -m pytest`，而本机裸 `python` 没有 pytest（2026-11-22 实测：裸 `python` 启动
   `review_packet.py --check` 直接报 `No module named pytest`）。`push_gate.py` 不跑测试，随便哪个都行。

   ```bash
   PY="$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
   "$PY" -c "import pytest; print('ok')"    # 先确认这个解释器真的有 pytest

   PYTHONPATH=src "$PY" src/arena/review_packet.py --check --full   # 先校验，不写文件
   PYTHONPATH=src "$PY" src/arena/review_packet.py                  # 确认无误再生成
   PYTHONPATH=src "$PY" scripts/test_evidence.py --note "扩到 20 题后全量"
   PYTHONPATH=src "$PY" scripts/push_gate.py                        # 必须 PUSH_GATE=CLEAN
   ```
   然后一次提交推送。评审包幂等判据是比 `packet-sha256`（正文摘要），
   **不要比文件 md5**——header 里有 `generated=` 时间戳，每生成一次 md5 都变。
6. 清理 worktree：
   ```bash
   git worktree remove ../arena-t-locate && git worktree remove ../arena-t-order \
     && git worktree remove ../arena-t-retry && git worktree remove ../arena-t-param
   ```
   （分支保留一段时间，出问题好回溯。）

**怎么判断某条线程压根没起来**：看它的 worktree，三条一起看——
`git status` 空、`git log --oneline -1` 还停在建 worktree 那个提交、目录 mtime 等于建目录那一刻。
三条同时成立 ⇒ 它一行都没写，不是"在思考"。
> 实测（2026-11-22）：param / order 两条从 13:18 建好到 14:28 收口，**70 分钟零改动**，
> 分支提交号仍是起点。而"外面跑了三个"这句话让我以为三条都在跑。
> **建议开工 20 分钟后查一次**，空的就直接接手，不要等。

**中途某条线程卡住怎么办**：其余几条照常合并，把卡住的那条的分支留着，`len` 按已合并的实际数写
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
这些只有你能判——你能判的时机是合并之后看全库清单（当时是 20 道），不是现在。
