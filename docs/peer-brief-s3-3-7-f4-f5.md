# 同侪评审简报：S3-3-7 第 3 步 / F4+F5 —— 「什么算通过」判据选择

> **round-31 订正（2026-11-10）**：同侪回件已收到并逐项本地核证（§0.1 指出的
> **§2.2 五夹具表两行 ratio 互换**属实——磁盘实测 `raw-no-file=0.0`、`raw-no-trace=0.5`，
> 本文件已订正）。Q1–Q5 五项回答均成立，隔离副本补丁验证通过。§3/§4 的「待拍板」
> 与「我方倾向」是 round-30 历史快照，现行决策见回件与维护者侧处置记录。
>
> **状态（2026-11-10）**：S3-3-7 序列为 F1 → F3 → **F4/F5** → F9。F1、F3 已拍板落地并闭合。
> **本轮只做复现与证据锁，未改任何判定代码**：`src/` 零改动，五份夹具、任务 YAML、
> DTO schema 一字未动。F4/F5 的修复落地**等本包的判据拍板**。
> 本文件是**可外发单文件**，只写仓库相对路径；本机根目录由维护者在本机侧填写，
> 不写进包内（避免破坏盲审匿名）。
>
> 请勿参考 `docs/verify-brief-s3-3-6.md`（过期）、`docs/peer-brief-s3-3-7-f1.md`
> （F1 历史留档）、`docs/peer-brief-s3-3-7-f3.md`（F3 历史留档，已落地）。
> `docs/review-response-v1.md` 是维护者内部处置记录，**不外发、不在只读清单内**。

---

## 1. 请求什么

请对下面 **Q1–Q5** 给出可判定意见：每个结论请配一个**十分钟内能在本仓重跑的验证命令或反例**。
不需要夸奖、不需要重写代码、不需要泛泛而谈架构。我们只采信能用仓库内命令复现的主张；
回件自述一律先当**待核证主张**。

本轮的盲区不是「现状会不会出错」（本地已证明会），而是**应然该怎么定**：
本地裁判（pytest / 字节级变异 / 评审包 `--check`）能锁住现状，判不了契约选择。
两处具体歧义：
1. F4-B 会**细化一条已拍板的边界**（「未知工具 → unscored」不再无条件成立）——细化是否站得住；
2. F5-B 须定义**「目标技能相关工具」**——口径本身有多种取法，且影响判据可判定性。

---

## 2. 已核证的现状（本地实测，可直接复现）

基线 `pytest tests/ -q` **596 passed**，评审包 `--check OK`；
`pytest tests/test_trace_pipeline.py -q -k "f4_td019a or f5_td019b"` **7 passed**（本轮新增的现状证据锁）。

所有场景都从 `fixtures/raw-perfect.json` 出发、只改内存中的 `trace.tool_calls`，
把 `trace_audit` 重算成 F1 的派生值以避开对账入口，再走完整链路
`recompute_all() → build_report() → validate_report()`。

| # | 场景 | 实测结果 | 判读 |
|---|---|---|---|
| F4-S1 | `fetch_orders(offset=0, limit=99)` 单独 | `fail / param_out_of_range`，`violations=[out_of_range/limit]` | 对照基线：违规可见（应然） |
| F4-S2 | 同上 + 一条 `imaginary_tool` | `unscored / trace_invalid`，`observed` **只剩** `unknown_tools` | **缺陷**：`out_of_range` 从证据里消失 |
| F4-S3 | 仅 `imaginary_tool` | `unscored / trace_invalid`，`observed=unknown_tools` | 既有边界（修复后必须仍成立） |
| F5-S1 | 空集（零工具调用） | `pass`，`checked_calls=0`，`violations=[]` | **缺陷**：空集平凡真 |
| F5-S2 | A1 / A2 / A0 全链路 | A1（4 次 `fetch_orders` 参数全错 + `write_report`）`0.5`；A2（仅 `write_report`）`0.75`；**A0（零调用）`0.75`** | 分数序倒置：努力多者得分低；「什么都不干」得 0.75 |
| F5-S3 | 归因策略 | `attribution_policy.pass.counts_toward_mastery=true`；A0 的 `credit_pass_weight=3`（R2 权重 2 + R4 空集 pass 权重 1） | 空集 pass 拿满 R4 权重且计入掌握度 |

