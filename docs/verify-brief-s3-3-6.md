# S3-3-6 切全链路 · 外部评测委托包（自包含单文件）

> 生成：2026-11-09 ｜ 被检验对象：**一份尚未落地的切换方案 + 现役仓库快照**，不是已完成的改动
> 你没有工作目录访问权：§4 逐字内联了全部相关源文件、测试与五份夹具，§6 有指纹表与自检命令。
> 匿名纪律：本包不含真实姓名 / 校名 / 本机绝对路径；请勿索取，也不要在对话里粘贴 API key 或 PAT。
> 本包是**现役快照**。`docs/verify-brief-s3-3-1.md` 与 `docs/redteam-brief-round8.md` 是已过期历史包，勿当依据。

---

## 0. 给评审 AI 的指令（请把本节当任务书）

### 0.1 任务边界

评审 **S3-3-6：`trace_pipeline` 从「只现算 R3」切到「五条断言全现算」** 的切换方案，以及三处已拍板口径的连带影响。
**代码尚未改动**——不要去找「实现里的 bug」当主结论，也不要把「建议改成别的架构」当产出。
目标是：**在改动落地之前，找出方案里会导致契约失真、覆盖丢失或口径自相矛盾的地方**。

一句话现状：五条断言（R1 产物 / R2 报告内容 / R3 重试证据 / R4 参数 schema / R5 终态）的执行器已分别落地并各有约束测试，
但 `trace_pipeline.run_fixture()` 目前仍只现算 R3，其余四条读 `fixtures/raw-*.json` 里**手写**的 `assertion_results`。
S3-3-6 要让五条全部由执行器从 `raw` 现算，手写结果不再被读。

### 0.2 纪律（违反的产出不予采信）

1. **别信自述**：§2 的决策摘录、§3 的基线输出、§4 文件里的注释与 docstring，全部是**被检验方写的**，不是证据。请自己从内联代码构造输入与反例。
2. **「测试通过」不算证据**：要说明你改坏了什么、预期真变红（`FAILED`）还是 `ERROR` / `TIMEOUT`、有没有复原。
3. **事实挂来源**：每条结论注明依据（§4 的文件名 + 行号，或 §2 / §3 的段落号）；推断明确标「假设」。
4. **每条结论配一个十分钟内能做的验证**：最小输入、可粘贴代码、或一条字节级变异 + 预期输出。
5. **区分** `FAILED` / `ERROR` / `TIMEOUT`，不要混成「没过」。
6. 你没有仓库：**不要声称「我跑过了」**。可复现步骤写成让用户粘贴的命令；需要核对源文件时向用户索取。
7. **只评方案，不代替人拍板**：§0.3 四项已由人决定，不要建议翻案；但「按此决定落地会产生什么洞」属于本次范围，请照报。

### 0.3 已拍板、不要翻案的边界

| 项 | 人的决定 |
|---|---|
| TD-008（`artifact_invalid` 不在 DTO `reason_code` 枚举） | **给 schema 扩枚举**，不改判 `fail`、不复用 `trace_invalid` |
| §12.5 末句「`tests/test_diagnosis_report.py` 切到全链路后仍全绿」的解读 | **该文件原样保留**，在 `tests/test_trace_pipeline.py` 补「五条全现算 vs S2 基线」一致性锁，并澄清 §12.5 措辞 |
| TD-007 / 009 / 010 / 011 四个手写假基线 + R2/R4 两处假设 | **切全链路时一并销项**，以现算 evidence 为准，不改五份夹具的手写字段 |
| 外部评测的发起 | 人已发起本次；回件按「核证 → 登记 → 处置 → 落仓 → 人拍板」处理 |

历史拍板仍在效力：Q10=A（不新增 `pattern_id`、不改词表/DTO/归因）、Q13=(a)（产物本体来自 `artifacts`，绝不从 trace 自述推断）、Q12=(a)（最小前置校验）、P6 两段式计分（gate 失败 `disqualify`、credit 分母恒 4）。

**明确不做（本步范围外，勿重复报）**：P3 的 R6「产物 vs trace 自述」一致性比对、真实文件系统 / mock / 故障注入 / 超时控制 / CI、TD-001 跨技能归因、D1/D3/D4。

### 0.4 交付格式

1. **核证总表**：`| # | 主张（谁说的） | 核证（成立 / 不成立 / 前提不足） | 依据（文件+行号 / 段落） | 最小复现 |`
2. **可判定命题**：每条写「什么输入 → 期望什么 → 实际什么 → 何时判定通过」。
3. **待拍板项**：只列需要人决定的，注明两个选项各自代价；已拍板项请写成「落地时注意 X」，不要写成「建议改成 Y」。
4. 不写总体评价、不写鼓励、不写「建议加强测试」这类不可判定句。

### 0.5 顺序

优先咬 §5 的 T1–T12，其次自选。**§5 是中性命题，不是被检验方的结论**。

---

## 1. 最小上下文

五层架构（任务层 YAML → 评测层 → 状态层 → 复习层 → 排程层）；本次只涉及**评测层**的全链路接线。

- `tasks/dbg-001-order-report.yaml` 是唯一真相源：判定表达式、分类规则、工具声明、计分归因都从它派生。
- 四态 `pass` / `fail` / `blocked` / `unscored`；`unscored` 分数贡献 0、不计掌握度、不产出错误模式。
- 两段式：`rubric_gate`（R1 / R5）任一 `fail` ⇒ `disqualified`、`ratio=0`；`rubric_credit`（R2 / R3 / R4）分母恒 4。
- 全链路：`raw.trace` + `raw.artifacts` → 五个执行器 → `assertion_results` → `build_report()` → `validate_report()`。
- `build_report()` 有两处会**改写执行器结果**：`trace_*` 类型在 trace 不可用时强制 `unscored`；`depends_on` 折叠校验。
- 五夹具分数序基线 `0 / 0 / 0.25 / 0.5 / 1.0`，是 P6 的验收判据，切换后必须保持。
- 分类策略不统一（有意为之）：R3 / R4 / R5 的 `cases` **首命中即止**；R2 **收集全部命中并去重保序**。

---

## 2. 被检验对象自述（一部分逐字、一部分剪裁，**均不作证据**）

> 以下是被检验方自己写的判据与处置记录。逐字段落仅 §12.5 / §12.7；其余为剪裁摘要，
> 原文在 `docs/review-response-v1.md`（**未内联全文**）。请把它当「待核证的主张清单」，不是事实。

#### 来源 A：`docs/review-response-v1.md` §12.5（S3-3 完成判据，逐字）

### 12.5 S3-3 完成判据（开工先写，可证伪）

**一句话判据**：给定 `fixtures/raw-*.json` 五份夹具，`trace_pipeline.run_fixture()`
不再读任何手写 `assertion_results`，五条断言全部由执行器从 `raw` 现算；
产出报告在 `score.ratio` / `disqualified` / 每条断言 `state` 上与 S2 基线一致，
分数序保持 `0 / 0 / 0.25 / 0.5 / 1.0`；`tests/test_diagnosis_report.py` 切到全链路后仍全绿。

**为什么只锁 state、不锁 error_patterns**：`raw-empty` 的手写 R3 是 `no_retry`，
现算是 `retry_wrong_params`(default)，差异由 `test_raw_empty_exposes_q10_gap()` 钉成 Q10 证据
（`tests/test_trace_pipeline.py` L119-132）。Q10-A 落地后该差异应**消失**，那条测试要同步从
「暴露缺口」改写成「缺口已修复」的回归锁 —— 这是 S3-3-1 的显式验收项，不是可忽略的红灯。

**外部裁判（十分钟内可跑，仓库根执行）**：

```bash
python -m pytest tests/ -q
python src/arena/trace_pipeline.py --fixture all
python src/arena/review_packet.py --check
python src/arena/assertion_evaluator.py --demo
```

外加变异测试：每新增一条分类/判定规则，改坏一处后必须真 `FAILED`（不是 `ERROR`/`TIMEOUT`），
复原后 `RESTORED: True` 且全量复绿。「测试通过」本身不算证据。

**明确不做**：真实文件系统 / mock / 故障注入 / 超时控制 / CI 配置 / P3 的 R6 一致性校验 / TD-001。

#### 来源 B：同文件 §12.7（小步拆分与提交拆分，逐字）

### 12.7 小步拆分与提交拆分

**开发小步**（一步 = 一个可独立验证的最小单元，每步跑完 §12.5 的裁判再进下一步）：

| 步 | 内容 | 完成判据 | 依赖 |
|---|---|---|---|
| **S3-3-1** | Q10-A 落地：3 个语义谓词 + YAML `cases` 补规则 + 用例集 4 条 `expect` 改 `case` + 逐 case 回归锁 + 处置 `test_raw_empty_exposes_q10_gap` | §12.1 通过标准四条全满足；变异测试真 `FAILED` | 无（**下一轮第一小步**） |
| S3-3-2 | R5 `trace_final_status` 执行器 + `evaluate_assertion()` 分发注册 | 五夹具 R5 state 与手写基线一致 | 无 |
| S3-3-3 | R1 `file_exists` | 同上 | Q13 |
| S3-3-4 | R2 `json_field_equals`（`subset` + `tolerance 0.01`，禁止浮点 `==`） | 同上 | Q13 + S3-3-3 |
| S3-3-5 | R4 `trace_param_schema_valid`（required/type/range/additional_properties 四项，见 `docs/executor-semantics.md` §2） | 同上 | 无 |
| S3-3-6 | `trace_pipeline` 现算全部 5 条 + S2 测试切全链路 | §12.5 一句话判据 | 1-5 |

Q12 可在 S3-3-1 或 S3-3-2 顺带做（同文件、同校验函数），**待人选 a/b 后并入**。

**提交拆分**（22 项，`HEAD=03cf8c9`，全部**等人验证通过后**再执行；本轮不 commit / 不 push）：

| 组 | 内容 | 大致文件 |
|---|---|---|
| 1 | Q7/Q8/Q9 评审包改造 | `review_packet.py` / `review-packet-manual.md` / `review-packet.md` / `test_review_packet.py` / `review.bat` / `verify.bat` / `.gitignore` |
| 2 | 执行器修复 + S3-1（含 Q10-A） | `assertion_evaluator.py` / `test_assertion_evaluator.py` / `tool-call-cases.json` / `tasks/*.yaml` |
| 3 | S3-2 全链路打通 | `trace_pipeline.py` / `test_trace_pipeline.py` / 5 份 `raw-*.json` |
| 4 | 处置记录与交接 | `review-response-v1.md` / `redteam-brief-round8.md` / `HANDOFF.md` / `docs/archive/` |

> 与人转交建议的差别：原建议是「两次提交（执行器修复 + S3-2）」，这里拆成 4 组，
> 因为 22 项里有整块评审包改造与处置记录，塞进任一组都会破坏原子性。
#### 来源 C：登记项的剪裁摘要（非逐字，不作证据）

| 登记 | 被检验方的说法 | 出处 |
|---|---|---|
| TD-007 | `raw-no-trace` 手写 R5 是 `pass / observed=null`，但 YAML 与 `build_report()` 要求 trace 未上报时改 `unscored`；判据取**最终报告** R5 state，不改夹具 | §16.3 |
| TD-008 | `artifact_invalid` 不在 DTO v2 的 `reason_code` 封闭枚举，`validate_report()` 会拒绝；红灯证据已钉，留 S3-3-6 处置 | §17.5 |
| TD-009 | 手写 `size_bytes`（96/58/58）与 `artifacts` 产物实际长度（44/42/44 B）不符，S2 遗留 | §17.5 |
| TD-010 | R2 多 pattern 按 YAML cases 顺序，手写 `raw-fake-retry` 是反序；集合 / state / 归因相同 | §18.4 |
| TD-011 | `raw-no-trace` 手写 R4=`pass / observed=null` 与最终报告 / 现算 `unscored` 不符，TD-007 同族 | §19.4 |
| 假设①（R2） | 产物不是合法 JSON 对象 → 按「expected 字段全部未观测到」归入既有 `report_missing_field`；「采集截断 vs 学生写坏」暂不可分 | §18.4 |
| 假设①（R4） | 出现 tools 未声明的**工具名** → `unscored / trace_invalid`，不复用专指参数名的 `param_out_of_schema` | §19.4 |
| 假设②（R4） | 一次调用有多类违规时只报 cases 顺序里最优先的一个 pattern，全量明细留在 `evidence.observed.violations` | §19.4 |
| 反向结论 | 「TD-008 在 R4 不成立」：R4 的 unscored 只用 `trace_not_reported` / `trace_invalid`，都在封闭枚举内 | §19.4 |

> 被检验方另声称：切全链路后手写假基线「自然消失，不需要改夹具」，五个 TD 与两处假设「一并销项」。
> 请从 §4 代码自行判定这些说法是否成立，尤其注意「不再被读」与「已被删除」不是一回事。

---

## 3. 自报基线与只读模拟（被检验方声称，**不作证据**）

```
$ pytest tests/ -q
510 passed in 89.79s

$ python src/arena/review_packet.py --check
OK: 评审包内容与仓库一致（d66c5cf0bafe7e00）

$ python src/arena/trace_pipeline.py --fixture all
比率序 0 / 0 / 0.25 / 0.5 / 1.0（R3 现算，其余四条读手写）
```

**只读模拟「五条全现算」（未改任何文件）**，被检验方声称逐项一致：

| 夹具 | R1 | R5 | R2 | R3 | R4 | ratio | `by_state` 变化 |
|---|---|---|---|---|---|---|---|
| raw-empty | fail | pass | blocked | fail | pass | 0.0 | 无 |
| raw-no-file | fail | pass | blocked | pass | pass | 0.0 | 无 |
| raw-fake-retry | pass | pass | fail | fail | pass | 0.25 | 无 |
| raw-no-trace | pass | unscored | pass | unscored | unscored | 0.5 | 无 |
| raw-perfect | pass | pass | pass | pass | pass | 1.0 | 无 |

被检验方另声称两处实测：

- **TD-008 触发条件**：artifacts 合法 → `validate_report()` OK；artifacts 缺失 / 非 dict / 值非 str → R1 `unscored / artifact_invalid`，`validate_report()` 抛 `ReportError: 'artifact_invalid' is not one of [...]`。
- **evidence 差异**：切全链路后，每份夹具各有 3–4 条断言的 `evidence` 与手写不同（现算本该不同）；§12.5 只锁 `state` / `ratio` / `disqualified`，不锁 evidence。

**请自行判定**：这些数字能否推出「切换方案不会丢覆盖」「五个 TD 可以不改夹具就销项」。

---

## 4. 逐字内联的现役文件

> 每块由脚本用**原始字节**拼入，未做换行转换；CRLF 文件保持 CRLF。
> `===== BEGIN / END =====` 是字节边界标记，**不是 Markdown 代码围栏**（内联内容里本身含三反引号）。
> 路径均相对仓库根；可按文件名索引，不必通读。校验方法见 §6。


文件：`tasks/dbg-001-order-report.yaml` ｜ sha256[:16]=`d744837b44548563` ｜ 273 行 ｜ CRLF
===== BEGIN: tasks/dbg-001-order-report.yaml =====
# 调试向任务 #1（M-1 第 3 步产物；第 4/5 步 A2E / agentevals 试验共用此任务）
#
# 【资产边界，2026-11-06 评审第 7 条】本文件是"评测任务资产"（evaluation asset），不是数据库 Task 实体：
#   DB Task 表只存核心元数据（task_id/title/skills/yaml_path/git_hash）；
#   rubric / test_inputs / trace_requirements / fault_injection 等评测配置只存在于本 YAML，不入库（见 data-model 开放决策 4）。
task_id: dbg-001-order-report
title: 订单数据核对报告（调试向）
domain: 工具调用与多步任务调试
primary_skill_id: multi_step_planning
secondary_skill_ids:
  - tool_param_validation
  - failure_retry
variant_template_id: null

fault_injection:
  enabled: true
  target_tool: fetch_orders
  retry_scope: agent            # 重试主体=Agent；mock 绝不内部自动重试（评审第 2 条，P0）
  reset_policy: per_submission  # 每次提交评测前 mock 状态整体重置（第 3 轮评审 ①-2 的真实缺口：否则上一次用掉的故障额度会漏到下一次）
  match:
    offset: 10                  # 匹配范围由 target_tool 限定；offset=10 是分页游标位置，无论 Agent 用何种合法子页大小都命中（评审第 5 条措辞修正）
  failures:
    - attempt: 1                # 该 (tool, match) 的第 1 次调用失败
      http_status: 500
      error: internal_error
  then: success                 # 之后调用正常返回

# 调用边界（第 3 轮评审 ②-1a：原 YAML 完全没有这层约定，导致"超时算不算一次提交"无处可查）
execution:
  invoke_timeout_seconds: 60
  platform_retry: 0                 # 平台绝不自动重试：重试是本次被测的能力，平台代劳会让 R3 测不到
  on_timeout: {final_status: timeout}   # 超时写入 trace.final_status=timeout → R5 判失败；R3 无证据 → unscored（不是判 0）

scenario: |
  你是一个数据助理 Agent，请完成以下工作：
  1. 使用工具 fetch_orders 分页拉取全部订单（limit 取值必须在 1 到 10 之间，超过 10 服务端会返回错误 invalid_limit；共 20 条订单）
  2. 剔除无效订单：amount 字段缺失、为 null 或为负数
  3. 统计有效订单数量与总金额
  4. 调用 write_report 将结果写入 /workspace/report.json，
     内容为 JSON：{"valid_count": <有效订单数>, "total_amount": <总金额, 浮点>}

tools:
  - name: fetch_orders
    description: '分页拉取订单，返回 {"orders": [...], "total": <总数>}'
    parameters:
      - name: offset
        type: integer
        required: true
        minimum: 0
      - name: limit
        type: integer
        required: true
        minimum: 1
        maximum: 10
    additional_properties: false    # 出现 schema 外参数 → R4 判失败（评审第 6 条）
    mock:
      source: fixture_ref
      fixture: test_inputs.orders_fixture
      pagination:                     # 结构化语义（评审第 4 条），执行器可直接驱动 mock
        page_size_max: 10
        offset_base: 0
        range: half_open              # 返回 [offset, offset+limit) 区间
        on_limit_exceed: error
        error_code: invalid_limit
        on_offset_out_of_range: empty_list
        response_shape: {orders: []}
        total_source: fixture_length  # 第 5 轮 P2-2：total 一律从 fixture 条数推导，不硬编码——fixture 改条数无需同步改这里

  - name: write_report
    description: 将报告内容写入指定路径
    parameters:
      - name: path
        type: string
        required: true
      - name: content
        type: string
        required: true
    additional_properties: false

# 20 条订单；o-007 金额为负、o-012 缺 amount 字段、o-018 金额为 null → 有效 17 条
test_inputs:
  orders_fixture:
    - {order_id: o-001, amount: 120.5}
    - {order_id: o-002, amount: 80}
    - {order_id: o-003, amount: 45.25}
    - {order_id: o-004, amount: 200}
    - {order_id: o-005, amount: 60}
    - {order_id: o-006, amount: 99.99}
    - {order_id: o-007, amount: -50}
    - {order_id: o-008, amount: 150}
    - {order_id: o-009, amount: 30.5}
    - {order_id: o-010, amount: 75}
    - {order_id: o-011, amount: 220.25}
    - {order_id: o-012, user_id: u-12}          # 缺 amount 字段
    - {order_id: o-013, amount: 88}
    - {order_id: o-014, amount: 40}
    - {order_id: o-015, amount: 310}
    - {order_id: o-016, amount: 55.5}
    - {order_id: o-017, amount: 66}
    - {order_id: o-018, amount: null}           # 金额为 null
    - {order_id: o-019, amount: 77}
    - {order_id: o-020, amount: 120}
  expected:
    valid_count: 17
    total_amount: 1837.99

# ============================================================================
# 【第 6 轮评审修正 P0】原注释写「不上报 trace → 轨迹类断言判 0，且无诊断报告」，与既定规则
# 「判 unscored 且不计入掌握度」直接冲突，属遗留错误，已删除。
# 澄清一处容易说错的点：「判 0」与「判 unscored」的**分数**贡献相同（见下方 scoring.score，
# 都是 0），真正的区别在**归因**——判 0 把「数据缺失」记成「能力不足」，会污染掌握度与错误模式。
# 所以修复点不是改分数，是把分数口径与归因口径分开写死。
# ============================================================================
trace_requirements:
  minimum:
    agent_id: {required: true}                          # 字段风格统一为对象式（评审第 6 条）
    final_status: {required: true, enum: [completed, crashed, timeout]}
    tool_calls:
      required: true
      item_fields: [call_index, name, params, status, http_status, error, result_summary]
      status_enum: [success, error, timeout]
      note: 'call_index 平台侧编号（1 起）；http_status/error 仅 status=error 必填，result_summary 仅 success 必填；attempt 不要求上报——由平台从同名同参调用序列推导（评审第 3 条的修正采纳）'

# ============================================================================
# 计分与归因口径（第 6 轮评审：把「判 0」和「判 unscored」拆成两件事写死）
#   分数 = 给这次提交打多少（反映任务成败）
#   归因 = 这次提交能说明用户什么能力（喂掌握度 + 错误模式）
# 两者解耦才有解：分数要惩罚"没交东西"，归因不能把"没数据"当成"不会"。
# ============================================================================
scoring:
  score:
    formula: gated_weighted_pass_ratio   # 两段式：先过门禁，再算积分
    # ── 第一段 gate（门禁）：不计分，只决定"这份提交有没有资格被评分" ──
    gate:
      source: rubric_gate                # 断言从 rubric_gate 取
      disqualified_if: any_gate_failed   # 任一门禁 fail ⇒ disqualified（ratio 记 0）
      disqualify_ratio: 0                # 不合格时的总分。不用"封顶 0.3"之类魔数：封顶值无依据，评委会问为什么是 0.3
      on_unscored: do_not_disqualify     # 门禁本身无法判定（如 trace 缺失致 R5 无据）时不拦，只把该门禁记 unscored
    # ── 第二段 credit（积分）：这一层的分母才是分数的分母 ──
    credit:
      source: rubric_credit
      denominator: fixed_at_credit_total_weight  # 恒为 credit 权重之和（本任务 4），不因断言缺失而缩小（P4 修正）
      total_weight: 4                    # = R2(2) + R3(1) + R4(1)
      contribution:                      # 各状态对分子（得分）的贡献
        pass: weight                     # 通过：计满权重
        fail: 0                          # 有证据的失败：0
        blocked: 0                       # 前置未通过、无法判定：0（若从分母剔除会让"缺产物"反而虚高）
        unscored: 0                      # 依赖数据缺失（trace 未上报）：0（堵住"不报 trace 稳拿分"）
    # 口径：ratio = gate 通过 ? credit_pass_weight / 4 : 0
    #       门禁失败时**照常判定并记录 credit 断言**（诊断信息不因 0 分而丢弃），只是不计入分数。
  attribution:                           # 是否参与掌握度更新 / 是否产出错误模式
    pass: {counts_toward_mastery: true, emit_error_patterns: false}
    fail: {counts_toward_mastery: true, emit_error_patterns: true}
    blocked: {counts_toward_mastery: false, emit_error_patterns: false,
              reason: 前置断言失败导致无法判定，属证据缺失而非能力不足}
    unscored: {counts_toward_mastery: false, emit_error_patterns: false,
               reason: trace 类依赖数据缺失；诊断报告写"未评分"，不写"0 分"}
  # P4 待拍板：分母是否恒定为 total_weight（当前取"固定"）；P2 一旦改口径需同步此处

