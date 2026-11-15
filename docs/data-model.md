# 数据模型 v0.1（M-1 第 2 步）

> 状态：**v0.1（2026-11-06 维护者拍板扩编至 7 实体；同日按外部评审第 7 条增补 Task.yaml_path 与资产边界，见开放决策 4）**。此后任何字段变更视为破坏性变更，走单独一步并更新本文件。
> 约定：字段名 snake_case（Python/SQLite）；实体名 PascalCase；字段变更视为破坏性变更，走单独一步并更新本文件。

## 0. 设计原则

1. **任务定义本体存 YAML 文件**（`tasks/<task_id>.yaml`：描述 + 原子 rubric + 测试输入 + 故障注入配置），数据库只存元数据与运行时状态——任务可走 Git 版本锁定（借鉴 PinchBench 的 Git 哈希锁）。
2. **不另设 mastery 表；掌握度与调度卡片同宿 UserSkillState**。技能节点的调度状态即 py-fsrs Card 的 D/S/R，
   **但 Card 不表示掌握度**（round-70 P1 拍板，权威定义见 §5.2）——掌握度 P 是"最近做题表现"的指数平滑量，
   与 Card 并列存放于同一实体、各管一件事。推论：一切"掌握度"查询仍走 UserSkillState，不引入第二套存储口径。
   > 原文「掌握度即 Card 的 D/S/R」自 round-70 起作废，与 §5.2「卡片参数只负责调度、不表示掌握度」相冲突，本轮（round-74）订正。
3. **错误模式标签词表先行**：`ErrorPattern.label` 全局唯一，新增标签先入词表再使用（对齐 HANDOFF 6.1）。
4. SQLite 起步，字段类型按 SQLite 可存 JSON 的现实设计（trace/diagnosis 存 JSON 文本）。

## 1. 骨架五实体

### User（学习者）

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | TEXT PK | UUID |
| display_name | TEXT | 匿名占位 |
| agent_endpoint_url | TEXT | 黑盒提交的 Agent 服务地址（注册填，单次提交可覆盖） |
| daily_review_cap | INTEGER, 默认 2 | 每日复习队列上限（风险对策 4.2-5） |
| created_at | DATETIME | |

### Task（情景任务，元数据）

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | TEXT PK | 与 YAML 文件名一致 |
| yaml_path | TEXT | 评测任务资产路径（tasks/<task_id>.yaml）——rubric/测试输入/故障注入等评测配置**只存 YAML，不入库**（资产边界见开放决策 4） |
| title | TEXT | |
| primary_skill_id | TEXT FK→Skill | 主考察技能 |
| secondary_skill_ids | JSON（FK 数组，可空） | 次要技能 |
| variant_template_id | TEXT, 可空 | 同模板 = 同变体组；FSRS 调度的是变体实例 |
| fault_injection | BOOLEAN | 是否含故障注入位 |
| git_hash | TEXT | 该版本任务的 Git 锁 |
| rubric_count | INTEGER | 原子断言条数（冗余存，免解析 YAML） |
| created_at | DATETIME | |

