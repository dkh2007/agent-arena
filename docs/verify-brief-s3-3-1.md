# S3-3-1 验收委托包（外部评审用 · 自包含单文件）

> ⚠️ **历史快照已过期，勿当现役契约（2026-11-08）**：round-13 落地 S3-3-1b / 1c 后，
> 磁盘上的 `assertion_evaluator.py`、`tool-call-cases.json`、
> `test_assertion_evaluator.py`、`test_trace_pipeline.py` 均已变化，§4 内联的是 round-12 旧版本。
> **注意：§6 自检只验「包内内联块 vs 包内指纹表」自洽（应为 PASS），并不与磁盘逐字比对** ——
> 自检通过 ≠ 与现役仓库一致。这是模板缺陷，下轮重生成时须补磁盘比对。
> 本包仅用于 round-12 回件留档；下轮外发须重新生成，并一并处理
> `docs/review-response-v1.md` §14.7 / §15.6 的模板修正。

> 生成：2026-11-07 ｜ 被检验对象：一次**已落地**的代码改动「Q10-A 分类器整改」
> 你没有工作目录访问权：§4 逐字内联了全部相关源文件与用例，§6 有指纹表与自检命令。
> 匿名纪律：本包不含真实姓名 / 校名 / 绝对路径；请勿索取，也不要在对话里粘贴 API key 或 PAT。

---

## 0. 给评审 AI 的指令（请把本节当任务书）

### 0.1 任务边界

验收 **S3-3-1：Q10-A 分类器整改**这一个改动单元。不是重写项目，不是开放式架构评审，不是替人重新拍板。
目标是找出 **实现里真实存在的洞**，以及 **被检验方自述中不成立或过度声称的主张**。

一句话背景：某评测执行器把失败轨迹分成错误模式，原先有 4 类边界轨迹只能靠 `default` 兜底（掩盖词表缺口）。
人拍板选 **A 方案**：只加 3 个从任务 YAML 派生的语义谓词，让这 4 类走明写规则；不改词表、不改 DTO、不改归因。

### 0.2 纪律（违反的产出不予采信）

1. **别信自述**：§2 的决策 / 验收摘录、§3 的基线输出、§4 文件里的注释与 docstring，全部是**被检验方写的**，不是证据。请自己构造输入与反例。
2. **「测试通过」不算证据**：要说明你改坏了什么、预期真变红（`FAILED`）还是 `ERROR` / `TIMEOUT`、有没有复原。
3. **事实挂来源**：每条结论注明依据（§4 的文件名 + 行号，或 §2 / §3 的段落号）；推断明确标「假设」。
4. **每条结论配一个十分钟内能做的验证**：最小输入、可粘贴代码、或一条字节级变异 + 预期输出。
5. **区分** `FAILED` / `ERROR` / `TIMEOUT`，不要混成「没过」。
6. 你没有仓库：**不要声称「我跑过了」**。可复现步骤写成让用户粘贴的命令；需要核对源文件时向用户索取。

### 0.3 已拍板、不要翻案的边界

- **Q10 = A（人拍板）**：不新增 `pattern_id`、不改 `mapped_skill_ids`、不改 DTO v2 `attribution_policy`、不改 `build_report()` 按 `spec.skill_id` 的归因、不动词表。「跨技能归因」已登记技术债 TD-001，**不要建议现在翻成 B 方案**。
- 但「A 的实现有洞」「A 没真正解决它声称解决的问题」属于本次验收范围，请照报。
- Q13=(a)、Q12=(a 最小) 是已授权**待落地**项，本步未实现（见 §7）；别当本步缺陷重复报，但可核对其是否真未实现。

### 0.4 交付格式

1. **核证总表**：`| # | 主张（谁说的） | 核证（成立 / 不成立 / 前提不足） | 依据（文件+行号 / 段落） | 最小复现 |`
2. **可判定命题**：每条写「什么输入 → 期望什么 → 实际什么 → 何时判定通过」。
3. **待拍板项**：只列需要人决定的，注明两个选项各自代价。
4. 不写总体评价、不写鼓励、不写「建议加强测试」这类不可判定句。

### 0.5 顺序

优先咬 §5 的 T1–T8，其次自选。**§5 是中性命题，不是被检验方的结论**。

---

## 1. 最小上下文

五层架构（任务层 YAML → 评测层 → 状态层 → 复习层 → 排程层）；本次只涉及**评测层**的 R3 断言执行器。

- `tasks/dbg-001-order-report.yaml` 是唯一真相源：判定表达式与错误模式分类规则都应从它派生，代码里不该有第二份任务常量。
- 四态 `pass` / `fail` / `blocked` / `unscored`；`unscored` 分数贡献 0、不计掌握度、不产出错误模式。
- `rubric_gate` 失败 `disqualify`；`rubric_credit` 分母 4。
- 全链路：`trace.tool_calls → assertion_evaluator → assertion_results → build_report() → DTO / schema`。
- 五夹具分数序基线 `0 / 0 / 0.25 / 0.5 / 1.0`。
- 分类规则按 YAML `cases` **顺序匹配、首命中即止**；未命中走 `default`（本次按 A 保留）。

---

## 2. 被检验对象自述（剪裁摘录，非逐字全文，**不作证据**）

> 以下是被检验方自己写的决策与验收记录摘录，原文在 `docs/review-response-v1.md`
> （全文 sha256[:16] = `ec91a86ddb1b5a6a`）。摘录经剪裁，**不参与 §6 的逐字校验**。
> 请把它当作「待核证的主张清单」，不是事实。

#### 来源 A：`docs/review-response-v1.md` §12.1（决策登记）

### 12.1 决策登记（Q10 = A）

