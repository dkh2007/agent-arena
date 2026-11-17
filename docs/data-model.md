# 数据模型 v0.1（M-1 第 2 步）

> 状态：**v0.7（2026-11-17 / round-101：`SCHEMA_VERSION` 5→6。新增第十一张表
> `unrecoverable_marks`（「确认不可回溯」的终止标记，见 §5.6）。
> 它只终止**重试**——补不上的行不再每轮被重新尝试；`blocked_submissions()`、T1/T2/T3 分档、
> 曝光表、对外文案**一处都没改**，标记过的行照样要交代。旧表一个字段没动，`submissions` 不加列：
> 标记只有一处真相源。
> 上一版：**v0.6（2026-11-16 / round-93：无表结构变更，补一条**写契约**——`store.atomic()`。
> 凡是"写结论 + 刷派生值"（提交↔P 快照、提交↔仲裁入队、重评落库↔P 快照）必须在同一笔里；
> 它是 SAVEPOINT 实现、可嵌套。`with conn:` 不可参与嵌套：它退出即 commit（内层会提交外层），
> 且**进入时不 BEGIN**（`SELECT` 不触发隐式事务），内层会自行 BEGIN+COMMIT。
> 详见 `review-response-v1.md` §94）**。
> 上一版：**v0.5（2026-11-16 / round-92：B-04d 接线第一步，`SCHEMA_VERSION` 4→5。
> 新增第十张表 `reappraisals`（重评记录，append-only，见 §5.5），并把「按时间重放」
> 的读数入口改成**生效报告**（`store.user_reports()` 返回 `report`，不再返回 `diagnosis`）。
> 旧表一个字段没动、没删；`submissions` 一行不新增——**一份作答在 P 的重放里永远只算一个 step**）**。
> 上一版：**v0.4（2026-11-16 / round-91：B-04d 升 `SCHEMA_VERSION` 3→4。两处变更，**一列 + 一条口径**：
> ① `submissions` 增 `artifacts` 列（原始产物）——这是「标准改了能不能按新判据重评历史提交」
> 唯一的输入来源，此前只存 trace 与报告，等于只存结论不存证据；
> ② 新增「标准待复核」标记口径：judge 双评不一致的提交照算 P、照排复习（甲），
> 但它的成绩**默认不进正式档案/排名**（`list_ranked()` 默认排除）。
> 标记**不落列**，从 `arbitration_queue` 现算，见 §5.3 末段。
> 旧表一个字段没删、没改，表数仍是 9）**。
> 上一版：**v0.3（2026-11-16 / round-90：B-04b 升 `SCHEMA_VERSION` 2→3，新增第九张表
> `arbitration_queue` 承接 judge 双评不一致的人工待办，见 §5.4；同轮报告 DTO 增两个可选字段
> `judge_scores` / `judge_agreement`（不加则无、不加不报错）；旧表一个字段没动）**。
> 上一版：**v0.2（2026-11-15 / round-88：B-07c 升 1→2，新增 `mastery_snapshots`）**。
> 上一版：**v0.1（2026-11-06 维护者拍板扩编至 7 实体；同日按外部评审第 7 条增补
> Task.yaml_path 与资产边界，见开放决策 4）**。此后任何字段变更视为破坏性变更，走单独一步并更新本文件。
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
| artifacts | JSON, 可空 | **原始产物**（v4 / B-04d）：提交方交上来的文件内容。`NULL` = 平台没收到产物，**不是**"收到空产物"（后者是 `{}`）。存它的唯一理由是「判据改了能不能按新标准重评历史提交」——只存报告等于只存结论，重评时无输入可评。已实测：有产物的提交改动 `R2` 标准答案后重评，`ratio` 1.00→0.50、`P` 1.00→0.00（`scripts/reappraisal_probe.py`）。**边界**：v4 之前落库的提交这一列是 `NULL`，补不回来 |
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

