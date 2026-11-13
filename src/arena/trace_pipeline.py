"""S3-2（Q11）：把原始 trace 接进诊断报告，消除「手工伪造断言结果」这一段。

此前 `fixtures/raw-*.json` 的 `assertion_results` 全是手写评测输出，
`diagnosis_report.build_report()` 只负责组装。本模块补上中间那一步：

    raw.trace.tool_calls + raw.artifacts
      -> assertion_evaluator.evaluate_assertion()   # 五条断言逐条现算
      -> 覆盖 raw.assertion_results（手写结果一律不读）
      -> diagnosis_report.build_report()            # 唯一的组装出口
      -> validate_report()

## 边界（刻意做小，别把它当成完整评测器）

- **S3-3-6 起五条全现算**：`recompute_all()` 是主链路，`run_fixture()` 只走它。
  spec / trace 约束 / 工具声明全部从**传入的 task** 派生，不回落到默认 YAML。
- **`recompute_r3()` 是 S3-2 的兼容入口**，只现算 R3，供 71 条历史锁继续调用；
  它不在主链路上，**不经过 F1 派生对账**（TD-024 剩余旁路，§55 下一小步收敛）。
- **不改写夹具**：本模块返回深拷贝。夹具里手写 `assertion_results` 作为 S2 历史基线
  **保留在磁盘上但不再被读**，供一致性锁比对（§20.2 第 3 项）。
- **不改 build_report 的语义**：trace 强制改写、依赖折叠、两段式计分仍归它管。

## 真相源一致性

`raw.trace` 与 `raw.trace_audit` 是同一份 raw 里两份描述同一件事的数据。
round-24 拍板：`trace_audit` 是 **trace 的派生物**（不是平台独立声明），
由 `_derive_trace_audit()` 从 trace + `trace_requirements.minimum` 算出；
手写声明五个字段（`reported` / `schema_valid` / `call_count` / `final_status` /
`invalid_reasons`）任一与派生值不同即 **R1 拒绝组装**，不猜哪个对、
也不静默改写——否则又回到「读不到 trace 却给满分」的坑。

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


# ---------------------------------------------------------------------------
# F1 / TD-016（round-24 拍板：② 派生物 + R1 严格拒绝 + 检查放 recompute_all）
#
# `raw.trace_audit` 原本是手写声明，同一份 raw 里的 `trace` 是另一份描述同一
# 件事的数据。五夹具实测两者严格一致（§25.2）；不一致即数据损坏，不是合法差异。
# 因此这里从 trace + trace_requirements.minimum 派生它，冲突就在入口拒绝组装。
#
# 刻意只检查 YAML 已声明的**结构**规则：执行器的语义 `trace_invalid`
# （未知工具、probe 投影丢失等）不算结构损坏，否则会推翻已拍板的 P-B 口径。
# ---------------------------------------------------------------------------
_REASON_MISSING = "missing_required_field"
_REASON_TYPE = "field_type_invalid"


_AUDIT_REASON_BY_CODE = {
    "item_not_object": _REASON_TYPE,
    "call_index_missing": _REASON_MISSING,
    "call_index_type_invalid": _REASON_TYPE,
    "call_index_not_sequential": "call_index_not_sequential",
    "status_missing": _REASON_MISSING,
    "status_invalid": _REASON_TYPE,
    "params_type_invalid": _REASON_TYPE,
}


def _trace_audit_issues(trace: dict, requirements: dict) -> list[str]:
    """只检查 trace_requirements.minimum 已声明的结构规则，返回 schema 枚举内的原因码。

    调用序列规则来自 `ae.call_sequence_problems()`（TD-024 / round-65 Step B）；
    这里只把问题码映射成 schema 枚举内的原因码，不另写一份规则。
    语义 `trace_invalid` 有意不计入（§4 分层纪律）。
    """
    calls_req = requirements.get("tool_calls") or {}
    status_enum = calls_req.get("status_enum")
    if not isinstance(status_enum, list) or not status_enum:
        raise PipelineError("trace_requirements.minimum.tool_calls.status_enum 缺失")
    reasons: list[str] = []

    calls = trace.get("tool_calls", _MISSING)
    if calls is _MISSING:
        reasons.append(_REASON_MISSING)
    elif not isinstance(calls, list):
        reasons.append(_REASON_TYPE)
    else:
        for _, code in ae.call_sequence_problems(calls, status_enum):
            reasons.append(_AUDIT_REASON_BY_CODE[code])

    final_req = requirements.get("final_status") or {}
    final = trace.get("final_status")
    if final_req.get("required") is True and final is None:
        reasons.append(_REASON_MISSING)
    elif final is not None and final not in (final_req.get("enum") or []):
        reasons.append(_REASON_TYPE)
    return list(dict.fromkeys(reasons))


def _derive_trace_audit(trace: dict | None, requirements: dict) -> dict:
    """F1 = ②：`trace_audit` 是 trace 的派生物，报告里不再接受手写声明。"""
    if trace is None:
        return {"reported": False, "schema_valid": False,
                "invalid_reasons": ["not_reported"], "call_count": 0}
    issues = _trace_audit_issues(trace, requirements)
    calls = trace.get("tool_calls")
    derived = {
        "reported": True,
        "schema_valid": not issues,
        "invalid_reasons": issues,
        "call_count": len(calls) if isinstance(calls, list) else 0,
    }
    final_enum = (requirements.get("final_status") or {}).get("enum") or []
    final = trace.get("final_status")
    if final in final_enum:
        derived["final_status"] = final
    return derived


def _refuse_audit_conflict(supplied: dict, derived: dict) -> None:
    """Q2 = R1：任何一项现有值/缺失与派生值不一致即拒绝组装，不产出半真报告。"""
    def show(value: object) -> str:
        return "缺失" if value is _MISSING else repr(value)

    problems: list[str] = []
    for key in ("reported", "schema_valid", "call_count", "final_status"):
        want, got = derived.get(key, _MISSING), supplied.get(key, _MISSING)
        if want != got:
            problems.append(f"{key}: 手写={show(got)} 派生={show(want)}")
    want_reasons = derived.get("invalid_reasons") or []
    got_reasons = supplied.get("invalid_reasons")
    if sorted(want_reasons) != sorted(got_reasons or []):
        problems.append(f"invalid_reasons: 手写={show(got_reasons)} 派生={show(want_reasons)}")
    if problems:
        raise PipelineError(
            "trace_audit 与 trace/任务声明不一致，拒绝组装：" + "；".join(problems)
        )


def _check_raw_consistency(raw: dict) -> dict | None:
    """T11：两个入口共用的真相源冲突检查，返回 `raw.trace`（可能为 None）。

    TD-024（round-65 Step A）：此前 `recompute_r3()` / `recompute_all()` 各写一份，
    未来会漂移；合并为单一实现，规则与报错文本逐字不变。
    """
    audit = raw.get("trace_audit")
    if not isinstance(audit, dict):
        raise PipelineError("raw 缺 trace_audit：无法判断 trace 是否上报")
    trace = raw.get("trace", _MISSING)
    if trace is _MISSING:
        raise PipelineError("raw 缺 trace 字段：无法区分「未上报」与「字段漏写」")
    if trace is not None and not isinstance(trace, dict):
        raise PipelineError(f"raw.trace 类型非法：{type(trace).__name__}")
    if bool(audit.get("reported")) != (trace is not None):
        raise PipelineError(
            f"trace 与 trace_audit.reported 冲突（trace={'有' if trace is not None else '无'}, "
            f"reported={audit.get('reported')!r}）：两个真相源打架，拒绝组装"
        )
    return trace


def recompute_r3(raw: dict, spec: dict | None = None, vocab: set[str] | None = None) -> dict:
    """返回 raw 的深拷贝，其中 R3 断言结果由 `raw.trace.tool_calls` 现算。"""
    if spec is None:
        spec = ae.load_r3_spec()

    out = copy.deepcopy(raw)
    trace = _check_raw_consistency(out)

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


def recompute_all(raw: dict, task: dict | None = None,
                  vocab: set[str] | None = None) -> dict:
    """S3-3-6：五条断言全部由执行器现算，手写 `assertion_results` 只读 id 壳、不读内容。

    所有任务常量都从传入的 `task` 派生：rubric 两份数组、`trace_requirements.minimum`、
    R4 的 `schema_ref` 指向的工具表。依赖结果按 rubric 顺序累积，供 R2 的
    `depends_on` 折叠（gate 在前，R1 一定先于 R2 算出）。
    """
    if task is None:
        task = dr.load_task(TASK_PATH)
    gate, credit = dr.rubric_layers(task)
    specs = gate + credit
    requirements = (task.get("trace_requirements") or {}).get("minimum")
    vocab_set = vocab if vocab is not None else dr.load_vocabulary()

    out = copy.deepcopy(raw)
    # T11 真相源冲突检查：两个入口共用一份实现（TD-024 Step A）。
    trace = _check_raw_consistency(out)
    audit = out["trace_audit"]

    handwritten = out.get("assertion_results")
    if not isinstance(handwritten, list):
        raise PipelineError("raw.assertion_results 不是数组")
    declared = [s["id"] for s in specs]
    ids = [r.get("id") for r in handwritten if isinstance(r, dict)]
    if len(ids) != len(handwritten) or len(ids) != len(declared) or set(ids) != set(declared):
        raise PipelineError(
            f"raw.assertion_results 的 id 集合与 rubric 不一致："
            f"实际 {sorted(map(str, ids))}，应为 {sorted(declared)}"
        )

    # F1：trace_audit 改为派生值。手写声明只用于对账，对不上就在入口拒绝。
    # 兼容入口 recompute_r3() 不经过这里（TD-024 剩余旁路，§55 下一小步收敛）。
    if not isinstance(requirements, dict):
        raise PipelineError("任务缺 trace_requirements.minimum，无法派生 trace_audit")
    derived = _derive_trace_audit(trace, requirements)
    _refuse_audit_conflict(audit, derived)
    out["trace_audit"] = derived

    artifacts = out.get("artifacts")
    computed: dict[str, dict] = {}
    results: list[dict] = []
    for spec in specs:
        kwargs: dict[str, Any] = {}
        if spec["type"] == "trace_param_schema_valid":
            kwargs["schemas"] = ae.load_tool_schemas(
                spec["expected"]["schema_ref"], task=task
            )
        result = ae.evaluate_assertion(
            spec, trace, vocab_set, requirements, artifacts, computed, **kwargs
        )
        computed[spec["id"]] = result
        results.append(result)
    out["assertion_results"] = results
    return out


def run_fixture(name_or_path: str | Path, task: dict | None = None,
                vocab: set[str] | None = None) -> dict:
    """读夹具 -> 五条全现算 -> 组装报告 -> 过 JSON Schema，返回报告。"""
    path = Path(name_or_path)
    if not path.is_file():
        path = FIXTURES / f"{name_or_path}.json"
    if not path.is_file():
        raise PipelineError(f"找不到夹具：{name_or_path}")

    if task is None:
        task = dr.load_task(TASK_PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    built = recompute_all(raw, task=task, vocab=vocab)
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