| 项 | 内容 |
|---|---|
| 决策 | **Q10 = A（保守方案）** |
| 拍板 | 人，2026-11-07；外部评审给理由，人做的选择 |
| 生效范围 | `src/arena/assertion_evaluator.py` 分类谓词、`tasks/dbg-001-order-report.yaml` 的 `error_pattern_detection.cases`、`fixtures/tool-call-cases.json` 的 `expect`、`tests/` 回归锁 |
| 明确不做 | 不新增 `pattern_id`；不改 `mapped_skill_ids`；不改 DTO v2 `attribution_policy`；不改 `build_report()` 按 `spec.skill_id` 的归因口径；不动词表 |
| 通过标准（照抄 §10.4，**不降低**） | ① `zero_calls` / `only_first_page` / `skips_page_10` / `wrong_offset_after_failure` 四个 case 的 `classification_source` 从 `default` 变 `case:`；② `raw-empty` 的 R3 不再靠 default；③ 五夹具分数序 `0/0/0.25/0.5/1.0` 不变；④ JSON Schema 全过 |
| 已知代价（接受） | 所有 R3 模式仍归 `failure_retry`，`multi_step_planning` 缺口表达不了 → 登记 TD-001 |

#### 来源 B：同文件 §13（S3-3-1 落地记录，摘录 13.1–13.6）

### 13.1 授权范围与两个待拍板项的落定

| 项 | 落定 | 依据 / 生效时点 |
|---|---|---|
| **Q13**（产物输入契约） | **(a)** 夹具加 `artifacts` 字段 | 依据人在本轮的授权「按照你的想法来」采纳 §12.6 的建议值。**本步不写该字段**，S3-3-3 实现 R1 时才落地；若否决请直接指出 |
| **Q12**（`_validate_calls()` 补齐） | **(a) 最小**：只补 `status ∈ status_enum` + `params` 是 dict | 同样按授权取最小方案：这两类输入是红队实测暴露的真缺口（§11.2），(b) 的 `name` 类型 / `http_status` 必填尚无反例，不扩张。**代码排进 S3-3-2**（同文件同函数，避免本步塞两个改动单元） |

> 两项都由 AI 依授权代定、写在此处供人复核；若人否决，本步的 Q10-A 实现**不依赖它们**，可独立回退。
### 13.2 三个语义谓词的精确定义（代码里没有第二份任务常量）

`ctx` 在 `evaluate()` 里构造，两个计数都来自**已存在的派生结果**，不新解析 trace：

| 谓词 | 输入 | 布尔判据 | 覆盖的边界 case |
|---|---|---|---|
| `no_tool_calls` | `call_count = len(offsets)`（YAML `probe` 取到的工具调用条数） | `call_count == 0` | `zero_calls` |
| `fault_point_not_reached` | `fault_call_count = len(projected)` | `fault_call_count == 0` | `only_first_page`、`skips_page_10` |
| `fault_point_not_repeated` | 同上 | `fault_call_count < 2` | `wrong_offset_after_failure` |

- `projected` 由 `_filter_expr(spec)` 生成 `calls[?(name==... && params.offset==...)]` 过滤得到，
  条件全部取自 `expected.calls`；**故障点是 10 还是 20 由 YAML 决定**，代码不写死。
  把 YAML 里的 `offset` 改成 20，同一组 trace 的分类必须跟着变 ——
  `test_q10a_predicates_follow_yaml_fault_point` 锁死这一点。
- 三个谓词在 YAML 里只接受 `true`；写成 `false` 抛 `EvaluatorError`，
  不会静默变成「永不命中」再悄悄退回 default（`test_count_predicates_reject_silent_misuse`）。
- YAML `cases` 按「现有 3 条 → 新增 3 条 → `default`」顺序匹配、首命中即止。
  **`default` 保留**：未枚举的序列（实测 `[10,0,10]` 两次 error）仍走兜底，
  `classification_source` 如实记为 `default`，覆盖度缺口继续可观测。
### 13.3 改动清单

| 文件 | 改动 |
|---|---|
| `src/arena/assertion_evaluator.py` | 新增 `_COUNT_PREDICATES` 与 3 个分支；`evaluate()` 的 ctx 加 `call_count` / `fault_call_count`；`--demo` 加「零调用 / 只拉首页」两行；顺手统一三处「四类 / 六类」的过时文案 |
| `tasks/dbg-001-order-report.yaml` | R3 的 `error_pattern_detection.cases` 末尾补 3 条明写规则 + Q10-A 注释；`default` 保留并注明用途 |
| `fixtures/tool-call-cases.json` | 4 条 `expect` 从 `default` 改 `case`（3 条 `no_retry`、1 条 `retry_wrong_params`），`note` 与 `_comment` 同步 |
| `tests/test_assertion_evaluator.py` | 33 → **39 条**：`test_zero_calls_is_classified_by_written_rule`、4 条参数化的 `test_q10a_boundary_cases_are_classified_by_written_rules`、YAML 派生锁、布尔谓词误用锁；`test_default_is_observable_when_rules_miss` 换成仍能触发兜底的 `[10,0,10]` |
| `tests/test_trace_pipeline.py` | `test_raw_empty_exposes_q10_gap` → `test_raw_empty_q10_gap_is_closed`（§12.5 预告的改写，差异应消失） |
| `docs/review-packet.md` | 重生成：179 个用例 / 179 passed，新指纹 `f2c7b2bce038884c` |
| 文档失真处置 | `docs/redteam-brief-round8.md` 头部加「历史快照已过期」警示；`HANDOFF.md` 索引同步用例数、`--demo` 行数、§13、快照状态 |
### 13.4 通过标准逐条核证（照抄 §12.1，未降低）

| # | 标准 | 实测 |
|---|---|---|
| ① | 四个边界的 `classification_source` 从 `default` 变 `case:` | `case:no_tool_calls`（零调用）／`case:fault_point_not_reached`（只拉首页、跳过故障点）／`case:fault_point_not_repeated`（失败后跳到 20）。用例集遍历 + `--demo` 两处一致 |
| ② | `raw-empty` 的 R3 不再靠 default | `--fixture all` 首行 `R3=fail patterns=['no_retry']`；手写基线也是 `no_retry`，round-8 钉住的差异消失 |
| ③ | 五夹具分数序不变 | `0 / 0 / 0.25 / 0.5 / 1.0`（五夹具实测） |
| ④ | JSON Schema 全过 | `tests/` 179 passed，含全链路 schema 校验 |
### 13.5 验证证据（外部裁判，不拿「测试通过」当证据）

