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

from .mock_tools import COVER_KEY, page_covers

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


def _filter_expr(spec: dict) -> tuple[str, int | None]:
    """由 expected.calls 派生过滤表达式，以及可选的「页覆盖」判定点。

    约束（不满足就抛错，不静默降级）：
    - `ordered` 必须为 true：本实现按子序列匹配，无序语义尚未定义；
    - `allow_interleaved` 必须为 true：过滤法天然允许穿插，若契约要求严格相邻，
      这个实现会误判，所以拒绝执行而不是给出看起来合理的结果；
    - 所有步骤的 (name, params_match) 必须一致——当前只支持"同一页重试"。

    B-31：`params_match` 允许写 `offset_covers: N`（这次请求的页区间覆盖第 N 条）。
    它**不能**塞进 JMESPath——jmespath 没有算术，`params.offset + params.limit`
    直接抛 LexerError。所以这里把它摘出来交给 Python 侧过滤，
    表达式照旧投影 offset/limit 两个字段（多带字段对 `_match_step` 无影响）。
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
    cover: int | None = None
    for k, v in match.items():
        if k == COVER_KEY:
            # 字符串会让 page_covers 比出 TypeError；配置错要在接触 trace 之前爆掉。
            if isinstance(v, bool) or not isinstance(v, int):
                raise EvaluatorError(
                    f"{spec.get('id')}: {COVER_KEY} 必须是整数（数据点下标），收到 {v!r}"
                )
            cover = v
            continue
        conds.append(f"{_param_path(k)}=={_literal(v)}")
    # i: call_index 用于定位「最后一次成功匹配的调用」；歧义检查只看它之后的候选。
    # o/l: 供 Python 侧做页覆盖判定，不参与 JMESPath 过滤条件。
    expr = ("calls[?(" + " && ".join(conds)
            + ")].{s: status, h: http_status, i: call_index,"
              " o: params.offset, l: params.limit}")
    return expr, cover


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


def call_sequence_problems(calls: list[dict], status_enum: list) -> list[tuple[int, str]]:
    """调用序列可信度检查的**单一真相源**（TD-024 / round-65 Step B）。

    返回 `(第几条, 问题码)` 列表，按上报顺序收集全部问题、不去重。
    执行器用它生成人类可读原因，F1 派生器用它生成 schema 枚举内的原因码；
    两边**只做输出格式映射**，规则不再各写一份。不排序、不重编号、不丢弃
    可疑调用——任何不一致都判数据不可信，避免把「上报数据坏」记成能力不足。

    Q12(a)：`status` 必须在 YAML 枚举内、`params` 必须是对象；少了这两项，
    JMESPath 投影会静默丢弃畸形调用。
    """
    problems: list[tuple[int, str]] = []
    for i, item in enumerate(calls, 1):
        if not isinstance(item, dict):
            problems.append((i, "item_not_object"))
            continue
        idx = item.get("call_index")
        if idx is None:
            problems.append((i, "call_index_missing"))
        elif isinstance(idx, bool) or not isinstance(idx, int):
            problems.append((i, "call_index_type_invalid"))
        elif idx != i:
            problems.append((i, "call_index_not_sequential"))
        status = item.get("status")
        if status is None:
            problems.append((i, "status_missing"))
        elif status not in status_enum:
            problems.append((i, "status_invalid"))
        if not isinstance(item.get("params"), dict):
            problems.append((i, "params_type_invalid"))
    return problems


def _validate_calls(calls: list[dict], status_enum: list) -> str | None:
    """校验调用序列可信度；返回违规原因，None 表示可用。

    规则来自 `call_sequence_problems()`；这里只把**第一条**问题映射成人类可读消息，
    不在此复刻规则（TD-024 Step B）。
    """
    problems = call_sequence_problems(calls, status_enum)
    if not problems:
        return None
    i, code = problems[0]
    item = calls[i - 1]
    if code == "item_not_object":
        return f"第 {i} 条调用不是对象（{type(item).__name__}）"
    idx = item.get("call_index")
    if code in ("call_index_missing", "call_index_type_invalid"):
        return f"第 {i} 条调用缺 call_index 或类型非整数：{idx!r}"
    if code == "call_index_not_sequential":
        return f"call_index 不连续或与上报顺序不符：第 {i} 条为 {idx}"
    if code in ("status_missing", "status_invalid"):
        return (f"第 {i} 条调用 status 非法：{item.get('status')!r}，"
                f"允许值 {list(status_enum)}")
    return (f"第 {i} 条调用 params 不是对象："
            f"{type(item.get('params')).__name__}")


def _judge(spec: dict, calls: list[dict], expr: str,
           cover: int | None = None) -> tuple[bool, list[dict], int, int]:
    """子序列匹配 expected.calls。

    `cover` 非 None 时，先只保留「页区间覆盖该数据点」的调用，再做子序列匹配。
    **过滤必须在匹配之前**：投影下标是 `_match_step` 游标的基准，事后再筛会让游标错位。

    返回 (是否通过, 投影序列, 已匹配步数, 最后命中调用的 call_index)。
    """
    projected = jmespath.search(expr, {"calls": calls}) or []
    if cover is not None:
        projected = [
            p for p in projected
            if page_covers({"offset": (p or {}).get("o"),
                            "limit": (p or {}).get("l")}, cover)
        ]
    cursor = 0
    matched = 0
    last_matched = 0
    for step in spec["expected"]["calls"]:
        hit = _match_step(projected, step, cursor)
        if hit < 0:
            return False, projected, matched, last_matched
        last_matched = projected[hit].get("i") or last_matched
        cursor = hit + 1
        matched += 1
    return True, projected, matched, last_matched


def _is_cross_tool(steps: list[dict]) -> bool:
    """`expected.calls` 里出现了不同工具 ⇒ 走跨工具匹配器。

    单纯"两个不同 name"就是判据：不需要额外的开关字段。多一个 `matcher: cross`
    之类的显式声明，只会让"写了跨工具配置但忘了开开关"变成一类新的静默错误。
    """
    return len({s.get("name") for s in steps}) > 1


def _validate_sequence_steps(spec: dict) -> None:
    """跨工具序列的加载期校验：配置错必须在接触 trace 之前爆。

    为什么不做成"缺 status 就当任意状态"：那会让一条永远为真的步骤静默通过，
    配置错的表现变成"这条断言从不失败"——比直接报错难查得多。
    """
    rid = spec.get("id")
    expected = spec.get("expected") or {}
    steps = expected.get("calls") or []
    if len(steps) < 2:
        raise EvaluatorError(f"{rid}: expected.calls 至少需要两步")
    if not expected.get("ordered"):
        raise EvaluatorError(f"{rid}: 只支持 ordered=true 的序列")
    if not expected.get("allow_interleaved"):
        raise EvaluatorError(
            f"{rid}: allow_interleaved=false 需要严格相邻匹配，本实现不支持"
        )
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not step.get("name"):
            raise EvaluatorError(f"{rid}: 第 {i} 步缺 name")
        if not step.get("status"):
            raise EvaluatorError(f"{rid}: 第 {i} 步缺 status（每步都必须锁状态）")


def _judge_cross(spec: dict, calls: list[dict]) -> tuple[bool, list[dict], int, int]:
    """跨工具子序列匹配：按 `expected.calls` 的顺序，在 trace 里依次找。

    与 `_judge` 的区别只在**候选集**：那边先用 jmespath 按 (name, params) 过滤出
    「同一页的多次尝试」，这边不预过滤——候选就是全部调用，逐步按
    (name, status, 可选 http_status, 可选 params_match) 匹配。
    返回值形状与 `_judge` 一致，所以下游 `ctx` 构造与分类逻辑可以原样复用。
    """
    steps = spec["expected"]["calls"]
    projected = [
        {
            "s": c.get("status"),
            "h": c.get("http_status"),
            "i": c.get("call_index"),
            "n": c.get("name"),
            "p": c.get("params") or {},
            "o": (c.get("params") or {}).get("offset"),
            "l": (c.get("params") or {}).get("limit"),
        }
        for c in calls
        if isinstance(c, dict)
    ]
    cursor = 0
    matched = 0
    last_matched = 0
    for step in steps:
        want = step.get("params_match") or {}
        hit = -1
        for i in range(cursor, len(projected)):
            item = projected[i]
            if item["n"] != step.get("name") or item["s"] != step.get("status"):
                continue
            if "http_status" in step and item["h"] != step["http_status"]:
                continue
            if any(item["p"].get(k) != v for k, v in want.items()):
                continue
            hit = i
            break
        if hit < 0:
            return False, projected, matched, last_matched
        last_matched = projected[hit]["i"] or last_matched
        cursor = hit + 1
        matched += 1
    return True, projected, matched, last_matched


def _ambiguous_step(calls: list[dict], spec: dict, matched: int, after: int) -> str | None:
    """失败步所需的匹配字段缺失时，无法排除「数据缺失」解释了这次失败。

    只检查 `after` 之后、与失败步同 name/status 的调用：若某个候选调用缺
    `params_match` 的键、显式置 null，或缺该步声明的 `http_status`，它就可能是
    本该命中的那一步，R3 的判据因此缺输入，不能把「数据缺字段」记成「能力不足」。
    值写错（而非缺失）不构成歧义：跳过该候选，最终仍按能力失败归因。
    """
    steps = spec["expected"]["calls"]
    if matched >= len(steps):
        return None
    step = steps[matched]
    match = step.get("params_match") or {}
    for item in calls:
        if not isinstance(item, dict) or item.get("name") != step.get("name"):
            continue
        if (item.get("call_index") or 0) <= after:
            continue
        if item.get("status") != step.get("status"):
            continue
        if "http_status" in step and step.get("http_status") is not None:
            if item.get("http_status") is None:
                return f"call_index={item.get('call_index')} 缺 http_status"
            if item["http_status"] != step["http_status"]:
                continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        missing: list[str] = []
        for k in match:
            if k == COVER_KEY:
                # 覆盖判定要 offset 与 limit 两个整数；缺任一都没法算「覆盖了没」。
                missing += [
                    n for n in ("offset", "limit")
                    if isinstance(params.get(n), bool) or not isinstance(params.get(n), int)
                ]
                continue
            if k not in params or (params[k] is None and match[k] is not None):
                missing.append(k)
        if missing:
            return f"call_index={item.get('call_index')} 缺 params.{','.join(missing)}"
    return None


# --------------------------------------------------------------------------
# 错误模式分类：YAML cases 是唯一真相源，未知谓词直接抛错
# --------------------------------------------------------------------------

# Q10-A（round-10）新增的三个语义谓词。它们的输入全部由 `expected.calls` 派生的
# 过滤表达式算出来，**不硬编码 offset=10，也不新增 pattern_id**：
#   call_count       = 目标工具的调用条数（与 probe 的 name 过滤同一 population）
#   fault_call_count = projected 的条数（即命中 name + params_match 的调用次数）
# 所以「故障点触达/重复」的定义跟着 YAML 走，YAML 一改谓词结论自动跟着变。
_COUNT_PREDICATES = {
    "no_tool_calls": lambda c: c.get("call_count", 0) == 0,
    "fault_point_not_reached": lambda c: c.get("fault_call_count", 0) == 0,
    # B-31：`fault_call_count < 2` 单独用会把「遇到故障、失败、没再试」也归进这条，
    # 那是 `no_retry` 不是 `retry_wrong_params`。拆成两条才分得清：
    #   hit_once          = 只碰过故障页一次（之后什么都没做）      ⇒ no_retry
    #   then_moved_on     = 碰过故障页一次、**那次确实失败了**、之后调了同工具但换了页
    #                        ⇒ retry_wrong_params
    # 「那次确实失败了」这个条件是必需的：只判「之后还有调用」会把正常翻页
    # （我翻下一页，不是重试）也算成换页重试。TD-013 的 [0,8,17] 夹具正是这种：
    # 故障页那次上报 success，之后翻到 17，判成 retry_wrong_params 就说不通。
    "fault_point_hit_once": lambda c: c.get("fault_call_count", 0) == 1,
    "fault_then_moved_on": (
        lambda c: c.get("fault_call_count", 0) == 1
        and c.get("fault_last_status") == "error"
        and c.get("calls_after_fault", 0) > 0
    ),
    "fault_point_not_repeated": lambda c: c.get("fault_call_count", 0) < 2,
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


def _projected_scalar(item: Any) -> Any:
    """对象投影只留一个字段时取它的值，标量投影原样返回。

    round-86（B-05b）：原来是写死的 `item.get("offset")`，等于假设"投影出来的字段一定
    叫 offset"。定位骨架要投影 `params.path`，写死会逼着任务 YAML 把路径塞进一个叫
    offset 的键里——名不符实，而且下一个人会以为它真是偏移量。
    只认"单字段对象"这一种形状：多字段对象原样返回，让匹配失败暴露成"看起来没命中"，
    而不是悄悄取了其中一个值。

    **它只进 evidence，不参与判定**：R3 的通过/失败看的是过滤后的调用序列，
    分类看的是调用次数与末次状态。所以这段改坏不会让分数变，只会让报告里的
    "读了哪几个文件"变成一串 None——那正是它必须被测试直接盯住的原因
    （`tools/mutations/mutate_locate.py` 的 M1 打的就是 `test_projected_scalar_takes_the_only_value`）。
    """
    if isinstance(item, dict) and len(item) == 1:
        return next(iter(item.values()))
    return item


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


def _json_excerpt(text: str, limit: int = 120) -> dict:
    """TD-015 / P-C=A：产物原文只留脱敏截断片段，不把整份报告灌进 evidence。

    控制字符转义成字面量，避免换行把下游日志切碎；`truncated` 与字符数让
    下游知道这只是片段，不是完整内容。
    """
    flat = (text.replace("\\", "\\\\").replace("\r", "\\r")
            .replace("\n", "\\n").replace("\t", "\\t"))
    return {"excerpt": flat[:limit], "truncated": len(flat) > limit,
            "content_chars": len(text)}


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

    # R2 假设①（S3-3-6 / P-C=A 定稿）：内容不是合法 JSON 对象时，仍按
    # 「expected 字段一个都没观测到」处理，归入既有 report_missing_field，
    # 不改词表 / DTO。TD-015：语法错 / 合法非对象 / 空白三类在 state、
    # pattern、observed 上完全坍缩，补结构化 evidence 让下游可判定。
    parsed, kind, detail = None, None, None
    if not content.strip():
        kind, detail = "empty_content", "产物内容是空串或纯空白"
    else:
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError) as exc:
            kind, detail = "syntax_error", f"{type(exc).__name__}: {exc}"
        else:
            if not isinstance(parsed, dict):
                kind = "not_json_object"
                detail = f"解析结果类型为 {type(parsed).__name__}，不是对象"
    valid_object = isinstance(parsed, dict)
    observed_obj = parsed if valid_object else {}
    parse_note = None if valid_object else (
        "报告内容不是合法 JSON 对象：按 expected 字段全部未观测到处理"
        "（R2 假设①，S3-3-6 / P-C=A 定稿；结构化区分见 evidence.extra.parse_error）"
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
    if kind is not None:
        extra["parse_error"] = {"kind": kind, "detail": detail}
        extra["raw_excerpt"] = _json_excerpt(content)
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


def load_tool_schemas(ref: str, task_path: str | Path = TASK_PATH,
                      task: dict | None = None) -> dict:
    """从任务 YAML 的 `task[ref]` 读工具声明，返回 {工具名: 声明}。

    `ref` 来自 R4 的 `expected.schema_ref`，不是代码里写死的 "tools"。
    S3-3-6：调用方可直接传已加载的 `task`（全链路拿到的就是这份），
    否则「换 task 跑」时 spec 换了、工具声明却仍从默认 YAML 读。
    """
    cfg = task if task is not None else load_task_config(task_path)
    tools = cfg.get(ref)
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
    target_tools = expected.get("target_tools")
    if not isinstance(target_tools, list) or not target_tools:
        raise EvaluatorError(
            f"{rid}: expected.target_tools 必须是非空数组（本断言考察的目标工具）"
        )
    if any(not isinstance(t, str) or not t for t in target_tools):
        raise EvaluatorError(f"{rid}: expected.target_tools 只能是非空字符串")
    if len(set(target_tools)) != len(target_tools):
        raise EvaluatorError(f"{rid}: expected.target_tools 含重复工具名")
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
    target_tools = spec["expected"]["target_tools"]
    outside = sorted(set(target_tools) - set(schemas))
    if outside:
        raise EvaluatorError(
            f"{rid}: expected.target_tools 不在 schema_ref 覆盖范围内：{outside}"
        )

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

    # 未知工具只让「无法对照 schema 的那部分」不可判：已观测到的已知工具违规
    # 是证据，不能因轨迹不完整被抹掉（F4 / TD-019a，与 F3-P1 同构）。
    if unknown_tools and not violations:
        return unscored("trace_invalid", {"unknown_tools": unknown_tools},
                        "调用含 tools 未声明的工具名：无法对照 schema，判 unscored、不归因"
                        "（假设①，S3-3-6 已拍板）")

    checked_calls = len(calls) - len(unknown_tools)
    # 全称命题不能对空集平凡为真：没有任何目标工具的受检调用时，R4 无证据可判。
    # 已观测违规优先（上一段），否则会出现「有违规却因覆盖率不足而 unscored」。
    if not violations:
        target_calls = sum(1 for c in calls if c.get("name") in target_tools)
        if target_calls == 0:
            return unscored(
                "trace_invalid",
                {"target_tools": target_tools, "target_call_count": 0,
                 "checked_calls": checked_calls},
                "无目标工具的受检调用：R4 无法判定，判 unscored、不归因；"
                "不把空集记为平凡 pass",
            )

    passed = not violations
    result: dict = {
        "id": rid,
        "state": "pass" if passed else "fail",
        "evidence": {
            "observed": {"violations": violations, "checked_calls": checked_calls},
            **base,
            "extra": {"schema_ref": schema_ref, "probe": probe},
        },
    }
    if unknown_tools:
        result["evidence"]["observed"]["unknown_tools"] = unknown_tools
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
    # 第三套骨架（tpl-ask-first）：跨工具序列的加载期校验，同样在任何 trace 之前。
    steps = (spec.get("expected") or {}).get("calls") or []
    cross_tool = _is_cross_tool(steps)
    if cross_tool:
        _validate_sequence_steps(spec)

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

    if cross_tool:
        passed, projected, matched, last_matched = _judge_cross(spec, calls)
        expr, cover = None, None
    else:
        expr, cover = _filter_expr(spec)
        passed, projected, matched, last_matched = _judge(spec, calls, expr, cover)

    detection = spec.get("error_pattern_detection") or {}
    probe = detection.get("probe")
    if not probe:
        raise EvaluatorError(f"{spec['id']}: 缺少 error_pattern_detection.probe")
    projected_offsets = jmespath.search(probe, {"calls": calls}) or []
    # TD-025：对象投影把缺 params.offset 的调用保留成 {"offset": null}，
    # 原位还原成标量序列（不丢弃、不重排、不挪末尾）；旧标量 probe 仍兼容。
    offsets = [_projected_scalar(item) for item in projected_offsets]
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
                # 哪条匹配器判的：跨工具序列没有 jmespath 过滤表达式（filter_expr=None），
                # 报告里只写 None 会让人以为是漏填。
                "matcher": "cross_tool" if cross_tool else "same_tool",
                "probe": probe,
                "matched_steps": matched,
                "total_steps": len(spec["expected"]["calls"]),
            },
        },
    }
    if passed:
        return result

    # 失败步所需的匹配字段（params_match 键 / 该步声明的 http_status）缺失时，
    # 失败可能只是「数据缺字段」，不是「不会重试」。判据缺输入 → unscored，不归因。
    # 跨工具序列不做歧义检查：`_ambiguous_step` 的判据是「失败步的匹配字段缺失」，
    # 它假设各步同参。跨工具时"字段缺失"不适用，硬套会把真实失败误判成 unscored。
    ambiguity = None if cross_tool else _ambiguous_step(calls, spec, matched, last_matched)
    if ambiguity:
        result["state"] = "unscored"
        result["reason_code"] = "trace_invalid"
        result["evidence"]["observed"]["ambiguous_match"] = ambiguity
        result["note"] = (
            "失败步的匹配字段缺失，无法排除数据缺失解释了该失败：判 unscored/trace_invalid"
        )
        return result

    # projected 由 _filter_expr(spec) 过滤得到，条件是 YAML 的 name + params_match。
    # 因此它的长度就是「故障点被触达的次数」，无需在代码里写 offset=10。
    target_name = spec["expected"]["calls"][0]["name"]
    # B-31：最后一次触达故障点之后还有没有对同一工具的调用。用来区分
    # 「失败了没再试」与「换了页重试」——两者给学习者的建议完全不同。
    last_fault_index = projected[-1].get("i") if projected else 0
    ctx = {
        "offsets": offsets,
        "last_status": statuses[-1] if statuses else None,
        "call_count": sum(
            1 for c in calls
            if isinstance(c, dict) and c.get("name") == target_name
        ),
        "fault_call_count": len(projected),
        # 故障点最后一次调用的状态。`fault_then_moved_on` 靠它区分
        # 「失败了再换页试」与「正常翻到下一页」——后者那一次是 success。
        "fault_last_status": statuses[-1] if statuses else None,
        "calls_after_fault": (
            sum(1 for c in calls
                if isinstance(c, dict) and c.get("name") == target_name
                and (c.get("call_index") or 0) > last_fault_index)
            if projected else 0
        ),
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
