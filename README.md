# Agent Arena

Agent 开发学习者的自适应诊断与训练闭环（第二十届全国大学生软件创新大赛参赛项目，AI+学习方向）。

> 匿名纪律：本仓库及所有提交记录不得出现真实姓名、学校名称。协作者一律使用代号（学生A / 学生B / 评审）。

## 结构

```
docs/       数据模型、spike 结论、任务规范
tasks/      任务 YAML 定义（技能节点 + 原子 rubric + 测试输入）
src/arena/  薄服务层（评测 / 状态 / 复习 / 排程，按步逐个加入）
tests/      pytest 用例（每步验证沉淀于此）
workbench/  Streamlit 内部工作台（不进交付物）
```

## 环境

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 填入 API key，勿提交 .env
```

## 跑一道题（学习者侧）

题面只写「使用工具 `fetch_orders`」——这个工具从哪来、怎么调，在这里：

```bash
python scripts/lab.py --list                        # 列任务（题号 + 标题）
python scripts/lab.py --task dbg-001-order-report   # 起本地 mock 服务，打印端点
python scripts/lab.py --show-trace report.json      # 看自己这几次到底发了什么请求
```

启动后屏幕上有服务地址、工具清单、参数签名，和一条可直接拷走的示例请求。调它用什么都行：

```bash
curl -s http://127.0.0.1:8765/tools
curl -s -X POST http://127.0.0.1:8765/tools/fetch_orders \
     -H 'Content-Type: application/json' -d '{"offset":0,"limit":10}'
```

`--show-trace` 收两种文件：平台返回的报告（`report.json`），或你提交时那份原始 JSON
（含 `trace` 即可）。它把逐次调用排成一行一条：

```
#  工具            参数                            状态
------------------------------------------------------------
1  fetch_orders  limit=10 offset=0               success
2  fetch_orders  limit=10 offset=10              error (500)
3  fetch_orders  limit=10 offset=10              success
```

四点要说清，免得误会：

- 这个服务**只按任务声明回放数据，不判对错**。本地调通 ≠ 能拿分，判分在提交之后；
- 它**不打印任何判分信息**（故障位、期望值、断言 id）。这是故意的：印出来就等于替学习者
  跳过「发现服务会失败」这一步，重试类题目会直接失效。`tests/test_lab.py` 有一条用例盯这个；
- `--show-trace` **不给返回体**，只给请求参数与状态码。返回里有几行、合计多少正是要你自己
  发现的东西——给了它，「发现异常」就退化成「读一段字」；
- 分层提示按钮还没做：它涉及「看提示要不要在掌握度上降权」，见 `docs/BACKLOG.md` B-32。

## 加一道题（内容维护侧）

任务库现在 25 道（五个家族 7/7/5/5/1，难度 1/2/3 全档覆盖；三套判分骨架）。加题**不用改测试代码**：

```bash
python scripts/new_task.py --list                      # 各家族梯度，看缺哪一档
python scripts/new_task.py --new --family retry-drill \
    --id ret-008-xxx --title "……" --rows 28 --page 10 --write
python scripts/new_task.py --check ret-008-xxx         # 只查这一道，实测 0.6 秒
```

三条口径写在代码里：

- **期望值不手抄**。生成时按任务自己声明的 `derivation` 现算（与判分侧同一条规则），
  改数据后 `--check` 会直接把正确值打出来；
- **数量不写死**。加题必须在 `docs/task-authoring-log.yaml` 写一句 `why`（这题变的是什么）——
  原来"改测试里写死的数字"这道闸是假的：改数字不需要判断；
- **家族表单一来源**。`docs/task-families.yaml` 一份，测试、脚本、文档共用。

判据仍在测试里：`--check` 报的每一条都对应 `tests/` 中一条用例；规矩与陷阱见
`docs/authoring-tasks.md`，并行开线程加题见 `docs/parallel-authoring-brief.md`。

## 今天该复习什么（提醒页数据侧）

```bash
python scripts/review_page.py                                   # 内置演示数据（不联网、不留库）
python scripts/review_page.py --db arena.db --user s1            # 读真实库
python scripts/review_page.py --at 2026-11-22T21:00:00+08:00     # 把「现在」钉死，便于对照
```

输出是页面要显示的**数据**（`src/arena/reminder.py` 的 `build_reminder`），不是页面：

```
今天 2 项要复习
  1. 工具参数校验 — 重做上次出错的那一页，逾期 2 天（订单数据核对报告）
  2. 多步任务分解 — 同族换一道，今天到期（采购单金额核对报告）