- `pytest tests/ -q` → **179 passed**（原 173，+6 条新约束）。
- 数字防漂移锁**真的工作了**：改完测试数后 `test_test_count_matches_repo` 先变红
  （包里仍是 173），迫使重生成评审包。生成跑了两遍：第一遍如实记录过渡态
  `1 failed, 178 passed`，第二遍在测试全绿后记录 `179 passed / 179 total` —— 对外发包不带假红。
- `--check` **OK**（`f2c7b2bce038884c`，锚点 `03cf8c9`）；`--demo` **九行**；
  `trace_pipeline.py --fixture all` **五行 OK**；匿名扫描无绝对路径 / 真名。
- **三组变异全部真 `FAILED`**（非 `ERROR` / `TIMEOUT`），`RESTORED: True`：

| 变异 | 结果 |
|---|---|
| M1 删掉 YAML 新增的 3 条 case | `12 failed, 167 passed` |
| M2 `fault_point_not_repeated` 判据 `< 2` 改 `< 1` | `3 failed, 176 passed` |
| M3 `fault_call_count` 硬编码成 `offset == 10` | `1 failed, 178 passed` |

> M1 第一次用 LF 锚点匹配到 0 处（任务 YAML 是 **CRLF**），按纪律 **ABORT 而非硬跑**，
> 换成 CRLF 字节锚点后补跑才算数。字节级替换 + 顶层 `finally` 复原，避免行尾改动污染仓库。
### 13.6 明确不做 / 下一步

- 未新增 `pattern_id`、未动词表 `mapped_skill_ids`、未改 DTO v2 归因 —— Q10=A 的边界守住，TD-001 仍在册。
- Q13 的 `artifacts` 字段、Q12(a) 的校验代码**本步都没写**，分别在 S3-3-3 / S3-3-2 落地。
- §10.6 与 §11.1 里的「173 passed」是**当时的历史验证快照**，不回改（回改等于篡改历史记录）；现役数字以本节与评审包为准。
- 用例集里 `source=default` 的样例归零，**不等于删了兜底**：`default` 仍在 YAML，
  可观测性改由 `test_default_is_observable_when_rules_miss`（`[10,0,10]` 两次 error）单独守住。
  这条要在下轮红队前说清楚，否则「12 个 case 全绿」会被误读成「分类已完备」。
- **下一步 S3-3-2**：R5 `trace_final_status` 执行器 + `evaluate_assertion()` 分发注册，顺带落 Q12(a)；仍是一次一步、验完再进下一步。
- 工作区仍是**未 commit、未 push** 状态，改动项由 §12 时的 22 变 **23**
  （本步新增 `tasks/dbg-001-order-report.yaml` 一项）；提交按 §12.7 的四组拆分等人事后执行。
---

## 3. 自报基线（被检验方声称，**不作证据**）

```
$ pytest tests/ -q
179 passed in 39.94s

$ python src/arena/review_packet.py --check
OK: 评审包内容与仓库一致（f2c7b2bce038884c）

$ python src/arena/trace_pipeline.py --fixture all
OK raw-empty        ratio=0.0    R3=fail      patterns=['no_retry']
OK raw-no-file      ratio=0.0    R3=pass      patterns=[]
OK raw-fake-retry   ratio=0.25   R3=fail      patterns=['retry_wrong_params']
OK raw-no-trace     ratio=0.5    R3=unscored  patterns=[]
OK raw-perfect      ratio=1.0    R3=pass      patterns=[]

$ python src/arena/assertion_evaluator.py --demo
derived filter: calls[?(name==`"fetch_orders"` && params.offset==`10`)].{s: status, h: http_status}
probe          : calls[?name=='fetch_orders'].params.offset

trace         state    pattern                 source                                  offsets
真重试           pass     -                       -                                       [0, 10, 10]
真重试+穿插        pass     -                       -                                       [0, 10, 10]
假重试           fail     retry_wrong_params      case:offsets_equals                     [0, 10, 0]
无重试           fail     no_retry                case:offsets_equals                     [0, 10]
重试耗尽          fail     retry_exhausted         case:last_status,offsets_starts_with    [0, 10, 10]
零调用           fail     no_retry                case:no_tool_calls                      []
只拉首页          fail     no_retry                case:fault_point_not_reached            [0]
乱序上报          unscored -                       trace_invalid                           -
未上报 trace     unscored -                       trace_not_reported                      -
```

被检验方另声称：三组变异（删 YAML 新增 3 条 case / `< 2` 改 `< 1` / 计数硬编码成 `offset == 10`）
分别导致 `12 failed` / `3 failed` / `1 failed`，且 `RESTORED: True`。**请自行判定这些数字能否推出「新增逻辑被测试真正守住」**。

---

## 4. 逐字内联的源文件

> 每块由脚本用**原始字节**拼入，未做换行转换；CRLF 文件保持 CRLF。
> `===== BEGIN / END =====` 是字节边界标记，**不是 Markdown 代码围栏**（内联内容里本身含三反引号）。
> 校验方法见 §6。路径均相对仓库根。


