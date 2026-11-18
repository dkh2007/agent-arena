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

判定：末尾一行是「共 N 组：DETECTED N / MISSED 0 / INVALID 0」且退出码 0；
出现 `MISSED` / `INVALID` 就是有洞。判定代码在 `_runner.py`，脚本里不再各写一份。

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
| `mutate_b04d.py` | 争议护栏（标记恒空集 / 标记扩散 / 已裁决仍算争议 / 单点判据架空 / **读榜方向反了** / 参数不校验 / 落库不写产物 / **提交入口不传产物** / v4 加列不判重 / 证据一律不计入=滑回乙）（10 组） | round-91 / B-04d |
| `mutate_reappraisal.py` | 重评链（生效报告不生效 / 重放读旧报告 / delta 起点退回最原始分 / **缺产物拿空对象顶替** / 不校验任务 / 落库不校验身份证 / 空理由照收 / **演练也落库** / 批量只报成功 / 批量不筛任务 / **读接口退回旧件** / **view 写错静默回落** / **重评链把「不存在」当「没重评过」** / **读不到指纹当已符合** / **指纹取落库时那份（范围永不收敛）** / **丢了 task 过滤（全库重评）** / **坏指纹静默当没指纹** / **不校验 rubric 就给范围**）（19 组） | round-92 / B-04d 接线（round-93 补 M12–M14，round-95 补 M15–M19 打受影响范围） |
| `mutate_fingerprint.py` | 判据指纹（只算 credit 漏 gate / **漏 `skill_id`＝分数没变所以不重评** / 不排序键 / 按 id 排序吃掉数组顺序 / 指纹恒常量 / 绕过 `rubric_layers` 给非法 rubric 也起名字 / 版本串不进 payload / 报告不写指纹 / 可选键塞进 `required` / 格式不校验）（10 组） | round-94 / B-04e |
| `mutate_baseline.py` | 全库基线 + 「补不上」分档（跑完不重算范围 / **只跑 stale 不跑无指纹** / 补不上的算成补上了 / 演练也落库 / 重复任务悄悄去重 / 没理由也照跑 / 结果不带 user_id / **报表说「已全部打平」** / **所有补不上的都要交代** / 0 分有证据的归进「没分可解释」 / 读冻结报告而非生效报告 / 缺 ratio 默认 0 / 坏 JSON 当空报告 / total 用补不上的冒充全库）（14 组） | round-96 / B-04f；round-97 补 M9–M14（B-04g 取数层） |
| `mutate_packet.py` | 评审包「参照物」（不告子进程准备写入的数 / **生成期间仍比上一代包** / 生成期间一律放行 / 非生成期间不比对 / 快照自己再数一次）（5 组） | round-98 / 生成器参照物修复 |
| `mutate_exposure.py` | 规则级曝光（五态混并 / `not_applied` 并进 untouched / 缺指纹当没变 / 只报 T1 漏 T3 / 不按 task 收范围 / 变更规则并集丢单份差异 …）（12 组） | round-99 / B-04g 取数第二段 |
| `mutate_notices.py` | 对外文案（少一件可比边界 / 少「不是你的问题」/ 待定并进「不用管」/ 把没分的也推上页面 / 放行内部术语 / 加将来承诺 / 缺参数静默填空 / 分数变了当没变）（8 组） | round-100 / B-04g 处置计划 |
| `mutate_retirement.py` | 终止标记（标记能删名单里的人 / 标记后仍重试 / 产物补回还跳过 / 撤销是删行 / 无署名也写库 …）（12 组） | round-101 / B-04h ② |
| `mutate_cycle.py` | 跑批流程（演练也写库 / 只比份数不比 delta / 演练文案与真跑不同 / 冲突仍写 …）（10 组） | round-101 / B-04h ③ |

> **判定只有一处实现（2026-11-18 / round-102 统一）**：所有脚本的 `DETECTED` / `MISSED` / `INVALID`
> 都由 `tools/mutations/_runner.py` 的 `judge()` 给，各脚本里只剩「变异长什么样」。
>
> 之前这里是分叉的两套：8 个脚本用旧规则（`rc == 5` 判 `INVALID`、输出含 `error` 判 `INVALID`，
> **其余非 0 一律 `DETECTED`**）。旧规则会把 rc=2/3/4 当成抓住，于是**测试改名后脚本照样打印
> `DETECTED`**——round-89 M14 实测过（节点不存在时 pytest 退出码是 4）。
> 假绿比漏网贵：它让人以为"这条测试在管这事"。
>
> 判定器自己有自查，不需要跑任何真实变异：
>
> ```bash
> python tools/mutations/_runner.py --self-test
> ```
>
> 四种情形（真变异 / 无害变异 / 锚点不存在 / **节点名写错**）分别应得
> `DETECTED` / `MISSED` / `INVALID` / `INVALID`，末行 `SELF_TEST_PASS=True`。
> 第四条就是旧规则假绿的那条路。

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
5. **测试改名会让变异"假抓住"，旧判定看不出来，现在的判定能。** round-89 把
   `test_gate_status_is_recorded_but_does_not_change_p` 改名后，M14 指向的节点不存在，
   pytest 退出码是 **4**（不是 5），旧脚本没拦，把 `no tests ran` 当成 `DETECTED` 打印出来。
   现在 `_runner.judge()` 只认 `returncode == 1`，2/3/4/5 一律 `INVALID` 并在详情里写出
   退出码的含义（`用法错误（节点名可能写错） rc=4`），不用去查表。
   改测试名的同一次提交里必须顺手改对应变异条目的节点。
6. **不要一边跑全量 `pytest`、一边跑变异脚本。** round-93 同时起了 `pytest tests/ -q`
   和 `mutate_mastery.py`（后者自己再 spawn 一批 pytest 子进程），全量那边报
   `84 failed` —— 而每个文件单跑、以及串行重跑全量都是 `1250 passed`。
   两个真实原因叠在一起：SQLite 同一批临时库上的锁竞争，以及临时目录清理互相踩。
   **这类假红最贵**：它会让人去改本来没问题的代码。判据很简单——**失败不集中在一个文件、
   且每个文件单跑都绿 ⇒ 先怀疑并发，别先怀疑代码**。
