# 同侪评审简报：S3-3-7 第 2 步 / F3 —— R3 的「数据缺损失真」与归因分界

> **状态（2026-11-09）**：S3-3-7 序列为 F1 → **F3** → F4/F5 → F9。F1 已于上一轮拍板落地
> （`trace_audit` 收敛为 trace 的派生物 + 冲突即拒绝组装）。**本轮只做复现与证据锁，
> 未改任何判定语义**：三个产品源文件、五份夹具、任务 YAML、DTO schema 一字未动。
> 本文件是**可外发单文件**，只写仓库相对路径；本机根目录由维护者在本机侧填写，
> 不写进包内（避免破坏盲审匿名）。
>
> **round-26 订正（2026-11-10）**：同侪回件已收到并逐项本地核证 —— 附录 A 的 13 个场景、
> 附录 B 的分数副作用、隔离副本里的补丁与 M1–M3 变异全部成立。**本文件 §3 的 Q1(c)、
> Q2(a)+(c)、Q5(a) 三处我方倾向已被反例推翻**（C2 / C1·C5 / 附录 B 实测 `0.75 → 0.5`）；
> §2.2「F3 第二层必须同时决定派生器产出原因码」亦被 P-B 先例与
> `docs/executor-semantics.md` §4 的结构-语义分界推翻。因此 **§3/§4 的「待拍板」是历史快照**，
> 现行决策点已换成回件的 D1–D6 与新增的 P-F3-1，由维护者拍板；F3 代码仍未落地。
> 逐条核证与 TD 登记见维护者侧内部处置记录（不外发）。
>
> **round-28 状态订正（2026-11-10，取代 round-27 订正）**：D1–D6 与 P-F3-1 **已由维护者
> 拍板并两步落地完毕**（不再待拍板）。**P1**：`call_count` 改数目标工具调用条数、失败步
> `params_match` 缺字段判 `unscored/trace_invalid`；**P2**：该步声明的 `http_status`
> 缺失/null 同判，值写错仍判 `fail`。均为**仅执行器层**、audit 不同步、DTO 未扩。
> **F3 判定层已闭合**：① ② 两层现状锁全部反转，各有变异裁判覆盖。
> 证据形状、全局条件必填、专用原因码、分数序倒置等残留边界单列为技术债，不属 F3。
> 本文件 §1–§4 的问题与「我方倾向」均为 round-25/26 历史快照，**勿再据此提建议**。
> 可复核判据（均为仓库相对路径命令，本机根目录由维护者在本机侧补）：
> `pytest tests/ -q`、`pytest tests/test_trace_pipeline.py -q -k f3_td018`、
> `python src/arena/review_packet.py --check`。**不写死测试数与指纹**，避免包内自指失真。
>
> 请勿参考 `docs/verify-brief-s3-3-6.md`（已过期历史快照）或
> `docs/peer-brief-s3-3-7-f1.md`（F1 的历史决策留档，已落地）。

---

## 1. 请求什么

请对下面 **Q1–Q6** 给出可判定意见：每个结论请配一个**十分钟内能在本仓重跑的验证命令或反例**。
不需要夸奖、不需要重写代码、不需要泛泛而谈架构。我们只采信能用仓库内命令复现的主张；
回件自述一律先当**待核证主张**。

本轮的盲区不是「现状会不会出错」（本地已证明会），而是**应然该怎么定**：
本地裁判（pytest / 字节级变异 / 评审包 `--check`）能锁住现状，判不了契约选择。

---

## 2. 已核证的现状（本地实测，可直接复现）

基线 `pytest tests/ -q` **573 passed**，评审包 `--check OK`；本轮追加 7 条现状证据锁后
`tests/test_trace_pipeline.py` 局部 **133 passed**，全量 **580 passed**（见 §2.3 过渡态说明）。

所有场景都从 `fixtures/raw-perfect.json` 出发、只改内存中的 `trace.tool_calls`，
并把 `trace_audit` 重算成 F1 的派生值以避开对账入口，再走完整链路
`recompute_all() → build_report() → validate_report()`。

| 场景 | 改动 | R3 实测 | 报告实测 |
|---|---|---|---|
| 基线 | 无 | `pass`，`offsets=[0,10,10,20]` | `ratio=1.0` |
| **A1** | 4 条 `fetch_orders` 的 `params.offset` 全误写成 `offsets` | `fail / no_retry / case:no_tool_calls`，`observed.offsets=[]` | `ratio=0.5`；`audit.call_count=5`；**R4 同时 `fail / param_out_of_schema`** |
| **A2** | 只保留 1 条 `write_report`（有调用，但无目标工具） | `fail / no_retry / case:no_tool_calls` | `ratio=0.75`；`audit.call_count=1`；R4 `pass` |
| **A3** | 只把 `status=error` 那条的 `offset` 误写成 `offsets` | `fail / retry_wrong_params / case:fault_point_not_repeated`，`offsets=[0,10,20]` | `ratio=0.5`；R4 `fail / param_out_of_schema` |
| **B1** | 真重试，但 `status=error` 那条**删掉** `http_status` | `fail / retry_wrong_params / default`，`http_statuses=[None,None]` | `ratio=0.75`；`audit.schema_valid=true`、`invalid_reasons=[]` |
| **B2** | 对照：同一条 `http_status` 写成 `404`（值错，字段在） | `fail / retry_wrong_params / default`，`http_statuses=[404,None]` | `ratio=0.75`；audit 同上 |