文件：`src/arena/assertion_evaluator.py` ｜ sha256[:16]=`0393c87e71758ef2` ｜ 431 行 ｜ LF
===== BEGIN: src/arena/assertion_evaluator.py =====
"""S3-1：从原始 trace 计算断言结果（先只实现 R3 的 trace_sequence）。

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

## 明确不做（S3-1 的边界）

- 不实现 R1/R2/R4/R5：遇到非 `trace_sequence` 类型直接抛错，不静默放过。
- 不接进 `diagnosis_report` 流水线、不改 fixtures：本步只交一个可独立验证的判定器。
- 不实现 mock / 故障注入 / 超时控制 / 参数 schema 校验（JMESPath 实测表达不了）。

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
import re
import sys
from pathlib import Path
from typing import Any

import jmespath

ROOT = Path(__file__).resolve().parents[2]
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"

SUPPORTED_TYPES = ("trace_sequence",)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EvaluatorError(ValueError):
    """判定器无法给出可信结果。宁可不产出，也不猜一个 pass/fail。"""


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


def _validate_calls(calls: list[dict]) -> str | None:
    """校验调用序列可信度；返回违规原因，None 表示可用。

    不排序、不重编号、不丢弃可疑调用：任何不一致都判数据不可信。
    """
    for i, item in enumerate(calls):
        if not isinstance(item, dict):
            return f"第 {i + 1} 条调用不是对象（{type(item).__name__}）"
        idx = item.get("call_index")
        if isinstance(idx, bool) or not isinstance(idx, int):
            return f"第 {i + 1} 条调用缺 call_index 或类型非整数：{idx!r}"
        if idx != i + 1:
            return f"call_index 不连续或与上报顺序不符：第 {i + 1} 条为 {idx}"
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
    detection = spec.get("error_pattern_detection") or {}
    cases = detection.get("cases") or []
    if not cases:
        raise EvaluatorError(f"{spec.get('id')}: 缺少 error_pattern_detection.cases，无法分类")
    # 先全量校验谓词名，不能依赖 all() 逐条求值：短路会让"前置条件为假"的
    # case 里的非法谓词永远不被发现，静默潜伏到某天词表一改就走错分类。
    for case in cases:
        for key in (case.get("match") or {}):
            if key not in _PREDICATES:
                raise EvaluatorError(
                    f"{spec.get('id')}: 未知的分类谓词 {key!r}；已支持：{_PREDICATES}"
                )
    for case in cases:
        match = case.get("match") or {}
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
# 对外入口
# --------------------------------------------------------------------------


def evaluate(spec: dict, trace: dict | None, vocab: set[str] | None = None) -> dict:
    """算一条断言，输出可直接放进 raw.assertion_results 的字典。

    `trace=None` 表示未上报；结构不合法表示上报了但不可用。两者都判 unscored，
    分数贡献与 fail 相同，但**不计入掌握度、不产出错误模式**（任务 YAML 的核心纪律）。
    """
    if spec.get("type") not in SUPPORTED_TYPES:
        raise NotImplementedError(
            f"S3-1 只实现 {SUPPORTED_TYPES}，收到 {spec.get('type')!r}（{spec.get('id')}）。"
            "请等后续步骤实现，不要伪造结果。"
        )

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

    problem = _validate_calls(calls)
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


def evaluate_assertion(spec: dict, trace: dict | None, vocab: set[str] | None = None) -> dict:
    """分发入口。后续 S3 步骤在这里注册新类型，调用方不用改。"""
    return evaluate(spec, trace, vocab)


def _load_vocab() -> set[str]:
    import yaml

    data = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))
    return {p["pattern_id"] for p in data["patterns"]}


def load_r3_spec(task_path: str | Path = TASK_PATH) -> dict:
    import yaml

    task = yaml.safe_load(Path(task_path).read_text(encoding="utf-8"))
    return next(r for r in task["rubric_credit"] if r["id"] == "R3_retry_evidence")


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

文件：`src/arena/trace_pipeline.py` ｜ sha256[:16]=`478a413201afd581` ｜ 141 行 ｜ LF
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

    results[hits[0]] = ae.evaluate(spec, trace, vocab)
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

文件：`fixtures/tool-call-cases.json` ｜ sha256[:16]=`4ee27abe4bd34187` ｜ 115 行 ｜ LF
===== BEGIN: fixtures/tool-call-cases.json =====
{
  "_comment": "S3-2 边界 trace 用例集。这是执行器的测试底座，不是诊断报告夹具：逐条喂 assertion_evaluator.evaluate()，期望值由本文件给出。Q10-A（round-10）已把 4 个原先走 default 的 case 改为明写规则命中；新增分类规则时仍先在这里补 case，再改 YAML。expect.source 取值 case / default / null；null 表示该状态不产出分类来源。",
  "cases": [
    {
      "name": "true_retry",
      "note": "标准真重试：500 后同参再拉一次成功",
      "expect": {"state": "pass", "pattern": null, "source": null, "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "page 2"}
      ]
    },
    {
      "name": "interleaved",
      "note": "两次 offset=10 之间穿插 write_report；allow_interleaved=true 应判过",
      "expect": {"state": "pass", "pattern": null, "source": null, "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "write_report", "params": {"path": "/workspace/report.json", "content": "{}"}, "status": "success", "result_summary": "written"},
        {"call_index": 4, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "page 2"}
      ]
    },
    {
      "name": "fake_retry",
      "note": "失败后退回首页：offset 0,10,0",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1 again"}
      ]
    },
    {
      "name": "no_retry",
      "note": "失败后完全没有后续同工具调用",
      "expect": {"state": "fail", "pattern": "no_retry", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"}
      ]
    },
    {
      "name": "retry_exhausted",
      "note": "同参连续重试仍失败",
      "expect": {"state": "fail", "pattern": "retry_exhausted", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"}
      ]
    },
    {
      "name": "zero_calls",
      "note": "Q10-A：零调用，命中 no_tool_calls 明写规则（归 no_retry）",
      "expect": {"state": "fail", "pattern": "no_retry", "source": "case", "reason_code": null},
      "tool_calls": []
    },
    {
      "name": "only_first_page",
      "note": "Q10-A：只拉首页，故障点未触达 → fault_point_not_reached",
      "expect": {"state": "fail", "pattern": "no_retry", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"}
      ]
    },
    {
      "name": "skips_page_10",
      "note": "Q10-A：分页步长跳过 10，故障点未触达 → fault_point_not_reached",
      "expect": {"state": "fail", "pattern": "no_retry", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 20, "limit": 10}, "status": "success", "result_summary": "empty page"}
      ]
    },
    {
      "name": "other_tool_after_failure",
      "note": "失败后只调了别的工具，probe 过滤后 offsets 仍是 [0,10]",
      "expect": {"state": "fail", "pattern": "no_retry", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "write_report", "params": {"path": "/workspace/report.json", "content": "{}"}, "status": "success", "result_summary": "written"}
      ]
    },
    {
      "name": "wrong_offset_after_failure",
      "note": "Q10-A：故障点只命中一次、后续 offset 跳到 20 → fault_point_not_repeated",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "case", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"},
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 3, "name": "fetch_orders", "params": {"offset": 20, "limit": 10}, "status": "success", "result_summary": "empty page"}
      ]
    },
    {
      "name": "shuffled_call_index",
      "note": "round-8 ① 反例：数组顺序与 call_index 相反，改造前会误判 pass",
      "expect": {"state": "unscored", "pattern": null, "source": null, "reason_code": "trace_invalid"},
      "tool_calls": [
        {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "error", "http_status": 500, "error": "internal_error"},
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "page 2"}
      ]
    },
    {
      "name": "missing_call_index",
      "note": "契约必填字段缺失：判数据不可信，不判能力不足",
      "expect": {"state": "unscored", "pattern": null, "source": null, "reason_code": "trace_invalid"},
      "tool_calls": [
        {"name": "fetch_orders", "params": {"offset": 10, "limit": 10}, "status": "success", "result_summary": "page 2"}
      ]
    }
  ]
}

===== END: fixtures/tool-call-cases.json =====

文件：`tests/test_assertion_evaluator.py` ｜ sha256[:16]=`5d9d9164ec7fe197` ｜ 348 行 ｜ LF
===== BEGIN: tests/test_assertion_evaluator.py =====
"""S3-1：R3 断言执行器的约束测试。