> **2026-11-16 / round-90（B-04b）补订**：上面这三个字段此前只存在于类型与表结构里，
> 报告本身不带、也没有任何调用方。现在报告可选地带上它们（`diagnosis-report.schema.json`
> 已登记，缺省即无），落库时写进 `submissions` 的两个同名派生列，低一致率进 §5.4 的队列。
> ③ 的可机械判定的形式就是：`schedule_submission()` 与 `mastery.build_series()` 对
> 「带 judge 字段的报告」与「不带的同一份报告」输出逐项相同——
> `tests/test_judge_pipeline.py::test_queue_membership_does_not_change_mastery_or_scheduling`
> 就是这条，它是**可以红**的（改判读法 B 时会红）。
> 另：`disqualified` 的 A 口径与 `voided` 的排除（round-89 甲）都不受影响。

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
> **P 的公式与落库（round-80 计算 / round-88 落库）**：`src/arena/mastery.py` 实现上面的更新式，
> 输入是 `submissions.diagnosis` 历史；**`build_series()` 的重放是唯一权威**。
> v2（B-07c）新增 `mastery_snapshots` 表把当前 `P_raw` 抄成**缓存**（见下文 §5.3），
> 本表（UserSkillState）**仍然没有 P 字段，一个字段都没动**。
> 因此对外材料可以说「P 已落库、可重启读回并逐位对账」，
> 但**仍不得说「Learner 画像已就绪 / 排名已可用」**——排名（B-25）未开，
> 且开榜前须先切 C 口径（B-07b）。
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
>      换言之"用 R(t,S) 这条路"我按"原样借用"理解了。**原登记为「对人拍条件①的暂定处置」；
>      2026-11-15 人已拍板接受**（原话「"半年没练只掉四成"我接受」）⇒ 该处置转为确定，
>      原写的"回滚成本 = 删 `decay.py` + 改回现状锁"作废。接受的是曲线**形状**与重尾后果，
>      **不是**标定结果；「曲线是借的、没标定」仍是本文件的边界。
>      反证条件：真实提交回测显示"多久没练"与"实际做对率"明显偏离 `R(t,S)` ⇒ 复开重拍；
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
>   （`ok` / `disqualified` / `voided`）。`disqualified` **不参与计算**（B-07b 的 A 口径）；
>   `voided` 自 round-89 起**整份排除**（见下一条）。留痕的目的是把 A→C→B 的切换降成
>   计入层的一条过滤/乘子，而不是回头改已积累的历史。**切换时禁止**把报告里的 credit 断言改标
>   `blocked`（那会用评分逻辑吞掉诊断数据，见 `diagnosis-report-schema.md` §1 第 4 条）。
> - **作废提交不计入 P（round-89 / 2026-11-16 人拍板「甲」，已落地）**：
>   人原话「作废不计成绩/不计掌握度，但保留练习提交记录」。落地分两层：
>   ① `submissions` 行与报告**照留**——它是一次练习记录，可回看、可重放；
>   ② `mastery.skill_evidence()` 对 `score.voided` 的提交返回 `counted_weight=0`，
>   每条断言标 `voided:state=...`，于是 `build_series()` 不被推动；只有作废提交时
>   `p is None`，`mastery_snapshots` **没有行**（决策 7：缺数据不是 0 分）。
>   **`voided` 优先于 `disqualified`**：既不合格又作废的提交（交了空白旧版）按作废算，
>   否则版本错位照样能刷高 P——这条顺序 round-89 实现时踩到过，已由测试钉住。
>   **这不等于切 B**：`disqualified`（没交产物/没跑完）仍是 A 口径、证据照记。
>   口径改动会让旧快照的 `mastery_p` 与重放不符 ⇒ 旧库上线后必须跑
>   `mastery_snapshot.recompute_all()`；`verify_all()` 会把不一致报成 `stale_value`，
>   不会静默混进读路径。
> - **P 不落库这件事不影响"给自己看"（2026-11-15 口径，仍成立）**：产品定位是
>   「成绩先给自己看、用于督促」，而"看进步曲线"不需要快照——每份报告都在 `submissions` 里，
>   `mastery.build_series()` 随时重放（`scripts/mastery_scorecard.py` 就是这一形态，
>   **至今零 schema 改动、也不读快照表**）。需要快照的是
>   **"按 P 排序多个用户"**（排名/班级榜）这类查询；B-07c 的开工条件原写死为"出现排序需求"。
>   **2026-11-15 订正（追加，不回改上文）**：该需求**尚未出现**，是人直接点名「可以继续推 B-07c」
>   提前做的技术地基，见 `docs/BACKLOG.md` B-07c 开头与 `review-response-v1.md` §87.3。
>   ⇒ B-25 排名仍为未开，A→C 的硬触发不动。