### 2.1 缺陷根因（只读核证）

- **F4**：`src/arena/assertion_evaluator.py` 中 R4（`trace_param_schema_valid`）的
  判定路径先查「未知工具名」，非空即整体 `unscored/trace_invalid`，
  已算出的 `violations` 被短路丢弃，`observed` 只剩 `unknown_tools`。
  于是「调了一个不存在的工具 + 已知工具参数越界」与「只调了一个不存在的工具」
  在证据层不可区分。
- **F5**：同一路径里，`violations` 是对「受检调用」逐条校验的并集；**零调用 → 并集为空 → `pass`**。
  任务 YAML 的 `attribution.pass.counts_toward_mastery=true` 使这个平凡真 `pass`
  计入掌握度并拿满 credit 权重。
- **分数序倒置（TD-028）**：credit 权重 R2=2 / R3=1 / R4=1（总 4，磁盘实测）。
  A0 靠 R2(2)+R4 空集 pass(1)=3 → `0.75`；A1 的 R3/R4 双 fail → `0.5`。
  「调了 4 次但参数全错」低于「一次都没调」。

### 2.2 五夹具现状（防波及面）

| 夹具 | R4 现状 | ratio |
|---|---|---|
| `raw-perfect` | `pass`，`checked_calls=5` | 1.0 |
| `raw-no-file` | — | 0.0 |
| `raw-fake-retry` | — | 0.25 |
| `raw-empty` | **`pass`，`checked_calls=0`**（正是 F5 空集场景，`credit_pass_weight=1`，ratio 全靠 gate 拦成 0） | 0.0 |
| `raw-no-trace` | 已 `unscored` | 0.5 |

分数序 `0 / 0 / 0.25 / 0.5 / 1.0` 是现行锁，**任何选项都不得改变它**。

### 2.3 既有的「设计锁」（本轮未动，修复时须一并处置）

`tests/test_param_schema.py` 里有两条以设计意图命名的锁：
`test_zero_calls_is_vacuously_true_by_design`（空集平凡真）与
`test_unknown_tool_name_is_unscored_by_assumption`（未知工具 → unscored）。
它们正是 F5 / F4-B 要反转或限定的对象。本仓惯例是**反转不删除**，但「by design / by assumption」
是否应被推翻，本身也在本轮问题范围内（见 Q1 / Q2）。

---

## 3. 可判定命题（请逐条回答，附验证方式）

| 编号 | 问题 | 选项（不穷尽，可提第三方案） |
|---|---|---|
| **Q1** | F4-B「能判的判」是否站得住？即：已知工具有已观测违规时按违规判 `fail`，`unknown_tools` 降为 evidence 注记，仅当违规为空才 `unscored` | (a) 成立，细化合理；(b) 不成立，未知工具必须无条件 `unscored`（现行 P-B 不可细化）；(c) 成立但须补条件（请写明） |
| **Q2** | F5-B 的「目标技能相关工具」口径怎么定，才可判定？ | (a) 从 `expected.schema_ref` 覆盖的工具推导；(b) 断言 spec 显式声明 `target_tools` 列表；(c) 从任务 YAML 的工具白名单推导；(d) 不区分，只修空集（即退到 F5-A） |
| **Q3** | A0 / A2 的目标分数该是多少，才算「分数序不倒置」？ | (a) A0=A2=`0.5`（与 A1 持平）；(b) A0/A2 应低于 A1（如 `0.25`）；(c) 别的可判定目标（请给出） |
| **Q4** | F5 判 `unscored` 时的 `reason_code` 用什么？ | (a) 新码进 DTO 枚举（走既有扩枚举惯例）；(b) 复用现有 `trace_invalid`；(c) 别的现有码 |
| **Q5** | 拆步方式：F4 与 F5 是否按「F4 先、F5 后」两个独立小步落地？F5 内部要不要再拆 A→B？ | (a) F4 / F5 两步，每步独立变异；(b) F5 再拆 A→B 三步；(c) 一步做完（反对拆步，请给理由） |

---

## 4. 我方倾向（**假设，不是结论**；请给反证条件）