守的不是"四个样例判对了"，而是三件结构性事实：
1. 判定表达式与分类规则都从任务 YAML 派生，代码里没有第二份任务常量；
2. trace 缺失判 unscored（不判 0、不归因），符合任务 YAML 的归因纪律；
3. 执行器输出能直接塞进 raw.assertion_results，被 build_report() 无改组装。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402


def spec() -> dict:
    return copy.deepcopy(ae.load_r3_spec())


def call(offset: int, status: str = "success", http_status: int = 200) -> dict:
    return {
        "name": "fetch_orders",
        "params": {"offset": offset, "limit": 10},
        "status": status,
        "http_status": http_status,
    }


def trace(calls: list[dict]) -> dict:
    """补平台侧合规编号，模拟正常上报。

    要测乱序 / 缺号 / 非法 call_index，请直接构造 {"tool_calls": ...}，
    不要用这个 helper——它会自动把编号改成合规的。
    """
    return {"tool_calls": [{**c, "call_index": i + 1} for i, c in enumerate(calls)]}


TRACES = {
    "真重试": [call(0), call(10, "error", 500), call(10)],
    "真重试+穿插": [call(0), call(10, "error", 500),
                {"name": "write_report", "params": {"path": "/workspace/report.json"},
                 "status": "success", "http_status": 200}, call(10)],
    "假重试": [call(0), call(10, "error", 500), call(0)],
    "无重试": [call(0), call(10, "error", 500)],
    "重试耗尽": [call(0), call(10, "error", 500), call(10, "error", 500)],
}
EXPECTED = {
    "真重试": ("pass", None),
    "真重试+穿插": ("pass", None),
    "假重试": ("fail", "retry_wrong_params"),
    "无重试": ("fail", "no_retry"),
    "重试耗尽": ("fail", "retry_exhausted"),
}


@pytest.mark.parametrize("name", list(TRACES))
def test_r3_judgement_and_classification(name):
    """spike 的四类 + 重试耗尽，判定与分类必须同时对。"""
    r = ae.evaluate(spec(), trace(TRACES[name]))
    want_state, want_pattern = EXPECTED[name]
    assert r["state"] == want_state, name
    got = (r.get("error_patterns") or [None])[0]
    assert got == want_pattern, name
    if want_state == "fail":
        assert r["evidence"]["extra"]["classification_source"].startswith("case:"), \
            f"{name} 应命中明写规则，而不是 default 兜底"


def test_interleaving_is_handled_by_filtering():
    """穿插一条无关调用不应改变判定——这是 JMESPath 过滤法的实测结论。"""
    plain = ae.evaluate(spec(), trace(TRACES["真重试"]))
    mixed = ae.evaluate(spec(), trace(TRACES["真重试+穿插"]))
    assert plain["state"] == mixed["state"] == "pass"


def test_filter_expr_derives_from_yaml():
    """把 YAML 的 params_match.offset 改成 20，表达式必须跟着变（防硬编码 10）。"""
    s = spec()
    for step in s["expected"]["calls"]:
        step["params_match"]["offset"] = 20
    expr = ae._filter_expr(s)
    assert "params.offset==`20`" in expr and "`10`" not in expr
    # 判定也要跟着变：只有 offset=10 的 trace 不再满足 offset=20 的契约
    assert ae.evaluate(s, trace(TRACES["真重试"]))["state"] == "fail"


def test_http_status_constraint_is_enforced():
    """YAML 首步明写 http_status=500；换成 404 的"失败后重试"不能算通过。"""
    s = spec()
    bad = [call(0), call(10, "error", 404), call(10)]
    assert ae.evaluate(s, trace(bad))["state"] == "fail"


def test_classification_rules_come_from_yaml():
    """改 cases 的期望序列，分类结果必须跟着变（YAML 是唯一真相源）。"""
    s = spec()
    s["error_pattern_detection"]["cases"][0]["match"]["offsets_equals"] = [0, 10, 0]
    s["error_pattern_detection"]["cases"][0]["pattern"] = "no_retry"
    r = ae.evaluate(s, trace(TRACES["假重试"]))
    assert r["error_patterns"] == ["no_retry"]


def test_unknown_predicate_fails_loud():
    """词表新增了没实现的谓词时，宁可报错也不能静默跳过、走 default。"""
    s = spec()
    s["error_pattern_detection"]["cases"][0]["match"]["offsets_contains"] = 10
    with pytest.raises(ae.EvaluatorError, match="未知的分类谓词"):
        ae.evaluate(s, trace(TRACES["假重试"]))


@pytest.mark.parametrize("bad", [None, {}, {"tool_calls": None}, {"tool_calls": "oops"}])
def test_unusable_trace_is_unscored_not_zero(bad):
    """数据缺失 ≠ 能力不足：判 unscored，且不得产出错误模式。"""
    r = ae.evaluate(spec(), bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] in ("trace_not_reported", "trace_invalid")
    assert not r.get("error_patterns")