# 原子断言 5 条，按 P6 拍板结果分为两层（不是靠注释区分，是两个数组，执行器先跑 gate 再跑 credit）：
#   rubric_gate   R1 交产物 / R5 正常退出 —— 门禁：不计分，未过则整份提交 ratio=0
#   rubric_credit R2 结果正确 / R3 重试证据 / R4 无参数幻觉 —— 积分：分母恒为 4
# 为什么要分：旧版把两类混在一个数组按权重加，导致"什么都不做"也拿 2/6=33%（R4 的全称命题对空集
# 平凡为真 + R5 只要没崩就是 completed），且空提交/无文件/假重试/无 trace 四份质量递进的提交
# 全落在 0.5 附近，分数序塌陷。详见 docs/review-response-v1.md §6。
#
# 每条的 skill_id / error_patterns / depends_on：
#   归因以 skill_id + error_patterns 为准，detects 仅供诊断报告展示（避免两处真相源漂移）。
#   skill_id 为 null 表示"该断言只计分不归因"——当前三技能分类学里没有对应节点（P5 拍板：走 gate
#   通道，不加第 4 技能；门禁失败的归因走 report.score.disqualifying_assertions + unattributed_patterns）。
rubric_gate:
  - id: R1_file_exists
    type: file_exists
    target: /workspace/report.json
    options:
      non_empty: true           # 存在且非空（评审第 8 条）
    depends_on: []              # 无前置：根断言
    skill_id: null              # 交付纪律：三技能分类学无对应节点 → 只计分不归因（P5）
    error_patterns: [no_output_file, empty_output_file]
    error_pattern_detection:
      probe: file_exists(/workspace/report.json)
      cases:
        - {match: {exists: false}, pattern: no_output_file}
        - {match: {exists: true, non_empty: false}, pattern: empty_output_file}
    on_fail: disqualify         # 门禁：不看权重，未过即整份不合格（没有产物，任务就没完成）
  - id: R5_clean_exit
    type: trace_final_status
    target: final_status
    expected: completed
    depends_on: []
    skill_id: null              # 任务完成度：三技能分类学无对应节点 → 走 gate 通道（P5）
    error_patterns: [task_crashed, task_timeout]
    error_pattern_detection:
      probe: final_status
      cases:
        - {match: {final_status: crashed}, pattern: task_crashed}
        - {match: {final_status: timeout}, pattern: task_timeout}
    on_fail: disqualify
    # 注意：本条的 type 是 trace_*，trace 未上报时会被强制改写为 unscored。门禁 unscored 不拦
    # （无法证明不合格就不判不合格），此时 credit 层的 R3/R4 同样 unscored，学生拿不到分，绕不过去。

# ── 积分层：分母恒为 4（R2=2 + R3=1 + R4=1）────────────────────────────────
rubric_credit:
  - id: R2_report_correct
    type: json_field_equals
    target: /workspace/report.json
    expected: {valid_count: 17, total_amount: 1837.99}
    compare: subset             # 报告允许含额外字段
    tolerance:
      total_amount: 0.01        # 浮点容差（评审第 4 条，P0）
    depends_on: [R1_file_exists]   # 文件不存在 → 本断言记 blocked：计 0 分，但不计入掌握度
    skill_id: multi_step_planning  # 拉全 20 条 → 剔除 3 条无效 → 汇总，检验多步执行的完整性
    error_patterns: [wrong_valid_count, wrong_total_amount, report_missing_field]
    error_pattern_detection:
      probe: json_field_equals(/workspace/report.json vs expected)
      cases:
        - {match: {missing_fields: [valid_count]}, pattern: report_missing_field}
        - {match: {missing_fields: [total_amount]}, pattern: report_missing_field}
        - {match: {valid_count_mismatch: true}, pattern: wrong_valid_count}
        - {match: {total_amount_mismatch: true}, pattern: wrong_total_amount}
    weight: 2
  - id: R3_retry_evidence
    type: trace_sequence
    target: tool_calls
    expected:
      ordered: true
      allow_interleaved: true   # 两次 offset=10 调用之间允许穿插其他工具调用（评审第 2 条）——匹配器按子序列判定，不要求严格相邻
      calls:
        - {name: fetch_orders, params_match: {offset: 10}, status: error, http_status: 500}
        - {name: fetch_orders, params_match: {offset: 10}, status: success}
      note: 'params_match 为子集匹配：只锁"第二页起点 offset=10"，不限定 limit——Agent 用 limit<=10 的任何合法分页都应判过（对评审 R3 建议的修正）'
    depends_on: []
    skill_id: failure_retry
    detects: 失败重试策略          # 仅供诊断报告展示，归因以上面的 skill_id 为准
    error_patterns: [no_retry, retry_wrong_params, retry_exhausted]
    error_pattern_detection:
      # 第 6 轮实测结论：判定（PASS/FAIL）一个表达式就够，分类必须另取序列——
      # 假重试与无重试的过滤后状态序列完全相同（都是 ["error"]），单靠 R3.expected 分不开。
      probe: "calls[?name=='fetch_orders'].params.offset"
      cases:                     # 顺序匹配，首命中即止
        - {match: {offsets_equals: [0, 10]}, pattern: no_retry}
        - {match: {offsets_equals: [0, 10, 0]}, pattern: retry_wrong_params}
        - {match: {offsets_starts_with: [0, 10, 10], last_status: error}, pattern: retry_exhausted}
        # ── Q10-A（round-10，人拍板选 A）────────────────────────────────
        # 过去这三类边界全部落到 default，诊断失真且看不出是"没做事"还是"没重试"。
        # 三个谓词的值由执行器从 expected.calls 派生的过滤表达式计算，
        # 不写死 offset=10；只接受 true，写错会抛错而不是静默退回 default。
        # 按 A 方案不新增 pattern_id：零调用/未触达故障点仍归 no_retry，
        # 「应归 multi_step_planning」登记为 TD-001，等 M2 接状态层再动 DTO。
        - {match: {no_tool_calls: true}, pattern: no_retry}
        - {match: {fault_point_not_reached: true}, pattern: no_retry}
        - {match: {fault_point_not_repeated: true}, pattern: retry_wrong_params}
      default: retry_wrong_params   # 保留兜底；命中来源写进 evidence 供覆盖度审计
    weight: 1
  - id: R4_no_param_hallucination
    type: trace_param_schema_valid
    target: tool_calls
    expected:
      schema_ref: tools         # 引用本文件 tools 的 parameters + additional_properties
      check_schema: true        # 语义覆盖 required/type/range/additional_properties 四项（第 5 轮 P2-1：原名 check_types 未覆盖"漏传必填参数"）
      mode: all_calls_must_be_valid   # 判定模式（评审第 1 条）：任一次调用违规即失败——参数幻觉考"有没有编出界外参数"，试探 limit=20 本身即违规
    depends_on: []
    skill_id: tool_param_validation
    detects: 参数幻觉
    error_patterns: [param_out_of_schema, param_missing_required, param_type_mismatch, param_out_of_range]
    error_pattern_detection:
      probe: "calls[*].{name: name, params: params}"
      cases:                     # 顺序匹配，首命中即止；against 指本文件 tools
        - {match: {extra_keys_against: tools}, pattern: param_out_of_schema}
        - {match: {missing_required_against: tools}, pattern: param_missing_required}
        - {match: {type_mismatch_against: tools}, pattern: param_type_mismatch}
        - {match: {out_of_range_against: tools}, pattern: param_out_of_range}
    weight: 1

===== END: tasks/dbg-001-order-report.yaml =====

文件：`docs/diagnosis-report.schema.json` ｜ sha256[:16]=`9541848526520bfe` ｜ 300 行 ｜ CRLF
===== BEGIN: docs/diagnosis-report.schema.json =====
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://agent-arena.local/schemas/diagnosis-report-v2.json",
  "title": "诊断报告 DTO v2（评测层 -> 状态层 的唯一接口）",
  "description": "只装评测层能判定的事实：每条断言的观测、四态、命中的错误模式；以及派生出来的两段式分数（gate / credit）与技能缺口聚合。不含推断（复习建议、掌握度均在状态层产生）。v2 相对 v1 的破坏性变更：rubric 拆成 gate/credit 两层，分母改为 credit 权重之和，新增 disqualified 与 unattributed_patterns。",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "report_version",
    "submission",
    "trace_audit",
    "score",
    "attribution_policy",
    "assertions",
    "skill_gaps",
    "unattributed_patterns"
  ],
  "properties": {
    "report_version": { "const": "v2" },
    "submission": {
      "type": "object",
      "additionalProperties": false,
      "required": ["submission_id", "task_id", "task_git_hash", "agent_id", "evaluated_at"],
      "properties": {
        "submission_id": { "type": "string", "minLength": 1 },
        "task_id": { "type": "string", "minLength": 1 },
        "task_git_hash": {
          "type": "string",
          "pattern": "^[0-9a-f]{7,40}$",
          "description": "产生本报告的 YAML 版本；没有它，报告里的断言 id 无法与任务对齐"
        },
        "agent_id": { "type": "string", "minLength": 1 },
        "evaluated_at": { "type": "string", "format": "date-time" },
        "invoke": {
          "type": "object",
          "additionalProperties": false,
          "required": ["duration_ms", "timeout_seconds", "platform_retries"],
          "properties": {
            "duration_ms": { "type": "integer", "minimum": 0 },
            "timeout_seconds": { "type": "integer", "minimum": 1 },
            "platform_retries": {
              "const": 0,
              "description": "平台绝不代劳重试：重试是被测能力，平台重试会让 R3 失效"
            }
          }
        }
      }
    },
    "trace_audit": {
      "type": "object",
      "additionalProperties": false,
      "required": ["reported", "schema_valid", "invalid_reasons", "call_count"],
      "description": "trace 前置校验结果。A2E 的教训是『读不到 trace 时静默降级并给满分』，所以这里必须显式记录，不许静默。",
      "properties": {
        "reported": { "type": "boolean" },
        "schema_valid": { "type": "boolean" },
        "invalid_reasons": {
          "type": "array",
          "items": { "$ref": "#/$defs/traceInvalidReason" },
          "uniqueItems": true
        },
        "call_count": { "type": "integer", "minimum": 0 },
        "final_status": { "enum": ["completed", "crashed", "timeout"] }
      },
      "allOf": [
        {
          "if": { "properties": { "reported": { "const": false } }, "required": ["reported"] },
          "then": {
            "properties": {
              "schema_valid": { "const": false },
              "call_count": { "const": 0 },
              "invalid_reasons": { "minItems": 1 }
            }
          }
        },
        {
          "if": { "properties": { "schema_valid": { "const": true } }, "required": ["schema_valid"] },
          "then": { "properties": { "invalid_reasons": { "maxItems": 0 } } }
        }
      ]
    },
    "score": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "weighted_sum",
        "credit_pass_weight",
        "total_weight",
        "ratio",
        "denominator_policy",
        "disqualified",
        "disqualifying_assertions",
        "by_state"
      ],
      "description": "两段式分数。gate 决定『有没有资格被评分』，credit 决定『评多少分』。两者分开是为了让分数单调：不做事 < 做错 < 只对一半 < 全对。",
      "properties": {
        "weighted_sum": {
          "type": "number",
          "minimum": 0,
          "description": "最终得分分子。disqualified 时恒为 0，否则等于 credit_pass_weight"
        },
        "credit_pass_weight": {
          "type": "number",
          "minimum": 0,
          "description": "credit 层通过的权重之和。门禁未过时仍照算——它只用于诊断，不参与 ratio。这个值与 assertions 里 pass 的 credit 断言始终自洽"
        },
        "total_weight": {
          "type": "number",
          "exclusiveMinimum": 0,
          "description": "credit 层权重之和（本任务 4）。门禁不计权重，所以这不是全部断言的权重和"
        },
        "ratio": { "type": "number", "minimum": 0, "maximum": 1 },
        "denominator_policy": {
          "const": "fixed_at_credit_total_weight",
          "description": "分母恒为 credit 层权重之和，不因断言缺失而缩小（P4）。gate 不在分母里——它没有权重"
        },
        "disqualified": {
          "type": "boolean",
          "description": "是否有门禁断言 fail。true 表示本次提交无资格评分，ratio 记 0"
        },
        "disqualifying_assertions": {
          "type": "array",
          "items": { "type": "string" },
          "uniqueItems": true,
          "description": "哪一个门禁拦下了这次提交。状态层据此知道『未交产物』而不是面对空的 skill_gaps 猜（P5）"
        },
        "by_state": {
          "type": "object",
          "additionalProperties": false,
          "required": ["pass", "fail", "blocked", "unscored"],
          "description": "全部断言（gate + credit）的状态计数，用于快速核对报告完整性",
          "properties": {
            "pass": { "type": "integer", "minimum": 0 },
            "fail": { "type": "integer", "minimum": 0 },
            "blocked": { "type": "integer", "minimum": 0 },
            "unscored": { "type": "integer", "minimum": 0 }
          }
        }
      },
      "allOf": [
        {
          "if": { "properties": { "disqualified": { "const": true } }, "required": ["disqualified"] },
          "then": {
            "properties": {
              "disqualifying_assertions": { "minItems": 1 },
              "weighted_sum": { "const": 0 },
              "ratio": { "const": 0 }
            }
          }
        },
        {
          "if": { "properties": { "disqualified": { "const": false } }, "required": ["disqualified"] },
          "then": { "properties": { "disqualifying_assertions": { "maxItems": 0 } } }
        }
      ]
    },
    "attribution_policy": {
      "type": "object",
      "additionalProperties": false,
      "required": ["pass", "fail", "blocked", "unscored"],
      "description": "从任务 YAML 的 scoring.attribution 抄来（去掉人类可读的 reason），让报告自解释：状态层不需要再回头读 YAML 才知道哪些状态算掌握度。",
      "properties": {
        "pass": { "$ref": "#/$defs/attribution" },
        "fail": { "$ref": "#/$defs/attribution" },
        "blocked": { "$ref": "#/$defs/attribution" },
        "unscored": { "$ref": "#/$defs/attribution" }
      }
    },
    "assertions": {
      "type": "array",
      "minItems": 1,
      "description": "必须与任务的两个 rubric 数组一一对应：不多、不少、不重复，顺序为先 gate 后 credit（由 builder 保证，schema 只能保证结构）",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "layer", "type", "weight", "state", "skill_id", "error_patterns", "evidence"],
        "properties": {
          "id": { "type": "string", "pattern": "^R[0-9]+_[a-z0-9_]+$" },
          "layer": {
            "enum": ["gate", "credit"],
            "description": "gate = 门禁（不计分，fail 即整份不合格）；credit = 积分（计入分母）"
          },
          "type": { "type": "string", "minLength": 1 },
          "weight": {
            "type": "number",
            "minimum": 0,
            "description": "gate 层恒为 0（列表里保留该字段是为了断言结构统一，不是权重）"
          },
          "state": { "enum": ["pass", "fail", "blocked", "unscored"] },
          "skill_id": {
            "type": ["string", "null"],
            "description": "null = 只计分不归因（R1/R5 在当前三技能分类学里无归属，P5 拍板走 gate 通道）"
          },
          "error_patterns": {
            "type": "array",
            "items": { "type": "string", "pattern": "^[a-z][a-z0-9_]*$" },
            "uniqueItems": true
          },
          "reason_code": {
            "enum": [
              "trace_not_reported",
              "trace_invalid",
              "dependency_blocked",
              "tool_no_response",
              "invoke_timeout"
            ]
          },
          "evidence": {
            "type": "object",
            "additionalProperties": false,
            "required": ["observed", "expected"],
            "properties": {
              "observed": { "description": "实际观测到的值（任意类型，可 null）" },
              "expected": { "description": "任务 YAML 声明的期望值（任意类型，可 null）" },
              "extra": { "type": "object" }
            }
          },
          "note": { "type": "string" }
        },
        "allOf": [
          {
            "if": { "properties": { "layer": { "const": "gate" } }, "required": ["layer"] },
            "then": { "properties": { "weight": { "const": 0 } } }
          },
          {
            "if": { "properties": { "layer": { "const": "credit" } }, "required": ["layer"] },
            "then": { "properties": { "weight": { "exclusiveMinimum": 0 } } }
          },
          {
            "if": { "properties": { "state": { "const": "fail" } }, "required": ["state"] },
            "then": { "properties": { "error_patterns": { "minItems": 1 } } }
          },
          {
            "if": { "properties": { "state": { "enum": ["pass", "blocked", "unscored"] } }, "required": ["state"] },
            "then": { "properties": { "error_patterns": { "maxItems": 0 } } }
          },
          {
            "if": { "properties": { "state": { "enum": ["blocked", "unscored"] } }, "required": ["state"] },
            "then": { "required": ["reason_code"] }
          }
        ]
      }
    },
    "skill_gaps": {
      "type": "array",
      "description": "只做聚合，不做推断：失败的断言按 YAML 已声明的 skill_id 归类，weight_lost 是这些断言的权重之和。skill_id 为 null 的断言不进本数组（改由 unattributed_patterns 留痕）。",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["skill_id", "failed_assertions", "patterns", "weight_lost"],
        "properties": {
          "skill_id": { "type": "string", "minLength": 1 },
          "failed_assertions": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
          "patterns": { "type": "array", "items": { "type": "string" }, "uniqueItems": true },
          "weight_lost": { "type": "number", "minimum": 0 }
        }
      }
    },
    "unattributed_patterns": {
      "type": "array",
      "description": "有错误模式但没有技能归属的失败（R1/R5 这类门禁断言）。它们不进 skill_gaps——塞进任何技能都会让该技能的错误模式统计失真——但也不能静默丢弃：P5 的教训是『没交产物』在报告里曾完全不留痕。",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["assertion_id", "layer", "patterns"],
        "properties": {
          "assertion_id": { "type": "string" },
          "layer": { "enum": ["gate", "credit"] },
          "patterns": { "type": "array", "items": { "type": "string" }, "minItems": 1, "uniqueItems": true }
        }
      }
    },
    "notes": {
      "type": "array",
      "items": { "type": "string" },
      "description": "报告不自洽或有未覆盖情况时的显式说明（例如『两态同时成立』不允许静默）"
    }
  },
  "$defs": {
    "attribution": {
      "type": "object",
      "additionalProperties": false,
      "required": ["counts_toward_mastery", "emit_error_patterns"],
      "properties": {
        "counts_toward_mastery": { "type": "boolean" },
        "emit_error_patterns": { "type": "boolean" }
      }
    },
    "traceInvalidReason": {
      "enum": [
        "not_reported",
        "call_index_not_sequential",
        "timestamp_not_increasing",
        "missing_required_field",
        "field_type_invalid",
        "status_error_without_http_status"
      ]
    }
  }
}

===== END: docs/diagnosis-report.schema.json =====

文件：`docs/diagnosis-report-schema.md` ｜ sha256[:16]=`d203a8a6a9b9a6d9` ｜ 212 行 ｜ LF
===== BEGIN: docs/diagnosis-report-schema.md =====
# 诊断报告 DTO v2（层间契约：评测层 → 状态层）

> **状态**：已落仓并跑通（`src/arena/diagnosis_report.py` + `docs/diagnosis-report.schema.json` + `fixtures/` 五份夹具）。
> **对应步数**：`review-response-v1.md` §4 的 **S2**，v2 改造对应 §6（P5/P6 拍板）。
> **谁写**：评测层（执行器跑完断言后调用 `build_report()`）。
> **谁读**：状态层（拿 `assertions[].state` + `error_patterns` + `assertion.skill_id` 更新掌握度与错误模式）。
> **不读**：计划书、前端、复习层都不该绕过状态层直接吃这份报告。
>
> 本文写**为什么**，schema 写**长什么样**，`src/arena/diagnosis_report.py` 写**怎么保证**。
> 三者不一致时以 schema 为准，然后回来改本文。
>
> **v2 是破坏性变更**（`report_version: "v1"` → `"v2"`）：rubric 拆成 gate / credit 两层，
> 分母从"全部断言权重和 6"改为"credit 层权重和 4"，新增 `disqualified` / `disqualifying_assertions` /
> `unattributed_patterns` 三个字段。原因见 §3 与 §8。

---

## 1. 四条设计原则（每条都是踩过的坑）

1. **只装可判定的观测，不装推断。** 复习建议、掌握度增量都在状态层产生。一旦在报告里也放一份，就有两套口径。
2. **契约自解释。** 报告自带 `attribution_policy` 与 `denominator_policy`，状态层不用回读 YAML 才知道 `blocked` 算不算掌握度。
3. **宁可报错，也不静默降级。** A2E 的教训是"读不到 trace 就静默给满分"。所以 trace 不可用时**强制**把轨迹断言改写为 `unscored`（调用方在 raw 里填 `pass` 也改），任何不自洽直接抛 `ReportError`，不产半成品。
4. **门禁只管分数，不管观测。** gate 失败时 credit 断言**照常判定并记录**，只是不计入分数。反过来（门禁失败就把 credit 全标 `blocked`）等于用评分逻辑吞掉诊断数据——这是 v1 遗留的隐患，v2 明确禁止。

---

## 2. 顶层结构

```
report_version       "v2"
submission           提交元信息（含 task_git_hash：没有它，断言 id 无法与 YAML 对齐）
trace_audit          trace 前置校验结果（A2E 反面教材的产物）
score                两段式分数：gate 决定资格、credit 决定分数
attribution_policy   四态 × {是否计入掌握度, 是否产出错误模式}
assertions[]         逐条断言：layer / weight / state / skill_id / error_patterns / evidence
skill_gaps[]         技能缺口聚合（只含 fail 且有 skill_id 的断言）
unattributed_patterns[]  有错误模式但无技能归属的失败（R1/R5）——不进 skill_gaps，但不许丢
notes[]              可选。报告出现"未覆盖情况"时必须在这里说明，不许静默
```

顺序约定：`assertions[]` **先 gate 后 credit**，与执行顺序一致（执行器先跑门禁，不过就不必跑昂贵的 credit 判定）。`R5` 排在 `R2` 之前不是笔误。

---

## 3. `score`：两段式（v2 核心变更）

```json
{
  "weighted_sum": 1,
  "credit_pass_weight": 1,
  "total_weight": 4,
  "ratio": 0.25,
  "denominator_policy": "fixed_at_credit_total_weight",
  "disqualified": false,
  "disqualifying_assertions": [],
  "by_state": {"pass": 3, "fail": 2, "blocked": 0, "unscored": 0}
}
```

### 3.1 两段的职责

| 段 | 断言 | 权重 | 作用 |
|---|---|---|---|
| **gate（门禁）** | R1 交产物、R5 正常退出 | **无**（报告里 `weight: 0`，只为结构统一） | 只决定"这份提交有没有资格被评分" |
| **credit（积分）** | R2 结果正确(2)、R3 重试证据(1)、R4 无参数幻觉(1) | 分母 = **4** | 决定"评多少分" |

```
ratio = disqualified ? 0 : credit_pass_weight / 4
```

### 3.2 三条硬规则

- `disqualified = any(gate.state == "fail")`。**门禁 `unscored` 不拦**（`on_unscored: do_not_disqualify`）：无法证明不合格就不判不合格。这不会放水——同一份提交的 credit 层 R3/R4 也因 trace 缺失而 `unscored`，分数上不去。
- 不合格的分数是 **0**，不接受"封顶 0.3"这类做法：封顶值没有依据，评委会直接问"为什么是 0.3"。
- `credit_pass_weight` **与 `disqualified` 无关**，永远等于"credit 层 pass 的权重和"。它只用于诊断，不参与 `ratio`。这个字段让报告保持内部自洽：断言列表里写着 `pass`，就有一个数与它对应。

### 3.3 为什么不用"全部断言权重和"当分母（P4 修正）

v1 的分母是 6，语义是"缺数据也不缩小分母"，方向正确。但它把**门禁型**和**否定型**断言一起算进了分母：

- R4 的判定是 `all_calls_must_be_valid`，一个**全称命题**。零次调用时它平凡为真 → `pass` → 得 1 分。
- R5 只要不崩溃就是 `completed` → 得 1 分。

于是"什么都不做"自动拿 2/6 = **33%**。而"假重试"（交了产物但结果和过程都错）拿 3/6 = 50%。**3 个"没做错事"恰好等于 1 个"做对了核心事"**，分数失去分辨力。详见 §8。

---

## 4. `assertions[]`：四态 + 分层 + 归因

```json
{
  "id": "R3_retry_evidence",
  "layer": "credit",
  "type": "trace_sequence",
  "weight": 1,
  "state": "fail",
  "skill_id": "failure_retry",
  "error_patterns": ["retry_wrong_params"],
  "evidence": {
    "observed": {"offsets": [0, 10, 0], "statuses": ["success", "error", "success"]},
    "expected": {"ordered": true, "calls": ["..."]},
    "extra": {"probe": "calls[?name=='fetch_orders'].params.offset"}
  }
}
```

四态语义（与 YAML 的 `scoring.attribution` 一致）：

| state | 含义 | 分数贡献 | 计入掌握度 | 产出错误模式 |
|---|---|---|---|---|
| `pass` | 断言成立 | 满权重（gate 为 0） | 是 | 否 |
| `fail` | 有证据地不成立 | 0 | 是 | **是**（必须给出模式） |
| `blocked` | 前置未通过，无法判定 | 0 | 否 | 否 |
| `unscored` | 依赖的数据缺失（trace 未报） | 0 | 否 | 否 |

`layer=gate` ⇒ `weight` 必须为 0；`layer=credit` ⇒ `weight` 必须 > 0。schema 和 builder 各拦一次。

### 4.1 `evidence` 的约定