> **B-05 补充：`variant_template_id` 与 `variant_meta.family_id` 不是一回事，别混用。**
> - `variant_template_id`（入库，`tpl-tool-debug`）＝**骨架模板**：共用同一套断言骨架、分数可比的那些任务。
>   现在有 **2 套**（round-86 / B-05b）：`tpl-tool-debug`（10 个汇报题：分页拉数据→剔无效行→写报告）与
>   `tpl-bug-locate`（2 个定位题：读日志 + 读源码→给根因与行号）。同骨架内断言 id / 类型 / 权重逐字段相同，
>   跨骨架允许不同——但 `scoring` / `execution` / `submission_requirements` 全库统一，
>   否则"分数怎么算"随题型变，跨题型的 P 就不可比。
> - `variant_meta.family_id`（**只存 YAML，不入库**）＝**变体组**：按"练哪个技能"分组
>   （`order-report` / `retry-drill` / `param-surface`）。复习层按它挑题，才不会一直推同一类。
> - `variant_meta.difficulty`（1–3，只存 YAML）＝难度。汇报题与"要翻几页"绑定、定位题与
>   "日志里有几条 ERROR（要分辨几条干扰）"绑定，两者都是"要处理多少东西"的代理量（测试锁了下界）。
> - `variant_meta.kind` 对齐下方 `ReviewItem.kind`（`variant` / `fault_injection` / `timed` / `replay`）。
> - **骨架专属的 YAML 块**（都只存 YAML）：汇报题用 `tools[].mock.pagination` + `test_inputs.orders_fixture`；
>   定位题用 `locate`（日志/源码/产物三个路径）+ `test_inputs.files`（路径→文本）+ `mock.lookup`。
>   注意定位题的产物路径写了**两遍**（`locate.diagnosis_path` 与 `R1.target`/`R2.target`），
>   `tests/test_task_variants.py` 强制它们相等——两处真相源必须被测试焊住，否则改一处忘一处时
>   Agent 会写到 A、断言去读 B，表现为「没交产物」。
>
> 为什么不把 `variant_meta` 也入库：它只被"挑题"用，而挑题发生在读到 YAML 之后；
> 先入库等于给它一个自己会漂移的副本。等复习层真需要按难度/家族做 SQL 过滤时再入库。

### Skill（技能节点，全局共享，不含用户状态）

| 字段 | 类型 | 说明 |
|---|---|---|
| skill_id | TEXT PK | |
| name | TEXT | 如"工具参数校验" |
| prerequisites | JSON（FK 数组，可空） | 先决关系；MVP 只存不推（pyBKT 升级时启用） |
| created_at | DATETIME | |

> 注意：Skill 是全局分类学节点，**不挂任何用户状态**——这是新增 UserSkillState 的原因。

### ErrorPattern（错误模式）

| 字段 | 类型 | 说明 |
|---|---|---|
| pattern_id | TEXT PK | |
| label | TEXT UNIQUE | 词表标签：参数幻觉 / 工具选择错误 / 循环不收敛 / 未检查返回值 / 命名不一致 / 目录缺失 / 日期陷阱 … |
| description | TEXT | 判定口径 |
| detection_rule | JSON | 检测来源：原子断言 id / judge prompt id / trace 分析规则 id，三者其一 |
| mapped_skill_ids | JSON（FK 数组） | **错误模式 → 技能缺口映射表（自研核心）**，一个模式可映射多个缺口 |
| created_at | DATETIME | |

> **词表已落文件**：`docs/error-patterns-v0.yaml`（round-4 新增，14 个模式）。此前"label 全局唯一、先入词表"
> 只是一句约定，没有可校验的载体。现在 `tasks/*.yaml` 的 `rubric[].error_patterns` 只能引用该文件的
> `pattern_id`，且每个模式的 `detection_rule.assertion_id` 必须回指所在断言——由 `tests/test_task_yaml.py`
> 强制执行，词表与任务定义不会各自漂移。`mapped_skill_ids` 为空表示「只诊断不归因」（计分，不驱动掌握度/复习）。

### ReviewItem（复习项 = 可执行任务实例）

| 字段 | 类型 | 说明 |
|---|---|---|
| review_id | TEXT PK | |
| user_id | TEXT FK→User | |
| skill_id | TEXT FK→Skill | |
| kind | TEXT enum | `variant`（同技能新变体）/ `fault_injection`（注入故障调试题）/ `timed`（限时练习）/ `replay`（错题重放） |
| task_id | TEXT FK→Task, 可空 | replay 引用原任务；variant 由模板生成后回填 |
| source_submission_id | TEXT FK→Submission, 可空 | replay 的失败来源 |
| due | DATETIME | py-fsrs 输出的到期时间 |
| status | TEXT enum | `pending` / `done` / `skipped` |
| created_at | DATETIME | |

## 2. 运行时实体（2026-11-06 拍板采纳）

### 5.1 Submission（评测记录）——诊断报告与分数的落库处