# --------------------------------------------------------------------------
# round-8 ①：call_index 乱序反例与前置校验
# --------------------------------------------------------------------------

SHUFFLED = [
    {"call_index": 2, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "error", "http_status": 500},
    {"call_index": 1, "name": "fetch_orders", "params": {"offset": 10, "limit": 10},
     "status": "success", "http_status": 200},
]


def test_shuffled_call_index_is_unscored_not_pass():
    """外部评审 ① 的原样反例：乱序上报绝不能判 pass，也不能重排后判 fail。"""
    r = ae.evaluate(spec(), {"tool_calls": copy.deepcopy(SHUFFLED)})
    assert r["state"] == "unscored"
    assert r["reason_code"] == "trace_invalid"
    assert not r.get("error_patterns")
    assert "call_sequence_problem" in r["evidence"]["observed"]


def test_why_we_do_not_sort_by_call_index():
    """驳回「排序预处理」处方的可执行证据。

    同一组调用排序后会判 fail + retry_wrong_params —— 那是拿不可信的数据
    断定「学生不会重试」，把数据问题记成能力不足。实现必须停在 unscored。
    """
    ordered = sorted(copy.deepcopy(SHUFFLED), key=lambda c: c["call_index"])
    r = ae.evaluate(spec(), {"tool_calls": ordered})
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["retry_wrong_params"]


@pytest.mark.parametrize("bad_calls", [
    [{"name": "fetch_orders"}],                    # 缺 call_index
    [{"call_index": 2, "name": "fetch_orders"}],   # 不从 1 起
    [{"call_index": 1}, {"call_index": 1}],        # 重复 / 不连续
    [{"call_index": 1}, {"call_index": "2"}],      # 类型非整数
    [{"call_index": True}],                        # bool 不算整数
    ["not-an-object"],                             # 元素非对象
])
def test_malformed_call_index_is_unscored(bad_calls):
    r = ae.evaluate(spec(), {"tool_calls": bad_calls})
    assert r["state"] == "unscored" and r["reason_code"] == "trace_invalid"
    assert not r.get("error_patterns")