### 2.1 三处最值得咬的矛盾

1. **A1：同一份报告里两条断言互相打架。** R4 已抓到参数幻觉（`param_out_of_schema`），
   R3 却给出 `case:no_tool_calls`（零调用），而 `trace_audit.call_count=5`。
   错误模式 `no_retry` 会进入掌握度与后续排程。
2. **A3：投影丢 null 不只是计数错，序列本身被改写。** 4 个 offset 变 3 个，
   `offsets_equals` / `offsets_starts_with` 两类谓词拿到的输入已不是原始轨迹。
3. **B1 vs B2：除 `R3.evidence.observed.http_statuses` 外，两份报告逐字相同。**
   `state` / `error_patterns` / `classification_source` / `score` / `trace_audit` / `notes` 全等。
   「数据缺字段」与「数据完整但值不符」在归因层完全不可区分。

根因（只读核证）：
- `src/arena/assertion_evaluator.py` 的 `evaluate()` 里
  `offsets = jmespath.search("calls[?name=='fetch_orders'].params.offset", ...) or []`；
  JMESPath 投影会丢弃 null，于是 `call_count = len(offsets)` 变成「有效 offset 的个数」，
  却被 `no_tool_calls` 当成「工具调用条数」。
- `_validate_calls()` 按已拍板的 **Q12(a) 最小校验**只查 `call_index` 连续、
  `status ∈ status_enum`、`params` 是 dict；不查 YAML `note` 里写的
  「`http_status`/`error` 仅 `status=error` 必填」。
- `docs/diagnosis-report.schema.json` 的 `$defs.traceInvalidReason.enum` **已包含**
  `status_error_without_http_status`，但五份夹具 + 上述六个场景里没有任何路径产出它
  （已用行为级锁钉住，不是静态 grep）。

### 2.2 与 F1 落地成果的跨模块张力（本轮新增，最需要外部意见）

F1 刚把 `trace_audit` 收敛为**派生物**，派生器 `_trace_audit_issues()` 只查结构规则，
**刻意不查条件必填**（`docs/executor-semantics.md` §4 写明「结构校验与语义 `trace_invalid` 的分界」）。
所以 B1 里 audit 说「数据合规」、断言侧判「能力不足」。

若 F3 第二层按 Q12(b) 把 B1 判成 `unscored / trace_invalid`，**必须同时决定 audit 派生器
要不要同步产出 `status_error_without_http_status`**：
- 不同步 → 重新引入 F1 刚消解的那类自相矛盾（audit 合规 vs 断言数据不可信）；
- 同步 → 派生器与执行器各写一份条件必填规则，正是 TD-024 记的「两处独立实现会漂移」。

另外 DTO 口径不对称：`traceInvalidReason` 有该原因码，
但 `assertions[].reason_code` 的枚举**没有**它（只有 `trace_invalid` 等 6 个值）。
所以「R3 用哪个 reason_code」也是拍板项，不是实现细节。

---

## 3. 可判定命题（请逐条回答，附验证方式）

| 编号 | 问题 | 选项（不穷尽，可提第三方案） |
|---|---|---|
| **Q1** | `no_tool_calls` / `call_count` 的口径该是什么？ | (a) 目标工具调用条数（现状隐含）；(b) `trace.tool_calls` 全部条数（与 `audit.call_count` 一致）；(c) 保留探针，但把「投影丢 null」显式判成数据不可信 → `unscored` |
| **Q2** | 第一层修在哪一层？ | (a) `_validate_calls()` 补「目标工具必填参数」；(b) `evaluate()` 里对 `offsets` 与 `calls` 做一致性对账；(c) `trace_pipeline._trace_audit_issues()` 派生器补；(d) 组合 |
| **Q3** | `status=error` 缺 `http_status` 该怎么判？ | (a) `unscored / trace_invalid`（Q12(b) 原意）；(b) 保持 `fail / default`（Q12(a) 边界）；(c) 只加诊断字段、不改 state |
| **Q4** | B1 与 B2 是否**必须**在归因层可区分？判据是什么？ | 若必须：给出「可区分」的最小可判定定义。注意 `observed.http_statuses` 已不同，下游是否算「已可区分」是歧义点 |
| **Q5** | 若 Q3 选 (a)，`status_error_without_http_status` 该落到哪里？ | (a) 只进 `trace_audit.invalid_reasons`；(b) 只做 R3 的 `reason_code`（需扩 DTO 枚举）；(c) 两处都要；(d) 都不用，复用 `trace_invalid` |
| **Q6** | F3 与 F4/F5 的边界？ | A2（只调 `write_report`）同时踩到 TD-013（零调用 R4 `pass` 仍计掌握度 → `0.75`）。F3 要不要连带处理，还是严格留给 F4/F5？ |