| 字段 | 类型 | 说明 |
|---|---|---|
| submission_id | TEXT PK | |
| user_id | TEXT FK→User | |
| task_id | TEXT FK→Task | |
| task_git_hash | TEXT | 提交时锁定的任务版本 |
| agent_endpoint_url | TEXT | 本次评测实际调用的端点快照 |
| trace | JSON | OTel GenAI 标准轨迹（最小 trace schema 为提交格式一部分） |
| assert_score | REAL | 硬分 = **两段式**：门禁未过记 0，否则 credit 层加权通过率（分母恒为 credit 权重和 4），见下方口径 |
| judge_scores | JSON | 双评软分数组：每项 `{rating: 1–4, suspected_patterns: [...], rationale, model, latency_ms}`（round-79 定形，见下注） |
| judge_agreement | REAL, 可空 | 双评一致率（4.2-4 机制）：两两比较中 `rating` 相同的比例 |
| detected_patterns | JSON（FK 数组） | 轨迹分析检出的错误模式 |
| diagnosis | JSON | 诊断报告 DTO **v2**，结构见 `docs/diagnosis-report-schema.md` + `docs/diagnosis-report.schema.json`；由 `src/arena/diagnosis_report.py` 组装并过 schema 校验 |
| fsrs_rating | INTEGER 1–4 | 本次表现映射成的 FSRS 评分，用于更新卡片 |
| created_at | DATETIME | |

> **断言四态与计分口径（round-5 改为两段式，权威定义在 `tasks/*.yaml` 的 `scoring` 块）**：
> 断言分两层——`rubric_gate`（R1 交产物 / R5 正常退出，**不计重**，fail 即 `disqualified`、ratio 记 0）
> 与 `rubric_credit`（R2 / R3 / R4，分母恒为 4）。
> 四态：`pass`（计满权重，计入掌握度）/ `fail`（0 分，计入掌握度并产出错误模式）/
> `blocked`（前置断言未通过，无法判定：0 分，**不计入掌握度**）/ `unscored`（依赖数据缺失，如 trace 未上报：0 分，**不计入掌握度**）。
> 分母恒为 **credit 层权重之和**，**不因断言缺失而缩小**——否则"缺产物/不报 trace"反而分数虚高。
> 为什么门禁要独立成层：R4 是全称命题（零次调用平凡为真）、R5 只要不崩就过，两者混进同一个分母时
> "什么都不做"自动拿 2/6 = 33%，四份质量递进的提交全撞在 0.5。改造后为 0 / 0 / 0.25 / 0.50 / 1.00。
> 一句话：**分数惩罚"没交东西"，归因不把"没数据"当成"不会"**；两者混写是 A2E 那类满分的根源（见 `spike/README.md` 2.2）。
> 另一条：**门禁只管分数，不管观测**——门禁失败时 credit 断言照常判定并记录，只不计分（见 DTO §1 第 4 条）。

> **judge 侧的三条「不」（round-79 落地时定，改要人拍）**：
> judge 是**软分侧证**，不是判定层。它产出 `judge_scores` 与 `judge_agreement` 两个字段，
> 且：① **不进 `assert_score` / `ratio`**（确定性断言才是可复算的对账地基，决策 10）；
> ② **不进 `detected_patterns`**（`docs/error-patterns-v0.yaml` 里每条模式都有唯一的
> `detection_rule.assertion_id`，judge 给的是"怀疑"，字段名因此是 `suspected_patterns`）；
> ③ **不驱动 `fsrs_rating`**（判据不明时不得污染掌握度，与四态里 `unscored` 同理）。
> judge 存在的意义只有一个：**换一个观察者重看同一份提交，能不能得出同样的结论**。
> `judge_agreement` 低说明的是「判据表述有歧义」，不是「模型不行」也不是「考生答错」——
> 处置是进人工仲裁，不是调分。

### 5.2 UserSkillState（用户×技能的 FSRS 卡片宿主）

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | TEXT PK(联合) | 联合主键 (user_id, skill_id) |
| skill_id | TEXT PK(联合) | |
| fsrs_card | JSON | py-fsrs Card 序列化（due / stability / difficulty / last_review…） |
| updated_at | DATETIME | |

