# 红队委托包 · round-8（自包含，上传/粘贴这一个文件即可）

> 生成时间：2026-11-07T20:13:35+08:00 ｜ 仓库 HEAD：`03cf8c9` ｜ 工作区有未提交改动，本包按**当前磁盘内容**抽取
> 内联正文保留源文件原始行尾（`tasks/dbg-001-order-report.yaml` 为 CRLF，其余为 LF）；指纹按**磁盘原始字节**计算，行尾规范化不影响代码语义。
> 本包由脚本从工作区**逐字抽取**代码与契约，不是手抄；文件指纹与校验方法见 §9。
>
> ⚠️ **历史快照，已过期（2026-11-07）**：Q10-A 落地后，本包逐字内联的
> `src/arena/assertion_evaluator.py`、`tasks/dbg-001-order-report.yaml`、
> `fixtures/tool-call-cases.json` 已与现役仓库不一致 —— 附录里标「Q10 缺口、走 default」
> 的四条边界 case 现在由明写规则命中，§9 的文件指纹也不再匹配。
> **本包仅供 round-8 留档，不得当作现役契约**；要开新一轮红队请重新生成。
> 现役状态见 `docs/review-response-v1.md` §13。

## 0. 分工（先读这段）

- 你（红队）**没有文件系统访问权，也不需要**。附录 A / B 是目标代码与只读契约的逐字原文，
  附录 C 是已有用例名，§5 是当前基线输出。读完这个包就可以开工，不需要向委托方要路径。
- 你**只写攻击用例**，不改产品代码、不要求运行环境、不需要跑 pytest。
  本地执行与结果回传由委托方负责。
- 你的产物：一个 `tests/test_redteam_round8.py`，或一个纯 JSON case 集（字段见 §4）。
- 你在攻击的是**一份快照**。若代码已变更，§9 的指纹会不匹配，请声明「基于哪个指纹」。

## 1. 唯一需要约定的「路径」：cwd = 仓库根目录

不需要绝对路径、盘符或用户名。所有路径都是**仓库内相对路径**。
环境：Python 3.11+，已装 `jmespath==1.1.0`、`PyYAML==6.0.3`。

```python
import sys; sys.path.insert(0, "src")
from arena import assertion_evaluator as ae
r = ae.evaluate(ae.load_r3_spec(), {"tool_calls": [...]})
print(r["state"], r.get("error_patterns"), r["evidence"].get("extra"))
```

主目标 `src/arena/assertion_evaluator.py`：`_filter_expr` / `_judge` / `_match_step` / `_validate_calls`。
次目标 `src/arena/trace_pipeline.py`：`recompute_r3` 的真相源一致性。

## 2. 必须知道的最小信息集

| 类别 | 内容 | 为什么必需 | 位置 |
|---|---|---|---|
| 目标代码全文 | 两个执行器模块 | 不看实现写不出针对性反例 | 附录 A |
| 判定规则来源 | 任务 YAML（R3 的 `expected.calls` / `trace_requirements` / `error_pattern_detection`） | 过滤表达式与分类规则**都从 YAML 派生**，硬编码 `offset=10` 之类的假设全是错的 | 附录 B |
| 错误模式词表 | `error-patterns-v0.yaml` | 分类输出是否合法受它约束 | 附录 B |
| 已覆盖 case | `tool-call-cases.json` 12 条 | 避免交重复 case | 附录 B |
| 已收集用例名 | 94 条 | 同上 | 附录 C |
| 已拍板语义 | 四态 / 不排序 / unscored 不归因 | 防止把设计当成漏洞 | §3 |
| 环境 + 复现入口 | cwd、依赖、3 行代码 | 保证产物能直接跑 | §1 |
| 当前基线输出 | `--demo` / `--fixture all` | 用来对照「实际行为」而非「我以为的行为」 | §5 |
| 版本锚点 | HEAD + 每个文件 sha256 前 16 位 | 防止攻击旧版本 | §9 |

