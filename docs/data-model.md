# 数据模型 v0.1（M-1 第 2 步）

> 状态：**v0.1（2026-11-06 小D 拍板扩编至 7 实体；同日按外部评审第 7 条增补 Task.yaml_path 与资产边界，见开放决策 4）**。此后任何字段变更视为破坏性变更，走单独一步并更新本文件。
> 约定：字段名 snake_case（Python/SQLite）；实体名 PascalCase；字段变更视为破坏性变更，走单独一步并更新本文件。

## 0. 设计原则

1. **任务定义本体存 YAML 文件**（`tasks/<task_id>.yaml`：描述 + 原子 rubric + 测试输入 + 故障注入配置），数据库只存元数据与运行时状态——任务可走 Git 版本锁定（借鉴 PinchBench 的 Git 哈希锁）。
2. **掌握度模型 = FSRS 状态，不另设 mastery 表**。技能节点掌握度即 py-fsrs Card 的 D/S/R，推论：一切"掌握度"查询都走 UserSkillState，不引入第二套掌握度口径。
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
| assert_score | REAL | 硬分 = 加权通过率，**分母恒为任务全部断言权重之和**（见下方口径） |
| judge_scores | JSON | 双评软分数组 |
| judge_agreement | REAL, 可空 | 双评一致率（4.2-4 机制） |
| detected_patterns | JSON（FK 数组） | 轨迹分析检出的错误模式 |
| diagnosis | JSON | 错误诊断报告（含按断言/judge 归类的混合呈现） |
| fsrs_rating | INTEGER 1–4 | 本次表现映射成的 FSRS 评分，用于更新卡片 |
| created_at | DATETIME | |

> **断言四态与计分口径（round-4 定，权威定义在 `tasks/*.yaml` 的 `scoring` 块）**：
> `pass`（计满权重，计入掌握度）/ `fail`（0 分，计入掌握度并产出错误模式）/
> `blocked`（前置断言未通过，无法判定：0 分，**不计入掌握度**）/ `unscored`（依赖数据缺失，如 trace 未上报：0 分，**不计入掌握度**）。
> 分母恒为任务全部断言权重之和，**不因断言缺失而缩小**——否则"缺产物/不报 trace"反而分数虚高。
> 一句话：**分数惩罚"没交东西"，归因不把"没数据"当成"不会"**；两者混写是 A2E 那类满分的根源（见 `spike/README.md` 2.2）。

### 5.2 UserSkillState（用户×技能的 FSRS 卡片宿主）

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | TEXT PK(联合) | 联合主键 (user_id, skill_id) |
| skill_id | TEXT PK(联合) | |
| fsrs_card | JSON | py-fsrs Card 序列化（due / stability / difficulty / last_review…） |
| updated_at | DATETIME | |

> FSRS 卡片粒度 = **(用户, 技能)** 二元组：同一技能每用户一张卡。首次提交产生检出错误模式后初始化卡片；此后每次 Submission 的 `fsrs_rating` 驱动 `review_card(card, rating)` 更新。技能掌握度 = 本表 R 值（可提取性），无第二口径。
> **P1 待拍板**：本行「掌握度 = R 值」与 `review-response-v1.md` §2 E2 的替代方案（掌握度 P = 断言通过率的 EWMA，时间衰减参数取 FSRS 的 S，R 只表示"现在复习收益多大"）冲突。拍板前两种口径都在文档里，**不得同时写进计划书**。
> ReviewLog（py-fsrs 的复习日志）**后置**：M0 不建表，复习事件可从 Submission + ReviewItem 重建；M1 接入参数优化时再加。

## 3. 关系一览

```
User ──1:N── Submission ──N:1── Task ──N:1── Skill（primary）
  │                │  N:N→ ErrorPattern          │
  │                └─ source_submission_id ←─────┼── ReviewItem ──N:1── Skill
  └──1:N── UserSkillState ──N:1── Skill          └── ErrorPattern ──N:N── Skill（mapped_skill_ids）
```

一次闭环的数据流：提交 → Submission（trace+分数+检出模式）→ detected_patterns ∘ mapped_skill_ids 定位技能缺口 → 初始化/更新 UserSkillState 卡片（fsrs_rating）→ 生成 ReviewItem（due）→ 排程层读 due 集合产出建议排期。

## 4. 开放决策记录

1. **运行时实体扩编**：✅ 已裁决（2026-11-06）——采纳扩到 7（+Submission、+UserSkillState）。
2. **fsrs_rating 映射**（断言通过率 → FSRS 1–4）：草案假设 ≥0.9→4 / ≥0.7→3 / ≥0.4→2 / <0.4→1。**标为假设**，dogfooding 后校准；学长若认为应纳入 judge 软分权重，在此改。
3. **prerequisites 先决关系**：MVP 只存不推（推荐层是规则表，用不到图遍历）；留字段是为 pyBKT 升级免迁移。
4. **评测资产 vs DB 实体边界**（2026-11-06 外部评审第 7 条采纳）：`tasks/<task_id>.yaml` 是**评测任务资产**（rubric/测试输入/故障注入/trace 要求/分页 mock 语义），只走 Git 版本控制；Task 表只存核心元数据 + `yaml_path` + `git_hash` 指向资产。`secondary_skill_ids` 等多对多关系 v0 用 JSON 数组存（SQLite 现实约束），M1 出现按技能反查任务的需求时再建关联表——届时为破坏性变更，单独一步。