> FSRS 卡片粒度 = **(用户, 技能)** 二元组：同一技能每用户一张卡。首次提交产生检出错误模式后初始化卡片；此后每次 Submission 的 `fsrs_rating` 驱动 `review_card(card, rating)` 更新。
> **卡片参数只负责调度**（下一次什么时候复习），**不表示掌握度**。
> **掌握度 P（P1，round-70 定）**：由最近做题表现估计并随时间衰减——指数平滑 `P_k = (1-λ)·P_{k-1} + λ·c_k`（λ 为固定平滑系数），衰减只沿用**复习间隔**这一个时间轴，不引入第二套时间常数。
> **不把 FSRS 的 stability S 当作衰减系数**：S 的定义是「可提取性降到 90% 所需天数」（值域可达上万天），与 λ ∈ (0,1) 不可直接换算；若要用 S，须先定义并**标定**映射 `α = f(S)`，当前登记为**假设**（须留出数据做消融验证，见 `review-response-v1.md` §58.5 / §60）。
> **P 的落字段仍未定，但公式已有实现**（round-80）：`src/arena/mastery.py` 落地了上面的更新式，
> 输入是 `submissions.diagnosis` 历史，输出是内存里的 P 序列；本表**仍无字段承接 P**，
> 新增字段属破坏性变更，按本文件开头约定单开一步（登记 `docs/BACKLOG.md` B-07c）。
> 因此对外材料只能说「P 的**计算**已实现、可复核」，**不得说「P 已落库/Learner 画像已就绪」**。
>
> 同一步定下的三件事（都写成了代码，可被测试否掉）：
> - **`c_k` 的取法**：按断言自带 `skill_id` 分组后的**权重加权通过率**，不是整份 `score.ratio`。
>   一份提交压到多个技能时，用整份 `ratio` 更新每个技能会让 R2 答错也拉低 `tool_param_validation`。
> - **`unscored` / `blocked` 从分子分母一起剔除**：该技能在这份提交上没有计入断言时不产生证据、
>   P 原地不动（决策 7 的机械表达）。**不是记 0 分**。
> - **λ 默认 0.3、初值默认「首次观测播种」**：两者都是**假设**，不随本文件升级为结论；
>   λ 的标定与初值口径的取舍登记为 B-07b，须有多任务数据后做回测。
> - **衰减已落地，但默认不生效（round-87 / B-08b，`src/arena/decay.py`）**：
>   `P_eff = P_raw × R(t,S)`，`R` 直接取 py-fsrs 的 `get_card_retrievability`（全项目只在
>   `scheduler.retrievability_at()` 算一处）。**不给 `as_of` 就仍是旧行为**——所以老调用与
>   `test_decay_is_opt_in_and_plain_replay_ignores_the_gap` 的第一段断言都没变。
>   三条与上文限制的关系必须讲清，否则就是偷偷越界：
>   ① **没有第二套时间常数**：曲线的形状、`desired_retention=0.9` 全来自 py-fsrs，
>      本项目一个参数都没加；
>   ② **没有发明 `α=f(S)`**：S 只作为那条曲线自己的输入，不被当作 λ 的替换系数；
>      换言之"用 R(t,S) 这条路"我按"原样借用"理解了，**这属于对人拍条件①的暂定处置**，
>      若判定越界，回滚成本 = 删 `decay.py` + 把那条测试改回现状锁；
>   ③ **不需要 ReviewLog**：锚点取 `max(卡片 last_review, 最后一条计入证据的时刻)`，
>      问的是"多久没有新证据"这个事实，不是"计划什么时候到期"。原条目担心的
>      "把计划当历史"因此没有发生；`as_of` 早于锚点直接报错，不许倒着用。
>   **实测后要订正一句话**：我原以为"长期不练 P 会趋近 0"，跑完发现 FSRS 的曲线是重尾的
>   （S=1 天：1 天 0.90 / 30 天 0.59 / 550 天 0.38 / 20 年 0.25）——真实风险是**衰减太慢**，
>   不是衰减到 0。这条已写进 `decay.py` 文件头与测试，不要再对外说"不练就归零"。
> - **一处必须知道的后果**：门禁失败时 credit 断言照常判定（本文件 §5.1「门禁只管分数，不管观测」），
>   所以**一份 `disqualified=True`、`ratio=0` 的提交仍会抬高它通过的那几个技能的 P**。
>   实测 `raw-no-file`（没交产物）会让 `failure_retry` 与 `tool_param_validation` 的 c 都到 1.0。
>   这不是 bug 而是口径的推论，但意味着"不交产物、重试规范"能刷高掌握度。
>   **量级已实测**（round-81）：因初值「首次观测播种」，**一份白卷的 P 就是 1.0000**，
>   与真做对一次不可区分（`test_one_white_paper_looks_like_a_perfect_run`）；从 P=0 起刷 7 次到 0.9176。
>   ⇒ "刷几十次才涨一点"的乐观假设不成立。要不要给门禁开例外属产品口径（A 现状 / B 排除 / C 降权），
>   登记 `docs/BACKLOG.md` B-07b，**在拍板前不改代码**。
> - **门禁状态逐条留痕（A2，round-81）**：每条证据与每一步递推都记 `gate_status`
>   （`ok` / `disqualified` / `voided`），但**不参与计算**。目的是把 A→C→B 的切换降成
>   计入层的一条过滤/乘子，而不是回头改已积累的历史。**切换时禁止**把报告里的 credit 断言改标
>   `blocked`（那会用评分逻辑吞掉诊断数据，见 `diagnosis-report-schema.md` §1 第 4 条）。
> - **P 不落库这件事不影响"给自己看"（2026-11-15 口径）**：产品定位是「成绩先给自己看、用于督促」，
>   而"看进步曲线"不需要快照——每份报告都在 `submissions` 里，`mastery.build_series()` 随时重放
>   （`scripts/mastery_scorecard.py` 就是这一形态，零 schema 改动）。需要快照的是
>   **"按 P 排序多个用户"**（排名/班级榜）这类查询；B-07c 的开工条件因此写死为"出现排序需求"。