### 5.3 MasterySnapshot（v2 / B-07c：P 的缓存，非第二真相源）

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | TEXT PK(联合), FK→User | 联合主键 (user_id, skill_id) |
| skill_id | TEXT PK(联合), FK→Skill | 只对**已注册**技能落快照 |
| mastery_p | REAL [0,1] | 当前 `P_raw`，**不是**衰减后的 `P_eff` |
| lam | REAL (0,1) | 算出这个值用的 λ（仍是假设，未标定） |
| p0 | REAL, 可空 | 初值口径；`NULL` = 首次观测播种 |
| evidence_count | INTEGER ≥0 | 计入证据条数 |
| last_submission_id | TEXT, 可空 | 最后一条计入证据的提交 |
| updated_at | DATETIME | 本次重算时刻 |

> **对 §0 原则 2 的一处字面偏离，摆到明面**：原则 2 写「不另设 mastery 表；掌握度与调度卡片
> 同宿 UserSkillState」。本轮**没有**把 P 加进 UserSkillState，而是另立本表。
> 理由：B-08b 的 `stability is None ⇒ None`／"没卡 ≠ 忘光了"契约靠 UserSkillState 的
> **空行 / 有卡**区分，往里塞 P 会逼出"只有 P 没卡"的半行，把"空卡"和"没卡"混成一件事。
> 原则 2 真正禁止的是**第二套掌握度口径**——本表不定义任何算法，P 仍只由
> `mastery.build_series()` 一处产出；这里存的只是它的可丢弃副本。
> **反证条件**：若判定这处偏离不可接受，回滚成本 = 删本表相关代码 +
> `SCHEMA_VERSION` 降回 1 + 反转三条现状锁，**无需数据迁移**（旧表一字节未动）。
>
> **四条契约**（每条都有测试否得掉）：
> ① **重放是权威**：`mastery_snapshot.verify_*()` 现场重放再比对，不一致信重放；
>    `recompute_all()` / `repair_one()` 是重算入口。核对一律纯读，绝不顺手写库。
>    **2026-11-15 外审 F2 订正（追加，不回改）**：`recompute_all()` 的扫描范围只有
>    「已注册用户 × 已注册技能」，范围外的孤儿行（技能未注册 / 用户未注册）
>    `verify_all()` 报得出却修不掉。原写「孤儿行照样会被清掉」是错的，已改源码 docstring；
>    是否扩成全表清理属产品口径（技能被删时快照可能是唯一历史痕迹），登记 B-07d。
> ② **只存 `P_raw`**：衰减是读法（`P_eff = P_raw × R(t,S)`，需显式 `as_of`），不是新状态；
>    把 `as_of` 钉进快照会造第二真相源（`review-response-v1.md` §86.8 ④）。
> ③ **没证据 ⇒ 没这行**，不写 0（决策 7：数据缺失 ≠ 能力不足）；
>    否则排序会把"没做过"排成"完全不会"。
> ④ **`lam` / `p0` / `evidence_count` / `last_submission_id` 必须一起比对**：
>    λ 标定（B-07b）后 P 可能碰巧不变，只比 P 会让旧口径的快照静默混进新榜。
>
> **迁移**：`store.migrate()` 把 `PRAGMA user_version` 1→2，只补本表与索引，
> 并按已有 `submissions` 重放回填；旧表不动、不许降级、重复调用幂等。
> `SCHEMA_SQL_V1` 是 v1 结构的唯一可复核定义（测试真的用它造老库）。
>
> **没有的东西**：没有名次字段、没有榜单页；没有增量更新（每次提交是全技能重放，
> 写侧 O(历史×技能数)）；没有多进程写锁。本表的存在**不等于**排名可以上。
>
> **但有的东西（2026-11-15 外审 F5 订正：原写「没有跨用户比较口径」与代码不符）**：
> `list_mastery_snapshots(order_by_p=True)` 的 `ORDER BY mastery_p DESC, user_id ASC`
> 已经把**跨用户比较**和**并列时按 `user_id` 字典序兜底**写死在 SQL 里。
> 该查询当前在全仓无生产调用方、B-25 排名未开 ⇒ 此刻零外发风险；
> 但 B-25 开工前必须重拍并列规则，并先核 `user_id` 是否含学号/账号
> （不得把账号字典序随对外榜单一起发布）。见 `review-response-v1.md` §88。
>
> **换 λ 的正确顺序（外审 F7，此前没写下来）**：先改 `mastery.LAMBDA_DEFAULT`
> → 立刻 `recompute_all()` 全库重算 → 再 `verify_all()`。
> 反过来会看到全库 `stale_params` 红灯，而它与「有人改坏了库」在信号上长得一样：
> 前者是**口径漂移**，后者才是数据损坏，两者处置完全不同。