| 编号 | 倾向 | 理由 | 反证条件（成立即推翻） |
|---|---|---|---|
| Q1 | **(a)** | 与 F3-P1 同构：判据缺输入只挡缺输入的那部分判断，不屏蔽已观测事实。「有未知工具」只说明轨迹不完整，不抹掉已知工具上已经查出的违规 | 若未知工具的存在会污染对已知工具调用的判读（例如工具名歧义导致同名不同义），(a) 会误判 |
| Q2 | **(b) 显式 `target_tools`**，从 `schema_ref` 推导做交叉校验 | 可判定、可审计；`(a)` 在 schema_ref 覆盖多工具时口径模糊 | 若断言 spec 已被契约冻结不可扩字段，只能退回 (a) 或 (c) |
| Q3 | **(a) A0=A2=`0.5`** | 与 A1 同为「未达成目标技能」，不应高于实际尝试者；不引入新的分数档 | 若「零调用」应被视作比「调了但错」更差（例如计机会惩罚），(b) 更合理，须给业务依据 |
| Q4 | **(b) 复用 `trace_invalid`** | 新语义「无受检调用」与「轨迹不可判」同类；少动 DTO | 若下游要按原因码分流复习策略，(b) 不够 |
| Q5 | **(a) 两步**；F5 内部若 A→B 判据差异大再拆 | 一步 = 一个可独立验证的最小单元（本仓纪律） | 若 F4 与 F5 共享同一处代码路径且拆开会造出中间态矛盾，(c) 更好 |

---

## 5. 只读清单（**只给相对路径**，勿给整个仓库）

```text
判定链  src/arena/assertion_evaluator.py      （R4 的短路点与空集 pass 在此）
        src/arena/diagnosis_report.py          （掌握度/credit 聚合）
        src/arena/trace_pipeline.py
契约    tasks/dbg-001-order-report.yaml        （attribution.pass、credit 权重）
        docs/diagnosis-report.schema.json      （reason_code / traceInvalidReason 枚举）
        docs/executor-semantics.md
        docs/diagnosis-report-schema.md
夹具    fixtures/raw-perfect.json
        fixtures/raw-empty.json
        fixtures/tool-call-cases.json
判据    tests/test_trace_pipeline.py           （本轮新增 7 条 test_f4_td019a_ / test_f5_td019b_ 现状锁）
        tests/test_param_schema.py             （两条「by design / by assumption」设计锁，见 §2.3）
        tests/test_assertion_evaluator.py
治理件  HANDOFF.md
        docs/review-packet.md                  （生成物，勿手改）
        src/arena/review_packet.py
```

**明确不给**：`docs/review-response-v1.md`（内部处置记录）、
`docs/verify-brief-s3-3-6.md` / `peer-brief-s3-3-7-f1.md` / `peer-brief-s3-3-7-f3.md`（历史包）、
`.redteam-tmp/`、`.git/`、任何本机绝对路径日志、任何 API key / PAT。

可重放的最小验证（在仓库根，用本仓隔离 venv 的 python）：

```text
pytest tests/ -q                                          # 596 passed
pytest tests/test_trace_pipeline.py -q -k "f4_td019a or f5_td019b"   # 7 passed
python src/arena/trace_pipeline.py --fixture all           # 0 / 0 / 0.25 / 0.5 / 1.0
python src/arena/review_packet.py --check                  # OK
```

### 5.1 关于测试数与指纹

本包写出的测试数是生成时实测。若你看到的数字不同，请先在本地跑一次
`git status --porcelain | wc -l` 与全量测试再下结论（可能是包生成后的正常漂移）。
评审包 `--check` 的指纹同理，**不要把包内写死的指纹当唯一真相**。

---

## 6. 我们不要什么

- 不要「整体设计合理」这类无判据评价；
- 不要一次性给出全套补丁：本仓纪律是**一步 = 一个可独立验证的最小单元**；
- 不要改动任何文件（只读评审）；若要给补丁，请给**统一 diff 文本**，由维护者在隔离副本里验证；
- 不要引用仓库外的规则、版本号或竞赛/政策条款——硬事实必须给可核验来源，否则标「假设」。
