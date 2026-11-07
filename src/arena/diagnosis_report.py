"""诊断报告 DTO 的组装器（评测层出口，见 docs/diagnosis-report-schema.md）。

设计原则（三条，都是踩过的坑换来的）：
1. **只装可判定的观测，不装推断**：复习建议、掌握度都在状态层产生，进了报告就是两套口径。
2. **契约自解释**：报告自带 attribute_policy 与 denominator_policy，状态层不必回读 YAML 才知道怎么算。
3. **宁可报错也不静默降级**：A2E 的教训是「读不到 trace 就静默给满分」，所以 trace 不可用时强制
   把轨迹断言改写成 unscored，reasons 写进 trace_audit；任何不自洽直接抛 ReportError，不产出报告。

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
TRACE_TYPE_PREFIX = "trace_"
REPORT_VERSION = "v1"

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


def build_report(task: dict, raw: dict, vocab: set[str] | None = None) -> dict:
    """把「逐条断言结果 + trace 校验结果」组装成符合 schema 的诊断报告。

    raw 结构见 docs/diagnosis-report-schema.md 第 4 节（fixtures/ 下有真实例子）。
    """
    vocab = vocab if vocab is not None else load_vocabulary()
    rubric = {r["id"]: r for r in task["rubric"]}
    raw_results = raw.get("assertion_results") or []

    results: dict[str, dict] = {}
    for item in raw_results:
        rid = item.get("id")
        if rid in results:
            raise ReportError(f"断言结果重复：{rid}")
        results[rid] = item

    missing, extra = set(rubric) - set(results), set(results) - set(rubric)
    if missing or extra:
        raise ReportError(f"断言结果与 rubric 不一致：缺 {sorted(missing)}，多 {sorted(extra)}")

    trace_audit = dict(raw["trace_audit"])
    trace_ok = bool(trace_audit["reported"]) and bool(trace_audit["schema_valid"])
    notes: list[str] = []

    assertions: list[dict] = []
    for rid, spec in rubric.items():
        result = results[rid]
        state = result["state"]
        error_patterns = list(result.get("error_patterns") or [])
        reason_code = result.get("reason_code")

        # 强制改写：trace 不可用时，轨迹类断言一律 unscored——不管调用方填了什么
        if not trace_ok and str(spec["type"]).startswith(TRACE_TYPE_PREFIX):
            state = "unscored"
            error_patterns = []
            reason_code = "trace_not_reported" if not trace_audit["reported"] else "trace_invalid"

        # 依赖折叠：前置断言未通过时，本断言只能是 blocked 或 pass（pass 说明产物的另一面确实可判）
        if state == "blocked":
            deps = spec.get("depends_on") or []
            satisfied = [d for d in deps if results[d]["state"] != "pass"]
            if not satisfied:
                raise ReportError(f"{rid}: 标了 blocked，但前置 {deps} 全部通过——blocked 无依据")

        item = {
            "id": rid,
            "type": spec["type"],
            "weight": spec["weight"],
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
        assertions.append(item)

    total_weight = sum(r["weight"] for r in task["rubric"])
    declared_total = (task.get("scoring") or {}).get("score", {}).get("total_weight")
    if declared_total is not None and declared_total != total_weight:
        raise ReportError(f"YAML 声明的 total_weight={declared_total}，但断言权重之和={total_weight}")

    weighted_sum = sum(a["weight"] for a in assertions if a["state"] == "pass")
    by_state = {s: sum(1 for a in assertions if a["state"] == s) for s in STATES}

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
            "total_weight": total_weight,
            "ratio": round(weighted_sum / total_weight, 6),
            "denominator_policy": "fixed_at_total_weight",
            "by_state": by_state,
        },
        "attribution_policy": attribution_policy,
        "assertions": assertions,
        "skill_gaps": sorted(gaps.values(), key=lambda g: (-g["weight_lost"], g["skill_id"])),
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
        print(f"OK   {path.stem:<16} ratio={score['ratio']:.4f} ({score['weighted_sum']}/{score['total_weight']}) {states}")
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