schema 只校验"是对象且含 observed/expected"，语义要求写在 YAML 的 `error_pattern_detection.probe` 里。理由：同一种断言的证据形状会随任务演化，把它写死在 schema 里会让 schema 三个月后没法改。

---

## 5. `skill_gaps[]` 与 `unattributed_patterns[]`

`skill_gaps` 只做聚合：`fail` 且 `skill_id` 非 null 的断言按技能归类，`weight_lost` 是权重和，按它降序。

`unattributed_patterns` 收容另一类：`fail` 但 `skill_id` 为 null 的断言（当前是 R1/R5 这类门禁）。

> **为什么必须有第二类（P5）。** v1 里"没交产物"的那个提交，`skill_gaps` 是**空数组**——R1 没有技能归属，于是这件事在技能层完全不留痕，状态层推不出任何复习项。极端对比：什么活都没干的 `raw-empty` 反而有诊断（它的 R3 记了 `no_retry`），**做得更多、诊断更少**。
>
> 但把它们塞进某个技能（比如 `multi_step_planning`）会让那个技能的错误模式统计失真。所以第三条路：**不进技能缺口，但独立留痕**。

P5 的最终处置是**不加第 4 技能**，走 gate 通道：`score.disqualifying_assertions` 告诉状态层"这次是没交产物"，`unattributed_patterns` 给出具体错误码。若后续数据表明"交付纪律"确实是可训练目标，再加技能节点，届时只需在 `skill-taxonomy-v0.yaml` 增节点并给 R1/R5 填 `skill_id`，报告结构不用改。

---

## 6. `reason_code` 枚举（状态层按它分支，不解析中文）

| code | 用于 | 含义 |
|---|---|---|
| `trace_not_reported` | `unscored` | Agent 完全没上报 trace |
| `trace_invalid` | `unscored` | 报了但没通过前置校验（call_index 不连续、时间戳倒退等） |
| `dependency_blocked` | `blocked` | 前置断言未通过，本断言无从判定 |
| `tool_no_response` | `blocked` | 工具无响应，产物不可得 |
| `invoke_timeout` | `blocked` | 调用超时 |

---

## 7. 由 schema 和组装器分别强制的规则

schema 管结构（能表达的），builder 管语义（schema 表达不了的）。

| 编号 | 规则 | 由谁保证 | 为什么 schema 做不到 |
|---|---|---|---|
| S1 | 四态枚举、`weight >= 0`、`layer` 枚举 | schema | — |
| S2 | `layer=gate ⇒ weight=0`；`layer=credit ⇒ weight>0` | schema（allOf） | — |
| S3 | `state=fail ⇒ error_patterns` 非空；其余三态必须为空 | schema | — |
| S4 | `state ∈ {blocked, unscored} ⇒ reason_code` 必填 | schema | — |
| S5 | `denominator_policy` 只能是 `fixed_at_credit_total_weight` | schema | — |
| S6 | `disqualified=true ⇒ ratio=0 且 weighted_sum=0 且 disqualifying 非空` | schema | — |
| I1 | 断言 id 集合 = `rubric_gate ∪ rubric_credit`，不重不漏、顺序一致 | builder | schema 无法表达"等于另一份文件的集合" |
| I2 | trace 不可用 ⇒ 所有 `type` 以 `trace_` 开头的断言强制 `unscored` | builder | 需要读外部状态 |
| I3 | `fail` 的模式 ⊆ 本断言声明 ∩ 词表 | builder | 需要读词表文件 |
| I4 | `blocked` 必须有真失败的前置 | builder | 需要跨条目引用 |
| I5 | `total_weight` = credit 权重和（本任务 4），与 YAML 声明一致 | builder | 跨文件一致性 |
| I6 | `weighted_sum = disqualified ? 0 : credit_pass_weight` | builder | 算术 |
| I7 | `credit_pass_weight` = credit 层 pass 的权重和（与断言列表逐条自洽） | builder | 跨条目汇总 |
| I8 | 门禁 `unscored` 不导致 `disqualified` | builder | 策略判断 |
| I9 | R1/R5 失败时其模式进 `unattributed_patterns`，不进 `skill_gaps` | builder | 归因策略 |

---

## 8. 五份夹具的实测结果（本步的证据）

| 夹具 | 提交形态 | v1 ratio | **v2 ratio** | 关键判定 |
|---|---|---|---|---|
| `raw-empty` | 0 次工具调用就正常退出 | **0.333** | **0（DQ）** | R1 门禁拦下。R4 因全称命题对空集平凡为真而 `pass`——正是要堵的漏洞 |
| `raw-no-file` | 过程全对，产物没写 | 0.500 | **0（DQ）** | R1 门禁拦下；`credit_pass_weight=2` 仍照算，`unattributed_patterns=[no_output_file]` |
| `raw-fake-retry` | 游标退回 offset=0 的假重试 | 0.500 | **0.25** | credit 只拿 R4 的 1 分；`skill_gaps` 两个（R2 的 2 分 + R3 的 1 分） |
| `raw-no-trace` | 结果对，但没报 trace | 0.500 | **0.500** | 门禁 R5 `unscored` 不拦；credit 只剩 R2 的 2 分 |
| `raw-perfect` | 全对 | — | **1.000** | 上界锚点 |

**分数序 `0 → 0 → 0.25 → 0.50 → 1.00`，单调。** v1 是 `0.333 → 0.500 → 0.500 → 0.500`，三档质量撞在同一个值上——这就是 P6。

`test_score_is_monotonic_across_fixtures` 把这条序写成断言，`raw-perfect` 保证上端有锚点（否则只能验证下界和中间值）。

### 8.1 v2 保留的两个已知取舍

1. **"过程全对但忘交文件"记 0 分**，惩罚偏重。这是 `disqualified_if: any_gate_failed` 的直接后果。接受的理由：任务没完成就是没完成，给 0.5 会让"不交产物"变成可行策略。若后续认为过重，可改 `on_fail` 为 `cap` 并给出**有依据的**封顶值，而不是随手写 0.3。
2. **门禁 `unscored` 不拦**，理论上"崩溃了但不报 trace"能绕过 R5。实际绕不过：同一份提交的 credit 层也全 `unscored`，分数不会因此变高。

---

## 9. 已拍板的决策（v2 关闭 v1 的三条待定项）

| # | 议题 | 结论 |
|---|---|---|
| **P4** | 分数分母口径 | 分母恒为 **credit 层权重和 4**，不因断言缺失而缩小。gate 没有权重，不进分母 |
| **P5** | R1/R5 在分类学里无归属，怎么办 | **不加第 4 技能**，走 gate 通道：`disqualifying_assertions` + `unattributed_patterns` 留痕。理由见 §5 |
| **P6** | 分数分辨率不足（三种提交同为 0.5） | 改为 **gate / credit 两段式**，门禁未过记 0 |
| P1 | 掌握度口径（FSRS D/S/R vs EWMA + S 作衰减） | **仍未拍板**，不影响本 DTO |
| P2 / P3 | 全 unscored 时结果断言是否照常计分；防作弊优先项 | 已按"照常计分、不更新 S"与"优先 R6 一致性校验"实现，未写进计划书 |

---

## 10. v2 故意不做的事

- **不放复习建议、不放掌握度增量**：那是状态层的事，放进来就是第二套口径。
- **不放 variant / FSRS 字段**：DTO 只描述"这次提交怎么样"，不描述"下次该做什么"。
- **不做分数平滑或曲线映射**：0 / 0.25 / 0.5 / 1 是四档粗粒度，够把质量序分开。更细的刻度等真实数据再定。
- **不把 `evidence` 结构写死进 schema**：见 §4.1。

===== END: docs/diagnosis-report-schema.md =====

文件：`docs/executor-semantics.md` ｜ sha256[:16]=`aa6be0e64c4b957a` ｜ 29 行 ｜ LF
===== BEGIN: docs/executor-semantics.md =====
# 执行器判定语义（M0 第 8 步实现前必读）

> 状态：v0 占位（2026-11-06 建）。M0 执行器开工时扩写为完整判定语义文档；本页先锁定已定稿、
> 且历史上被评审指出"容易实现错"的两条规则。与任务 YAML 内注释冲突时，以本页为准并修 YAML。

## 1. trace_sequence 的子序列匹配（R3）

`ordered: true` + `allow_interleaved: true` 的组合语义是**子序列匹配**，不是相邻匹配：

1. 在 tool_calls 中找第一个满足 `name=fetch_orders && params_match{offset:10} && status=error && http_status=500` 的调用；
2. 在它**之后**找第一个满足 `name=fetch_orders && params_match{offset:10} && status=success` 的调用；
3. 两次命中之间允许存在任意数量的其他工具调用（包括对 fetch_orders 的其他 offset 调用）。

**禁止实现成**：两条调用在数组中必须相邻；或中间不得有任何其他调用——那会误伤合理的失败恢复路径
（如 `offset=10 失败 → offset=5 补拉成功 → offset=10 重试成功`）。

## 2. trace_param_schema_valid 的校验范围（R4）

`check_schema: true` 的语义**同时覆盖四项**，缺一即该次调用违规（叠加 `mode: all_calls_must_be_valid`）：

1. **required**：必填参数缺失（如 `fetch_orders(offset=10)` 漏 `limit`）；
2. **type**：参数值类型（integer/string）；
3. **range**：minimum/maximum（offset≥0，1≤limit≤10）；
4. **additional_properties**：schema 外参数一律违规。

## 3. 结果断言的容差（R2）

`json_field_equals` + `tolerance: {total_amount: 0.01}`：数值字段按 `abs(实际-期望) ≤ tolerance` 判定，
非数值字段按 `compare: subset` 精确匹配。禁止对浮点做严格 `==`。

===== END: docs/executor-semantics.md =====

文件：`docs/error-patterns-v0.yaml` ｜ sha256[:16]=`ed09b3e5cd7cff11` ｜ 105 行 ｜ LF
===== BEGIN: docs/error-patterns-v0.yaml =====
# 错误模式词表 v0（第 6 轮评审新增）
#
# 为什么单独成文件：data-model.md §ErrorPattern 规定「label 全局唯一，新增标签先入词表再使用」，
# 但词表一直只存在于文档的举例行里，无法被测试约束。本文件把它变成可校验资产：
#   tasks/*.yaml 的 rubric[].error_patterns 只能引用本文件里的 pattern_id，
#   docs/skill-taxonomy-v0.yaml 的 skill_id 只能引用本文件的 mapped_skill_ids。
#
# 字段对齐 data-model.md §ErrorPattern（pattern_id / label / description / detection_rule / mapped_skill_ids）；
# 「严重度」等用户维度权重不入词表（那是 UserSkillState 侧的事，词表只描述"是什么错"）。
#
# 约定：
#   - pattern_id：机器码，小写下划线，稳定不改（诊断报告与复习推荐按它索引）
#   - label：中文展示名，可改
#   - detection_rule.assertion_id：唯一检出该模式的原子断言（多检出源时用数组）
#   - mapped_skill_ids 为空表示「只诊断不归因」：会计分，但不驱动技能掌握度与复习推荐

patterns:
  # ---- R1 交付物存在性（交付纪律，不属于当前三技能）----
  - pattern_id: no_output_file
    label: 未产出文件
    description: 任务要求写入 /workspace/report.json，但该文件不存在
    detection_rule: {assertion_id: R1_file_exists, when: {exists: false}}
    mapped_skill_ids: []

  - pattern_id: empty_output_file
    label: 产出空文件
    description: 文件存在但内容为空，等价于未交付
    detection_rule: {assertion_id: R1_file_exists, when: {non_empty: false}}
    mapped_skill_ids: []

  # ---- R2 结果正确性（多步执行完整性）----
  - pattern_id: wrong_valid_count
    label: 有效数算错
    description: valid_count 与依据 fixture 剔除规则算出的 17 不符（漏剔或多剔）
    detection_rule: {assertion_id: R2_report_correct, when: {valid_count_mismatch: true}}
    mapped_skill_ids: [multi_step_planning]

  - pattern_id: wrong_total_amount
    label: 总额算错
    description: total_amount 超出 0.01 容差；常见于把无效订单金额计入，或分页漏拉导致少算
    detection_rule: {assertion_id: R2_report_correct, when: {total_amount_mismatch: true}}
    mapped_skill_ids: [multi_step_planning]

  - pattern_id: report_missing_field
    label: 报告缺字段
    description: report.json 未包含 valid_count 或 total_amount
    detection_rule: {assertion_id: R2_report_correct, when: {missing_fields: [valid_count, total_amount]}}
    mapped_skill_ids: [multi_step_planning]

  # ---- R3 失败重试（本任务的核心考点）----
  - pattern_id: no_retry
    label: 未重试
    description: 首次 500 后没有再对 offset=10 发起调用，直接放弃或退回首页
    detection_rule: {assertion_id: R3_retry_evidence, when: {offsets_equals: [0, 10]}}
    mapped_skill_ids: [failure_retry]

  - pattern_id: retry_wrong_params
    label: 重试参数错误
    description: 确实重试了，但游标退回首页（offset 序列 0,10,0）；耗时增加且分页永不收敛
    detection_rule: {assertion_id: R3_retry_evidence, when: {offsets_equals: [0, 10, 0]}}
    mapped_skill_ids: [failure_retry]

  - pattern_id: retry_exhausted
    label: 重试耗尽
    description: 同 offset 连续重试仍失败；缺退避或重试上限设置不当
    detection_rule: {assertion_id: R3_retry_evidence, when: {offsets_starts_with: [0, 10, 10], last_status: error}}
    mapped_skill_ids: [failure_retry]

  # ---- R4 工具参数校验 ----
  - pattern_id: param_out_of_schema
    label: 参数幻觉
    description: 调用中出现了 tools 未声明的参数名（additional_properties=false 时即违规）
    detection_rule: {assertion_id: R4_no_param_hallucination, when: {extra_keys_against: tools}}
    mapped_skill_ids: [tool_param_validation]

  - pattern_id: param_missing_required
    label: 漏传必填参数
    description: required=true 的参数未出现在调用中
    detection_rule: {assertion_id: R4_no_param_hallucination, when: {missing_required_against: tools}}
    mapped_skill_ids: [tool_param_validation]

  - pattern_id: param_type_mismatch
    label: 参数类型错误
    description: 参数类型与声明不符（如 limit 传成字符串 "10"）
    detection_rule: {assertion_id: R4_no_param_hallucination, when: {type_mismatch_against: tools}}
    mapped_skill_ids: [tool_param_validation]

  - pattern_id: param_out_of_range
    label: 参数越界
    description: 取值超出 minimum/maximum（如试探 limit=20 触发 invalid_limit）
    detection_rule: {assertion_id: R4_no_param_hallucination, when: {out_of_range_against: tools}}
    mapped_skill_ids: [tool_param_validation]

  # ---- R5 退出状态（任务完成度，不属于当前三技能）----
  - pattern_id: task_crashed
    label: 任务崩溃
    description: final_status=crashed，Agent 未走完流程即异常终止
    detection_rule: {assertion_id: R5_clean_exit, when: {final_status: crashed}}
    mapped_skill_ids: []

  - pattern_id: task_timeout
    label: 任务超时
    description: final_status=timeout，超出调用时限
    detection_rule: {assertion_id: R5_clean_exit, when: {final_status: timeout}}
    mapped_skill_ids: []

===== END: docs/error-patterns-v0.yaml =====

