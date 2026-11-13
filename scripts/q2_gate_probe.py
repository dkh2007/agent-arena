"""Q2 / Q5 探针（round-69）：把三个命题做成可复核输出，不用自评当证据。

评审包 Q2 的提示原话：「R4 是全称命题，零次调用时平凡为真——这正是改造前 33%
地板分的来源」。本脚本检验这句话在**当前实现**下还剩多少，以及「把 R4 设成门禁」
到底是不是一个配置开关。

三个命题（每个都有明确的 PASS/FAIL 判据）：

  命题 1（配置层）R4 能不能「设成门禁」？
     变体 W1：R4 整体移入 gate，保留 weight=1、on_fail 改成 disqualify
              → 期望被 rubric_layers 拒绝（门禁不允许带权重）。
     变体 W2：R4 移入 gate，去掉 weight，但 scoring.credit.total_weight 仍写 4
              → rubric_layers 放行（它不校验 total_weight），但 build_report 必拒
              （声明权重和 ≠ 实际权重和）。两层判据不同，脚本分开报，不合并。
     变体 W3：R4 移入 gate、去掉 weight、total_weight=3 → 唯一能跑通的写法。
     判据：W1 被 rubric_layers 拒、W2 被 build_report 拒、W3 两关都过，才算 PASS。
     任一不符 ⇒ 「门禁」是可随手挪的配置项，Q2 的前提要重写。

  命题 2（语义层）R4 放到门禁位置，对五份夹具的分数有没有改善？
     变体 A：现状（R4 在 credit，分母 4）。
     变体 B：R4 在 gate、无权重、total_weight=3（能通过的合法写法）。
     判据：若 B 下「无 trace」「空提交」两个夹具的 ratio 不低于 A，则「R4 设门禁」
     对这两类提交无效。只看这两行，不拿全表平均糊过去。

  命题 3（Q5 攻击面）R4 在「trace 结构合法、但零次目标工具调用」时判什么？
     直接调 evaluate_assertion，六个场景：
       none / zero_calls / write_only_skip_fetch / legal / limit_out_of_range / missing_required
     判据（**这是本脚本最想证伪的一条**）：Q2 提示说「R4 是全称命题，零次调用时平凡
     为真」。若 zero_calls 或 write_only_skip_fetch 判 pass，则该攻击面真实存在；
     若判 unscored，则代码里已有「全称命题不能对空集平凡为真」的防护，提示已过时。
     `legal` 是本脚本自己的锚点：它必须判 pass，否则说明探针构造错了，其余结论不可信。

退出码：0 = 三个判据全部如预期（PASS）；1 = 有判据不符（FAIL，需回来改文档结论）。
只读：不改任何文件、不改 fixture、不改任务 YAML，全部 deepcopy 后现算。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
FIXTURES = ROOT / "fixtures"
R4_ID = "R4_no_param_hallucination"


def _load_task() -> dict:
    return ae.load_task_config(TASK_PATH)


def _move_r4_to_gate(task: dict, *, keep_weight: bool, total_weight: int | None) -> dict:
    t = copy.deepcopy(task)
    r4 = next(r for r in t["rubric_credit"] if r["id"] == R4_ID)
    t["rubric_credit"] = [r for r in t["rubric_credit"] if r["id"] != R4_ID]
    moved = dict(r4)
    moved["on_fail"] = "disqualify"
    if not keep_weight:
        moved.pop("weight", None)
    t["rubric_gate"] = list(t["rubric_gate"]) + [moved]
    if total_weight is not None:
        t["scoring"]["score"]["credit"]["total_weight"] = total_weight
    return t


def _try_layers(task: dict) -> tuple[str, str]:
    """返回 (结果, 说明)；结果 ∈ {ACCEPTED, REJECTED}。"""
    try:
        gate, credit = dr.rubric_layers(task)
    except dr.ReportError as exc:
        return "REJECTED", str(exc)
    except Exception as exc:  # noqa: BLE001 - 非预期异常也要如实分类，不吞掉
        return "REJECTED", f"{type(exc).__name__}: {exc}"
    return "ACCEPTED", f"gate={[r['id'] for r in gate]} credit={[r['id'] for r in credit]}"


def _try_build(task: dict) -> tuple[str, str]:
    """走完整组装：rubric_layers + 权重和一致性 + schema 校验。"""
    raw = json.loads((FIXTURES / "raw-perfect.json").read_text(encoding="utf-8"))
    try:
        report = dr.build_report(task, raw)
    except dr.ReportError as exc:
        return "REJECTED", str(exc)
    except Exception as exc:  # noqa: BLE001
        return "REJECTED", f"{type(exc).__name__}: {exc}"
    return "ACCEPTED", f"ratio={report['score']['ratio']} total={report['score']['total_weight']}"


def _scores(task: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(FIXTURES.glob("raw-*.json")):
        report = dr.build_report(task, json.loads(path.read_text(encoding="utf-8")))
        score = report["score"]
        out[path.stem.replace("raw-", "")] = {
            "ratio": score["ratio"],
            "credit_pass": score["credit_pass_weight"],
            "total": score["total_weight"],
            "dq": score["disqualified"],
        }
    return out


def _call(idx: int, name: str, params: dict, status: str = "success",
          http_status: int = 200) -> dict:
    return {
        "call_index": idx,
        "name": name,
        "params": params,
        "status": status,
        "http_status": http_status,
        "error": "internal_error" if status == "error" else None,
        "result_summary": None if status == "error" else "ok",
    }


def _trace_scenarios() -> dict[str, dict | None]:
    ok_call = _call(1, "fetch_orders", {"offset": 0, "limit": 10})
    write = _call(2, "write_report", {"path": "/workspace/report.json", "content": "{}"})
    write_first = _call(1, "write_report", {"path": "/workspace/report.json", "content": "{}"})
    return {
        "none": None,
        "zero_calls": {"final_status": "completed", "tool_calls": []},
        "write_only_skip_fetch": {"final_status": "completed", "tool_calls": [write_first]},
        "legal": {"final_status": "completed", "tool_calls": [ok_call, write]},
        "limit_out_of_range": {
            "final_status": "completed",
            "tool_calls": [_call(1, "fetch_orders", {"offset": 0, "limit": 20}), write],
        },
        "missing_required": {
            "final_status": "completed",
            "tool_calls": [_call(1, "fetch_orders", {"offset": 0}), write],
        },
    }


def main() -> int:
    task = _load_task()
    checks: list[tuple[str, bool, str]] = []
    say = print

    say("=" * 78)
    say("命题 1（配置层）：R4 能不能「设成门禁」")
    say("=" * 78)
    w1 = _try_layers(_move_r4_to_gate(task, keep_weight=True, total_weight=3))
    w2 = _try_build(_move_r4_to_gate(task, keep_weight=False, total_weight=4))
    w3 = _try_build(_move_r4_to_gate(task, keep_weight=False, total_weight=3))
    for label, (verdict, detail) in (("W1 保留权重进 gate（layers）", w1),
                                     ("W2 去权重但 total_weight 仍 4（build）", w2),
                                     ("W3 去权重且 total_weight=3（build）", w3)):
        say(f"  {label:42s} -> {verdict}: {detail}")
    checks.append(("W1 门禁带权重被拒", w1[0] == "REJECTED", w1[1]))
    checks.append(("W2 声明与实际权重和不一致被拒", w2[0] == "REJECTED", w2[1]))
    checks.append(("W3 合法写法被接受", w3[0] == "ACCEPTED", w3[1]))

    say()
    say("=" * 78)
    say("命题 2（语义层）：A 现状 vs B R4→gate（分母 3）")
    say("=" * 78)
    task_b = _move_r4_to_gate(task, keep_weight=False, total_weight=3)
    a, b = _scores(task), _scores(task_b)
    say(f"  {'夹具':18s} {'A ratio':>10s} {'B ratio':>10s} {'A pass/total':>14s} {'B pass/total':>14s} {'A dq':>5s} {'B dq':>5s}")
    for name in sorted(a):
        say(f"  {name:18s} {a[name]['ratio']:>10.4f} {b[name]['ratio']:>10.4f} "
            f"{a[name]['credit_pass']:>7d}/{a[name]['total']:<6d} "
            f"{b[name]['credit_pass']:>7d}/{b[name]['total']:<6d} "
            f"{str(a[name]['dq']):>5s} {str(b[name]['dq']):>5s}")
    raised = {n: v["ratio"] > a[n]["ratio"] + 1e-9 for n, v in b.items()}
    checks.append(("B 下存在夹具 ratio 升高（挪 R4 会让部分提交虚高）", any(raised.values()),
                   f"升高={[n for n, ok in raised.items() if ok]} "
                   f"no-trace {a['no-trace']['ratio']}→{b['no-trace']['ratio']}"))

    say()
    say("=" * 78)
    say("命题 3（Q5 攻击面）：R4 在六种 trace 下判什么")
    say("=" * 78)
    r4_spec = next(r for r in task["rubric_credit"] if r["id"] == R4_ID)
    reqs = task["trace_requirements"]["minimum"]
    schemas = ae.load_tool_schemas("tools", task=task)
    results: dict[str, str] = {}
    for name, trace in _trace_scenarios().items():
        res = ae.evaluate_assertion(r4_spec, trace, requirements=reqs, schemas=schemas)
        results[name] = res["state"]
        say(f"  {name:20s} -> {res['state']:9s} "
            f"reason={res.get('reason_code') or '-'}")
        say(f"      evidence={json.dumps(res.get('evidence'), ensure_ascii=False)}")
        if res.get("note"):
            say(f"      note={res['note']}")
    checks.append(("zero_calls 判 unscored（空集不平凡为真）", results["zero_calls"] == "unscored",
                   f"state={results['zero_calls']}"))
    checks.append(("write_only_skip_fetch 判 unscored（空集不平凡为真）",
                   results["write_only_skip_fetch"] == "unscored",
                   f"state={results['write_only_skip_fetch']}"))
    checks.append(("legal 判 pass（探针自锚点，构造没写错）", results["legal"] == "pass",
                   f"state={results['legal']}"))
    checks.append(("none 判 unscored 不判 pass", results["none"] == "unscored",
                   f"state={results['none']}"))
    checks.append(("limit_out_of_range 判 fail", results["limit_out_of_range"] == "fail",
                   f"state={results['limit_out_of_range']}"))
    checks.append(("missing_required 判 fail", results["missing_required"] == "fail",
                   f"state={results['missing_required']}"))

    say()
    say("=" * 78)
    bad = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        say(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
    say("=" * 78)
    say(f"VERDICT={'PASS' if not bad else 'FAIL'}  checks={len(checks)} failed={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