**不需要 / 不该给的**：绝对路径（对方读不到，且含本机标识）、整个仓库
（会把 S2 已冻结的语义卷进来，稀释目标）、任何凭据、真名 / 校名。

**可读不可改**：附录 B 的 YAML 与 fixtures 是**题目定义**，改它等于改题，不算攻击成功。
也不要攻击 `diagnosis_report.py`（S2 语义已冻结）或 `review_packet.py`（文档生成器）。

## 3. 已拍板语义 —— 以下不是漏洞，提了算误报

1. 四态 `pass / fail / blocked / unscored`；`unscored` 分数贡献 0、**不计掌握度、不产出错误模式**。
2. trace 缺失 → `unscored / trace_not_reported`；结构非法 → `unscored / trace_invalid`。
   **不是 fail，也不是「判 0 分」**——改的是归因，不是分数。
3. `call_index` 乱序 / 缺失 / 为 bool / 不从 1 连续 → `unscored / trace_invalid`。
   **刻意不按 call_index 排序**：编号与数组顺序不符说明上报数据不可信，
   重排后判 `fail` 会把数据问题记成「学习者不会重试」，污染掌握度。
   把「排序后应判 fail」当漏洞提 = 误报（仓库有回归锁 `test_why_we_do_not_sort_by_call_index`）。
4. `trace_requirements.minimum.tool_calls.item_fields` 里**没有时间戳字段**；
   早期决策写过「时间戳递增」，已登记为无契约支撑的过度声称，不要拿它当漏洞。
5. Q10（default 兜底把「零调用 / 只拉首页 / 跳过故障点」全归成 `retry_wrong_params`）
   **已知且正在等人拍板 A/B**。别作为新发现重复提交；可以提语义观察，不要要求现在修。

## 4. 攻击命题（每条都要给可复现证据）

| # | 命题 | 重点 |
|---|---|---|
| 1 | 假阳性：本该 `fail` 却 `pass` | 同一调用能否被两步复用、`params_match` 子集匹配、`http_status` 约束能否绕过、穿插调用 |
| 2 | 假阴性：本该 `pass` 却 `fail` | 合法分页 / 合法穿插 / `limit` 变化 / 多余字段 |
| 3 | 绕过 `unscored` | `_validate_calls` 是否放过不可信数据：float 整数值、`-0`、超大 int、`params` 非 dict、额外字段 |
| 4 | 真相源冲突 | `trace` 与 `trace_audit` 的组合，能否造出「不可信 trace 却拿满分」 |

每条必须给：**具体 `tool_calls` JSON + 期望 `state` + 实际 `state` + 命中的函数**。
攻击成功 = 可复现；不接受「可能有漏洞」的看法。

交付格式（二选一）：
- `tests/test_redteam_round8.py`，每条用例带 `expect` 与一句说明攻击目标；
- 纯 JSON：`[{"name": ..., "tool_calls": [...], "expect": {"state": ..., "pattern": ..., "source": ..., "reason_code": ...}}]`。

## 5. 当前基线输出（用来判断你写的是不是新东西）

`python src/arena/assertion_evaluator.py --demo`

```text
derived filter: calls[?(name==`"fetch_orders"` && params.offset==`10`)].{s: status, h: http_status}
probe          : calls[?name=='fetch_orders'].params.offset

trace         state    pattern                 source                                  offsets
真重试           pass     -                       -                                       [0, 10, 10]
真重试+穿插        pass     -                       -                                       [0, 10, 10]
假重试           fail     retry_wrong_params      case:offsets_equals                     [0, 10, 0]
无重试           fail     no_retry                case:offsets_equals                     [0, 10]
重试耗尽          fail     retry_exhausted         case:last_status,offsets_starts_with    [0, 10, 10]
乱序上报          unscored -                       trace_invalid                           -
未上报 trace     unscored -                       trace_not_reported                      -
```

`python src/arena/trace_pipeline.py --fixture all`