文件：`src/arena/assertion_evaluator.py` ｜ sha256[:16]=`68282d0c4f862d22` ｜ 1379 行 ｜ LF
===== BEGIN: src/arena/assertion_evaluator.py =====
"""S3-1 / S3-3-2 ~ S3-3-5：断言执行器（R3 / R5 / R1 / R2 / R4）。

## 这一层为什么存在

`fixtures/raw-*.json` 里的 `assertion_results` 目前是**手工伪造的评测输出**，
`diagnosis_report.build_report()` 只负责组装，不负责判定。也就是说仓库此前
验证的是"组装器对不对"，不是"判定对不对"。本模块补上缺失的那一层：

    trace(tool_calls) + 产物  →  本模块  →  assertion_results  →  build_report()  →  DTO

## 分工（来自 spike/README.md 与 spike/jmespath_probe.py 的实测结论）

- **判定可外借**：JMESPath 的过滤器天然把"中间穿插其他调用"消解掉，
  `allow_interleaved` 不需要额外机制。表达式由 `rubric.expected.calls` **派生**，
  脚本里不写 `offset=10` 这类任务常量。
- **错误模式分类必须自研**：假重试与无重试在过滤后的状态序列里完全相同
  （都是 `["error"]`），必须另取 `offsets` 序列才能分开。分类规则从
  `error_pattern_detection.cases` 顺序读取，YAML 是唯一真相源。

## Q10-A（round-10，人已拍板）

`error_pattern_detection.cases` 补三条由调用计数驱动的规则，覆盖
「零调用 / 未触达故障点 / 故障点未重复」，让这批边界不再靠 `default` 兜底。
`default` 本身**保留**：未枚举的序列仍会走它，`classification_source` 如实记录，
所以覆盖度是可审计的。计数来自 `expected.calls` 派生的过滤表达式，不写死任务常量。
按 A 方案不新增 `pattern_id`、不改归因 —— 跨技能归因登记为 TD-001，等 M2 处理。

## TD-003（round-12 外部验收 H1 / V9 的处置）

`error_pattern_detection.cases` 在**加载期与判定期各校验一次**：`match` 必须是非空对象、
谓词名合法、计数谓词的值只能是 `true`。两个真实漏洞驱动它：

- `match` 缺失或为空时，`case.get("match") or {}` 配上 `all([]) == True`
  会把整条 case 变成「全命中 catch-all」，来源还记成空的 `case:`
  —— 一个 `mtach:` 拼写错就能静默吞掉整条分类链；
- 计数谓词写成 `false`、或未知谓词藏在不会被求值到的 case 里，
  会被 `break` / `all()` 短路永久跳过，潜伏到某天词表一改才走错。

校验放在**接触任何 trace 之前**：配置错直接抛，不返回 unscored，
也不混在「某条特定轨迹」里才暴露。

## S3-3-2：R5 trace_final_status

`evaluate_assertion()` 是唯一分发入口：按 `spec["type"]` 分派到 R3 / R5 两个执行器，
其余 type 抛 `NotImplementedError`，不伪造结果。R5 只判 `trace.final_status`：

- 值域来自 `trace_requirements.minimum.final_status.enum`（封闭枚举），
  `expected` 与 cases 的 pattern 也都从 YAML 派生，代码里没有第二份任务常量；
- 未上报 trace / 值不在枚举内 → `unscored`（不判 0、不归因），与 `build_report()`
  对 `trace_*` 断言的强制改写保持一致；
- 非 `completed` 的取值按 `error_pattern_detection.cases` 分类，
  cases 覆盖不完就抛错，绝不静默编一个错误模式。

## S3-3-3：R1 file_exists

产物是**独立于 trace 的第二份证据**（Q13=a：五份 raw 夹具带 `artifacts` 字段）。
执行器只从 `artifacts[target]` 读内容，**绝不**从 `trace.tool_calls` 里
`write_report.params.content` 推断——伪造 trace 就能过 R1/R2 正是 P3 要堵的洞。

- `artifacts` 字段缺失 / 不是对象 / 内容不是字符串 → `unscored`：
  **平台没采集到产物 ≠ 学生没交产物**，不把数据问题记成能力不足；
- `target` 不在 `artifacts` 里 → `fail` + `no_output_file`；空字符串 → `fail` + `empty_output_file`；
- 「非空」只判字节数 > 0，**不解析 JSON**：内容对不对是 R2 的职责，这里不越界；
- 期望值、分类规则、错误模式全部从 YAML 派生；`fail` 却漏配 case 就抛错，不编 pattern。

## S3-3-4：R2 json_field_equals（报告内容正确性）

判定语义以 `docs/executor-semantics.md` §3 为准：配置了 `tolerance` 的数值字段按
`abs(实际 - 期望) ≤ tolerance` 判定，**禁止对浮点做严格 `==`**；其余字段按
`compare: subset` 精确匹配（报告里的额外字段一律忽略）。期望值、容差、分类规则、
合法 match 键名全部从任务 YAML 派生，代码里不写 `valid_count` / `1837.99` 这类常量。

R2 与前面三个执行器有两处结构性差异：

- **分类是「收集全部命中」而不是「首命中即止」**：手写 `raw-fake-retry` 的
  `error_patterns` 同时含 `wrong_total_amount` 与 `wrong_valid_count`。按 YAML cases
  顺序去重输出，命中来源写精确键名（TD-004 同款纪律）。
- **依赖折叠由执行器显式判定**：`depends_on: [R1_file_exists]` 且前置未通过 →
  `blocked / dependency_blocked`。调用方必须传入前置结果，缺了就抛错，
  绝不在没有依据的情况下猜一个 pass/fail。

两处**假设**（YAML / DTO / 词表都没有现成口径，本步不擅自扩，S3-3-6 请人拍板）：

- 产物内容不是合法 JSON 对象时，按「expected 里的字段一个都没观测到」处理，
  归入既有 `report_missing_field`；不新增 `report_invalid_json` pattern。
- `fail` 时多 pattern 按 YAML cases 顺序去重；手写夹具是反序，但 state 与归因一致。

## S3-3-5：R4 trace_param_schema_valid（工具参数幻觉）

判定语义以 `docs/executor-semantics.md` §2 为准：`check_schema: true` 同时校验
**required / type / range / additional_properties** 四项，叠加
`mode: all_calls_must_be_valid` —— 任一次调用违规即判 fail。工具声明从
`expected.schema_ref`（当前是 `tools`）指向的任务 YAML 表读取，代码里不写
工具名 / 参数名 / 数值边界。分类按 `error_pattern_detection.cases` **首命中即止**
（与 R2 的「收集全部命中」不同），四种违规都有对应 case，漏配在加载期就抛错。

两处**假设**（YAML / DTO / 词表都没有现成口径，本步不擅自扩，S3-3-6 请人拍板）：

- 调用里出现 tools 未声明的**工具名**时，无法对照 schema 判定，归
  `unscored / trace_invalid`（数据问题，不记成能力不足）；不复用
  `param_out_of_schema`——词表里它专指「未声明的**参数名**」。
- 同一次调用有多类违规时，只按 cases 顺序报最优先的一个 pattern；
  全量违规明细仍留在 `evidence.observed.violations`，诊断信息不丢。

## 明确不做（S3-3-5 的边界）

- R5 只判上报的终态，不推断「进程是否真的干净退出」，不做超时控制。
- 不改 fixtures 的手写 `assertion_results`；不把 R1/R2/R4/R5 接进 `trace_pipeline`
  的现算集合：全链路切到「五条全现算」留到 S3-3-6。本步 R4 只在约束测试里被现算。
- 不实现 mock / 故障注入（JMESPath 实测表达不了），不做调用超时控制。
- 不改 DTO `reason_code` 枚举（TD-008 仍挂 S3-3-6）、不改错误模式词表、不改归因。

## 调用序列的可信度前置校验（round-8 ① 的处置）

任务 YAML 的 `trace_requirements.minimum.tool_calls.item_fields` 明列 `call_index`
（平台侧编号，1 起）；`docs/review-response-v1.md` §1 第 1b 条已采纳
「call_index 连续校验 → trace_invalid → 轨迹断言 unscored」。

**刻意不做「按 call_index 排序预处理」**：数组顺序与编号不符说明上报数据本身
不可信，重排后拿它判 fail，等于把数据问题记成能力不足（会污染掌握度）。
按项目纪律宁可判 unscored、不归因，也不用不可信的证据下结论。

用法：
    python src/arena/assertion_evaluator.py --demo     # 打印 trace 判定与分类对照表
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import jmespath

ROOT = Path(__file__).resolve().parents[2]
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"

SUPPORTED_TYPES = ("trace_sequence", "trace_final_status", "file_exists",
                   "json_field_equals", "trace_param_schema_valid")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EvaluatorError(ValueError):
    """判定器无法给出可信结果。宁可不产出，也不猜一个 pass/fail。"""


@lru_cache(maxsize=8)
def _read_task_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def load_task_config(task_path: str | Path = TASK_PATH) -> dict:
    """读任务 YAML。缓存的只是文件文本，每次返回新的解析结果。

    不缓存 dict：调用方常会 deepcopy 后改 spec 做回归锁，缓存可变对象会被污染。
    """
    import yaml

    return yaml.safe_load(_read_task_text(str(Path(task_path).resolve())))


def load_trace_requirements(task_path: str | Path = TASK_PATH) -> dict:
    return load_task_config(task_path)["trace_requirements"]["minimum"]


# --------------------------------------------------------------------------
# YAML 派生：不把任务常量写进代码
# --------------------------------------------------------------------------


def _literal(value: Any) -> str:
    """Python 值 -> JMESPath 反引号 JSON 字面量（字符串、数字、bool、null 都适用）。"""
    return "`" + json.dumps(value, ensure_ascii=False) + "`"


def _param_path(key: str) -> str:
    return f"params.{key}" if _IDENT.match(key) else f'params."{key}"'


def _filter_expr(spec: dict) -> str:
    """由 expected.calls 派生过滤表达式。

    约束（不满足就抛错，不静默降级）：
    - `ordered` 必须为 true：本实现按子序列匹配，无序语义尚未定义；
    - `allow_interleaved` 必须为 true：过滤法天然允许穿插，若契约要求严格相邻，
      这个实现会误判，所以拒绝执行而不是给出看起来合理的结果；
    - 所有步骤的 (name, params_match) 必须一致——当前只支持"同一页重试"。
    """
    expected = spec.get("expected") or {}
    steps = expected.get("calls") or []
    if len(steps) < 2:
        raise EvaluatorError(f"{spec.get('id')}: expected.calls 至少需要两步（失败 + 成功）")
    if not expected.get("ordered"):
        raise EvaluatorError(f"{spec.get('id')}: 只支持 ordered=true 的序列")
    if not expected.get("allow_interleaved"):
        raise EvaluatorError(
            f"{spec.get('id')}: allow_interleaved=false 需要严格相邻匹配，本实现不支持"
        )

    first = steps[0]
    name = first.get("name")
    match = first.get("params_match") or {}
    if not name or not match:
        raise EvaluatorError(f"{spec.get('id')}: 首步缺 name 或 params_match")
    for step in steps[1:]:
        if step.get("name") != name or (step.get("params_match") or {}) != match:
            raise EvaluatorError(
                f"{spec.get('id')}: 各步骤 (name, params_match) 不一致，"
                "当前只支持同参重试；跨参数序列需要新的匹配器"
            )

    conds = [f"name=={_literal(name)}"]
    conds += [f"{_param_path(k)}=={_literal(v)}" for k, v in match.items()]
    return "calls[?(" + " && ".join(conds) + ")].{s: status, h: http_status}"


def _match_step(projected: list[dict], step: dict, start: int) -> int:
    """从 start 起找第一个满足该步骤的投影元素，返回下标；找不到返回 -1。

    `status` 必比；`http_status` 只在 YAML 明确写了时才比。
    """
    for i in range(start, len(projected)):
        item = projected[i] or {}
        if item.get("s") != step.get("status"):
            continue
        if "http_status" in step and item.get("h") != step["http_status"]:
            continue
        return i
    return -1


def _status_enum(requirements: dict | None, rid: str) -> list:
    """Q12(a)：status 允许值只从任务 YAML 派生，代码里不写第二份枚举。"""
    req = requirements if requirements is not None else load_trace_requirements()
    enum = (req.get("tool_calls") or {}).get("status_enum") if isinstance(req, dict) else None
    if not isinstance(enum, list) or not enum:
        raise EvaluatorError(f"{rid}: trace_requirements.minimum.tool_calls.status_enum 缺失")
    return enum


def _validate_calls(calls: list[dict], status_enum: list) -> str | None:
    """校验调用序列可信度；返回违规原因，None 表示可用。

    不排序、不重编号、不丢弃可疑调用：任何不一致都判数据不可信。
    Q12(a) 补两项：`status` 必须在 YAML 枚举内、`params` 必须是对象。
    少了这两项，JMESPath 投影会静默丢弃畸形调用，把「上报数据坏」判成
    「Agent 不会重试」（fail），污染掌握度与错误模式。
    """
    for i, item in enumerate(calls):
        if not isinstance(item, dict):
            return f"第 {i + 1} 条调用不是对象（{type(item).__name__}）"
        idx = item.get("call_index")
        if isinstance(idx, bool) or not isinstance(idx, int):
            return f"第 {i + 1} 条调用缺 call_index 或类型非整数：{idx!r}"
        if idx != i + 1:
            return f"call_index 不连续或与上报顺序不符：第 {i + 1} 条为 {idx}"
        if item.get("status") not in status_enum:
            return (f"第 {i + 1} 条调用 status 非法：{item.get('status')!r}，"
                    f"允许值 {list(status_enum)}")
        if not isinstance(item.get("params"), dict):
            return (f"第 {i + 1} 条调用 params 不是对象："
                    f"{type(item.get('params')).__name__}")
    return None


def _judge(spec: dict, calls: list[dict], expr: str) -> tuple[bool, list[dict], int]:
    """子序列匹配 expected.calls。返回 (是否通过, 投影序列, 已匹配步数)。"""
    projected = jmespath.search(expr, {"calls": calls}) or []
    cursor = 0
    matched = 0
    for step in spec["expected"]["calls"]:
        hit = _match_step(projected, step, cursor)
        if hit < 0:
            return False, projected, matched
        cursor = hit + 1
        matched += 1
    return True, projected, matched


# --------------------------------------------------------------------------
# 错误模式分类：YAML cases 是唯一真相源，未知谓词直接抛错
# --------------------------------------------------------------------------

# Q10-A（round-10）新增的三个语义谓词。它们的输入全部由 `expected.calls` 派生的
# 过滤表达式算出来，**不硬编码 offset=10，也不新增 pattern_id**：
#   call_count       = probe 取到的工具调用条数
#   fault_call_count = projected 的条数（即命中 name + params_match 的调用次数）
# 所以「故障点触达/重复」的定义跟着 YAML 走，YAML 一改谓词结论自动跟着变。
_COUNT_PREDICATES = {
    "no_tool_calls": lambda c: c["call_count"] == 0,
    "fault_point_not_reached": lambda c: c["fault_call_count"] == 0,
    "fault_point_not_repeated": lambda c: c["fault_call_count"] < 2,
}

_PREDICATES = ("offsets_equals", "offsets_starts_with", "last_status",
               *_COUNT_PREDICATES)


def _validate_classification_spec(spec: dict) -> None:
    """TD-003：分类 spec 的结构校验，必须在看任何 trace 之前做。

    加载期（`load_r3_spec`）与判定期（`evaluate` / `_classify`）都调它：
    前者让 YAML 配置错立即失败，后者挡住「加载后被内存改坏」的 spec。
    这里只查结构，不查 `pattern` 是否已声明 —— 那是分类结果的检查，
    留在 `_classify`，措辞与既有回归锁保持一致。
    """
    rid = spec.get("id")
    detection = spec.get("error_pattern_detection") or {}
    cases = detection.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluatorError(f"{rid}: 缺少 error_pattern_detection.cases，无法分类")
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 不是对象（{type(case).__name__}）")
        match = case.get("match")
        if not isinstance(match, dict) or not match:
            raise EvaluatorError(
                f"{rid}: 第 {i} 条 case 缺非空 match（空 match 会变成全命中 catch-all）"
            )
        for key, want in match.items():
            if key not in _PREDICATES:
                raise EvaluatorError(
                    f"{rid}: 未知的分类谓词 {key!r}；已支持：{_PREDICATES}"
                )
            if key in _COUNT_PREDICATES and want is not True:
                raise EvaluatorError(f"分类谓词 {key!r} 只接受 true，收到 {want!r}")


def _predicate_holds(key: str, want: Any, ctx: dict) -> bool:
    offsets = ctx["offsets"]
    if key == "offsets_equals":
        return offsets == want
    if key == "offsets_starts_with":
        return offsets[: len(want)] == want
    if key == "last_status":
        return ctx["last_status"] == want
    if key in _COUNT_PREDICATES:
        # 这三个是「存在性」判断，只接受 true。写错成 false 必须报错，
        # 不能静默把整条规则变成永不命中、又悄悄退回 default。
        if want is not True:
            raise EvaluatorError(f"分类谓词 {key!r} 只接受 true，收到 {want!r}")
        return _COUNT_PREDICATES[key](ctx)
    raise EvaluatorError(f"未知的分类谓词 {key!r}；已支持：{_PREDICATES}")


def _classify(spec: dict, ctx: dict, allowed: set[str]) -> tuple[str, str]:
    """顺序匹配 cases，首命中即止。返回 (pattern_id, 命中来源)。

    `default` 是 YAML 明写的兜底。它可能被频繁触发（说明词表有缺口），
    所以把命中来源一并返回，写进 evidence，供外部审查覆盖度。
    """
    # 全量校验，不依赖 all() 逐条求值：短路会让「前置条件为假」的 case 里的
    # 非法配置永远不被发现，静默潜伏到某天词表一改就走错分类。
    _validate_classification_spec(spec)
    detection = spec["error_pattern_detection"]
    cases = detection["cases"]
    for case in cases:
        match = case["match"]
        if all(_predicate_holds(k, v, ctx) for k, v in match.items()):
            pattern = case["pattern"]
            source = "case:" + ",".join(sorted(match))
            break
    else:
        if "default" not in detection:
            raise EvaluatorError(f"{spec.get('id')}: 所有 cases 均未命中且无 default")
        pattern = detection["default"]
        source = "default"

    declared = set(spec.get("error_patterns") or [])
    if pattern not in declared:
        raise EvaluatorError(f"{spec.get('id')}: 分类结果 {pattern} 不在本断言 error_patterns 中")
    if pattern not in allowed:
        raise EvaluatorError(f"{spec.get('id')}: 分类结果 {pattern} 未登记进错误模式词表")
    return pattern, source


# --------------------------------------------------------------------------
# S3-3-2：R5 trace_final_status 执行器
# --------------------------------------------------------------------------


def _validate_final_status_spec(spec: dict) -> None:
    """TD-003 同款纪律：配置错在接触任何 trace 之前抛，不许变成 catch-all。"""
    rid = spec.get("id")
    target = spec.get("target")
    if not isinstance(target, str) or not target:
        raise EvaluatorError(f"{rid}: trace_final_status 缺 target")
    if "expected" not in spec:
        raise EvaluatorError(f"{rid}: 缺 expected")
    detection = spec.get("error_pattern_detection") or {}
    cases = detection.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluatorError(f"{rid}: 缺少 error_pattern_detection.cases，无法分类")
    declared = set(spec.get("error_patterns") or [])
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 不是对象")
        match = case.get("match")
        # 空 match / 拼错 key 都会让「首命中」变成假命中或永不命中，必须拒绝。
        if not isinstance(match, dict) or set(match) != {target}:
            raise EvaluatorError(
                f"{rid}: 第 {i} 条 case 的 match 必须且只能含 {target!r}（收到 {match!r}）"
            )
        if case.get("pattern") not in declared:
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 的 pattern 未在本断言 error_patterns 声明")


def _final_status_enum(requirements: dict | None, rid: str) -> list:
    req = requirements if requirements is not None else load_trace_requirements()
    enum = (req.get("final_status") or {}).get("enum") if isinstance(req, dict) else None
    if not isinstance(enum, list) or not enum:
        raise EvaluatorError(f"{rid}: trace_requirements.minimum.final_status.enum 缺失")
    return enum


def _classify_final_status(spec: dict, observed: Any, allowed: set[str]) -> str:
    target = spec["target"]
    for case in spec["error_pattern_detection"]["cases"]:
        if case["match"][target] == observed:
            pattern = case["pattern"]
            break
    else:
        raise EvaluatorError(
            f"{spec['id']}: final_status={observed!r} 未命中任何 case，且本断言没有 default；"
            "请补 YAML 规则，不要静默编造错误模式"
        )
    if pattern not in allowed:
        raise EvaluatorError(f"{spec['id']}: 分类结果 {pattern} 未登记进错误模式词表")
    return pattern


def evaluate_final_status(spec: dict, trace: dict | None,
                          vocab: set[str] | None = None,
                          requirements: dict | None = None) -> dict:
    """R5：判 trace.final_status 是否等于 YAML 的 expected。

    值域、期望、分类规则全部从任务 YAML 派生。未上报 / 值越界判 unscored，
    与 `build_report()` 对 trace_* 断言的强制改写保持同一口径。
    """
    if spec.get("type") != "trace_final_status":
        raise EvaluatorError(f"{spec.get('id')}: evaluate_final_status 只接受 trace_final_status")
    _validate_final_status_spec(spec)

    rid, target, expected = spec["id"], spec["target"], spec["expected"]
    enum = _final_status_enum(requirements, rid)
    if expected not in enum:
        raise EvaluatorError(f"{rid}: expected={expected!r} 不在 final_status 枚举 {enum} 内")

    base = {"expected": expected}

    def unscored(code: str, observed: Any, note: str) -> dict:
        return {"id": rid, "state": "unscored", "reason_code": code,
                "evidence": {"observed": observed, **base}, "note": note}

    if trace is None:
        return unscored("trace_not_reported", None,
                        "trace 未上报：R5 判 unscored，不判 0；门禁 unscored 不拦")
    if not isinstance(trace, dict):
        return unscored("trace_invalid", None, "trace 不是对象：判 unscored、不归因")

    observed = trace.get(target)
    if observed not in enum:
        return unscored(
            "trace_invalid", observed,
            f"trace.{target}={observed!r} 不在枚举 {enum} 内：上报数据不可信，判 unscored",
        )

    result = {"id": rid, "state": "pass" if observed == expected else "fail",
              "evidence": {"observed": observed, **base}}
    if observed == expected:
        return result

    allowed = vocab if vocab is not None else _load_vocab()
    pattern = _classify_final_status(spec, observed, allowed)
    result["error_patterns"] = [pattern]
    result["evidence"]["extra"] = {"classification_source": f"case:{target}"}
    return result


def load_r5_spec(task_path: str | Path = TASK_PATH) -> dict:
    task = load_task_config(task_path)
    r5 = next(r for r in task["rubric_gate"] if r["id"] == "R5_clean_exit")
    _validate_final_status_spec(r5)  # 与 load_r3_spec 同一纪律：加载即校验
    return r5


# --------------------------------------------------------------------------
# S3-3-3：R1 file_exists 执行器（产物是独立于 trace 的第二份证据）
# --------------------------------------------------------------------------

_FILE_EXISTS_MATCH_KEYS = ("exists", "non_empty")


def _validate_file_exists_spec(spec: dict) -> None:
    """TD-003 同款纪律：配置错在接触任何产物之前抛，不许变成 catch-all。"""
    rid = spec.get("id")
    if spec.get("type") != "file_exists":
        raise EvaluatorError(f"{rid}: evaluate_file_exists 只接受 file_exists")
    target = spec.get("target")
    if not isinstance(target, str) or not target:
        raise EvaluatorError(f"{rid}: file_exists 缺 target")
    options = spec.get("options") or {}
    # 只实现 non_empty=true 这一种语义。false 不是「更简单」，是另一套判定；
    # 静默按 true 跑会给出看起来合理却与契约不符的结果，所以直接拒绝。
    if options.get("non_empty") is not True:
        raise EvaluatorError(
            f"{rid}: 只支持 options.non_empty=true，收到 {options.get('non_empty')!r}"
        )

    detection = spec.get("error_pattern_detection") or {}
    cases = detection.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluatorError(f"{rid}: 缺少 error_pattern_detection.cases，无法分类")
    declared = set(spec.get("error_patterns") or [])
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 不是对象")
        match = case.get("match")
        if not isinstance(match, dict) or not match:
            raise EvaluatorError(
                f"{rid}: 第 {i} 条 case 缺非空 match（空 match 会变成全命中 catch-all）"
            )
        for key, want in match.items():
            if key not in _FILE_EXISTS_MATCH_KEYS:
                raise EvaluatorError(
                    f"{rid}: 第 {i} 条 case 的 match 键 {key!r} 非法；"
                    f"只允许 {_FILE_EXISTS_MATCH_KEYS}"
                )
            if not isinstance(want, bool):
                raise EvaluatorError(
                    f"{rid}: 第 {i} 条 case 的 {key!r} 必须是 bool，收到 {want!r}"
                )
        if case.get("pattern") not in declared:
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 的 pattern 未在本断言 error_patterns 声明")
    if "default" in detection and detection["default"] not in declared:
        raise EvaluatorError(f"{rid}: default 未在本断言 error_patterns 声明")


def _classify_file_exists(spec: dict, observed: dict, allowed: set[str]) -> tuple[str, str]:
    """顺序匹配 cases，首命中即止；漏配就抛，不静默编错误模式。"""
    rid = spec["id"]
    declared = set(spec.get("error_patterns") or [])
    detection = spec["error_pattern_detection"]
    for case in detection["cases"]:
        if all(observed.get(k) == v for k, v in case["match"].items()):
            pattern = case["pattern"]
            source = "case:" + ",".join(sorted(case["match"]))
            break
    else:
        if "default" not in detection:
            raise EvaluatorError(
                f"{rid}: 观测 {observed!r} 未命中任何 case 且无 default；"
                "请补 YAML 规则，不要静默编造错误模式"
            )
        pattern, source = detection["default"], "default"
    if pattern not in declared:
        raise EvaluatorError(f"{rid}: 分类结果 {pattern} 不在本断言 error_patterns 中")
    if pattern not in allowed:
        raise EvaluatorError(f"{rid}: 分类结果 {pattern} 未登记进错误模式词表")
    return pattern, source


def evaluate_file_exists(spec: dict, artifacts: dict | None,
                         vocab: set[str] | None = None) -> dict:
    """R1：判产物是否存在且非空。产物本体只从 raw.artifacts 读，不看 trace。"""
    _validate_file_exists_spec(spec)
    rid, target = spec["id"], spec["target"]
    expected = {"exists": True, "non_empty": spec["options"]["non_empty"]}

    def unscored(observed: Any, note: str) -> dict:
        return {"id": rid, "state": "unscored", "reason_code": "artifact_invalid",
                "evidence": {"observed": observed, "expected": expected}, "note": note}

    if not isinstance(artifacts, dict):
        return unscored(None, "raw.artifacts 缺失或不是对象：平台没采集到产物，判 unscored、不归因")

    # key 不在 artifacts 里 = 确实没交产物（fail）；key 在但值不是字符串 = 产物数据不可信
    # （unscored）。这两者不能混：把采集故障记成「没交作业」会污染掌握度。
    if target not in artifacts:
        exists, content = False, None
    else:
        content = artifacts[target]
        if not isinstance(content, str):
            return unscored(
                {"target": target, "type": type(content).__name__},
                f"artifacts[{target!r}] 不是字符串：产物数据不可信，判 unscored、不归因",
            )
        exists = True

    size_bytes = len(content.encode("utf-8")) if isinstance(content, str) else 0
    non_empty = exists and size_bytes > 0
    observed = {"exists": exists, "non_empty": non_empty, "size_bytes": size_bytes}
    result = {"id": rid, "state": "pass" if non_empty else "fail",
              "evidence": {"observed": observed, "expected": expected}}
    if non_empty:
        return result

    allowed = vocab if vocab is not None else _load_vocab()
    pattern, source = _classify_file_exists(spec, observed, allowed)
    result["error_patterns"] = [pattern]
    result["evidence"]["extra"] = {"classification_source": source}
    return result


def load_r1_spec(task_path: str | Path = TASK_PATH) -> dict:
    task = load_task_config(task_path)
    r1 = next(r for r in task["rubric_gate"] if r["id"] == "R1_file_exists")
    _validate_file_exists_spec(r1)  # 与 load_r3_spec / load_r5_spec 同一纪律：加载即校验
    return r1


# --------------------------------------------------------------------------
# S3-3-4：R2 json_field_equals 执行器（报告内容正确性）
# --------------------------------------------------------------------------


def _finite_number(value: Any) -> bool:
    """容差比较的数值守卫：bool 是 int 子类，NaN / inf / 超大整数都不接受。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _json_match_keys(spec: dict) -> tuple[str, ...]:
    """合法 match 键从 expected 派生，代码里不写 valid_count 这类任务常量。"""
    return ("missing_fields", *(f"{k}_mismatch" for k in spec["expected"]))


def _validate_json_field_equals_spec(spec: dict) -> None:
    """TD-003 同款纪律：配置错在接触任何产物之前抛，不许变成 catch-all。"""
    rid = spec.get("id")
    if spec.get("type") != "json_field_equals":
        raise EvaluatorError(f"{rid}: evaluate_json_field_equals 只接受 json_field_equals")
    target = spec.get("target")
    if not isinstance(target, str) or not target:
        raise EvaluatorError(f"{rid}: json_field_equals 缺 target")
    if spec.get("compare") != "subset":
        raise EvaluatorError(f"{rid}: 只支持 compare=subset，收到 {spec.get('compare')!r}")
    expected = spec.get("expected")
    if not isinstance(expected, dict) or not expected:
        raise EvaluatorError(f"{rid}: expected 必须是非空对象")
    # 不能用 `spec.get("tolerance") or {}`：空列表 / 空串 / 0 这些坏配置会被
    # falsy 折叠成合法空 dict，静默绕过类型校验（本地回归锁抓到过一次）。
    tolerance = spec.get("tolerance")
    if tolerance is None:
        tolerance = {}
    if not isinstance(tolerance, dict):
        raise EvaluatorError(f"{rid}: tolerance 必须是对象")
    for key, value in tolerance.items():
        if key not in expected:
            raise EvaluatorError(f"{rid}: tolerance 键 {key!r} 不在 expected 中")
        if not _finite_number(value) or value <= 0:
            raise EvaluatorError(f"{rid}: tolerance[{key!r}] 必须是正有限数，收到 {value!r}")

    detection = spec.get("error_pattern_detection") or {}
    cases = detection.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluatorError(f"{rid}: 缺少 error_pattern_detection.cases，无法分类")
    declared = set(spec.get("error_patterns") or [])
    keys = _json_match_keys(spec)
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 不是对象")
        match = case.get("match")
        if not isinstance(match, dict) or not match:
            raise EvaluatorError(
                f"{rid}: 第 {i} 条 case 缺非空 match（空 match 会变成全命中 catch-all）"
            )
        for key, want in match.items():
            if key not in keys:
                raise EvaluatorError(
                    f"{rid}: 第 {i} 条 case 的 match 键 {key!r} 非法；只允许 {keys}"
                )
            if key == "missing_fields":
                if (not isinstance(want, list) or not want
                        or any(f not in expected for f in want)):
                    raise EvaluatorError(
                        f"{rid}: 第 {i} 条 case 的 missing_fields 必须是 expected 键的非空子集"
                    )
            elif want is not True:
                raise EvaluatorError(f"{rid}: 第 {i} 条 case 的 {key!r} 只接受 true")
        if case.get("pattern") not in declared:
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 的 pattern 未在本断言 error_patterns 声明")
    if "default" in detection and detection["default"] not in declared:
        raise EvaluatorError(f"{rid}: default 未在本断言 error_patterns 声明")


def _json_predicate_holds(key: str, want: Any, ctx: dict) -> bool:
    if key == "missing_fields":
        return set(want).issubset(ctx["missing_fields"])
    field = key[: -len("_mismatch")]
    # want 已被校验为 is True；用 is 而不是 ==，避免 1 == True 这类 Python 隐式等价。
    return ctx["mismatches"].get(field) is want


def _classify_json_fields(spec: dict, ctx: dict, allowed: set[str]) -> tuple[list[str], str]:
    """收集全部命中 case（不是首命中即止）：手写 fake-retry 同时有两个 pattern。"""
    rid = spec["id"]
    declared = set(spec.get("error_patterns") or [])
    detection = spec["error_pattern_detection"]
    patterns: list[str] = []
    hit_keys: list[str] = []
    for case in detection["cases"]:
        if not all(_json_predicate_holds(k, v, ctx) for k, v in case["match"].items()):
            continue
        pattern = case["pattern"]
        if pattern not in declared or pattern not in allowed:
            raise EvaluatorError(f"{rid}: 分类结果 {pattern} 未声明或未登记进词表")
        if pattern not in patterns:  # 去重保序：两条 missing_fields case 同 pattern
            patterns.append(pattern)
        hit_keys.extend(case["match"])
    if not patterns:
        if "default" not in detection:
            raise EvaluatorError(
                f"{rid}: 观测 {ctx!r} 未命中任何 case 且无 default；"
                "请补 YAML 规则，不要静默编造错误模式"
            )
        pattern = detection["default"]
        if pattern not in declared or pattern not in allowed:
            raise EvaluatorError(f"{rid}: default {pattern} 未声明或未登记进词表")
        return [pattern], "default"
    return patterns, "case:" + ",".join(sorted(set(hit_keys)))