> 「掌握度 = FSRS 的 R 值」的原写法**作废**；两种口径并存的过渡状态随之结束。
> ReviewLog（py-fsrs 的复习日志）**后置**：M0 不建表，复习事件可从 Submission + ReviewItem 重建；M1 接入参数优化时再加。
> **round-84 的补充（B-08 落地后）**：现在的"复习做完了"只有一条来路——**又交了一份有效提交**。
> `store.close_due_reviews()` 会把该技能下「本次提交时刻之前已到期」的 `pending` 项标 `done`，
> 所以队列不会只增不减。**没有**"我看了题但没交代码"这类事件（那是 B-08c）。
> 三条与调度相关的口径写在 `src/arena/scheduler.py` 文件头：
> ① 每份有效提交都更新卡片（做题即复习，间隔往后推），只有"这次没过"才建复习项；
> ② 「本次不算数」的提交（`suppression_reasons()` 非空：门禁不合格 / 版本不一致 / 缺过程）
>    **不动卡、不建项、不关旧项**——卡是排期不是能力估计，用不算数的一次推进间隔会让真实需求延后；
>    这**不改** P 的口径（P 仍按 A 现状照记，见 B-07b）；
> ③ `daily_review_cap`（本文件 `User` 表那个字段）只截断**今日队列视图**，被截掉的项仍是 `pending`。

### 5.3 排程层（建议排期）的口径（round-85 / B-09）

> 本层**不新增实体、不改任何字段**。它只读 `review_items` / `user_skill_states` / `submissions` /
> `error_patterns`，产出一份"建议 + 理由 + 改序审计"。权威实现见 `src/arena/schedule_advisor.py` 文件头。

**四个因子的数据来源（第三列就是对外该怎么说）**

| 因子（优先级由高到低） | 来源 | 现状 |
|---|---|---|
| 项目截止 | —— | **无数据源**：`SCHEMA_SQL` 无任何截止字段，也无项目实体 ⇒ 一律**弃权** |
| 遗忘风险 `1-R` | py-fsrs `get_card_retrievability(card, at)` | 有。`stability is None`（未复习过的卡）记 `None`，**不用 py-fsrs 的 0.0**——那会把"还没开始"说成"已经忘完" |
| 错误热度 | `submissions.detected_patterns` × `error_patterns.mapped_skill_ids`，近 7 天 | 有。按**提交**计数：一份提交同时踩两个同类错，对该技能只算 1 次 |
| 掌握度缺口 `1-P` | `mastery.replay()`（`submissions.diagnosis`） | 有。P 不落库，但排程只要"此刻的 P"，不需要历史快照 |

