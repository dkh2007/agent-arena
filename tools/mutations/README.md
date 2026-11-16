# 变异测试脚本（作者侧证据，可复核）

这里的每个脚本干同一件事：**故意把源码改坏一处**，跑指定的测试节点，看测试有没有变红。
红的叫 `DETECTED`（测试真的在管这事），不红叫 `MISSED`（测试没管），
脚本自己写错锚点叫 `INVALID`（看起来抓住了，其实没测）。

## 为什么放在仓库里而不是 `.git/`

这些脚本原本躺在 `.git/` 下（那里 git 不跟踪，等于**只有作者本机能跑**）。
既然 `docs/review-response-v1.md` 把它们当作「十分钟可复核路径」引用，就得能被第三方拿到，
所以 2026-11-15 挪到这里。

`assemble_variants.py` / `gen_fixtures.py` 是纯一次性的装配/生成工具（依赖 `.git/gen_out.txt`
这种中间文件，本身不入库），仍留在 `.git/`。

## 跑法

脚本自己会往上找到仓根（按 `src/arena` 是否存在判断），所以**在哪个目录调用都一样**：

```bash
python tools/mutations/mutate_locate.py      # 相对路径也行
python /abs/path/to/agent-arena/tools/mutations/mutate_locate.py   # 绝对路径也行
```

> 本文件刻意不写作者的机器绝对路径：仓库里一旦出现「盘符 + 用户名」那种路径，
> `scripts/push_gate.py` 的 `ABS_PATH` 规则就会拦下（那是匿名要求的一部分）。
> 你本机要完整路径时，把仓根换成你自己的路径即可。

判定：末尾一行是「全 DETECTED（N 条）」且退出码 0；出现 `MISSED` / `INVALID` 就是有洞。

## 每个脚本对应哪一轮的哪条结论

| 脚本 | 覆盖 | 出处 |
|---|---|---|
| `mutate_b04.py` | 工具客户端（超时/重试/参数校验） | round 早 |
| `mutate_client.py` | 同上，补充分支 | round 早 |
| `mutate_judge.py` | 裁判口径（四态、门禁） | round 早 |
| `mutate_mastery.py` | 掌握度 EMA 与证据收敛；round-89 补 2 组：作废也计入 / 作废被不合格压过（17 组） | round-80 / round-89 |
| `mutate_scheduler.py` | FSRS 调度与 ReviewItem 生成 | round-84 / B-08 |
| `mutate_advisor.py` | 四因子排程（缺数据均值顶、平手格、改序审计） | round-85 / B-09 |
| `mutate_locate.py` | 第二套骨架（定位题）与按路径回放 | round-86 / B-05b |
| `mutate_decay.py` | P 的衰减（缺卡不衰减、锚点取更晚、时间不倒流、R 只算一处） | round-87 / B-08b |
| `mutate_snapshot.py` | 掌握度快照（重放唯一权威、P_raw 不存 P_eff、参数同存、没证据不写 0、v1→v2 迁移）；round-88 外审回件后补 3 组：词表拼错 / 迁移版本号超前 / 名次字段偷长（21 组） | round-88 / B-07c + 外审回件 |
| `mutate_b04b.py` | judge 接线层 + 仲裁队列（阈值边界 `<=`、永不入队、attach 改分、就地改原对象、配置检查、崩溃吞掉、派生列不填、重复入队覆盖阈值、裁决无理由、改判已裁决）（13 组） | round-90 / B-04b |

> **判定规则尚未统一（如实记）**：只有 `mutate_mastery.py` 与 `mutate_b04b.py` 是
> 「`rc == 1` 才算抓住」。其余 7 个仍是旧规则（`rc == 5` 判 `INVALID`、含 `error` 判 `INVALID`），
> round-89 已实测过旧规则会把节点不存在的 `no tests ran`（rc=4）判成 `DETECTED`。
> 它们各自的全 `DETECTED` **目前仍可信**（节点都在，跑出来是 `1 failed`），
> 但**改过测试名之后**必须人工核对一次，别只看脚本结论。统一判定登记在 BACKLOG（工具债）。

## 踩过的坑（写进脚本注释了，这里再记一次）

1. **每条变异必须换一个新的 `PYTEST_DEBUG_TEMPROOT`。** 复用同一个目录时，pytest 开场会清理
   旧编号目录，沙箱的 bulk-delete 守卫拦掉删除 ⇒ 整场 INTERNALERROR，输出里既没有 `failed`
   也没有断言信息，看起来像「变异无效」。round-84 和 round-85 各栽过一次。
2. **锚点打错地方会得到 `MISSED`，别急着加测试。** round-86 的 M1 打在只进 evidence、不参与
   判定的代码上，端到端用例当然抓不住——该换成打单元测试。
3. **全量 `pytest` 也要给一个全新的 `PYTEST_DEBUG_TEMPROOT`。** round-88 收尾时直接跑
   `pytest tests/ -q`，默认临时根下攒了 140 个旧编号目录，pytest 收尾的 `atexit` 清理
   撞上沙箱 bulk-delete 守卫 ⇒ 抛 `SystemExit(1)`、正常输出被 `tail` 挤掉、看起来像挂了
   6 分钟。这不是测试变红也不是死循环，换一个空临时根即可（与第 1 条同源，只是发生在收尾）。
4. **改过被测源码就必须重跑变异脚本。** round-88 外审处置把 `verify_one()` 里的
   reason 字面量改成从词表解包的常量，M5 / M6 两条锚点立刻失效 → `INVALID`。
   锚点是「源码里的一串字」，不是「语义」，重构会悄悄把它变成假抓住；
   所以每次改 `src/` 后要把对应变异脚本完整跑一遍，确认仍是全 `DETECTED`。
5. **测试改名会让变异"假抓住"，而旧判定看不出来。** round-89 把
   `test_gate_status_is_recorded_but_does_not_change_p` 改名后，M14 指向的节点不存在，
   pytest 退出码是 **4**（不是 5），旧脚本没拦，把 `no tests ran` 当成 `DETECTED` 打印出来。
   现在判定收紧成：**只有 `returncode == 1` 才算抓住**，2/3/4/5 一律 `INVALID`。
   改测试名的同一次提交里必须顺手改对应变异条目的节点。