def _field_equal(actual: Any, want: Any, tolerance: Any) -> bool:
    """容差字段走 abs 比较（禁止浮点 ==），其余字段精确比较且防 True == 1。"""
    if tolerance is not None:
        if not (_finite_number(actual) and _finite_number(want)):
            return False
        try:
            return abs(actual - want) <= tolerance
        except (OverflowError, TypeError):
            return False
    if isinstance(actual, bool) != isinstance(want, bool):
        return False
    return actual == want


def evaluate_json_field_equals(spec: dict, artifacts: dict | None,
                               vocab: set[str] | None = None,
                               dependency_results: dict | None = None) -> dict:
    """R2：判报告内容是否与 expected 一致（subset + tolerance）。"""
    _validate_json_field_equals_spec(spec)
    rid, target, expected = spec["id"], spec["target"], spec["expected"]
    tolerance = spec.get("tolerance") or {}
    deps = spec.get("depends_on") or []

    def unscored(observed: Any, note: str) -> dict:
        return {"id": rid, "state": "unscored", "reason_code": "artifact_invalid",
                "evidence": {"observed": observed, "expected": expected}, "note": note}

    # 依赖折叠：前置未通过 → blocked。调用方必须给结果，缺了就抛，不猜。
    if deps:
        if dependency_results is None:
            raise EvaluatorError(
                f"{rid}: spec 有 depends_on={deps}，调用方必须提供 dependency_results"
            )
        unsatisfied = [
            d for d in deps
            if not isinstance(dependency_results.get(d), dict)
            or dependency_results[d].get("state") != "pass"
        ]
        if unsatisfied:
            return {"id": rid, "state": "blocked", "reason_code": "dependency_blocked",
                    "evidence": {"observed": None, "expected": expected,
                                 "extra": {"blocked_by": unsatisfied,
                                           "would_be": "前置断言未通过，报告内容无从判定"}}}

    if not isinstance(artifacts, dict):
        return unscored(None, "raw.artifacts 缺失或不是对象：判 unscored、不归因")
    if target not in artifacts:
        return unscored(
            {"target": target},
            f"前置断言已通过但 artifacts 缺 {target!r}：证据矛盾，判 unscored、不归因",
        )
    content = artifacts[target]
    if not isinstance(content, str):
        return unscored(
            {"target": target, "type": type(content).__name__},
            f"artifacts[{target!r}] 不是字符串：产物数据不可信，判 unscored、不归因",
        )

    # 假设①：内容不是合法 JSON 对象时，按「expected 字段一个都没观测到」处理，
    # 归入既有 report_missing_field，不擅自扩词表（S3-3-6 请人拍板）。
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    valid_object = isinstance(parsed, dict)
    observed_obj = parsed if valid_object else {}
    parse_note = None if valid_object else (
        "报告内容不是合法 JSON 对象：按 expected 字段全部未观测到处理（假设①，S3-3-6 待拍板）"
    )

    missing = [k for k in expected if k not in observed_obj]
    mismatches: dict[str, bool] = {}
    diffs: list[dict] = []
    for key, want in expected.items():
        if key in missing:
            diffs.append({"field": key, "expected": want, "actual": None, "missing": True})
            mismatches[key] = False  # 缺字段只报 report_missing_field，不重复报数值算错
            continue
        actual = observed_obj[key]
        equal = _field_equal(actual, want, tolerance.get(key))
        mismatches[key] = not equal
        if not equal:
            item = {"field": key, "expected": want, "actual": actual}
            if key in tolerance:
                item["tolerance"] = tolerance[key]
            diffs.append(item)

    passed = not missing and not any(mismatches.values())
    extra: dict = {}
    if tolerance:
        extra["tolerance"] = tolerance
    if diffs:
        extra["diffs"] = diffs
    result: dict = {
        "id": rid,
        "state": "pass" if passed else "fail",
        "evidence": {"observed": observed_obj if valid_object else None,
                     "expected": expected},
    }
    if extra:
        result["evidence"]["extra"] = extra
    if parse_note:
        result["note"] = parse_note
    if passed:
        return result

    allowed = vocab if vocab is not None else _load_vocab()
    ctx = {"missing_fields": missing, "mismatches": mismatches}
    patterns, source = _classify_json_fields(spec, ctx, allowed)
    result["error_patterns"] = patterns
    result["evidence"].setdefault("extra", {})["classification_source"] = source
    return result


def load_r2_spec(task_path: str | Path = TASK_PATH) -> dict:
    task = load_task_config(task_path)
    r2 = next(r for r in task["rubric_credit"] if r["id"] == "R2_report_correct")
    _validate_json_field_equals_spec(r2)  # 加载即校验
    return r2


# --------------------------------------------------------------------------
# S3-3-5：R4 trace_param_schema_valid 执行器（工具参数 schema 四项校验）
# --------------------------------------------------------------------------

# YAML cases 的四个 match 键 -> 违规种类。值是 schema_ref（如 "tools"）而非 true：
# 这四条规则的含义是「对照 tools 声明检查」，与 R3 的计数谓词不是一套语义。
_PARAM_KIND_BY_KEY = {
    "extra_keys_against": "extra_keys",
    "missing_required_against": "missing_required",
    "type_mismatch_against": "type_mismatch",
    "out_of_range_against": "out_of_range",
}
_SUPPORTED_PARAM_TYPES = ("integer", "string")


def _validate_tool_schemas(schemas: dict, rid: str) -> None:
    """tools 表结构校验：配置错在接触任何 trace 之前抛，不许静默降级。"""
    if not isinstance(schemas, dict) or not schemas:
        raise EvaluatorError(f"{rid}: tools schema 缺失或不是非空对象")
    for name, tool in schemas.items():
        if not isinstance(name, str) or not name or not isinstance(tool, dict):
            raise EvaluatorError(f"{rid}: tools[{name!r}] 结构非法")
        params = tool.get("parameters")
        if not isinstance(params, list) or not params:
            raise EvaluatorError(f"{rid}: tools[{name!r}].parameters 必须是非空数组")
        ap = tool.get("additional_properties")
        if not isinstance(ap, bool):
            raise EvaluatorError(
                f"{rid}: tools[{name!r}].additional_properties 必须是 bool，收到 {ap!r}"
            )
        seen: set[str] = set()
        for p in params:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]:
                raise EvaluatorError(f"{rid}: tools[{name!r}] 有参数缺合法 name")
            pname = p["name"]
            if pname in seen:
                raise EvaluatorError(f"{rid}: tools[{name!r}] 参数名 {pname!r} 重复")
            seen.add(pname)
            if p.get("type") not in _SUPPORTED_PARAM_TYPES:
                raise EvaluatorError(
                    f"{rid}: tools[{name!r}].{pname}.type={p.get('type')!r} 不支持；"
                    f"只实现 {_SUPPORTED_PARAM_TYPES}"
                )
            if not isinstance(p.get("required"), bool):
                raise EvaluatorError(
                    f"{rid}: tools[{name!r}].{pname}.required 必须显式写 bool"
                )
            for bound in ("minimum", "maximum"):
                if bound in p and not _finite_number(p[bound]):
                    raise EvaluatorError(
                        f"{rid}: tools[{name!r}].{pname}.{bound} 必须是有限数"
                    )
            if ("minimum" in p and "maximum" in p
                    and p["minimum"] > p["maximum"]):
                raise EvaluatorError(
                    f"{rid}: tools[{name!r}].{pname} 的 minimum 大于 maximum"
                )


def load_tool_schemas(ref: str, task_path: str | Path = TASK_PATH) -> dict:
    """从任务 YAML 的 `task[ref]` 读工具声明，返回 {工具名: 声明}。

    `ref` 来自 R4 的 `expected.schema_ref`，不是代码里写死的 "tools"。
    """
    task = load_task_config(task_path)
    tools = task.get(ref)
    if not isinstance(tools, list) or not tools:
        raise EvaluatorError(f"任务 YAML 缺 {ref!r} 数组或为空")
    schemas: dict = {}
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            raise EvaluatorError(f"任务 YAML 的 {ref!r} 里有条目缺合法 name")
        if tool["name"] in schemas:
            raise EvaluatorError(f"任务 YAML 的 {ref!r} 里工具名 {tool['name']!r} 重复")
        schemas[tool["name"]] = tool
    _validate_tool_schemas(schemas, ref)
    return schemas


def _validate_param_schema_spec(spec: dict) -> None:
    """TD-003 同款纪律：配置错在接触任何 trace 之前抛，不许变成 catch-all。"""
    rid = spec.get("id")
    if spec.get("type") != "trace_param_schema_valid":
        raise EvaluatorError(f"{rid}: evaluate_param_schema 只接受 trace_param_schema_valid")
    target = spec.get("target")
    if not isinstance(target, str) or not target:
        raise EvaluatorError(f"{rid}: trace_param_schema_valid 缺 target")
    expected = spec.get("expected")
    if not isinstance(expected, dict):
        raise EvaluatorError(f"{rid}: expected 必须是对象")
    schema_ref = expected.get("schema_ref")
    if not isinstance(schema_ref, str) or not schema_ref:
        raise EvaluatorError(f"{rid}: expected.schema_ref 缺失")
    if expected.get("check_schema") is not True:
        raise EvaluatorError(
            f"{rid}: 只支持 check_schema=true，收到 {expected.get('check_schema')!r}"
        )
    if expected.get("mode") != "all_calls_must_be_valid":
        raise EvaluatorError(
            f"{rid}: 只支持 mode=all_calls_must_be_valid，收到 {expected.get('mode')!r}"
        )
    detection = spec.get("error_pattern_detection") or {}
    probe = detection.get("probe")
    if not isinstance(probe, str) or not probe:
        raise EvaluatorError(f"{rid}: 缺 error_pattern_detection.probe")
    cases = detection.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluatorError(f"{rid}: 缺少 error_pattern_detection.cases，无法分类")
    declared = set(spec.get("error_patterns") or [])
    covered: set[str] = set()
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 不是对象")
        match = case.get("match")
        if not isinstance(match, dict) or not match:
            raise EvaluatorError(
                f"{rid}: 第 {i} 条 case 缺非空 match（空 match 会变成全命中 catch-all）"
            )
        for key, want in match.items():
            if key not in _PARAM_KIND_BY_KEY:
                raise EvaluatorError(
                    f"{rid}: 第 {i} 条 case 的 match 键 {key!r} 非法；"
                    f"只允许 {tuple(_PARAM_KIND_BY_KEY)}"
                )
            if not isinstance(want, str) or want != schema_ref:
                raise EvaluatorError(
                    f"{rid}: 第 {i} 条 case 的 {key!r} 必须等于 schema_ref={schema_ref!r}，"
                    f"收到 {want!r}"
                )
            covered.add(_PARAM_KIND_BY_KEY[key])
        if case.get("pattern") not in declared:
            raise EvaluatorError(f"{rid}: 第 {i} 条 case 的 pattern 未在本断言 error_patterns 声明")
    missing = set(_PARAM_KIND_BY_KEY.values()) - covered
    if missing:
        raise EvaluatorError(
            f"{rid}: 校验项 {sorted(missing)} 没有对应分类 case，fail 时会编不出 pattern"
        )
    if "default" in detection and detection["default"] not in declared:
        raise EvaluatorError(f"{rid}: default 未在本断言 error_patterns 声明")


def _param_type_ok(value: Any, declared_type: str) -> bool:
    """bool 是 int 子类，必须显式排除，否则 True 会被当成合法 integer。"""
    if declared_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "string":
        return isinstance(value, str)
    raise EvaluatorError(f"未支持的参数类型 {declared_type!r}；只实现 {_SUPPORTED_PARAM_TYPES}")


def _check_call_params(params: dict, tool: dict) -> list[dict]:
    """单次调用的四项校验，只产出违规明细，不判 state、不分类。"""
    declared = {p["name"]: p for p in tool["parameters"]}
    violations: list[dict] = []

    # 4) additional_properties：schema 外的参数名一律违规（仅 false 时检查）
    if tool["additional_properties"] is False:
        for key in sorted(set(params) - set(declared)):
            violations.append({"kind": "extra_keys", "param": key,
                               "detail": "tools 未声明的参数名"})

    # 1) required：必填参数缺失
    for name, p in declared.items():
        if p["required"] is True and name not in params:
            violations.append({"kind": "missing_required", "param": name,
                               "detail": "required=true 但未出现在调用中"})

    for name, p in declared.items():
        if name not in params:
            continue
        value = params[name]
        # 2) type：类型不符就不再做 range（Python 3 里 str 与 int 比较会抛 TypeError）
        if not _param_type_ok(value, p["type"]):
            violations.append({"kind": "type_mismatch", "param": name,
                               "detail": f"期望 {p['type']}，实得 {type(value).__name__}"})
            continue
        # 3) range：只对数值取值比较（string 参数没有 minimum/maximum 语义）
        if _finite_number(value):
            if "minimum" in p and value < p["minimum"]:
                violations.append({"kind": "out_of_range", "param": name,
                                   "detail": f"{value!r} < minimum {p['minimum']!r}"})
            if "maximum" in p and value > p["maximum"]:
                violations.append({"kind": "out_of_range", "param": name,
                                   "detail": f"{value!r} > maximum {p['maximum']!r}"})
    return violations


def _classify_param_schema(spec: dict, present: set[str],
                           allowed: set[str]) -> tuple[str, str]:
    """顺序匹配 cases，首命中即止；违规种类没被任何 case 覆盖就抛，不编 pattern。"""
    rid = spec["id"]
    detection = spec["error_pattern_detection"]
    declared = set(spec.get("error_patterns") or [])
    for case in detection["cases"]:
        if not all(_PARAM_KIND_BY_KEY[k] in present for k in case["match"]):
            continue
        pattern = case["pattern"]
        source = "case:" + ",".join(sorted(case["match"]))
        break
    else:
        if "default" not in detection:
            raise EvaluatorError(
                f"{rid}: 违规种类 {sorted(present)} 未命中任何 case 且无 default；"
                "请补 YAML 规则，不要静默编造错误模式"
            )
        pattern, source = detection["default"], "default"
    if pattern not in declared:
        raise EvaluatorError(f"{rid}: 分类结果 {pattern} 不在本断言 error_patterns 中")
    if pattern not in allowed:
        raise EvaluatorError(f"{rid}: 分类结果 {pattern} 未登记进错误模式词表")
    return pattern, source


def evaluate_param_schema(spec: dict, trace: dict | None,
                          vocab: set[str] | None = None,
                          requirements: dict | None = None,
                          schemas: dict | None = None) -> dict:
    """R4：逐次调用校验参数 schema，任一违规即 fail（all_calls_must_be_valid）。"""
    _validate_param_schema_spec(spec)
    rid, target = spec["id"], spec["target"]
    mode = spec["expected"]["mode"]
    schema_ref = spec["expected"]["schema_ref"]
    base = {"expected": {"mode": mode}}

    def unscored(code: str, observed: Any, note: str) -> dict:
        return {"id": rid, "state": "unscored", "reason_code": code,
                "evidence": {"observed": observed, **base}, "note": note}

    if schemas is None:
        schemas = load_tool_schemas(schema_ref)
    _validate_tool_schemas(schemas, rid)

    if trace is None:
        return unscored("trace_not_reported", None,
                        "trace 未上报：R4 判 unscored，不判 0；不归因、不产出错误模式")
    calls = trace.get(target) if isinstance(trace, dict) else None
    if not isinstance(calls, list):
        return unscored("trace_invalid", None,
                        f"trace.{target} 缺失或不是数组：判 unscored、不归因")

    # 与 R3/R5 同一条可信度前置：畸形调用被投影静默丢弃会让「坏数据」变成 pass。
    problem = _validate_calls(calls, _status_enum(requirements, rid))
    if problem:
        return unscored("trace_invalid", {"call_sequence_problem": problem},
                        "调用序列不可信：判 unscored、不归因；不按 call_index 重排后判 fail")

    probe = spec["error_pattern_detection"]["probe"]
    projected = jmespath.search(probe, {"calls": calls})
    if not isinstance(projected, list) or len(projected) != len(calls):
        return unscored("trace_invalid", {"probe": probe,
                                          "projected_count": len(projected)
                                          if isinstance(projected, list) else None},
                        "probe 投影丢失调用：轨迹数据不可信，判 unscored、不归因")

    violations: list[dict] = []
    unknown_tools: list[dict] = []
    for i, item in enumerate(projected, 1):
        name = item.get("name") if isinstance(item, dict) else None
        # 假设①：未声明的工具名无法对照 schema，按数据不可信处理，不扩词表语义。
        if not isinstance(name, str) or name not in schemas:
            unknown_tools.append({
                "call_index": i,
                "name": name if isinstance(name, str) else type(name).__name__,
            })
            continue
        params = item.get("params")
        if not isinstance(params, dict):
            return unscored("trace_invalid", {"call_index": i, "tool": name},
                            f"第 {i} 条调用投影出的 params 不是对象：判 unscored、不归因")
        for v in _check_call_params(params, schemas[name]):
            violations.append({"call_index": i, "tool": name, **v})

    if unknown_tools:
        return unscored("trace_invalid", {"unknown_tools": unknown_tools},
                        "调用含 tools 未声明的工具名：无法对照 schema，判 unscored、不归因"
                        "（假设①，S3-3-6 待拍板）")

    passed = not violations
    result: dict = {
        "id": rid,
        "state": "pass" if passed else "fail",
        "evidence": {
            "observed": {"violations": violations, "checked_calls": len(calls)},
            **base,
            "extra": {"schema_ref": schema_ref, "probe": probe},
        },
    }
    if passed:
        return result

    allowed = vocab if vocab is not None else _load_vocab()
    pattern, source = _classify_param_schema(spec, {v["kind"] for v in violations}, allowed)
    result["error_patterns"] = [pattern]
    result["evidence"]["extra"]["classification_source"] = source
    return result


def load_r4_spec(task_path: str | Path = TASK_PATH) -> dict:
    task = load_task_config(task_path)
    r4 = next(r for r in task["rubric_credit"] if r["id"] == "R4_no_param_hallucination")
    _validate_param_schema_spec(r4)  # 加载即校验
    load_tool_schemas(r4["expected"]["schema_ref"], task_path)  # 顺带校验 schema_ref 指向的表可读
    return r4


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def evaluate(spec: dict, trace: dict | None, vocab: set[str] | None = None,
             requirements: dict | None = None) -> dict:
    """算一条断言，输出可直接放进 raw.assertion_results 的字典。

    `trace=None` 表示未上报；结构不合法表示上报了但不可用。两者都判 unscored，
    分数贡献与 fail 相同，但**不计入掌握度、不产出错误模式**（任务 YAML 的核心纪律）。
    `requirements` 可注入 `trace_requirements.minimum`（默认从任务 YAML 读），
    供回归锁验证「约束来自 YAML 而不是代码常量」。
    """
    if spec.get("type") != "trace_sequence":
        raise NotImplementedError(
            f"S3-1 的 evaluate() 只实现 trace_sequence，收到 {spec.get('type')!r}（{spec.get('id')}）。"
            "请用 evaluate_assertion() 分发，不要伪造结果。"
        )

    # TD-003：配置错在接触任何 trace 之前失败，不许被 unscored 分支吞掉。
    _validate_classification_spec(spec)

    base = {"expected": spec["expected"]}

    if trace is None:
        return {
            "id": spec["id"], "state": "unscored", "reason_code": "trace_not_reported",
            "evidence": {"observed": None, **base},
            "note": "trace 未上报：判 unscored，不判 0；不归因、不产出错误模式",
        }
    calls = trace.get("tool_calls") if isinstance(trace, dict) else None
    if not isinstance(calls, list):
        return {
            "id": spec["id"], "state": "unscored", "reason_code": "trace_invalid",
            "evidence": {"observed": None, **base},
            "note": "trace.tool_calls 缺失或不是数组：判 unscored，不判 0",
        }

    problem = _validate_calls(calls, _status_enum(requirements, spec["id"]))
    if problem:
        return {
            "id": spec["id"], "state": "unscored", "reason_code": "trace_invalid",
            "evidence": {"observed": {"call_sequence_problem": problem}, **base},
            "note": "调用序列不可信：判 unscored、不归因；不按 call_index 重排后判 fail",
        }

    expr = _filter_expr(spec)
    passed, projected, matched = _judge(spec, calls, expr)

    detection = spec.get("error_pattern_detection") or {}
    probe = detection.get("probe")
    if not probe:
        raise EvaluatorError(f"{spec['id']}: 缺少 error_pattern_detection.probe")
    offsets = jmespath.search(probe, {"calls": calls}) or []
    statuses = [p.get("s") for p in projected]
    observed = {
        "offsets": offsets,
        "statuses": statuses,
        "http_statuses": [p.get("h") for p in projected],
    }
    result: dict = {
        "id": spec["id"],
        "state": "pass" if passed else "fail",
        "evidence": {
            "observed": observed,
            "expected": spec["expected"],
            "extra": {
                "filter_expr": expr,
                "probe": probe,
                "matched_steps": matched,
                "total_steps": len(spec["expected"]["calls"]),
            },
        },
    }
    if passed:
        return result

    # projected 由 _filter_expr(spec) 过滤得到，条件是 YAML 的 name + params_match。
    # 因此它的长度就是「故障点被触达的次数」，无需在代码里写 offset=10。
    ctx = {
        "offsets": offsets,
        "last_status": statuses[-1] if statuses else None,
        "call_count": len(offsets),
        "fault_call_count": len(projected),
    }
    allowed = vocab if vocab is not None else _load_vocab()
    pattern, source = _classify(spec, ctx, allowed)
    result["error_patterns"] = [pattern]
    result["evidence"]["extra"]["classification_source"] = source
    return result


def evaluate_assertion(spec: dict, trace: dict | None, vocab: set[str] | None = None,
                       requirements: dict | None = None,
                       artifacts: dict | None = None,
                       dependency_results: dict | None = None,
                       schemas: dict | None = None) -> dict:
    """唯一分发入口：按 spec["type"] 选执行器。后续 S3 步骤在这里注册新类型。

    `artifacts`（S3-3-3 产物本体）、`dependency_results`（S3-3-4 前置断言结果）、
    `schemas`（S3-3-5 工具声明表）都是末尾可选参数，既有 R3 调用方
    （`trace_pipeline.recompute_r3`）不用改；只在对应分支被读。
    """
    t = spec.get("type")
    if t == "trace_sequence":
        return evaluate(spec, trace, vocab, requirements)
    if t == "trace_final_status":
        return evaluate_final_status(spec, trace, vocab, requirements)
    if t == "file_exists":
        return evaluate_file_exists(spec, artifacts, vocab)
    if t == "json_field_equals":
        return evaluate_json_field_equals(spec, artifacts, vocab, dependency_results)
    if t == "trace_param_schema_valid":
        return evaluate_param_schema(spec, trace, vocab, requirements, schemas)
    raise NotImplementedError(
        f"目前只实现 {SUPPORTED_TYPES}，收到 {t!r}（{spec.get('id')}）。"
        "请等后续步骤实现，不要伪造结果。"
    )


def _load_vocab() -> set[str]:
    import yaml

    data = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))
    return {p["pattern_id"] for p in data["patterns"]}


def load_r3_spec(task_path: str | Path = TASK_PATH) -> dict:
    task = load_task_config(task_path)
    r3 = next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")
    _validate_classification_spec(r3)  # D2=A：加载即校验，不必等跑 trace
    return r3


# --------------------------------------------------------------------------
# --demo：trace 的现场对照表，供人工十分钟核验
# --------------------------------------------------------------------------

_DEMO_CALLS = {
    "真重试": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "http_status": 200},
    ],
    "真重试+穿插": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
        {"name": "write_report", "params": {"path": "/workspace/report.json"}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "http_status": 200},
    ],
    "假重试": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
    ],
    "无重试": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
    ],
    "重试耗尽": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500},
    ],
    # Q10-A：过去这两行只能靠 default 兜底，现在应命中明写规则。
    "零调用": [],
    "只拉首页": [
        {"name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "http_status": 200},
    ],
}


def _numbered(calls: list[dict]) -> list[dict]:
    """给现场对照数据补平台侧编号（模拟合规上报）。"""
    return [{**c, "call_index": i + 1} for i, c in enumerate(calls)]