**缺数据的三条处置（顺序不能乱）**：① 个别项缺 ⇒ 用**同批均值**顶，并在理由里标出来；
② 整批都缺 ⇒ 该因子退出、权重归一化（此时对所有项一视同仁，不改相对顺序）；
③ **绝不记 0** ——那是把"未知"说成"不紧急"（决策 7 的反面），也绝不重归一化到只剩已有因子
（那会让"数据越少分越高"）。

**数字全是假设**：权重 8:4:2:1、平手格 0.02、热度窗口 7 天、截止视界 7 天，与 λ / rating 阈值同一性质，
未标定（`docs/BACKLOG.md` B-09c）。**权重必须沿优先级严格递减**，由导入期校验强制——
否则"截止优先"是空话，而排序结果看着仍然正常。

**改序审计不落库**：审计行（系统建议第几 → 实际第几 + 理由 + 时刻）目前写本地 JSONL
（`advise.append_audit`）。它**不是防篡改的**，作用只是"改过就会被看见"；
要"改不了"需要服务端 append-only + 身份认证，等 B-10 有前端之后再定表。
**禁止**在对外材料里说"四因子排程已实现"——准确说法是「四因子框架、三因子已接入数据、
截止因子留接口并会显式弃权」。

## 3. 关系一览

```
User ──1:N── Submission ──N:1── Task ──N:1── Skill（primary）
  │                │  N:N→ ErrorPattern          │
  │                └─ source_submission_id ←─────┼── ReviewItem ──N:1── Skill
  └──1:N── UserSkillState ──N:1── Skill          └── ErrorPattern ──N:N── Skill（mapped_skill_ids）
```

一次闭环的数据流：提交 → Submission（trace+分数+检出模式）→ detected_patterns ∘ mapped_skill_ids 定位技能缺口 → 初始化/更新 UserSkillState 卡片（fsrs_rating）→ 生成 ReviewItem（due）→ 排程层读 due 集合 + 卡片 + 历史产出建议排期（§5.3：四因子加权，三因子有数据、截止弃权）。

## 4. 开放决策记录

1. **运行时实体扩编**：✅ 已裁决（2026-11-06）——采纳扩到 7（+Submission、+UserSkillState）。
2. **fsrs_rating 映射**（断言通过率 → FSRS 1–4）：草案假设 ≥0.9→4 / ≥0.7→3 / ≥0.4→2 / <0.4→1。**标为假设**，dogfooding 后校准；学长若认为应纳入 judge 软分权重，在此改。
3. **prerequisites 先决关系**：MVP 只存不推（推荐层是规则表，用不到图遍历）；留字段是为 pyBKT 升级免迁移。
4. **项目截止挂在什么粒度**（round-85 新增，**待裁决**）：排程层四个因子里唯一没有数据的一个。
   候选：按**任务**（`tasks.deadline`，改动最小，但"这个技能什么时候要用"表达不出来）/
   按**技能**（最能驱动复习，但与"项目"的语义脱节）/ 按**项目**（新增实体，最贴计划书措辞，
   改动最大）。三者都会动 schema，按本文件开头约定单开一步（`docs/BACKLOG.md` B-09b）。
   **未裁决前**截止因子一律弃权，不编数据、也不把"没截止"当"不紧急"。

5. **评测资产 vs DB 实体边界**（2026-11-06 外部评审第 7 条采纳）：`tasks/<task_id>.yaml` 是**评测任务资产**（rubric/测试输入/故障注入/trace 要求/分页 mock 语义），只走 Git 版本控制；Task 表只存核心元数据 + `yaml_path` + `git_hash` 指向资产。`secondary_skill_ids` 等多对多关系 v0 用 JSON 数组存（SQLite 现实约束），M1 出现按技能反查任务的需求时再建关联表——届时为破坏性变更，单独一步。