---

## 4. 我方倾向（**假设，不是结论**；请给反证条件）

| 编号 | 倾向 | 理由 | 反证条件（成立即推翻） |
|---|---|---|---|
| Q1 | **(c)** | 症状不是「计数口径选错」，是**投影静默丢 null**；换个计数口径仍会把 A3 的序列改短 | 若生产 trace 里目标工具缺 `offset` 是合法业务形态（例如多种分页参数），(c) 会误判合法提交 |
| Q2 | **(a) + (c) 分开两步**，本轮只做一步 | 单点改动可独立验证；但 (c) 单独做会造出 §2.2 的矛盾 | 若能让执行器与派生器共用同一份「必填参数」声明（从 YAML 派生），(d) 更好，请给出共用方式 |
| Q3 | **(a)** | YAML `note` 已声明条件必填，schema 已备原因码，缺字段属数据问题不是能力问题 | 若业务上「缺字段」和「值写错」都算未按规范重试，(a) 会让缺字段拿到更宽松的 `unscored` |
| Q4 | **必须可区分** | 归因层要分开「数据缺失」与「能力不足」，这是本项目 Q12 / 四态纪律的既定目标 | 若下游排程只读 `state` 与 `score`、从不读 `reason_code`，则「可区分」的收益为零，判据要重定 |
| Q5 | **(a) + 复用 `trace_invalid`**，暂不扩 DTO | `status_error_without_http_status` 已在 `traceInvalidReason` 枚举内，无需动 `reason_code` 枚举 | 若需要按原因码驱动差异化复习策略，(d) 不够，须扩枚举 |
| Q6 | **留给 F4/F5** | 按已拍板的 P-E 序列，一步只做一个可独立验证的单元 | 若 A2 的 `0.75` 被判为 F3 的直接后果而非独立缺陷，边界要重画 |

---

## 5. 只读清单（**只给相对路径**，勿给整个仓库）

```text
判定链  src/arena/assertion_evaluator.py
        src/arena/trace_pipeline.py
        src/arena/diagnosis_report.py
契约    tasks/dbg-001-order-report.yaml
        docs/diagnosis-report.schema.json
        docs/diagnosis-report-schema.md
        docs/executor-semantics.md
夹具    fixtures/raw-perfect.json
        fixtures/tool-call-cases.json
判据    tests/test_assertion_evaluator.py
        tests/test_trace_pipeline.py          （本轮新增 7 条以 test_f3_td018_ 开头的现状锁）
        tests/test_review_packet.py
治理件  HANDOFF.md
        docs/review-packet.md                 （生成物，勿手改）
        src/arena/review_packet.py
```

**明确不给**：`docs/review-response-v1.md`（内部处置记录）、`docs/verify-brief-s3-3-6.md`（过期包）、
`.redteam-tmp/`、`.git/`、任何本机绝对路径日志、任何 API key / PAT。

可重放的最小验证（在仓库根，用本仓隔离 venv 的 python）：

```text
pytest tests/test_trace_pipeline.py -q -k f3_td018       # 7 passed
pytest tests/ -q                                          # 580 passed
python src/arena/trace_pipeline.py --fixture all           # 0 / 0 / 0.25 / 0.5 / 1.0
python src/arena/review_packet.py --check                  # OK
```

### 2.3 关于测试数

本轮新增 7 条锁，全量从 573 变 580。评审包按既有 Q7 规则**两遍重生成**，
第一遍会如实出现 `1 failed`（测试数锁报红），第二遍转绿；这不是产品缺陷。
若你看到的数字与此处不同，请先在本地跑一次 `git status --porcelain | wc -l`
与全量测试再下结论。

---

## 6. 我们不要什么

- 不要「整体架构很扎实」这类无判据评价；
- 不要一次性给出全套补丁：本仓纪律是**一步 = 一个可独立验证的最小单元**；
- 不要改动任何文件（只读评审）；若要给补丁，请给**统一 diff 文本**，由维护者在隔离副本里验证；
- 不要引用仓库外的规则、版本号或竞赛/政策条款——硬事实必须给可核验来源，否则标「假设」。