```text
OK raw-empty        ratio=0.0    R3=fail      patterns=['retry_wrong_params']
OK raw-no-file      ratio=0.0    R3=pass      patterns=[]
OK raw-fake-retry   ratio=0.25   R3=fail      patterns=['retry_wrong_params']
OK raw-no-trace     ratio=0.5    R3=unscored  patterns=[]
OK raw-perfect      ratio=1.0    R3=pass      patterns=[]
```

---

## 6. 附录 A：目标代码全文（逐字）

<!-- FILE: src/arena/assertion_evaluator.py | sha256=80ba5486ff390911 -->
```python
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
    python src/arena/assertion_evaluator.py --demo     # 打印六类 trace 的判定与分类
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

_PREDICATES = ("offsets_equals", "offsets_starts_with", "last_status")


def _predicate_holds(key: str, want: Any, ctx: dict) -> bool:
    offsets = ctx["offsets"]
    if key == "offsets_equals":
        return offsets == want
    if key == "offsets_starts_with":
        return offsets[: len(want)] == want
    if key == "last_status":
        return ctx["last_status"] == want
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

    ctx = {"offsets": offsets, "last_status": statuses[-1] if statuses else None}
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
# --demo：四类 trace 的现场对照表，供人工十分钟核验
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
    ap.add_argument("--demo", action="store_true", help="打印四类 trace 的判定/分类对照表")
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
```
<!-- END FILE -->

<!-- FILE: src/arena/trace_pipeline.py | sha256=478a413201afd581 -->
```python
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
```
<!-- END FILE -->


---

## 7. 附录 B：只读契约全文（逐字，可读不可改）

<!-- FILE: tasks/dbg-001-order-report.yaml | sha256=f90374a57d6e0bae -->
```yaml
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
      default: retry_wrong_params
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
```
<!-- END FILE -->

<!-- FILE: docs/error-patterns-v0.yaml | sha256=ed09b3e5cd7cff11 -->
```yaml
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
```
<!-- END FILE -->

<!-- FILE: fixtures/tool-call-cases.json | sha256=57828bb6400433ed -->
```json
{
  "_comment": "S3-2 边界 trace 用例集。这是执行器的测试底座，不是诊断报告夹具：逐条喂 assertion_evaluator.evaluate()，期望值由本文件给出。后续 Q10 改分类谓词时，先在这里补 case，再改 YAML。expect.source 取值 case / default / null；null 表示该状态不产出分类来源。",
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
      "note": "Q10 缺口：零调用，只能靠 default 兜底",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "default", "reason_code": null},
      "tool_calls": []
    },
    {
      "name": "only_first_page",
      "note": "Q10 缺口：只拉首页，从没触发故障点",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "default", "reason_code": null},
      "tool_calls": [
        {"call_index": 1, "name": "fetch_orders", "params": {"offset": 0, "limit": 10}, "status": "success", "result_summary": "page 1"}
      ]
    },
    {
      "name": "skips_page_10",
      "note": "Q10 缺口：分页步长跳过 10，故障点没触发",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "default", "reason_code": null},
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
      "note": "Q10 缺口：确实重试了但 offset 跳到 20，规则覆盖不到",
      "expect": {"state": "fail", "pattern": "retry_wrong_params", "source": "default", "reason_code": null},
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
```
<!-- END FILE -->


---

## 8. 附录 C：已收集用例名（这些已覆盖，别重复交）