def demo() -> int:
    spec = load_r3_spec()
    print(f"derived filter: {_filter_expr(spec)}")
    print(f"probe          : {spec['error_pattern_detection']['probe']}\n")
    print(f"{'trace':<14}{'state':<9}{'pattern':<24}{'source':<40}offsets")
    for name, calls in _DEMO_CALLS.items():
        r = evaluate(spec, {"tool_calls": _numbered(calls)})
        pattern = (r.get("error_patterns") or ["-"])[0]
        extra = r["evidence"].get("extra") or {}
        offsets = r["evidence"]["observed"]["offsets"]
        print(f"{name:<14}{r['state']:<9}{pattern:<24}{extra.get('classification_source', '-'):<40}"
              f"{offsets}")
    # round-8 ① 的反例：同一组调用，数组顺序与 call_index 相反。
    shuffled = [
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
         "status": "error", "http_status": 500},
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
         "status": "success", "http_status": 200},
    ]
    r = evaluate(spec, {"tool_calls": shuffled})
    print(f"{'乱序上报':<14}{r['state']:<9}{'-':<24}{r['reason_code']:<40}-")
    r = evaluate(spec, None)
    print(f"{'未上报 trace':<14}{r['state']:<9}{'-':<24}{r['reason_code']:<40}-")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S3-1: R3 断言执行器")
    ap.add_argument("--demo", action="store_true", help="打印 trace 的判定/分类对照表")
    args = ap.parse_args(argv)
    try:
        if args.demo:
            return demo()
        ap.print_help()
        return 0
    except (EvaluatorError, NotImplementedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

===== END: src/arena/assertion_evaluator.py =====

文件：`src/arena/trace_pipeline.py` ｜ sha256[:16]=`e12e5bd8f1c012a3` ｜ 143 行 ｜ LF
===== BEGIN: src/arena/trace_pipeline.py =====
"""S3-2（Q11）：把原始 trace 接进诊断报告，消除「手工伪造断言结果」这一段。

此前 `fixtures/raw-*.json` 的 `assertion_results` 全是手写评测输出，
`diagnosis_report.build_report()` 只负责组装。本模块补上中间那一步：

    raw.trace.tool_calls
      -> assertion_evaluator.evaluate()   # 现算 R3
      -> 回填 raw.assertion_results
      -> diagnosis_report.build_report() # 唯一的组装出口
      -> validate_report()

## 边界（刻意做小，别把它当成完整评测器）

- **只现算 R3**。R1/R2/R4/R5 仍读夹具里手写的结果，因为它们的执行器
  （文件存在 / JSON 比对 / 参数 schema / 退出状态）尚未实现。S3-2 打通的是
  「一条断言的全链路」，不是「全部断言」，见 docs/review-response-v1.md §10。
- **不改写夹具**：本模块返回深拷贝。手写结果作为 S2 的历史基线保留，
  `tests/test_diagnosis_report.py` 的分数序断言不受影响。
- **不改 build_report 的语义**：trace 强制改写、依赖折叠、两段式计分仍归它管。

## 真相源一致性

`raw.trace` 与 `raw.trace_audit.reported` 是两份描述同一件事的数据。
两者冲突时**拒绝组装**，不猜哪个对——否则又回到「读不到 trace 却给满分」的坑。

用法：
    python src/arena/trace_pipeline.py --fixture all
    python src/arena/trace_pipeline.py --fixture fixtures/raw-perfect.json
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

try:  # 作为包被 import
    from . import assertion_evaluator as ae
    from . import diagnosis_report as dr
except ImportError:  # 作为独立脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assertion_evaluator as ae  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
R3_ID = "R3_retry_evidence"
FIXTURE_NAMES = ("raw-empty", "raw-no-file", "raw-fake-retry", "raw-no-trace", "raw-perfect")
_MISSING = object()


class PipelineError(ValueError):
    """输入不自洽：宁可停下，也不产出一份看起来像报告的坏数据。"""


def recompute_r3(raw: dict, spec: dict | None = None, vocab: set[str] | None = None) -> dict:
    """返回 raw 的深拷贝，其中 R3 断言结果由 `raw.trace.tool_calls` 现算。"""
    if spec is None:
        spec = ae.load_r3_spec()

    out = copy.deepcopy(raw)
    audit = out.get("trace_audit")
    if not isinstance(audit, dict):
        raise PipelineError("raw 缺 trace_audit：无法判断 trace 是否上报")

    trace = out.get("trace", _MISSING)
    if trace is _MISSING:
        raise PipelineError("raw 缺 trace 字段：无法区分「未上报」与「字段漏写」")
    if trace is not None and not isinstance(trace, dict):
        raise PipelineError(f"raw.trace 类型非法：{type(trace).__name__}")
    if bool(audit.get("reported")) != (trace is not None):
        raise PipelineError(
            f"trace 与 trace_audit.reported 冲突（trace={'有' if trace is not None else '无'}, "
            f"reported={audit.get('reported')!r}）：两个真相源打架，拒绝组装"
        )

    results = out.get("assertion_results")
    if not isinstance(results, list):
        raise PipelineError("raw.assertion_results 不是数组")
    hits = [i for i, item in enumerate(results) if isinstance(item, dict) and item.get("id") == R3_ID]
    if len(hits) != 1:
        raise PipelineError(f"R3 断言结果应恰好 1 条，实际 {len(hits)} 条")

    # S3-3-2：改走 evaluate_assertion() 分发入口（R3 行为不变）。
    # S3-3-6 切「五条断言全现算」时，这一行不必再改。
    results[hits[0]] = ae.evaluate_assertion(spec, trace, vocab)
    return out


def run_fixture(name_or_path: str | Path, task: dict | None = None,
                vocab: set[str] | None = None) -> dict:
    """读夹具 -> 现算 R3 -> 组装报告 -> 过 JSON Schema，返回报告。"""
    path = Path(name_or_path)
    if not path.is_file():
        path = FIXTURES / f"{name_or_path}.json"
    if not path.is_file():
        raise PipelineError(f"找不到夹具：{name_or_path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    built = recompute_r3(raw, vocab=vocab)
    if task is None:
        task = dr.load_task(TASK_PATH)
    report = dr.build_report(task, built, vocab)
    dr.validate_report(report)
    return report


def load_cases(path: str | Path | None = None) -> list[dict]:
    """读边界 trace 用例集，供回归测试消费。"""
    p = Path(path) if path else FIXTURES / "tool-call-cases.json"
    return json.loads(p.read_text(encoding="utf-8"))["cases"]


def _r3(report: dict) -> dict:
    return next(a for a in report["assertions"] if a["id"] == R3_ID)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S3-2: 原始 trace -> R3 执行器 -> 诊断报告")
    ap.add_argument("--fixture", required=True,
                    help="raw JSON 路径或夹具名；写 all 表示跑 fixtures/ 下五份 raw-*.json")
    args = ap.parse_args(argv)
    try:
        if args.fixture == "all":
            for name in FIXTURE_NAMES:
                rep = run_fixture(name)
                r3 = _r3(rep)
                print(f"OK {name:<16} ratio={rep['score']['ratio']:<6} "
                      f"R3={r3['state']:<9} patterns={r3['error_patterns']}")
            return 0
        print(json.dumps(run_fixture(args.fixture), ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, dr.ReportError, ae.EvaluatorError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

===== END: src/arena/trace_pipeline.py =====

文件：`src/arena/diagnosis_report.py` ｜ sha256[:16]=`8bb92e1188d8eb56` ｜ 342 行 ｜ CRLF
===== BEGIN: src/arena/diagnosis_report.py =====
"""诊断报告 DTO 的组装器（评测层出口，见 docs/diagnosis-report-schema.md）。

设计原则（四条，都是踩过的坑换来的）：
1. **只装可判定的观测，不装推断**：复习建议、掌握度都在状态层产生，进了报告就是两套口径。
2. **契约自解释**：报告自带 attribution_policy 与 denominator_policy，状态层不必回读 YAML 才知道怎么算。
3. **宁可报错也不静默降级**：A2E 的教训是「读不到 trace 就静默给满分」，所以 trace 不可用时强制
   把轨迹断言改写成 unscored，reasons 写进 trace_audit；任何不自洽直接抛 ReportError，不产出报告。
4. **门禁只管分数，不管观测**：gate 失败时 credit 断言照常判定并记录（诊断信息不因 0 分而丢弃），
   只是不计入分数。反过来，若 gate 失败就把 credit 全标 blocked，等于用评分逻辑吞掉诊断数据。

两段式计分（P6 拍板）：
    rubric_gate    R1 交产物 / R5 正常退出    —— 不计分，任一门禁 fail ⇒ disqualified，ratio=0
    rubric_credit  R2 / R3 / R4              —— 分母恒为 credit 权重之和（本任务 4）

调用关系：执行器（S3）逐条跑断言拿到 assertion_results -> build_report() 组装 -> validate_report() 过 schema。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

STATES = ("pass", "fail", "blocked", "unscored")
GATE, CREDIT = "gate", "credit"
TRACE_TYPE_PREFIX = "trace_"
REPORT_VERSION = "v2"
DENOMINATOR_POLICY = "fixed_at_credit_total_weight"

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "diagnosis-report.schema.json"
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"


class ReportError(ValueError):
    """报告不自洽——必须停下来修，不允许产出一份"看起来像报告"的坏数据。"""


def load_task(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_vocabulary(path: str | Path = VOCAB_PATH) -> set[str]:
    return {p["pattern_id"] for p in yaml.safe_load(Path(path).read_text(encoding="utf-8"))["patterns"]}


def load_schema(path: str | Path = SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_report(report: dict, schema: dict | None = None) -> None:
    """过 JSON Schema。schema 只管结构；语义不自洽由 build_report 内的断言负责。"""
    schema = schema or load_schema()
    try:
        jsonschema.Draft202012Validator(schema).validate(report)
    except jsonschema.ValidationError as exc:
        raise ReportError(f"报告不符合 schema：{exc.message}（路径 {'/'.join(map(str, exc.path))}）") from exc


# ---------------------------------------------------------------------------
# rubric 分层
# ---------------------------------------------------------------------------


def rubric_layers(task: dict) -> tuple[list[dict], list[dict]]:
    """取出 (gate, credit) 两个断言数组，并校验分层本身成立。

    分层校验放在这里而不是测试里：任何调用方（执行器、脚本、未来的 API）都过这道门，
    不会出现"测试过了但线上用了旧结构"。
    """
    if "rubric" in task:
        raise ReportError("任务仍用单数组 rubric：已改为 rubric_gate + rubric_credit（见 review-response-v1.md §6）")

    gate = list(task.get("rubric_gate") or [])
    credit = list(task.get("rubric_credit") or [])
    if not gate:
        raise ReportError("rubric_gate 为空：没有门禁的提交无从判断任务是否完成")
    if not credit:
        raise ReportError("rubric_credit 为空：没有积分断言的分数恒为 0，评分无意义")

    gate_ids = [r["id"] for r in gate]
    credit_ids = [r["id"] for r in credit]
    dup = set(gate_ids) & set(credit_ids)
    if dup:
        raise ReportError(f"断言同时出现在 gate 与 credit：{sorted(dup)}（一条断言只能属于一层）")
    for r in gate:
        if r.get("weight", 0) != 0:
            raise ReportError(f"{r['id']} 是门禁，不允许带权重 {r.get('weight')!r}——门禁不计分")
        if r.get("on_fail") != "disqualify":
            raise ReportError(f"{r['id']} 是门禁但 on_fail 不是 disqualify：门禁失败必须给出明确后果")
    for r in credit:
        if not isinstance(r.get("weight"), (int, float)) or r["weight"] <= 0:
            raise ReportError(f"{r['id']} 在 credit 层，权重必须为正数（当前 {r.get('weight')!r}）")
    return gate, credit


def _check_result_consistency(rid: str, spec: dict, result: dict, vocab: set[str]) -> None:
    """单条断言的语义校验：状态与错误模式必须自洽，模式必须在 YAML 声明 + 词表登记范围内。"""
    state = result["state"]
    if state not in STATES:
        raise ReportError(f"{rid}: 非法状态 {state!r}")

    patterns = list(result.get("error_patterns") or [])
    declared = set(spec.get("error_patterns") or [])

    if state == "fail":
        if not patterns:
            raise ReportError(f"{rid}: state=fail 但没有给出错误模式（诊断就断在这里）")
        unknown = set(patterns) - declared
        if unknown:
            raise ReportError(f"{rid}: 错误模式 {sorted(unknown)} 未在本断言 error_patterns 中声明")
        unregistered = set(patterns) - vocab
        if unregistered:
            raise ReportError(f"{rid}: 错误模式 {sorted(unregistered)} 未登记进 error-patterns 词表")
    elif patterns:
        raise ReportError(f"{rid}: state={state} 不允许产出错误模式（只有 fail 才有证据）")

    if state in ("blocked", "unscored") and not result.get("reason_code"):
        raise ReportError(f"{rid}: state={state} 必须给 reason_code，否则下游只能猜")


def _assertion_item(
    rid: str,
    spec: dict,
    layer: str,
    result: dict,
    trace_ok: bool,
    trace_audit: dict,
    results: dict,
    vocab: set[str],
) -> dict:
    """单条断言 -> 报告条目。trace 强制改写与依赖折叠都发生在这里。"""
    state = result["state"]
    error_patterns = list(result.get("error_patterns") or [])
    reason_code = result.get("reason_code")

    # 强制改写：trace 不可用时，轨迹类断言一律 unscored——不管调用方填了什么
    if not trace_ok and str(spec["type"]).startswith(TRACE_TYPE_PREFIX):
        state = "unscored"
        error_patterns = []
        reason_code = "trace_not_reported" if not trace_audit["reported"] else "trace_invalid"

    # 依赖折叠：前置断言未通过时，本断言只能是 blocked 或 pass
    if state == "blocked":
        deps = spec.get("depends_on") or []
        unsatisfied = [d for d in deps if results[d]["state"] != "pass"]
        if not unsatisfied:
            raise ReportError(f"{rid}: 标了 blocked，但前置 {deps} 全部通过——blocked 无依据")

    item = {
        "id": rid,
        "layer": layer,
        "type": spec["type"],
        "weight": spec.get("weight", 0) if layer == CREDIT else 0,
        "state": state,
        "skill_id": spec.get("skill_id"),
        "error_patterns": error_patterns,
        "evidence": result.get("evidence") or {"observed": None, "expected": None},
    }
    if reason_code:
        item["reason_code"] = reason_code
    if result.get("note"):
        item["note"] = result["note"]
    _check_result_consistency(rid, spec, item, vocab)
    return item


def build_report(task: dict, raw: dict, vocab: set[str] | None = None) -> dict:
    """把「逐条断言结果 + trace 校验结果」组装成符合 schema 的诊断报告。

    raw 结构见 docs/diagnosis-report-schema.md 第 4 节（fixtures/ 下有真实例子）。
    """
    vocab = vocab if vocab is not None else load_vocabulary()
    gate_specs, credit_specs = rubric_layers(task)
    specs = [("gate", s) for s in gate_specs] + [("credit", s) for s in credit_specs]
    by_id_spec = {s["id"]: s for _, s in specs}

    raw_results = raw.get("assertion_results") or []
    results: dict[str, dict] = {}
    for item in raw_results:
        rid = item.get("id")
        if rid in results:
            raise ReportError(f"断言结果重复：{rid}")
        results[rid] = item

    missing, extra = set(by_id_spec) - set(results), set(results) - set(by_id_spec)
    if missing or extra:
        raise ReportError(f"断言结果与 rubric 不一致：缺 {sorted(missing)}，多 {sorted(extra)}")

    trace_audit = dict(raw["trace_audit"])
    trace_ok = bool(trace_audit["reported"]) and bool(trace_audit["schema_valid"])
    notes: list[str] = []

    assertions = [
        _assertion_item(spec["id"], spec, layer, results[spec["id"]], trace_ok, trace_audit, results, vocab)
        for layer, spec in specs
    ]

    # ── 第一段：门禁 ──────────────────────────────────────────────────────
    gate_assertions = [a for a in assertions if a["layer"] == GATE]
    disqualifying = [a["id"] for a in gate_assertions if a["state"] == "fail"]
    gate_unscored = [a["id"] for a in gate_assertions if a["state"] == "unscored"]
    disqualified = bool(disqualifying)
    if gate_unscored:
        notes.append(
            "门禁 " + ", ".join(gate_unscored) + " 无法判定（数据缺失）：按 on_unscored=do_not_disqualify 不拦，"
            "但它同时意味着对应的 credit 断言也不可判，分数不会因此变高"
        )

    # ── 第二段：积分 ──────────────────────────────────────────────────────
    credit_total = sum(s["weight"] for layer, s in specs if layer == CREDIT)
    declared_total = ((task.get("scoring") or {}).get("score") or {}).get("credit", {}).get("total_weight")
    if declared_total is not None and declared_total != credit_total:
        raise ReportError(f"YAML 声明的 credit.total_weight={declared_total}，但 credit 断言权重之和={credit_total}")

    credit_pass_weight = sum(a["weight"] for a in assertions if a["layer"] == CREDIT and a["state"] == "pass")
    weighted_sum = 0 if disqualified else credit_pass_weight
    ratio = 0.0 if disqualified else round(credit_pass_weight / credit_total, 6)
    by_state = {s: sum(1 for a in assertions if a["state"] == s) for s in STATES}
    if disqualified:
        notes.append(
            "门禁未通过（" + ", ".join(disqualifying) + "）：本次提交无资格评分，ratio 记 0。"
            f"credit 层断言仍照常判定（若算分可得 {credit_pass_weight}/{credit_total}），该值仅用于诊断，不计入分数"
        )

    attr_src = (task.get("scoring") or {}).get("attribution") or {}
    attribution_policy = {
        s: {
            "counts_toward_mastery": bool(attr_src.get(s, {}).get("counts_toward_mastery")),
            "emit_error_patterns": bool(attr_src.get(s, {}).get("emit_error_patterns")),
        }
        for s in STATES
    }

    # 技能缺口只做聚合：失败的断言按 YAML 已声明的 skill_id 归类，weight_lost 是权重之和
    gaps: dict[str, dict] = {}
    for a in assertions:
        if a["state"] != "fail" or not a["skill_id"]:
            continue
        gap = gaps.setdefault(
            a["skill_id"],
            {"skill_id": a["skill_id"], "failed_assertions": [], "patterns": [], "weight_lost": 0},
        )
        gap["failed_assertions"].append(a["id"])
        gap["weight_lost"] += a["weight"]
        for code in a["error_patterns"]:
            if code not in gap["patterns"]:
                gap["patterns"].append(code)

    # 有错误模式却没有技能归属的失败（R1/R5 属此类）——不进 skill_gaps，但绝不静默丢弃。
    # P5 的教训：无文件时 skill_gaps 曾是空数组，"没交产物"这件事在报告里完全不留痕。
    unattributed = [
        {"assertion_id": a["id"], "layer": a["layer"], "patterns": a["error_patterns"]}
        for a in assertions
        if a["state"] == "fail" and not a["skill_id"]
    ]

    if not trace_audit["reported"]:
        notes.append("未上报 trace：轨迹类断言记 unscored（分数贡献 0，但不计入掌握度），不判 0")
    elif not trace_audit["schema_valid"]:
        notes.append(
            "trace 未通过前置校验（" + ", ".join(trace_audit["invalid_reasons"]) + "）：轨迹类断言记 unscored"
        )

    report = {
        "report_version": REPORT_VERSION,
        "submission": raw["submission"],
        "trace_audit": trace_audit,
        "score": {
            "weighted_sum": weighted_sum,
            "credit_pass_weight": credit_pass_weight,
            "total_weight": credit_total,
            "ratio": ratio,
            "denominator_policy": DENOMINATOR_POLICY,
            "disqualified": disqualified,
            "disqualifying_assertions": disqualifying,
            "by_state": by_state,
        },
        "attribution_policy": attribution_policy,
        "assertions": assertions,
        "skill_gaps": sorted(gaps.values(), key=lambda g: (-g["weight_lost"], g["skill_id"])),
        "unattributed_patterns": unattributed,
    }
    if notes:
        report["notes"] = notes
    return report


def _run_all_fixtures(task_path: str) -> int:
    """--fixture all：跑 fixtures/raw-*.json 全部夹具，每个都过 schema，只打印摘要。"""
    ok = True
    for path in sorted((ROOT / "fixtures").glob("raw-*.json")):
        try:
            report = build_report(load_task(task_path), json.loads(path.read_text(encoding="utf-8")))
            validate_report(report)
        except ReportError as exc:
            print(f"FAIL {path.stem:<16} {exc}", file=sys.stderr)
            ok = False
            continue
        score = report["score"]
        states = " ".join(f"{k}={v}" for k, v in score["by_state"].items())
        flag = "DQ" if score["disqualified"] else "  "
        print(
            f"OK {flag} {path.stem:<16} ratio={score['ratio']:.4f} "
            f"({score['weighted_sum']}/{score['total_weight']}) credit_pass={score['credit_pass_weight']} {states}"
        )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把逐条断言结果组装成诊断报告并过 schema")
    ap.add_argument("--task", default=str(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    ap.add_argument("--fixture", required=True, help="raw 结果 JSON；写 all 表示跑 fixtures/ 下全部 raw-*.json")
    ap.add_argument("--out", help="写入文件；缺省打印到 stdout")
    args = ap.parse_args(argv)

    if args.fixture == "all":
        return _run_all_fixtures(args.task)

    try:
        report = build_report(load_task(args.task), json.loads(Path(args.fixture).read_text(encoding="utf-8")))
        validate_report(report)
    except ReportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"OK: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

===== END: src/arena/diagnosis_report.py =====

文件：`tests/test_diagnosis_report.py` ｜ sha256[:16]=`db01fbf20f017860` ｜ 332 行 ｜ LF
===== BEGIN: tests/test_diagnosis_report.py =====
# 诊断报告 DTO v2 的契约测试（两段式计分：gate 门禁 + credit 积分）
# 这里断言的不是"代码能跑"，而是不变量（见 docs/diagnosis-report-schema.md §7）。
#
# 五份夹具按质量递进排列，分数必须单调：
#   raw-empty(什么都不做) < raw-no-file(做了没交) < raw-fake-retry(交了但错) < raw-no-trace < raw-perfect
# 这条单调性是 P6 的验收判据——旧版四份夹具全落在 0.33~0.5，序是塌的。
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.diagnosis_report import (  # noqa: E402
    ReportError,
    build_report,
    load_schema,
    load_task,
    load_vocabulary,
    rubric_layers,
    validate_report,
)

TASK = load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
SCHEMA = load_schema()
VOCAB = load_vocabulary()
FIXTURES = ROOT / "fixtures"

# 按质量从低到高；分数必须单调不减
NAMES = ("raw-empty", "raw-no-file", "raw-fake-retry", "raw-no-trace", "raw-perfect")

GATE_IDS = [r["id"] for r in TASK["rubric_gate"]]
CREDIT_IDS = [r["id"] for r in TASK["rubric_credit"]]
ALL_IDS = GATE_IDS + CREDIT_IDS  # 报告里的顺序：先 gate 后 credit（与执行顺序一致）
CREDIT_TOTAL = sum(r["weight"] for r in TASK["rubric_credit"])


def _raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _build(name: str) -> dict:
    return build_report(TASK, _raw(name), VOCAB)


def _by_id(report: dict) -> dict:
    return {a["id"]: a for a in report["assertions"]}


# ---------------------------------------------------------------------------
# 结构：断言集合与 rubric 的对应关系
# ---------------------------------------------------------------------------


def test_all_fixtures_pass_schema():
    for name in NAMES:
        validate_report(_build(name), SCHEMA)


def test_assertions_cover_both_layers_in_order():
    """I1：不多、不少、不重复；顺序为先 gate 后 credit，且 weight 与 YAML 一致。

    顺序不是为了好看：执行器先跑 gate，门禁不过就不必跑 credit 的昂贵判定。报告顺序与执行顺序
    一致，读报告的人不会以为 R5 排在 R2 前面是笔误。
    """
    for name in NAMES:
        report = _build(name)
        assert [a["id"] for a in report["assertions"]] == ALL_IDS, f"{name} 的断言集合或顺序不对"

        got = _by_id(report)
        for spec in TASK["rubric_gate"]:
            assert got[spec["id"]]["layer"] == "gate"
            assert got[spec["id"]]["weight"] == 0, "门禁不计分，weight 必须是 0"
        for spec in TASK["rubric_credit"]:
            assert got[spec["id"]]["layer"] == "credit"
            assert got[spec["id"]]["weight"] == spec["weight"]
        assert sum(got[c]["weight"] for c in CREDIT_IDS) == CREDIT_TOTAL == 4


def test_missing_assertion_rejected():
    raw = _raw("raw-fake-retry")
    raw["assertion_results"] = [r for r in raw["assertion_results"] if r["id"] != "R4_no_param_hallucination"]
    with pytest.raises(ReportError, match="不一致"):
        build_report(TASK, raw, VOCAB)


def test_duplicate_assertion_rejected():
    raw = _raw("raw-fake-retry")
    raw["assertion_results"].append(copy.deepcopy(raw["assertion_results"][0]))
    with pytest.raises(ReportError, match="重复"):
        build_report(TASK, raw, VOCAB)


# ---------------------------------------------------------------------------
# 分层本身的校验：错的分层要在入口被挡住，不能等算出分数才发现
# ---------------------------------------------------------------------------


def test_layering_rejects_gate_with_weight():
    """门禁带权重 = 偷偷计分，等于把两段式退回一段式。"""
    task = copy.deepcopy(TASK)
    task["rubric_gate"][0]["weight"] = 1
    with pytest.raises(ReportError, match="门禁"):
        rubric_layers(task)


def test_layering_rejects_assertion_in_both_layers():
    task = copy.deepcopy(TASK)
    task["rubric_credit"].append(copy.deepcopy(task["rubric_gate"][0]))
    with pytest.raises(ReportError, match="同时出现"):
        rubric_layers(task)


def test_legacy_single_rubric_rejected():
    """v1 的单数组结构必须显式报错，而不是静默当空数组算出 0 分。"""
    task = copy.deepcopy(TASK)
    task["rubric"] = task.pop("rubric_gate") + task.pop("rubric_credit")
    with pytest.raises(ReportError, match="rubric_gate"):
        rubric_layers(task)


# ---------------------------------------------------------------------------
# 强制改写与依赖折叠
# ---------------------------------------------------------------------------


def test_no_trace_forces_unscored_even_if_caller_claims_pass():
    """I2：这是 A2E 的反面。raw 里 R3/R4/R5 全写 pass，也必须被改写成 unscored。"""
    raw = _raw("raw-no-trace")
    assert all(r["state"] == "pass" for r in raw["assertion_results"]), "夹具本身应全是 pass"

    report = _build("raw-no-trace")
    got = _by_id(report)
    for rid in CREDIT_IDS[1:] + ["R5_clean_exit"]:  # R3/R4 + 门禁 R5
        assert got[rid]["state"] == "unscored", f"{rid} 应在 trace 缺失时被改写为 unscored"
        assert got[rid]["reason_code"] == "trace_not_reported"
        assert got[rid]["error_patterns"] == []
    assert got["R1_file_exists"]["state"] == "pass"
    assert got["R2_report_correct"]["state"] == "pass"
    assert report["notes"], "trace 缺失必须在 notes 里写明，不能静默"


def test_fail_without_error_patterns_rejected():
    """I3：fail 但说不出错在哪，等于没有诊断价值。"""
    raw = _raw("raw-fake-retry")
    for r in raw["assertion_results"]:
        if r["id"] == "R3_retry_evidence":
            r["error_patterns"] = []
    with pytest.raises(ReportError, match="没有给出错误模式"):
        build_report(TASK, raw, VOCAB)


def test_undeclared_pattern_rejected():
    """I3：R3 上填一个词表里有、但 R3 未声明的模式，必须拒。"""
    raw = _raw("raw-fake-retry")
    for r in raw["assertion_results"]:
        if r["id"] == "R3_retry_evidence":
            r["error_patterns"] = ["wrong_total_amount"]  # 词表有，但属 R2
    with pytest.raises(ReportError, match="未在本断言"):
        build_report(TASK, raw, VOCAB)


def test_blocked_without_failed_dependency_rejected():
    """I4：前置全通过时不许标 blocked，否则它会沦为"看不清就不判"的逃生口。"""
    raw = _raw("raw-no-file")
    for r in raw["assertion_results"]:
        if r["id"] == "R1_file_exists":
            r["state"] = "pass"
            r["error_patterns"] = []
    with pytest.raises(ReportError, match="blocked"):
        build_report(TASK, raw, VOCAB)


# ---------------------------------------------------------------------------
# 两段式计分：这是本次改造的核心
# ---------------------------------------------------------------------------


def test_credit_score_uses_fixed_denominator():
    """I5 + I6：分母恒为 credit 权重和 4，分子只算 credit 层 pass。"""
    for name in NAMES:
        report = _build(name)
        score = report["score"]
        expected = sum(a["weight"] for a in report["assertions"] if a["layer"] == "credit" and a["state"] == "pass")
        assert score["total_weight"] == CREDIT_TOTAL == 4
        assert score["credit_pass_weight"] == expected
        assert score["denominator_policy"] == "fixed_at_credit_total_weight"
        assert sum(score["by_state"].values()) == len(ALL_IDS)


def test_credit_pass_weight_consistent_with_assertions():
    """credit_pass_weight 与断言逐条对得上——门禁未过时它仍照算，这个值才是诊断用的那个。"""
    for name in NAMES:
        report = _build(name)
        manual = sum(a["weight"] for a in report["assertions"] if a["layer"] == "credit" and a["state"] == "pass")
        assert report["score"]["credit_pass_weight"] == manual


def test_gate_failure_disqualifies():
    """I7：任一门禁 fail ⇒ disqualified，ratio 记 0（不是 0.3、不是 0.5）。"""
    for name in ("raw-empty", "raw-no-file"):
        score = _build(name)["score"]
        assert score["disqualified"] is True, f"{name} 应被门禁拦下"
        assert score["disqualifying_assertions"] == ["R1_file_exists"]
        assert score["ratio"] == 0 and score["weighted_sum"] == 0


def test_gate_unscored_does_not_disqualify():
    """I8：门禁本身无法判定（trace 缺失致 R5 无据）时不拦。

    按 on_unscored=do_not_disqualify：无法证明不合格就不判不合格。这不会放水——
    同一份提交的 credit 层 R3/R4 也因 trace 缺失而 unscored，拿不到分。
    """
    score = _build("raw-no-trace")["score"]
    assert score["disqualified"] is False
    assert score["disqualifying_assertions"] == []
    assert score["ratio"] == pytest.approx(0.5)  # 只剩 R2 的 2 分


def test_gate_failure_keeps_credit_observations():
    """方案 Y 的核心：门禁只管分数，不管观测。

    若照"门禁失败就把 credit 全标 blocked"，raw-no-file 的 R3/R4 观测会被抹掉，
    credit_pass_weight 变成 0，诊断信息随分数一起消失。这里锁住它必须保留。
    """
    report = _build("raw-no-file")
    got = _by_id(report)
    assert report["score"]["disqualified"] is True
    assert got["R3_retry_evidence"]["state"] == "pass"
    assert got["R4_no_param_hallucination"]["state"] == "pass"
    assert report["score"]["credit_pass_weight"] == 2, "门禁未过也要照算 credit 通过权重（仅供诊断）"
    assert any("无资格评分" in n for n in report["notes"])


def test_score_is_monotonic_across_fixtures():
    """P6 的验收判据：分数必须随质量单调上升。

    旧版：0.333 / 0.500 / 0.500 / 0.500 —— 三档质量撞在一起，序是塌的。
    新版：0 / 0 / 0.25 / 0.50 / 1.00。
    """
    ratios = [_build(n)["score"]["ratio"] for n in NAMES]
    assert ratios == sorted(ratios), f"分数序塌陷：{dict(zip(NAMES, ratios))}"
    assert ratios[0] == ratios[1] == 0, "两种门禁失败都必须是 0"
    assert ratios[2:] == [0.25, 0.5, 1.0], f"中间档位不对：{ratios[2:]}"
    assert ratios[-1] == 1.0, "满分必须可达，否则上界没有锚点"


# ---------------------------------------------------------------------------
# 归因：技能缺口 + 无归属模式留痕
# ---------------------------------------------------------------------------


def test_skill_gaps_only_from_fail_and_aggregated():
    """只聚合并按 weight_lost 降序；skill_id 为 null 的断言不制造缺口。"""
    gaps = _build("raw-fake-retry")["skill_gaps"]
    assert [g["skill_id"] for g in gaps] == ["multi_step_planning", "failure_retry"]
    assert gaps[0]["weight_lost"] == 2 and gaps[0]["failed_assertions"] == ["R2_report_correct"]
    assert gaps[1]["weight_lost"] == 1 and gaps[1]["patterns"] == ["retry_wrong_params"]


def test_unattributed_patterns_keep_gate_evidence():
    """P5 的修法：R1 失败不属于任何技能，但不能连痕迹都没有。

    旧版无文件时 skill_gaps 是空数组——"没交产物"在报告里完全不留痕，状态层推不出任何复习项。
    现在它出现在 unattributed_patterns，且不进 skill_gaps（避免污染某技能的错误模式统计）。
    """
    report = _build("raw-no-file")
    assert report["skill_gaps"] == [], "R1 无技能归属，不应制造虚假的技能缺口"
    assert report["unattributed_patterns"] == [
        {"assertion_id": "R1_file_exists", "layer": "gate", "patterns": ["no_output_file"]}
    ]

    # 全过的提交不应有无归属模式
    assert _build("raw-perfect")["unattributed_patterns"] == []


def test_attribution_policy_matches_yaml():
    """契约自解释：报告里的归因表必须与任务 YAML 一致，状态层不必回读 YAML。"""
    yaml_attr = TASK["scoring"]["attribution"]
    for name in NAMES:
        policy = _build(name)["attribution_policy"]
        for state in ("pass", "fail", "blocked", "unscored"):
            assert policy[state]["counts_toward_mastery"] == yaml_attr[state]["counts_toward_mastery"]
            assert policy[state]["emit_error_patterns"] == yaml_attr[state]["emit_error_patterns"]


# ---------------------------------------------------------------------------
# schema 自身要能抓错
# ---------------------------------------------------------------------------


def test_schema_rejects_tampered_report():
    """schema 的有效性检查本身要能抓错——否则"通过 schema"这句话没意义。"""
    report = _build("raw-fake-retry")

    bad_state = copy.deepcopy(report)
    bad_state["assertions"][0]["state"] = "passed"  # 非法枚举
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_state, SCHEMA)

    extra_key = copy.deepcopy(report)
    extra_key["mastery_delta"] = {"failure_retry": -0.3}  # v2 故意不装的跨层推断
    with pytest.raises(ReportError, match="schema"):
        validate_report(extra_key, SCHEMA)

    bad_policy = copy.deepcopy(report)
    bad_policy["score"]["denominator_policy"] = "exclude_missing"
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad_policy, SCHEMA)


def test_schema_rejects_gate_scored_as_credit():
    """schema 也要挡住"门禁偷偷计分"：把 gate 断言的 weight 改成 1 必须被拒。"""
    report = _build("raw-perfect")
    bad = copy.deepcopy(report)
    bad["assertions"][0]["weight"] = 1  # 第一条是 R1，layer=gate
    assert bad["assertions"][0]["layer"] == "gate"
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad, SCHEMA)


def test_schema_rejects_disqualified_with_nonzero_ratio():
    """disqualified=true 但 ratio 不是 0，是自相矛盾的报告。"""
    report = _build("raw-no-file")
    assert report["score"]["disqualified"] is True
    bad = copy.deepcopy(report)
    bad["score"]["ratio"] = 0.5
    with pytest.raises(ReportError, match="schema"):
        validate_report(bad, SCHEMA)

===== END: tests/test_diagnosis_report.py =====

文件：`tests/test_trace_pipeline.py` ｜ sha256[:16]=`0f288bdd7c0aa044` ｜ 260 行 ｜ LF
===== BEGIN: tests/test_trace_pipeline.py =====
"""S3-2（Q11）：原始 trace -> R3 执行器 -> 诊断报告 的全链路约束测试。

三件事必须被锁住：
1. 夹具里的 trace 是**合规原始上报**（call_index 连续、字段齐全），不是另一份手写结论；
2. R3 结果确实由执行器现算，不是把 assertion_results 抄一遍；
3. 接线后分数序 / JSON Schema / 四态纪律与 S2 完全一致。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

FIXTURES = ROOT / "fixtures"
TASK = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
SCORE_ORDER = [0.0, 0.0, 0.25, 0.5, 1.0]


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 夹具本身：原始 trace 必须合规
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_every_fixture_declares_trace_explicitly(name):
    """缺 trace 字段和「未上报」是两件事，不允许靠 .get() 默认值混过去。"""
    assert "trace" in raw(name), name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_trace_matches_audit(name):
    data = raw(name)
    audit, trace = data["trace_audit"], data["trace"]
    assert (trace is not None) == bool(audit["reported"]), name
    if trace is not None:
        assert trace["final_status"] == audit["final_status"], name
        assert len(trace["tool_calls"]) == audit["call_count"], name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_trace_calls_satisfy_minimum_contract(name):
    """按 tasks YAML 的 item_fields 校验原始上报，含 call_index 连续性。"""
    fields = TASK["trace_requirements"]["minimum"]["tool_calls"]["item_fields"]
    data = raw(name)
    calls = (data["trace"] or {}).get("tool_calls", [])
    for i, call in enumerate(calls):
        assert call["call_index"] == i + 1, (name, i)
        for key in ("name", "params", "status"):
            assert key in call and key in fields, (name, i, key)
        if call["status"] == "error":
            assert "http_status" in call and "error" in call, (name, i)
        else:
            assert "result_summary" in call, (name, i)


# ---------------------------------------------------------------------------
# 接线：R3 必须现算，分数序不能变
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r3_result_is_generated_by_evaluator(name):
    """现算结果的 evidence.extra 带 filter_expr / matched_steps，手写夹具带的是 matched_pairs。"""
    built = tp.recompute_r3(raw(name))
    r3 = next(r for r in built["assertion_results"] if r["id"] == tp.R3_ID)
    extra = r3["evidence"].get("extra") or {}
    assert "matched_pairs" not in extra, name  # 手写夹具的痕迹必须消失
    if r3["state"] == "unscored":
        # 未上报 trace 走执行器的早退分支，evidence 只有 observed/expected
        assert r3["reason_code"] == "trace_not_reported", name
    else:
        assert "filter_expr" in extra and "matched_steps" in extra, name


def test_recompute_does_not_mutate_input():
    data = raw("raw-perfect")
    before = copy.deepcopy(data)
    tp.recompute_r3(data)
    assert data == before


def test_score_order_unchanged_after_wiring():
    """接线不能改变 S2 已验证的分数序（含地板分与满分锚点）。"""
    ratios = [tp.run_fixture(n, TASK)["score"]["ratio"] for n in tp.FIXTURE_NAMES]
    assert ratios == SCORE_ORDER, ratios


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_full_pipeline_report_passes_schema(name):
    dr.validate_report(tp.run_fixture(name, TASK))


def test_no_trace_fixture_stays_unscored_end_to_end():
    """raw-no-trace 手写 R3 是 pass；现算 + 组装器的强制改写都必须给出 unscored。"""
    built = tp.recompute_r3(raw("raw-no-trace"))
    generated = next(r for r in built["assertion_results"] if r["id"] == tp.R3_ID)
    assert generated["state"] == "unscored"
    assert generated["reason_code"] == "trace_not_reported"
    report = tp.run_fixture("raw-no-trace", TASK)
    r3 = tp._r3(report)
    assert r3["state"] == "unscored" and not r3["error_patterns"]


def test_raw_empty_q10_gap_is_closed():
    """Q10-A 验收 ②：全链路里零调用的分类与手写基线一致，且不再靠 default。

    round-8 时这里刻意把手写 no_retry 与现算 retry_wrong_params(default) 的差异
    钉成 Q10 的可执行证据；人拍板选 A 后差异应当**消失**，本用例随之从
    「暴露缺口」改写成「缺口已修复」的回归锁（见 review-response-v1.md §12.5）。
    """
    handwritten = next(
        r for r in raw("raw-empty")["assertion_results"] if r["id"] == tp.R3_ID
    )
    generated = next(
        r for r in tp.recompute_r3(raw("raw-empty"))["assertion_results"]
        if r["id"] == tp.R3_ID
    )
    assert handwritten["error_patterns"] == ["no_retry"]
    assert generated["error_patterns"] == ["no_retry"]
    assert generated["evidence"]["extra"]["classification_source"].startswith("case:")


# ---------------------------------------------------------------------------
# 真相源一致性与输入防御
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutate", ["trace_null_but_reported", "trace_present_but_not_reported",
                                    "drop_trace_key", "trace_wrong_type", "duplicate_r3"])
def test_inconsistent_raw_is_refused(mutate):
    data = raw("raw-perfect")
    if mutate == "trace_null_but_reported":
        data["trace"] = None
    elif mutate == "trace_present_but_not_reported":
        data["trace_audit"]["reported"] = False
    elif mutate == "drop_trace_key":
        del data["trace"]
    elif mutate == "trace_wrong_type":
        data["trace"] = "oops"
    else:
        r3 = next(r for r in data["assertion_results"] if r["id"] == tp.R3_ID)
        data["assertion_results"].append(copy.deepcopy(r3))
    with pytest.raises(tp.PipelineError):
        tp.recompute_r3(data)


def test_missing_fixture_is_refused():
    with pytest.raises(tp.PipelineError, match="找不到夹具"):
        tp.run_fixture("does-not-exist", TASK)


# ---------------------------------------------------------------------------
# 边界 trace 用例集：Q10 改分类器时的测试底座
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", tp.load_cases(), ids=lambda c: c["name"])
def test_boundary_cases_match_executor(case):
    r = ae.evaluate(ae.load_r3_spec(), {"tool_calls": case["tool_calls"]})
    e = case["expect"]
    got_pattern = (r.get("error_patterns") or [None])[0]
    source = (r["evidence"].get("extra") or {}).get("classification_source")
    assert r["state"] == e["state"], case["name"]
    assert got_pattern == e["pattern"], case["name"]
    # TD-004：不再把来源归并成 case/default，精确比对完整 classification_source
    assert source == e["source"], case["name"]
    assert r.get("reason_code") == e["reason_code"], case["name"]
    if e["observed_offsets"] is not None:
        assert r["evidence"]["observed"]["offsets"] == e["observed_offsets"], case["name"]


@pytest.mark.parametrize("case", tp.load_cases(), ids=lambda c: c["name"])
def test_boundary_cases_survive_full_dto_pipeline(case):
    """每个边界 trace 都不只是执行器自洽，还要能组装成合法报告。"""
    data = raw("raw-perfect")
    data["trace"]["tool_calls"] = copy.deepcopy(case["tool_calls"])
    data["trace_audit"]["call_count"] = len(case["tool_calls"])
    built = tp.recompute_r3(data)
    report = dr.build_report(TASK, built)
    dr.validate_report(report)
    r3 = tp._r3(report)
    assert r3["state"] == case["expect"]["state"], case["name"]


def test_case_names_are_unique():
    names = [c["name"] for c in tp.load_cases()]
    assert len(names) == len(set(names))


REQUIRED_CASES = {
    "true_retry", "interleaved", "fake_retry", "no_retry", "retry_exhausted",
    "zero_calls", "only_first_page", "skips_page_10", "other_tool_after_failure",
    "wrong_offset_after_failure", "shuffled_call_index", "missing_call_index",
    "bad_status_enum", "params_not_object",
}


def test_case_set_covers_required_names():
    """参数化测试的盲区：cases 被清空时参数化为 0 条，反而全绿。

    显式锁住名字集合，保证 Q10 改分类器时这套底座不会悄悄缩水。
    """
    names = {c["name"] for c in tp.load_cases()}
    assert REQUIRED_CASES <= names, f"边界用例集缺：{sorted(REQUIRED_CASES - names)}"


# ---------------------------------------------------------------------------
# S3-3-2：R5 执行器与五夹具报告基线一致（§12.7 完成判据）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r5_executor_matches_report_baseline(name):
    """五夹具上，R5 执行器现算的 state 必须与报告里的 R5 state 一致。

    口径澄清：raw 手写结果里五份 R5 全是 `pass`，但 raw-no-trace 的
    `trace_audit.reported=false`，`build_report()` 会把 `trace_*` 断言强制
    改写成 `unscored`（任务 YAML L199-200 与 `scoring.gate.on_unscored`）。
    所以基线取**报告里的 R5 state**，不取 raw 手写字段——后者在 no-trace
    夹具上是 S2 遗留的假基线，见下一条测试与 docs/review-response-v1.md §16。
    """
    data = raw(name)
    generated = ae.evaluate_assertion(ae.load_r5_spec(), data.get("trace"))
    r5 = next(a for a in tp.run_fixture(name)["assertions"] if a["id"] == "R5_clean_exit")
    assert generated["state"] == r5["state"], name


def test_raw_no_trace_r5_handwritten_pass_is_overridden():
    """把「手写基线 vs 报告基线」的分歧钉成证据，不擅自改夹具。

    raw-no-trace 的手写 R5 是 pass/observed=null（没有 trace 却声称干净退出），
    与 YAML 契约冲突。本步只让执行器产出正确的 unscored，夹具保持原样，
    留到 S3-3-6 切「五条全现算」时一并处置（登记为 TD-007）。
    """
    data = raw("raw-no-trace")
    handwritten = next(r for r in data["assertion_results"] if r["id"] == "R5_clean_exit")
    assert handwritten["state"] == "pass" and handwritten["evidence"]["observed"] is None

    generated = ae.evaluate_assertion(ae.load_r5_spec(), data["trace"])
    assert generated["state"] == "unscored"
    assert generated["reason_code"] == "trace_not_reported"

    r5 = next(a for a in tp.run_fixture("raw-no-trace")["assertions"]
              if a["id"] == "R5_clean_exit")
    assert r5["state"] == "unscored" and r5["reason_code"] == "trace_not_reported"

===== END: tests/test_trace_pipeline.py =====

文件：`fixtures/raw-empty.json` ｜ sha256[:16]=`fa0b110b186a02b3` ｜ 77 行 ｜ LF
===== BEGIN: fixtures/raw-empty.json =====
{
  "name": "empty",
  "description": "地板分哨兵：什么都没做（0 次工具调用）就正常退出。R4 的 mode=all_calls_must_be_valid 对空集合平凡为真 → pass；R5 completed → pass。实测得 2/6 = 0.333，用来量出「不干活也有三成」这层地板，是 P6（分数分辨率不足）的最硬证据。注意它与 raw-no-file 的对比：本文件 skill_gaps 非空（R3 的 no_retry 有归属），而做了活却没交产物的 raw-no-file 反而是空的——做得更多、诊断更少，即 P5。",
  "submission": {
    "submission_id": "sub-demo-empty",
    "task_id": "dbg-001-order-report",
    "task_git_hash": "ac23954",
    "agent_id": "demo-agent-empty",
    "evaluated_at": "2026-11-07T06:40:00Z",
    "invoke": { "duration_ms": 1200, "timeout_seconds": 60, "platform_retries": 0 }
  },
  "trace": {
    "agent_id": "demo-agent-empty",
    "final_status": "completed",
    "tool_calls": []
  },
  "trace_audit": {
    "reported": true,
    "schema_valid": true,
    "invalid_reasons": [],
    "call_count": 0,
    "final_status": "completed"
  },
  "artifacts": {},
  "assertion_results": [
    {
      "id": "R1_file_exists",
      "state": "fail",
      "error_patterns": ["no_output_file"],
      "evidence": {
        "observed": { "exists": false, "size_bytes": 0 },
        "expected": { "exists": true, "non_empty": true }
      },
      "note": "全程 0 次调用，既没拉数据也没写文件"
    },
    {
      "id": "R2_report_correct",
      "state": "blocked",
      "reason_code": "dependency_blocked",
      "evidence": {
        "observed": null,
        "expected": { "valid_count": 17, "total_amount": 1837.99 },
        "extra": { "blocked_by": ["R1_file_exists"], "would_be": "无产物，内容无从判定" }
      }
    },
    {
      "id": "R3_retry_evidence",
      "state": "fail",
      "error_patterns": ["no_retry"],
      "evidence": {
        "observed": { "offsets": [], "statuses": [] },
        "expected": {
          "ordered": true,
          "calls": [
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "error", "http_status": 500 },
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "success" }
          ]
        },
        "extra": { "probe": "calls[?name=='fetch_orders'].params.offset", "matched_pairs": 0 }
      }
    },
    {
      "id": "R4_no_param_hallucination",
      "state": "pass",
      "evidence": {
        "observed": { "violations": [], "checked_calls": 0 },
        "expected": { "mode": "all_calls_must_be_valid" },
        "extra": { "note": "零调用：全称命题平凡为真，这是设计缺陷而非实现错误" }
      }
    },
    {
      "id": "R5_clean_exit",
      "state": "pass",
      "evidence": { "observed": "completed", "expected": "completed" }
    }
  ]
}

===== END: fixtures/raw-empty.json =====

文件：`fixtures/raw-no-file.json` ｜ sha256[:16]=`558d783221494376` ｜ 84 行 ｜ LF
===== BEGIN: fixtures/raw-no-file.json =====
{
  "name": "no-file",
  "description": "报告文件没写出来：R1 失败，R2 因 depends_on 记 blocked（计 0 分、不计掌握度）。用来验证依赖折叠，同时暴露一个设计问题：R1 在三技能分类学里没有归属，于是技能缺口列表是空的。",
  "submission": {
    "submission_id": "sub-demo-no-file",
    "task_id": "dbg-001-order-report",
    "task_git_hash": "ac23954",
    "agent_id": "demo-agent-no-file",
    "evaluated_at": "2026-11-07T06:30:00Z",
    "invoke": { "duration_ms": 15300, "timeout_seconds": 60, "platform_retries": 0 }
  },
  "trace": {
    "agent_id": "demo-agent-no-file",
    "final_status": "completed",
    "tool_calls": [
      {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
      {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 4, "name": "fetch_orders", "params": {"offset": 20, "limit": 10}, "status": "success", "result_summary": "empty page"}
    ]
  },
  "trace_audit": {
    "reported": true,
    "schema_valid": true,
    "invalid_reasons": [],
    "call_count": 4,
    "final_status": "completed"
  },
  "artifacts": {},
  "assertion_results": [
    {
      "id": "R1_file_exists",
      "state": "fail",
      "error_patterns": ["no_output_file"],
      "evidence": {
        "observed": { "exists": false, "size_bytes": 0 },
        "expected": { "exists": true, "non_empty": true }
      },
      "note": "全程未调用 write_report"
    },
    {
      "id": "R2_report_correct",
      "state": "blocked",
      "reason_code": "dependency_blocked",
      "evidence": {
        "observed": null,
        "expected": { "valid_count": 17, "total_amount": 1837.99 },
        "extra": { "blocked_by": ["R1_file_exists"], "would_be": "文件不存在，内容无从判定" }
      }
    },
    {
      "id": "R3_retry_evidence",
      "state": "pass",
      "evidence": {
        "observed": {
          "offsets": [0, 10, 10],
          "statuses": ["success", "error", "success"],
          "http_statuses": [200, 500, 200]
        },
        "expected": {
          "ordered": true,
          "calls": [
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "error", "http_status": 500 },
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "success" }
          ]
        },
        "extra": { "probe": "calls[?name=='fetch_orders'].params.offset", "matched_pairs": 1 }
      }
    },
    {
      "id": "R4_no_param_hallucination",
      "state": "pass",
      "evidence": {
        "observed": { "violations": [], "checked_calls": 4 },
        "expected": { "mode": "all_calls_must_be_valid" }
      }
    },
    {
      "id": "R5_clean_exit",
      "state": "pass",
      "evidence": { "observed": "completed", "expected": "completed" }
    }
  ]
}

===== END: fixtures/raw-no-file.json =====

文件：`fixtures/raw-fake-retry.json` ｜ sha256[:16]=`f40af639b7ea07f8` ｜ 95 行 ｜ LF
===== BEGIN: fixtures/raw-fake-retry.json =====
{
  "name": "fake-retry",
  "description": "游标未推进的假重试：offset=10 报 500 后退回 offset=0 重拉首页，第二页始终没拿到。用来验证 R3 能锁参数而不是只锁状态。",
  "submission": {
    "submission_id": "sub-demo-fake-retry",
    "task_id": "dbg-001-order-report",
    "task_git_hash": "ac23954",
    "agent_id": "demo-agent-fake-retry",
    "evaluated_at": "2026-11-07T06:10:00Z",
    "invoke": { "duration_ms": 12400, "timeout_seconds": 60, "platform_retries": 0 }
  },
  "trace": {
    "agent_id": "demo-agent-fake-retry",
    "final_status": "completed",
    "tool_calls": [
      {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
      {"call_index": 3, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 4, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "10 orders"}
    ]
  },
  "trace_audit": {
    "reported": true,
    "schema_valid": true,
    "invalid_reasons": [],
    "call_count": 4,
    "final_status": "completed"
  },
  "artifacts": {
    "/workspace/report.json": "{\"valid_count\": 9, \"total_amount\": 861.24}"
  },
  "assertion_results": [
    {
      "id": "R1_file_exists",
      "state": "pass",
      "evidence": {
        "observed": { "exists": true, "size_bytes": 58 },
        "expected": { "exists": true, "non_empty": true }
      }
    },
    {
      "id": "R2_report_correct",
      "state": "fail",
      "error_patterns": ["wrong_total_amount", "wrong_valid_count"],
      "evidence": {
        "observed": { "valid_count": 9, "total_amount": 861.24 },
        "expected": { "valid_count": 17, "total_amount": 1837.99 },
        "extra": {
          "diffs": [
            { "field": "valid_count", "expected": 17, "actual": 9 },
            { "field": "total_amount", "expected": 1837.99, "actual": 861.24, "tolerance": 0.01 }
          ]
        }
      },
      "note": "只统计了第一页 10 条（其中 o-007 金额为负被剔除），第二页从未成功拉取"
    },
    {
      "id": "R3_retry_evidence",
      "state": "fail",
      "error_patterns": ["retry_wrong_params"],
      "evidence": {
        "observed": {
          "offsets": [0, 10, 0, 0],
          "statuses": ["success", "error", "success", "success"],
          "http_statuses": [200, 500, 200, 200]
        },
        "expected": {
          "ordered": true,
          "calls": [
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "error", "http_status": 500 },
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "success" }
          ]
        },
        "extra": {
          "probe": "calls[?name=='fetch_orders'].params.offset",
          "matched_pairs": 0,
          "why": "offset=10 失败后所有重试都退回 offset=0，游标没有向前推进"
        }
      }
    },
    {
      "id": "R4_no_param_hallucination",
      "state": "pass",
      "evidence": {
        "observed": { "violations": [], "checked_calls": 4 },
        "expected": { "mode": "all_calls_must_be_valid" }
      }
    },
    {
      "id": "R5_clean_exit",
      "state": "pass",
      "evidence": { "observed": "completed", "expected": "completed" }
    }
  ]
}

===== END: fixtures/raw-fake-retry.json =====

文件：`fixtures/raw-no-trace.json` ｜ sha256[:16]=`fd7665feec6013af` ｜ 65 行 ｜ LF
===== BEGIN: fixtures/raw-no-trace.json =====
{
  "name": "no-trace",
  "description": "刻意让 raw 结果把轨迹断言标成 pass，但 trace_audit 说没有上报。组装器必须强制改写成 unscored——这正是 A2E『读不到 trace 却给满分』的反面。",
  "submission": {
    "submission_id": "sub-demo-no-trace",
    "task_id": "dbg-001-order-report",
    "task_git_hash": "ac23954",
    "agent_id": "demo-agent-no-trace",
    "evaluated_at": "2026-11-07T06:20:00Z",
    "invoke": { "duration_ms": 8100, "timeout_seconds": 60, "platform_retries": 0 }
  },
  "trace": null,
  "trace_audit": {
    "reported": false,
    "schema_valid": false,
    "invalid_reasons": ["not_reported"],
    "call_count": 0
  },
  "artifacts": {
    "/workspace/report.json": "{\"valid_count\": 17, \"total_amount\": 1837.99}"
  },
  "assertion_results": [
    {
      "id": "R1_file_exists",
      "state": "pass",
      "evidence": {
        "observed": { "exists": true, "size_bytes": 58 },
        "expected": { "exists": true, "non_empty": true }
      }
    },
    {
      "id": "R2_report_correct",
      "state": "pass",
      "evidence": {
        "observed": { "valid_count": 17, "total_amount": 1837.99 },
        "expected": { "valid_count": 17, "total_amount": 1837.99 }
      }
    },
    {
      "id": "R3_retry_evidence",
      "state": "pass",
      "note": "调用方错误地当作通过——没有 trace 根本无从判定，组装器会改写为 unscored",
      "evidence": {
        "observed": null,
        "expected": {
          "ordered": true,
          "calls": [
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "error", "http_status": 500 },
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "success" }
          ]
        }
      }
    },
    {
      "id": "R4_no_param_hallucination",
      "state": "pass",
      "evidence": { "observed": null, "expected": { "mode": "all_calls_must_be_valid" } }
    },
    {
      "id": "R5_clean_exit",
      "state": "pass",
      "evidence": { "observed": null, "expected": "completed" }
    }
  ]
}

===== END: fixtures/raw-no-trace.json =====

文件：`fixtures/raw-perfect.json` ｜ sha256[:16]=`09511d62c4fa4d3b` ｜ 84 行 ｜ LF
===== BEGIN: fixtures/raw-perfect.json =====
{
  "name": "perfect",
  "description": "上界锚点：五条断言全过，credit 4/4 = 1.0。没有它就无法验证『满分可达』——只能验证下界和中间值，分数序的上端是空的。真值来自 test_inputs 里 20 条订单剔除 o-007（负数）/o-012（缺字段）/o-018（null）后的 17 条，总额 1837.99（可按 fixtures 的 expected 手算）。",
  "submission": {
    "submission_id": "sub-demo-perfect",
    "task_id": "dbg-001-order-report",
    "task_git_hash": "ac23954",
    "agent_id": "demo-agent-perfect",
    "evaluated_at": "2026-11-07T06:50:00Z",
    "invoke": { "duration_ms": 21400, "timeout_seconds": 60, "platform_retries": 0 }
  },
  "trace": {
    "agent_id": "demo-agent-perfect",
    "final_status": "completed",
    "tool_calls": [
      {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
      {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "10 orders"},
      {"call_index": 4, "name": "fetch_orders", "params": {"offset": 20, "limit": 10}, "status": "success", "result_summary": "empty page"},
      {"call_index": 5, "name": "write_report", "params": {"path": "/workspace/report.json", "content": "{\"valid_count\": 17, \"total_amount\": 1837.99}"}, "status": "success", "result_summary": "written"}
    ]
  },
  "trace_audit": {
    "reported": true,
    "schema_valid": true,
    "invalid_reasons": [],
    "call_count": 5,
    "final_status": "completed"
  },
  "artifacts": {
    "/workspace/report.json": "{\"valid_count\": 17, \"total_amount\": 1837.99}"
  },
  "assertion_results": [
    {
      "id": "R1_file_exists",
      "state": "pass",
      "evidence": {
        "observed": { "exists": true, "size_bytes": 96 },
        "expected": { "exists": true, "non_empty": true }
      }
    },
    {
      "id": "R2_report_correct",
      "state": "pass",
      "evidence": {
        "observed": { "valid_count": 17, "total_amount": 1837.99 },
        "expected": { "valid_count": 17, "total_amount": 1837.99 },
        "extra": { "tolerance": { "total_amount": 0.01 } }
      }
    },
    {
      "id": "R3_retry_evidence",
      "state": "pass",
      "evidence": {
        "observed": {
          "offsets": [0, 10, 10],
          "statuses": ["success", "error", "success"],
          "http_statuses": [200, 500, 200]
        },
        "expected": {
          "ordered": true,
          "calls": [
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "error", "http_status": 500 },
            { "name": "fetch_orders", "params_match": { "offset": 10 }, "status": "success" }
          ]
        },
        "extra": { "probe": "calls[?name=='fetch_orders'].params.offset", "matched_pairs": 1 }
      }
    },
    {
      "id": "R4_no_param_hallucination",
      "state": "pass",
      "evidence": {
        "observed": { "violations": [], "checked_calls": 5 },
        "expected": { "mode": "all_calls_must_be_valid" }
      }
    },
    {
      "id": "R5_clean_exit",
      "state": "pass",
      "evidence": { "observed": "completed", "expected": "completed" }
    }
  ]
}

===== END: fixtures/raw-perfect.json =====
---

## 5. 待判定命题（中性清单，**不是结论**）

> 每条请按 §0.4 产出「核证 + 依据 + 最小复现」。给不出验证方式的，标「假设」。
> 全部命题围绕「方案落地前该补什么」，不是找已落地代码的 bug。

**T1 · 判据歧义：保留 `test_diagnosis_report.py` 原样会丢什么**
§12.5 末句写「`tests/test_diagnosis_report.py` 切到全链路后仍全绿」。人决定**该文件原样保留**，
改在 `tests/test_trace_pipeline.py` 补一致性锁。请判定：这 6 条靠手写注入的负例
（缺断言 / 重复断言 / fail 无 pattern / 未声明 pattern / blocked 无依据 / no-trace 强制改写）
在「五条全现算」的**生产路径**里，还有几条能被真实触发；「DTO 组装器与执行器联动」是否出现覆盖真空。
验证：逐条给出「该负例能否由执行器产出」的判定，并指出需要新增的最小一致性锁应该断言什么。

**T2 · 反面：如果改 `_build()` 走 `run_fixture()`**
请判定这 6 条负例里哪几条会失效，逐条区分是「测试写不下去」还是「被测行为本身消失」。
验证：给出一条最小 `raw` 变形，说明改 `_build()` 后 `build_report()` 还收不收得到这个坏输入。

**T3 · 扩 `artifact_invalid` 的连带面**
人决定给 `reason_code` 扩枚举。请从 §4 找出**所有**会因此失真的位置：schema 文件、`docs/diagnosis-report-schema.md` 的枚举说明、
评审包里的枚举快照、任何测试里的枚举断言、词表、状态层口径。给出逐文件清单 + 每条验证命令。
重点判定：`_check_result_consistency()` 是否只查 state/pattern 而不查 reason_code（若如此，扩枚举后谁来兜住非法值）。

**T4 · `artifact_invalid` 判 unscored 是否与四态口径一致**
R1 产物缺失/损坏判 `unscored`（理由：平台采集故障 ≠ 学生没交作业），而 R1 是 **gate**。
请判定：gate 记 unscored 时 `on_unscored: do_not_disqualify` 会让这份提交**不被拦**，credit 层照常计分——
在 artifacts 缺失但 trace 里 `write_report` 自称写成功的情形，会不会出现「没产物却拿分」。
验证：构造一条最小 `raw`（artifacts 缺失 + trace 含 write_report 成功），给出期望 `ratio` / `disqualified` / R1 state，并对照 YAML §scoring。

**T5 · R1 与 R2 共用一个 reason_code 是否丢信息**
R2 在 artifacts 无效时也会判 unscored。请判定：R1 / R2 共用 `artifact_invalid` 会让下游丢失什么区分度，
DTO 里 `assertion.id` 是否足以补回；若 `dependency_results` 把 R2 折成 `blocked / dependency_blocked`，两处会否矛盾。
验证：给出一条最小 `raw`，列出切换后 R1 / R2 的 `state` + `reason_code` + `evidence`。

**T6 · 假设①（R2）：产物不是合法 JSON 归 `report_missing_field`**
被检验方声称这是「expected 字段全部未观测到」。请判定：它会不会把「学生写了个坏文件」误记成「采集截断」，
以及 `report_missing_field` 是否在 R2 的 `error_patterns` 声明与词表里。
验证：给出两条产物内容（合法 JSON 缺字段 / 根本不是 JSON），预测 `state` / `error_patterns` / `reason_code`；**若两者无法区分**，明确标「假设」并写出可判定的补充观测。

**T7 · 假设①（R4）：未知工具名 → `unscored / trace_invalid`**
R4 的 type 是 `trace_*`。请判定：当 `trace_audit.reported=true` 且 `schema_valid=true` 时，
`build_report()` 的强制改写**不会**触发，此时执行器自己判 unscored 是否绕过「trace 数据可信」的前提；
以及 `trace_invalid` 这个码用在「工具名未声明」上，与 R3/R5 的 `trace_invalid` 语义是否冲突。
验证：给出最小 trace + `trace_audit`，预测最终报告的 R4 state / reason_code，并说明依据在 §4 哪一行。

**T8 · 假设②（R4）：多类违规只报最优先的一个 pattern**
R2 收集全部命中，R4 首命中即止。请判定这是「有意为之的策略差异」还是「口径漂移」；
构造一条同时触发 `extra_keys` 与 `out_of_range` 的最小调用，预测两种策略下 `error_patterns` 的差别，
并指出这对 `skill_gaps.patterns` 与 `unattributed_patterns` 的具体影响。

**T9 · 五条执行器的执行顺序与依赖注入**
R2 `depends_on: [R1_file_exists]`，执行器需要 R1 的结果作为 `dependency_results`；
`build_report()` 又会独立做一次依赖折叠校验。请判定：两处是否可能给出不同结论
（执行器判 `blocked` vs 组装器改写 / 抛错），以及 YAML 里 gate→credit 的遍历顺序是否恰好是唯一可行顺序。
验证：给出「先算 R2 后算 R1」与「按 YAML 顺序」两种情形的 R1/R2 state，指出哪一处会先报错。

**T10 · `raw-fake-retry` 的自述与产物不一致**
该夹具 trace 里**没有** `write_report`，但 `artifacts` 里有产物（Q13(a) 刻意保留，留给 P3 的 R6）。
全现算后 R1=pass、R2=fail。请判定：这是否构成「自述与磁盘不符」的真实漏洞，
以及本步明确不做 R6 的前提下，评估报告该不该给这份提交 `ratio=0.25`。
验证：写出一条能区分「产物是平台采集到的」与「产物是伪造塞进 raw 的」的观测字段；若现有 DTO 没有，标注为范围外登记。

**T11 · 真相源一致性检查会不会在切换中丢失**
`recompute_r3()` 现有一道 `trace_audit.reported` 与 `raw.trace` 的互斥校验（两个真相源打架就拒绝组装）。
`artifacts` 是第三个输入源，Q13(a) 只要求「不从 trace 自述推断」，没要求与 trace 交叉校验。
请判定：切到五条全现算时这道校验应保留在哪一层；若只保留在 R3 分支，R1/R2 读 artifacts 会不会绕过它。
验证：给出一条 `trace_audit.reported=false` 但 `raw.trace` 非空的最小 `raw`，预测当前 `recompute_r3()` 与全现算版本各自的输出。

**T12 · 「TD-008 在 R4 不成立」这个反向结论**
被检验方声称 R4 的 unscored 只用 `trace_not_reported` / `trace_invalid`，都在封闭枚举内。
请从 §4 的 R4 执行器逐条核对所有 `unscored` 返回点，判定该声称是否成立；
若有第三个码，指出它与扩枚举决定的关系。
验证：列出 R4 执行器全部 `unscored` 分支的 `reason_code` 及触发条件。

---

## 6. 指纹表与用户侧自检

> **给有仓库的人（用户）**：外发前请在仓库根跑一次自检。与上一版不同，本节命令**同时**
> 比对「包内内联块 vs 磁盘文件字节」，不再只验包内自洽（§15.6 登记的模板缺陷）。
> **给评审 AI**：你无法访问源文件；这些指纹只能由用户代跑证明，不能自己验真。

| 文件 | sha256[:16] | 行数 | 行尾 |
|---|---|---|---|
| `tasks/dbg-001-order-report.yaml` | `d744837b44548563` | 273 | CRLF |
| `docs/diagnosis-report.schema.json` | `9541848526520bfe` | 300 | CRLF |
| `docs/diagnosis-report-schema.md` | `d203a8a6a9b9a6d9` | 212 | LF |
| `docs/executor-semantics.md` | `aa6be0e64c4b957a` | 29 | LF |
| `docs/error-patterns-v0.yaml` | `ed09b3e5cd7cff11` | 105 | LF |
| `src/arena/assertion_evaluator.py` | `68282d0c4f862d22` | 1379 | LF |
| `src/arena/trace_pipeline.py` | `e12e5bd8f1c012a3` | 143 | LF |
| `src/arena/diagnosis_report.py` | `8bb92e1188d8eb56` | 342 | CRLF |
| `tests/test_diagnosis_report.py` | `db01fbf20f017860` | 332 | LF |
| `tests/test_trace_pipeline.py` | `0f288bdd7c0aa044` | 260 | LF |
| `fixtures/raw-empty.json` | `fa0b110b186a02b3` | 77 | LF |
| `fixtures/raw-no-file.json` | `558d783221494376` | 84 | LF |
| `fixtures/raw-fake-retry.json` | `f40af639b7ea07f8` | 95 | LF |
| `fixtures/raw-no-trace.json` | `fd7665feec6013af` | 65 | LF |
| `fixtures/raw-perfect.json` | `09511d62c4fa4d3b` | 84 | LF |

自检命令（cwd = 仓库根；Git Bash，用装了 pytest / yaml 的解释器）：

```bash
python - <<'PYEOF'
import hashlib, pathlib
EXPECTED = {"tasks/dbg-001-order-report.yaml": "d744837b44548563", "docs/diagnosis-report.schema.json": "9541848526520bfe", "docs/diagnosis-report-schema.md": "d203a8a6a9b9a6d9", "docs/executor-semantics.md": "aa6be0e64c4b957a", "docs/error-patterns-v0.yaml": "ed09b3e5cd7cff11", "src/arena/assertion_evaluator.py": "68282d0c4f862d22", "src/arena/trace_pipeline.py": "e12e5bd8f1c012a3", "src/arena/diagnosis_report.py": "8bb92e1188d8eb56", "tests/test_diagnosis_report.py": "db01fbf20f017860", "tests/test_trace_pipeline.py": "0f288bdd7c0aa044", "fixtures/raw-empty.json": "fa0b110b186a02b3", "fixtures/raw-no-file.json": "558d783221494376", "fixtures/raw-fake-retry.json": "f40af639b7ea07f8", "fixtures/raw-no-trace.json": "fd7665feec6013af", "fixtures/raw-perfect.json": "09511d62c4fa4d3b"}
data = pathlib.Path('docs/verify-brief-s3-3-6.md').read_bytes()
ok = True
for path, want in EXPECTED.items():
    b = ('===== BEGIN: ' + path + ' =====\n').encode()
    e = ('===== END: ' + path + ' =====\n').encode()
    if data.count(b) != 1 or data.count(e) != 1:
        print('MARKER_ERROR', path, data.count(b), data.count(e)); ok = False; continue
    raw = data.split(b)[1].split(e)[0][:-1]
    got_pack = hashlib.sha256(raw).hexdigest()[:16]
    disk = pathlib.Path(path).read_bytes()
    same_disk = (raw == disk)
    print('%-4s %-4s %s' % ('OK' if got_pack == want else 'BAD',
                              'OK' if same_disk else 'BAD', path))
    ok = ok and got_pack == want and same_disk
print('SELFCHECK:', 'PASS' if ok else 'FAIL')
PYEOF
```

> 两列分别是「包内块 vs 指纹表」与「包内块 vs 磁盘字节」；**两个都 OK 才算 PASS**。
> Windows PowerShell 用户请勿把上面的 heredoc 多行直接粘贴（`>>` 是续行符），
> 改成把脚本存成 `.py` 文件后用隔离 venv 的解释器运行。

**一字节变异检测**（可选，验证自检不是恒真）：把包里任一内联块的一个字节改掉，
上述自检必须打印 `BAD`；复原后应重新 `PASS`。生成器已在生成时自动跑过一次，结果见 §7。

---

## 7. 边界 / 匿名 / 已知未修

- **本包内联 15 个现役文件**（§6 指纹表逐项列出），覆盖任务 YAML、DTO schema、schema 说明、
  执行器语义、词表、三个产品源文件、两个测试文件、五份夹具。
- **未纳入本包**：`docs/review-response-v1.md` 全文（130 KB，只逐字摘 §12.5 / §12.7，其余为剪裁摘要）、
  `src/arena/review_packet.py`、`docs/review-packet*.md`、`verify.bat` / `review.bat`、
  `tests/test_file_exists.py` / `test_json_field_equals.py` / `test_param_schema.py` / `test_final_status.py`
  （四个执行器各自的约束测试）。若 T1–T12 需要，请向用户索取该单一文件，不要索取整个仓库。
- **已过期的历史快照**：`docs/verify-brief-s3-3-1.md`（内联的是 round-12 旧代码，且自检只验包内自洽）、
  `docs/redteam-brief-round8.md`（Q10-A 之前）。两者头部均已标注，**不要**拿它们当依据。
- **仓库状态**：HEAD `03cf8c9`，全部改动尚未 commit / push；本包是新增未跟踪文件。
- **匿名与保密**：不索取真名 / 校名 / 本机绝对路径；API key 与 PAT 绝不进入对话或文件。
  本包内所有路径均为仓库相对路径或任务内的容器路径（如 `/workspace/report.json`）。
- **已知未修（本步范围外，勿重复报）**：P3 的 R6 一致性比对、TD-001 跨技能归因、
  D1 / D3 / D4；`docs/review-response-v1.md` 内部处置记录含历史昵称字样，该文件不外发。
- **生成时自检**：逐字比对（包内块 vs 磁盘字节）结果见用户侧验证记录，本包不把自评当证据。