> #### 「标准待复核」标记（v4 / B-04d，2026-11-16 人拍板「甲 + 护栏」）
>
> **人拍板的原话拆成四句**（落地形态照这四句，不多不少）：
> ① 临时成绩照出（争议提交照算 P，学习者看得到自己的分）；
> ② 复习照排（不让"下次什么时候练"变成空白）；
> ③ 争议答卷打「标准待复核」标记（这是系统侧的账，不是学生的）；
> ④ 正式档案/排名**默认不含**它，标准改完后批量回溯重算。
>
> **为什么不是乙（先冻结这份提交）**：双评不同档是**判据表述有歧义**的信号，
> 不是"答卷无效"的证据。冻结等于把系统缺陷记成学习者的成绩缺失（决策 7 的反面），
> 而且"等人把标准改清楚"没有确定时限——冻结事实上会无限期。
> **代价要认**：甲比乙多一步返工（判据改完后要回溯重算），所以护栏必须能真回溯。
>
> **标记怎么算**（`mastery_snapshot.provisional_pairs()`）：
> 对每份 `arbitration_queue` 里 `status='pending'` 的提交，重跑一次 `mastery.skill_evidence()`，
> 问它是否给该技能贡献了**计入**证据。是 ⇒ 该 `(user, skill)` 的 P 标「待复核」。
> 三条边界都实测锁住（`tests/test_b04d_guardrail.py`）：
> - 只标它**真喂过证据**的技能，不扩散成"这个人整体可疑"；
> - **作废**（`voided`）的争议提交不标——成绩里根本没有它，标了就成了无从下手的谜题；
> - 一致率够的提交不标（反面对照，防"全都标"也能过）。
>
> **为什么不落一列**：存了就得在入队、裁决、重评、迁移四处记得刷新，漏一处会永久留下
> 一条"标准待复核"而队列里什么都没有。现算的成本只与**争议条数**有关，与历史长度无关。
> `is_provisional()` 是唯一判据，读榜与展示层共用。
>
> **读榜方向不能反**：`list_ranked(..., include_provisional=False)` 是**默认**。
> 若默认含、要显式排掉，任何新写的读榜接口都会在"忘了传参数"时把含糊成绩放出去。
> `include_provisional=True` 时每行带 `provisional` 布尔，供"进档案但带争议标记"的场景用。
>
> **回流（裁决之后）**：标记是派生的，`resolve_arbitration()` 一落状态就自动消失，
> 不需要额外刷新——所以不存在"标记烂在库里"的情形。
> **但"成绩要不要按新判据重算"是另一件事**：它需要原始产物重评，
> 不是 `recompute_all()`（那只是把已有报告重放一遍，报告没变，数还是同一个）。
> 重评入口**尚未接进主流程**，可跑的证据是 `scripts/reappraisal_probe.py`；
> 接线登记在 `BACKLOG.md` B-04d 的遗留项。

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
| 掌握度缺口 `1-P` | `mastery.replay()`（`submissions.diagnosis`） | 有。排程只要"此刻的 P"，走重放、不读快照表（避免把缓存当权威）；v2 已有 `mastery_snapshots` 缓存，但本层刻意不用 |

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

### 5.4 ArbitrationEntry（v3 / B-04b：judge 双评不一致时的人工待办）