```text
tests/test_assertion_evaluator.py::test_r3_judgement_and_classification[\u771f\u91cd\u8bd5]
tests/test_assertion_evaluator.py::test_r3_judgement_and_classification[\u771f\u91cd\u8bd5+\u7a7f\u63d2]
tests/test_assertion_evaluator.py::test_r3_judgement_and_classification[\u5047\u91cd\u8bd5]
tests/test_assertion_evaluator.py::test_r3_judgement_and_classification[\u65e0\u91cd\u8bd5]
tests/test_assertion_evaluator.py::test_r3_judgement_and_classification[\u91cd\u8bd5\u8017\u5c3d]
tests/test_assertion_evaluator.py::test_interleaving_is_handled_by_filtering
tests/test_assertion_evaluator.py::test_filter_expr_derives_from_yaml
tests/test_assertion_evaluator.py::test_http_status_constraint_is_enforced
tests/test_assertion_evaluator.py::test_classification_rules_come_from_yaml
tests/test_assertion_evaluator.py::test_unknown_predicate_fails_loud
tests/test_assertion_evaluator.py::test_unusable_trace_is_unscored_not_zero[None]
tests/test_assertion_evaluator.py::test_unusable_trace_is_unscored_not_zero[bad1]
tests/test_assertion_evaluator.py::test_unusable_trace_is_unscored_not_zero[bad2]
tests/test_assertion_evaluator.py::test_unusable_trace_is_unscored_not_zero[bad3]
tests/test_assertion_evaluator.py::test_shuffled_call_index_is_unscored_not_pass
tests/test_assertion_evaluator.py::test_why_we_do_not_sort_by_call_index
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls0]
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls1]
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls2]
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls3]
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls4]
tests/test_assertion_evaluator.py::test_malformed_call_index_is_unscored[bad_calls5]
tests/test_assertion_evaluator.py::test_new_unscored_branch_survives_schema_validation
tests/test_assertion_evaluator.py::test_unsupported_assertion_type_raises
tests/test_assertion_evaluator.py::test_unsupported_contract_shape_refused[ordered]
tests/test_assertion_evaluator.py::test_unsupported_contract_shape_refused[allow_interleaved]
tests/test_assertion_evaluator.py::test_unsupported_contract_shape_refused[params]
tests/test_assertion_evaluator.py::test_pattern_must_be_declared_and_registered
tests/test_assertion_evaluator.py::test_default_is_observable_when_rules_miss
tests/test_assertion_evaluator.py::test_empty_but_valid_trace_exposes_vocabulary_gap
tests/test_assertion_evaluator.py::test_output_feeds_straight_into_dto
tests/test_assertion_evaluator.py::test_generated_result_survives_schema_validation
tests/test_assertion_evaluator.py::test_r3_yaml_contract_is_what_evaluator_reads
tests/test_trace_pipeline.py::test_every_fixture_declares_trace_explicitly[raw-empty]
tests/test_trace_pipeline.py::test_every_fixture_declares_trace_explicitly[raw-no-file]
tests/test_trace_pipeline.py::test_every_fixture_declares_trace_explicitly[raw-fake-retry]
tests/test_trace_pipeline.py::test_every_fixture_declares_trace_explicitly[raw-no-trace]
tests/test_trace_pipeline.py::test_every_fixture_declares_trace_explicitly[raw-perfect]
tests/test_trace_pipeline.py::test_trace_matches_audit[raw-empty]
tests/test_trace_pipeline.py::test_trace_matches_audit[raw-no-file]
tests/test_trace_pipeline.py::test_trace_matches_audit[raw-fake-retry]
tests/test_trace_pipeline.py::test_trace_matches_audit[raw-no-trace]
tests/test_trace_pipeline.py::test_trace_matches_audit[raw-perfect]
tests/test_trace_pipeline.py::test_trace_calls_satisfy_minimum_contract[raw-empty]
tests/test_trace_pipeline.py::test_trace_calls_satisfy_minimum_contract[raw-no-file]
tests/test_trace_pipeline.py::test_trace_calls_satisfy_minimum_contract[raw-fake-retry]
tests/test_trace_pipeline.py::test_trace_calls_satisfy_minimum_contract[raw-no-trace]
tests/test_trace_pipeline.py::test_trace_calls_satisfy_minimum_contract[raw-perfect]
tests/test_trace_pipeline.py::test_r3_result_is_generated_by_evaluator[raw-empty]
tests/test_trace_pipeline.py::test_r3_result_is_generated_by_evaluator[raw-no-file]
tests/test_trace_pipeline.py::test_r3_result_is_generated_by_evaluator[raw-fake-retry]
tests/test_trace_pipeline.py::test_r3_result_is_generated_by_evaluator[raw-no-trace]
tests/test_trace_pipeline.py::test_r3_result_is_generated_by_evaluator[raw-perfect]
tests/test_trace_pipeline.py::test_recompute_does_not_mutate_input
tests/test_trace_pipeline.py::test_score_order_unchanged_after_wiring
tests/test_trace_pipeline.py::test_full_pipeline_report_passes_schema[raw-empty]
tests/test_trace_pipeline.py::test_full_pipeline_report_passes_schema[raw-no-file]
tests/test_trace_pipeline.py::test_full_pipeline_report_passes_schema[raw-fake-retry]
tests/test_trace_pipeline.py::test_full_pipeline_report_passes_schema[raw-no-trace]
tests/test_trace_pipeline.py::test_full_pipeline_report_passes_schema[raw-perfect]
tests/test_trace_pipeline.py::test_no_trace_fixture_stays_unscored_end_to_end
tests/test_trace_pipeline.py::test_raw_empty_exposes_q10_gap
tests/test_trace_pipeline.py::test_inconsistent_raw_is_refused[trace_null_but_reported]
tests/test_trace_pipeline.py::test_inconsistent_raw_is_refused[trace_present_but_not_reported]
tests/test_trace_pipeline.py::test_inconsistent_raw_is_refused[drop_trace_key]
tests/test_trace_pipeline.py::test_inconsistent_raw_is_refused[trace_wrong_type]
tests/test_trace_pipeline.py::test_inconsistent_raw_is_refused[duplicate_r3]
tests/test_trace_pipeline.py::test_missing_fixture_is_refused
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[true_retry]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[interleaved]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[fake_retry]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[no_retry]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[retry_exhausted]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[zero_calls]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[only_first_page]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[skips_page_10]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[other_tool_after_failure]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[wrong_offset_after_failure]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[shuffled_call_index]
tests/test_trace_pipeline.py::test_boundary_cases_match_executor[missing_call_index]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[true_retry]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[interleaved]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[fake_retry]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[no_retry]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[retry_exhausted]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[zero_calls]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[only_first_page]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[skips_page_10]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[other_tool_after_failure]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[wrong_offset_after_failure]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[shuffled_call_index]
tests/test_trace_pipeline.py::test_boundary_cases_survive_full_dto_pipeline[missing_call_index]
tests/test_trace_pipeline.py::test_case_names_are_unique
tests/test_trace_pipeline.py::test_case_set_covers_required_names
```

## 9. 附录 D：版本锚点与校验

仓库 HEAD：`03cf8c9`（工作区另有未提交改动，本包按磁盘现状抽取）。

| 文件 | sha256 前 16 位 |
|---|---|
| `src/arena/assertion_evaluator.py` | `80ba5486ff390911` |
| `src/arena/trace_pipeline.py` | `478a413201afd581` |
| `tasks/dbg-001-order-report.yaml` | `f90374a57d6e0bae` |
| `docs/error-patterns-v0.yaml` | `ed09b3e5cd7cff11` |
| `fixtures/tool-call-cases.json` | `57828bb6400433ed` |

委托方可在本地用一条命令重算，与本表比对（不一致 = 包已过期）：

```bash
python -c "import hashlib,pathlib; [print(p, hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:16]) for p in ['src/arena/assertion_evaluator.py','src/arena/trace_pipeline.py','tasks/dbg-001-order-report.yaml','docs/error-patterns-v0.yaml','fixtures/tool-call-cases.json']]"
```

本包**不需要任何绝对路径**。如果委托方后面把文件挪了位置，只要保持仓库内相对结构，
§1 的 3 行复现入口依然成立。