另有 1 项排到明天——一天不塞太多，它们不会丢
```

三条口径写在代码里、也有测试盯着：每日上限来自用户设置（默认 2），看不完的项**不删**、
但必须在页面上被点名；`cap=0` 说的是「今天你设成了不复习」，跟「今天没有要复习的」是两句话；
逾期按整天算，今天到期就写「今天到期」。

## 开发流程

**给 AI Agent 的开工上下文先读 `docs/agent-guide.md`**（硬口径、已否决的外部建议、已知坑、当前该干什么）。

见 `docs/COLLABORATION.md`（协作规范 v2）：小步迭代，一步一验；`feat/*` 短命分支 + PR 是**两人协作期**的流程。
两人协作的分支/提交/合并/验收规则、文件所有权表、AI 代码质量清单都在那里；开 PR 时模板会自动带出验收清单。
交接说明见 `HANDOFF.md`，润色范围见 `docs/peer-brief-mvp-polish.md`。

> **分支口径（2026-11-21 人拍板，B-29）**：现在是**单人维护 + AI 协作**，`main` **直推**，
> 前置条件是**闸门绿**——全量测试绿 + `PUSH_GATE=CLEAN` 才允许推；不 force push、不改写已推送历史。
> 出现第二个真人提交者时，立刻打开 `COLLABORATION` §1 的分支保护、回到「只经 PR」。详见 `docs/BACKLOG.md` B-29。

## 测试证据（评委/评审可直接复核）

「测试全绿」这句话本身不是证据。每次全量运行都会留一份档案：

```bash
python scripts/test_evidence.py      # 跑一遍 pytest，把结果写成档案
```

产物在 `docs/test-evidence/`：

- `INDEX.md`——一行一次运行：时间、commit、通过/总数、失败数、耗时，点进去是当次全文；
- `<日期>-<sha7>.md`——结论 + 运行身份（commit / 分支 / 工作区是否干净 / Python / pytest / 依赖 pin）
  + 自校验类与变异类测试计数 + **逐文件用例数** + 失败用例（若有）+ 复现命令；
- `<日期>-<sha7>.json`——同一份数据的机器可读版。

档案由脚本生成、不手改；工作区有未提交改动时会显式标注「对应工作区状态而非干净提交树」。
判定「干净」时排除 `docs/test-evidence/` 自身——否则每份档案都会因为「要写这份档案」而自称不干净。
最近一次干净树运行：`1852 passed / 1852 total`，被测提交 `75a6e38`（见 `INDEX.md`）。

## 文案总表

页面上每一句用户能看到的字都收在 `docs/copy-inventory.md`（169 条，带 `文件:行` 定位）。
表由脚本从源码生成、**不许手改**——手改会让表和代码脱钩，变成错的第二真相源。

```bash
python scripts/copy_inventory.py          # 改过文案后重生成
python scripts/copy_inventory.py --check  # 只校验；不同步时退出 1
```

`pytest tests/test_copy_inventory.py -q` 里还有两条额外的锁：一是**提取器不许静默空转**
（扫到的条数少于阈值就红，否则空表对空文件也会「一致」），二是**页面不许出现评价性用词**
（「不错」「加油」「满分」这类，那是「复述不判分」这条对外承诺的机器判据）。

### 跑测试到底要多久（2026-11-23 实测）

三个不同的事情，速度差三个数量级，别混着算：

| 做什么 | 花多久 | 谁在用 |
|---|---|---|
| **判一次提交**（`prepare` + `evaluate`） | **42 ms**；含落库 66 ms | 学习者每次提交，已经是 OJ 级 |
| 改一道题后自检（`scripts/new_task.py --check <id>`） | 0.6 秒/题 | 加题、改数据 |
| 评审包一致性（`src/arena/review_packet.py --check`） | 6.4 秒 | 收工前 |
| 单文件回归（`pytest tests/test_mock_tools.py -q`） | 0.3–30 秒 | 改哪块跑哪块 |
| **全量回归**（`pytest tests -q`，1902 用例） | **830 秒（13 分 50 秒）** | 收工必跑 |

洛谷那种「几秒出结果」是**判一道题**，对应第一行：我们比它快——它还要编译 + 跑测试点，
我们只读 trace 判断言。第三行以下是**给整个仓库做体检**（1902 个用例，其中一批要起子进程、
起 HTTP 服务、建 SQLite 库），性质不同。

全量为什么慢，账很清楚：`tests/test_review_packet.py` 的 20 条用例每条都要跑一次
`pytest --collect-only` 子进程（单条 5–21 秒），合计约 4 分钟；剩下 12 分钟摊在 1880 条用例上
（多数要新建 `TaskRegistry()`，冷启动读 25 个 YAML ≈ 1 秒）。**要压到分钟级有两条路**：
① 装 `pytest-xdist` 并行（`pytest -n 4`，未装，装了要重跑一轮确认没有端口/临时目录打架）；
② 给评审包用例打 `slow` 标记，日常 `-m "not slow"` 跑剩下的。两条都还没做，等人拍。

两点实话，免得复核时误会：

- 全量跑一次约 14 分钟（830 秒，2026-11-23 实测；机器上有并发时会到 19 分钟），不是秒级命令；
- 跑完可能看到一段 `atexit cleanup_numbered_dir` 的 traceback——那是 pytest 收尾时删自己的临时目录，
  被本机沙箱的批量删除保护拦下了。它发生在**测试结束之后**，不影响用例结果；档案里的数字取自 junit。
- 只改了档案模板、不想再等十四分钟时：`python scripts/test_evidence.py --rerender docs/test-evidence/<档案>.json`，
  按同一份运行数据重排 `.md`（不重算、不追加索引行）。