| 字段 | 类型 | 说明 |
|---|---|---|
| submission_id | TEXT PK, FK→Submission | **主键就是提交**：一份提交最多一条待办，重复入队幂等 |
| user_id | TEXT, FK→User | 从 `submissions` 现读，不由调用方传（少一个会对不上的副本） |
| task_id | TEXT, FK→Task | 同上 |
| judge_agreement | REAL [0,1] | 入队时的一致率（两两比较中 `rating` 相同的比例） |
| threshold | REAL (0,1] | 入队时用的阈值。**记下来是因为它会被标定改变**：不记就无法复核"当时按多少判的" |
| reason | TEXT | 目前只有 `judge_disagreement`；留列是为了将来别的入队原因不至于改表 |
| status | TEXT | `pending` / `resolved` / `dismissed` |
| opened_at | DATETIME | 入队时刻 |
| resolved_at | DATETIME, 可空 | 裁决时刻 |
| resolution_note | TEXT, 可空 | 裁决理由 |

> **它是什么**：`judge_agreement < threshold` 时的一份**待办**——提示"判据表述可能有歧义，
> 需要人看一眼"。`judge` 与 `threshold` 的算法仍只在 `llm_judge.py` 一处。

> **它不是什么（本轮最关键的一条）**：**不是闸门**。队列里的提交照常计入 P、照常排复习。
> 理由：低一致率说明的是**判据表述**有歧义（系统侧），把它冻结成"这份成绩不算"等于把自己的
> 缺陷记在学习者账上（决策 7「数据缺失 ≠ 能力不足」的同一条推论）。
> `llm_judge` 模块文档早就写了「它不是『模型不好』，也不是『考生答错了』」。
> **代价**：人工判定"判据要改"之后，历史成绩需要重算（`mastery_snapshot.recompute_all()` 现成）。
> ⇒ 另一个读法（低一致率先冻结、裁决后再放行）**已登记待人拍**；要切只需让计入层/调度层读队列
> 状态，**不必改本表结构**。见 `review-response-v1.md` §90。

> **与 `submissions.judge_agreement` 的关系**：那一列是"这份提交评出的一致率"，
> 本表是"因为低到阈值以下而开出的待办"。两件事方向不同：前者可能有多份、
> 后者只有一条，且带自己的时间轴与结论。**没有把待办状态抄回 `submissions`**——
> 抄一份就变成第二真相源。

> **迁移**：`store.migrate()` 把 `PRAGMA user_version` 2→3，只补本表与索引；
> 从 v1 直接升会逐级跑 v2、v3 两段。旧表一个字段不动、不许降级、重复调用幂等。

> **没有的东西**：没有自动清理（裁决后保留，作为"判据改过几次"的历史）、
> 没有分配仲裁人（无鉴权，见 B-10 前的边界）、没有 SLA / 提醒。

### 5.5 Reappraisal（v5 / B-04d 接线：同一份作答按改后的判据重新打的分）

| 字段 | 类型 | 说明 |
|---|---|---|
| submission_id | TEXT, FK→Submission | 重评的是**哪一份作答**。联合主键的前半 |
| revision | INTEGER ≥ 1 | 第几次重评。联合主键的后半，`save_reappraisal()` 自动取 `max+1` |
| report | JSON | 重评得到的**整份新报告**（与 `submissions.diagnosis` 同结构） |
| reason | TEXT NOT NULL | 为什么重评：改了哪一条判据。**空理由被表级 CHECK 拒绝** |
| created_at | DATETIME | 重评时刻 |

> **它是什么**：人裁决"判据要改"之后，把受影响的作答按**新 rubric** 重新打一遍分，
> 新结论存在这里。这是「标准改完后批量回溯重算」这句口径在库里的落点。

> **为什么不是"插一条新 submission"**：那样同一份作答会在 P 的重放里贡献两个 step，
> 一个人的掌握度会因为"系统改了一次判据"而凭空变化（练一次变成练两次）。
> **一份作答永远只算一个 step**，换的是那一步用的报告，不是步数。
> 这条有实测锁：`test_one_submission_still_contributes_exactly_one_step`。

> **为什么不覆盖 `submissions.diagnosis`**：那是"当时按什么判的"的唯一证据。
> 覆盖它等于让分数争议失去可比对的基线，与"诊断记录不覆盖"是同一条铁律。
> `submissions.assert_score` 同样不更新——它是**当时算出的分数**，不是当前生效的分数。
> 当前生效的分数看 `store.effective_ratio()`。