def test_new_unscored_branch_survives_schema_validation():
    """新增的 trace_invalid 分支必须能被 build_report 组装并过 JSON Schema。"""
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    generated = ae.evaluate(spec(), {"tool_calls": copy.deepcopy(SHUFFLED)})
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    dr.validate_report(report)
    r3 = next(a for a in report["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3["state"] == "unscored"
    assert report["score"]["ratio"] == 0.75


def test_unsupported_assertion_type_raises():
    s = spec()
    s["type"] = "json_field_equals"
    with pytest.raises(NotImplementedError, match="S3-1"):
        ae.evaluate(s, trace(TRACES["真重试"]))


@pytest.mark.parametrize("mutate", ["ordered", "allow_interleaved", "params"])
def test_unsupported_contract_shape_refused(mutate):
    """契约要求本实现做不到的语义时，拒绝执行而不是给出看似合理的结果。"""
    s = spec()
    if mutate == "ordered":
        s["expected"]["ordered"] = False
    elif mutate == "allow_interleaved":
        s["expected"]["allow_interleaved"] = False
    else:
        s["expected"]["calls"][1]["params_match"]["offset"] = 20
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate(s, trace(TRACES["真重试"]))


def test_pattern_must_be_declared_and_registered():
    s = spec()
    with pytest.raises(ae.EvaluatorError, match="未登记进错误模式词表"):
        ae.evaluate(s, trace(TRACES["假重试"]), vocab=set())
    s = spec()
    s["error_patterns"] = ["no_retry"]
    with pytest.raises(ae.EvaluatorError, match="不在本断言 error_patterns"):
        ae.evaluate(s, trace(TRACES["假重试"]))


def test_default_is_observable_when_rules_miss():
    """覆盖度证据：cases 都不命中时仍走 default，并把来源写进 evidence。

    Q10-A 补了三条计数规则后，原来的 [0,10,0,0] 反例已命中明写规则，
    这里换成「故障点触达两次、但整体序列未被枚举」的 [10,0,10]（两次 error）：
    仍应落在 default —— 既证明兜底没被偷偷删掉，也证明覆盖度缺口依旧可观测。
    """
    r = ae.evaluate(spec(), trace([call(10, "error", 500), call(0), call(10, "error", 500)]))
    assert r["state"] == "fail"
    assert r["evidence"]["extra"]["classification_source"] == "default"
    assert r["error_patterns"] == ["retry_wrong_params"]


def test_zero_calls_is_classified_by_written_rule():
    """Q10-A 验收：上报了 trace 但零调用 → 命中 no_tool_calls 明写规则，不靠 default。

    round-8 时这条把「只能兜底」钉成 Q10 证据；现在缺口已由人拍板选 A 补上。
    按 A 方案仍归 no_retry（与手写夹具一致）；跨技能归因是 TD-001，不在本步做。
    """
    r = ae.evaluate(spec(), trace([]))
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["no_retry"]
    assert r["evidence"]["extra"]["classification_source"] == "case:no_tool_calls"


# --------------------------------------------------------------------------
# Q10-A：四条明写规则的回归锁（用例集里的输入/期望是唯一来源）
# --------------------------------------------------------------------------

Q10A_CASE_NAMES = ("zero_calls", "only_first_page", "skips_page_10",
                   "wrong_offset_after_failure")


def _case(name: str) -> dict:
    data = json.loads((ROOT / "fixtures" / "tool-call-cases.json").read_text(encoding="utf-8"))
    return next(c for c in data["cases"] if c["name"] == name)


@pytest.mark.parametrize("name", Q10A_CASE_NAMES)
def test_q10a_boundary_cases_are_classified_by_written_rules(name):
    """通过标准 ①：四个过去靠 default 的边界必须命中明写规则。

    显式列出名字，防止有人改用例集的名字让参数化静默漏测。
    """
    case = _case(name)
    r = ae.evaluate(spec(), {"tool_calls": case["tool_calls"]})
    assert r["state"] == case["expect"]["state"], name
    assert r["error_patterns"] == [case["expect"]["pattern"]], name
    source = r["evidence"]["extra"]["classification_source"]
    assert source.startswith("case:"), f"{name} 退回 default 兜底：{source}"
    assert case["expect"]["source"] == "case"


def test_q10a_predicates_follow_yaml_fault_point():
    """计数谓词必须由 expected.calls 派生，不能硬编码 offset=10。

    把 YAML 故障点改成 20 后，[0,20] 这组调用就「触达了故障点但没重复」，
    应命中 fault_point_not_repeated；硬编码 10 的实现会误判为 not_reached。
    """
    s = spec()
    for step in s["expected"]["calls"]:
        step["params_match"]["offset"] = 20
    r = ae.evaluate(s, trace([call(0), call(20, "error", 500)]))
    assert "fault_point_not_repeated" in r["evidence"]["extra"]["classification_source"]


def test_count_predicates_reject_silent_misuse():
    """YAML 把布尔谓词写成 false 必须报错，不能静默变成永不命中又退回 default。"""
    s = spec()
    for c in s["error_pattern_detection"]["cases"]:
        if "no_tool_calls" in (c.get("match") or {}):
            c["match"]["no_tool_calls"] = False
    with pytest.raises(ae.EvaluatorError, match="只接受 true"):
        ae.evaluate(s, trace([]))


def test_output_feeds_straight_into_dto():
    """端到端：执行器输出替换 raw 里的 R3，build_report() 必须无改组装。"""
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    generated = ae.evaluate(spec(), trace(TRACES["真重试"]))
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    r3 = next(a for a in report["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3["state"] == generated["state"] == "pass"
    assert report["score"]["ratio"] == 1.0

    # 换成假重试：DTO 应给出 fail + 错误模式，且分数下降
    raw2 = copy.deepcopy(raw)
    bad = ae.evaluate(spec(), trace(TRACES["假重试"]))
    raw2["assertion_results"] = [
        bad if item["id"] == "R3_retry_evidence" else item
        for item in raw2["assertion_results"]
    ]
    report2 = dr.build_report(task, raw2)
    r3b = next(a for a in report2["assertions"] if a["id"] == "R3_retry_evidence")
    assert r3b["state"] == "fail" and r3b["error_patterns"] == ["retry_wrong_params"]
    assert report2["score"]["ratio"] == 0.75


def test_generated_result_survives_schema_validation():
    """执行器的 evidence 结构要通过 JSON Schema，不能只让 build_report 内部自洽。"""
    generated = ae.evaluate(spec(), trace(TRACES["假重试"]))
    raw = json.loads((ROOT / "fixtures" / "raw-perfect.json").read_text(encoding="utf-8"))
    raw["assertion_results"] = [
        generated if item["id"] == "R3_retry_evidence" else item
        for item in raw["assertion_results"]
    ]
    task = dr.load_task(ROOT / "tasks" / "dbg-001-order-report.yaml")
    report = dr.build_report(task, raw)
    dr.validate_report(report)


def test_r3_yaml_contract_is_what_evaluator_reads():
    """YAML 里的 probe / cases / default 必须齐全，否则执行器会拒绝生成。"""
    s = spec()
    assert s["type"] in ae.SUPPORTED_TYPES
    assert s["error_pattern_detection"]["probe"]
    assert s["error_pattern_detection"]["cases"]
    assert s["error_pattern_detection"]["default"] in s["error_patterns"]
    task = yaml.safe_load((ROOT / "tasks" / "dbg-001-order-report.yaml").read_text(encoding="utf-8"))
    assert "http_status" in task["trace_requirements"]["minimum"]["tool_calls"]["item_fields"]

===== END: tests/test_assertion_evaluator.py =====

文件：`tests/test_trace_pipeline.py` ｜ sha256[:16]=`7961885a92872565` ｜ 216 行 ｜ LF
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
    got_source = None if source is None else ("default" if source == "default" else "case")
    assert r["state"] == e["state"], case["name"]
    assert got_pattern == e["pattern"], case["name"]
    assert got_source == e["source"], case["name"]
    assert r.get("reason_code") == e["reason_code"], case["name"]


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
}


def test_case_set_covers_required_names():
    """参数化测试的盲区：cases 被清空时参数化为 0 条，反而全绿。

    显式锁住名字集合，保证 Q10 改分类器时这套底座不会悄悄缩水。
    """
    names = {c["name"] for c in tp.load_cases()}
    assert REQUIRED_CASES <= names, f"边界用例集缺：{sorted(REQUIRED_CASES - names)}"

===== END: tests/test_trace_pipeline.py =====
---

## 5. 待判定命题（中性清单，**不是结论**）

> 每条请按 §0.4 产出「核证 + 依据 + 最小复现」。给不出验证方式的，标「假设」。

**T1 · 名称与实现是否一致**
`no_tool_calls` 的判据写作 `call_count == 0`，而 `call_count = len(offsets)`，`offsets` 来自 `probe`
（`calls[?name=='fetch_orders'].params.offset`），**不是** `len(trace.tool_calls)`。
请判定：当 trace 只含一个别的工具（如 `write_report`）调用时，是否仍会被分成 `case:no_tool_calls` / `no_retry`；
这与谓词名称及 §2 里「零调用」的措辞是否冲突；若冲突，属于命名失真还是分类错误。
验证：给出 1 条最小 trace 与 `evaluate()` 的 `state` / `error_patterns` / `classification_source`，并指明期望值取自哪条契约。

**T2 · 分类正确性是否只靠 YAML 行序**
`fault_point_not_repeated`（`fault_call_count < 2`）对零调用、只拉首页、跳过故障点**同样成立**。
请判定：4 个边界 case 的正确分类是否唯一由「前两条新增 case 排在它前面」保证；
删掉前两条、或把这条提到最前，哪些 case 会静默改判成什么。
验证：给出两条字节级变异（注意任务 YAML 是 **CRLF**）及各自预期的 `classification_source`。

**T3 · 是否真的没有第二份任务常量**
被检验方声称「故障点是 10 还是 20 由 YAML 决定，代码不写死」。
请在 §4 代码里找出所有字面量 `10` / `fetch_orders` / `offset`，逐一判定属于「从 YAML 派生」还是「硬编码常量」。
验证：把 YAML 的 offset 改成 20，`--demo` 哪几行应变化、哪几行不该变。

**T4 · default 是否还活着、能否被误读**
`default` 保留，但 12 个用例的 `expect.source` 里 `default` 已归零。
请判定：是否存在一条合法 trace 仍能触发 `default`；「12 个 case 全绿」能否被误读成「分类已完备」。
验证：给出一条未命中 6 条 `cases` 的 offsets 序列及其 `classification_source`。

**T5 · 配置错误会不会伪装成证据不可信**
布尔谓词写 `false` 时按契约抛 `EvaluatorError`。请追一遍 `evaluate()` 调用链，
判定该异常是冒泡，还是被某层捕获后转成 `unscored` / `trace_invalid`；若是后者，「规则配置写错」会不会被记成「trace 不可信」。
验证：给出最小输入、预期最终 `state` 与 `reason_code`。

**T6 · 变异是否覆盖关键分支 / 有无测试盲区**
§3 声称三组变异全部真 `FAILED`。请判定这三组是否覆盖新增逻辑的全部关键分支；
构造至少一条**新的**变异（改坏谓词、顺序或计数），预测它让哪条测试变红；
若你预测「仍全绿」，说明这就是测试盲区，并给出补测用例。

**T7 · 「差异消失」是不是巧合**
`raw-empty` 的手写 R3 与现算一致（`no_retry`）被当作「Q10 缺口已修复」的证据。
请判定：这是否只是两份手写数据恰好取了同一个值；若手写基线当初写错，现有测试是否会发现。
验证：指出全链路里**唯一**由执行器现算的断言是哪条，以及其余四条的结果来源。

**T8 · 范围外核查（只登记，不计入 Q10-A 缺陷数）**
确认 Q12(a) 的 `status ∈ status_enum`、`params` 是 dict 校验**尚未**实现；
说明 `status="sucess"`、`params=None` 时当前执行器给出什么 `state`，与契约期望（`unscored` / `trace_invalid`）差在哪。
另确认 Q13(a) 的 `artifacts` 字段是否真的不在五份 `raw-*.json` 中（若需要这些夹具，向用户索取）。

---

## 6. 指纹表与用户侧自检

> **给有仓库的人（用户）**：外发前请在仓库根跑一次下面的自检，确认 §4 的 8 个块与磁盘文件逐字一致。
> **给评审 AI**：你无法访问源文件；这些指纹只能证明「包没被改过」需要用户代跑，不能自己验真。

| 文件 | sha256[:16] | 行数 | 行尾 |
|---|---|---|---|
| `src/arena/assertion_evaluator.py` | `0393c87e71758ef2` | 431 | LF |
| `src/arena/trace_pipeline.py` | `478a413201afd581` | 141 | LF |
| `src/arena/diagnosis_report.py` | `8bb92e1188d8eb56` | 342 | CRLF |
| `tasks/dbg-001-order-report.yaml` | `d744837b44548563` | 273 | CRLF |
| `docs/error-patterns-v0.yaml` | `ed09b3e5cd7cff11` | 105 | LF |
| `fixtures/tool-call-cases.json` | `4ee27abe4bd34187` | 115 | LF |
| `tests/test_assertion_evaluator.py` | `5d9d9164ec7fe197` | 348 | LF |
| `tests/test_trace_pipeline.py` | `7961885a92872565` | 216 | LF |

自检命令（cwd = 仓库根；用装了 pytest / yaml 的解释器）：

```bash
python - <<'PYEOF'
import hashlib, pathlib
EXPECTED = {"src/arena/assertion_evaluator.py": "0393c87e71758ef2", "src/arena/trace_pipeline.py": "478a413201afd581", "src/arena/diagnosis_report.py": "8bb92e1188d8eb56", "tasks/dbg-001-order-report.yaml": "d744837b44548563", "docs/error-patterns-v0.yaml": "ed09b3e5cd7cff11", "fixtures/tool-call-cases.json": "4ee27abe4bd34187", "tests/test_assertion_evaluator.py": "5d9d9164ec7fe197", "tests/test_trace_pipeline.py": "7961885a92872565"}
data = pathlib.Path('docs/verify-brief-s3-3-1.md').read_bytes()
ok = True
for path, want in EXPECTED.items():
    b = ('===== BEGIN: ' + path + ' =====\n').encode()
    e = ('===== END: ' + path + ' =====\n').encode()
    if data.count(b) != 1 or data.count(e) != 1:
        print('MARKER_ERROR', path, data.count(b), data.count(e)); ok = False; continue
    body = data.split(b)[1].split(e)[0]
    raw = body[:-1]
    got = hashlib.sha256(raw).hexdigest()[:16]
    print(('OK   ' if got == want else 'MISMATCH '), path, got, want)
    ok = ok and got == want
print('SELFCHECK:', 'PASS' if ok else 'FAIL')
PYEOF
```
---

## 7. 边界 / 匿名 / 已知未修

- **未纳入本包**：`fixtures/raw-*.json`（五份夹具）、`docs/diagnosis-report-schema.md`、
  `docs/executor-semantics.md`、`src/arena/review_packet.py`、`docs/review-packet.md`、
  `tests/test_review_packet.py`。本步未改动它们；若 T7 / T8 需要，请向用户索取。
- **已过期的历史快照**：`docs/redteam-brief-round8.md` 内联的是 Q10-A 之前的代码与用例集，
  头部已标「勿当现役契约」。**不要**拿它当依据。
- **已知未修（本步范围外，勿重复报）**：Q12(a) 的 `status` 枚举 / `params` 类型校验（排 S3-3-2）；
  Q13(a) 的 `artifacts` 字段（排 S3-3-3）；TD-001 跨技能归因；TD-002 文档状态不一致。
- **仓库状态**：HEAD `03cf8c9`，全部改动尚未 commit / push；生成前工作区 23 项改动，
  本包生成后为 24 项（本包自身是新增未跟踪文件）。
- **匿名与保密**：不索取真名 / 校名 / 绝对路径；API key 与 PAT 绝不进入对话或文件。
  本包内所有路径均为仓库相对路径。