> **为什么不存 ratio / delta / P**：全是 `report` 的派生量。落一列就多一处漂移点，
> 而这张表的唯一真相源就是 `report` 本身。

> **append-only**：已有 revision 不许改、不许删（主键 + 不提供 UPDATE/DELETE 接口）。
> 要改结论就再追加一条，`delta` 会从**上一次的结论**起算，不是从最原始那个分数。

> **身份校验（落库层强制）**：新报告的 `submission_id` / `task_id` 必须与原件一致；
> 报告若带 `user_id`，也必须与提交人一致。把成绩记到别人头上、或拿别的题的 rubric
> 重评，都在 `store.save_reappraisal()` / `reappraisal.rebuild_report()` 两处被拒。

> **生效报告的读法**：`store.effective_report()`（有重评取最新，否则原始）与
> `store.effective_ratio()`。**`store.load_report()` 仍然返回当时那份**——两个函数分开
> 而不是加 `effective=True` 开关：叫 `load_report` 却可能返回另一份，迟早有人拿它去对账历史。
> 「按时间重放」的入口 `store.user_reports()` 返回的键已从 `diagnosis` 改名为 `report`
> 且值是生效版，`detected_patterns` 也按生效报告重算（重评可能让某个错误消失或新增）。

> **缺产物就不重评**：`artifacts IS NULL` 的提交返回 `state="missing_artifacts"`，成绩不动。
> 用 `{}` 顶替会算出"没交产物"的结论——把"我们没存下来"算成"他没交"，
> 两份在报告里长得一模一样（决策 7）。代价：v4 之前落库的提交永远重评不了，这不是本表能修的。

> **迁移**：`store.migrate()` 4→5，只建本表；从 v1 直连会逐级跑 v2、v3、v4、v5 四段。

### 5.6 UnrecoverableMark（v6 / B-04h ②：「确认不可回溯」的终止标记）

| 列 | 类型 | 说明 |
|---|---|---|
| `submission_id` | TEXT PK → `submissions` | 一份提交一个标记 |
| `reason` | TEXT NOT NULL 非空 | 为什么判定"输入永远回不来" |
| `marked_by` | TEXT NOT NULL 非空 | 谁说的（没有署名的结案 = 悄悄跳过） |
| `marked_at` | TEXT NOT NULL | 什么时候说的 |
| `revoked_at` / `revoked_reason` | TEXT NULL，成对 | 撤销时间与理由；两列同时有或同时无（CHECK 锁） |

**它终止什么**：`reappraisal.scope_for_task()` 里该行进 `retired` 桶、不进 `all_ids`，
于是跑批不再每轮重试它、也不再每轮报一次 `missing_artifacts`。

**它不终止什么**（这一条比上面重要）：`store.blocked_submissions()` 仍返回它、
`tier_split()` 仍给它分档、`exposure_split()` 仍可能把它放进 `must_explain()`、
`notices.plan_notices()` 的状态/告知形态/文案**逐字不变**（只多一个 `retired=True`）。
把标记做成"从名单里消失"，它就变成静默忘掉一个人的开关。

**前提每次都要复核**：标记说的是"输入永远回不来"，而 `submissions.artifacts` 是可变的。
产物补回来之后，该行按普通行重新分类，并进 `ReappraisalScope.ignored_marks` 显名——
"标记被现实推翻"和"标记被撤销"是两种现实，前者没人签过字，所以必须自己浮出来。

**为什么带撤销字段而不是 DELETE**：标记是一个判断，判断会被推翻。删行会让
"谁在什么时候说过它补不回来"消失，而那正是事后复盘唯一能追的线。

**为什么不做成自动规则**（"试了三次就跳过"）：那会把**平台当年没存产物**变成
**这个人不重要**——两件事在数据上一样，在责任上相反。出口必须是人给的、留痕的、可撤回的。

> **迁移**：`store.migrate()` 5→6，只建本表；从 v1 直连会逐级跑 v2…v6 六段。
> 反证条件：若将来发现更完整的输入（平台侧全量抓包）能替代 `artifacts`，这一列的前提变了，
> 本表应先被批量撤销再谈要不要留。

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
