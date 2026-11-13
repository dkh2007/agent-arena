"""Equivalence probe: OLD (git baseline) vs NEW (current working tree).

Baseline is commit 0ca8492 -- the four functions compared here were NOT
touched by D5, so that commit represents exactly "after D5, before D6".
Old function sources are extracted from REAL GIT OBJECTS (not reconstructed
by hand), via ast line slicing, then executed against the current modules.

    python scripts/equivalence_probe.py

Exit 0 = every compared output identical; 1 = divergence found.

Scope note: this is branch-exhaustive at the rule level plus a full-chain
sample (five fixtures + F3 variants + structural mutations). It is direct
equivalence evidence, NOT a formal proof over the full input space.
"""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from arena import assertion_evaluator as ae
from arena import trace_pipeline as tp
from arena import diagnosis_report as dr
import test_trace_pipeline as t

BASELINE_REF = "0ca8492"
STATUS = ["success", "error", "timeout"]
REQ = {"tool_calls": {"status_enum": STATUS},
       "final_status": {"required": True, "enum": ["completed", "crashed", "timeout"]}}
TASK = dr.load_task(tp.TASK_PATH)


def old_fn(rel: str, name: str) -> str:
    p = subprocess.run(
        ["git", "show", BASELINE_REF + ":" + rel], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    lines = p.stdout.splitlines()
    for node in ast.parse(p.stdout).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise RuntimeError("baseline function not found: " + name)


old_validate_src = old_fn("src/arena/assertion_evaluator.py", "_validate_calls")
old_audit_src = old_fn("src/arena/trace_pipeline.py", "_trace_audit_issues")
old_r3_src = old_fn("src/arena/trace_pipeline.py", "recompute_r3")
old_all_src = old_fn("src/arena/trace_pipeline.py", "recompute_all")


class PipelineError(ValueError):
    pass


oldns: dict = {"PipelineError": PipelineError, "_MISSING": object(),
               "_REASON_MISSING": "missing_required_field",
               "_REASON_TYPE": "field_type_invalid"}
exec(old_validate_src, oldns)
exec(old_audit_src, oldns)
old_validate, old_audit = oldns["_validate_calls"], oldns["_trace_audit_issues"]

ORIG = {"validate": ae._validate_calls, "audit": tp._trace_audit_issues,
        "r3": tp.recompute_r3, "all": tp.recompute_all}


def use(mode: str) -> None:
    if mode == "new":
        ae._validate_calls = ORIG["validate"]
        tp._trace_audit_issues = ORIG["audit"]
        tp.recompute_r3 = ORIG["r3"]
        tp.recompute_all = ORIG["all"]
    else:
        ae._validate_calls = old_validate
        tp._trace_audit_issues = old_audit
        exec(old_r3_src, tp.__dict__)
        exec(old_all_src, tp.__dict__)


def call(i: int, **kw):
    d = {"name": "fetch_orders", "call_index": i, "status": "success",
         "params": {"offset": i * 10}}
    d.update(kw)
    return d


# L1: function level -- every rule branch, plus combinations and container edges.
traces = {
    "clean": {"tool_calls": [call(1), call(2), call(3)], "final_status": "completed"},
    "item_not_object": {"tool_calls": [call(1), "oops", call(3)], "final_status": "completed"},
    "idx_missing": {"tool_calls": [call(1, call_index=None), call(2)], "final_status": "completed"},
    "idx_bool": {"tool_calls": [call(1, call_index=True), call(2)], "final_status": "completed"},
    "idx_str": {"tool_calls": [call(1, call_index="1"), call(2)], "final_status": "completed"},
    "idx_shuffled": {"tool_calls": [call(1), call(2, call_index=9), call(3)], "final_status": "completed"},
    "status_missing": {"tool_calls": [call(1, status=None), call(2)], "final_status": "completed"},
    "status_invalid": {"tool_calls": [call(1, status="weird"), call(2)], "final_status": "completed"},
    "params_bad": {"tool_calls": [call(1, params="oops"), call(2)], "final_status": "completed"},
    "combo": {"tool_calls": [call(1, call_index=7), call(2, status="weird", params=None)], "final_status": "completed"},
    "empty": {"tool_calls": [], "final_status": "completed"},
    "all_bad": {"tool_calls": [None, "x", 3], "final_status": "completed"},
    "no_calls_key": {"final_status": "completed"},
    "calls_not_array": {"tool_calls": "oops", "final_status": "completed"},
    "final_missing": {"tool_calls": [call(1)]},
    "final_invalid": {"tool_calls": [call(1)], "final_status": "bogus"},
}

l1 = []
for name, tr in traces.items():
    calls = tr.get("tool_calls")
    if isinstance(calls, list):
        l1.append(("validate", name, old_validate(calls, STATUS),
                   ORIG["validate"](calls, STATUS)))
    l1.append(("audit", name, old_audit(tr, REQ), ORIG["audit"](tr, REQ)))
l1_diffs = [x for x in l1 if x[2] != x[3]]
print("BASELINE_REF=", BASELINE_REF)
print("L1_CHECKS=", len(l1), "L1_DIFFS=", len(l1_diffs))
for x in l1_diffs[:5]:
    print("L1_DIFF", x[0], x[1], ascii(x[2])[:90], ascii(x[3])[:90])

# L2: full chain -- five fixtures + F3 variants + structural mutations.
raws = {}
for n in tp.FIXTURE_NAMES:
    raws["fixture_" + n] = json.loads(
        (ROOT / "fixtures" / (n + ".json")).read_text(encoding="utf-8"))
variants = ["_f3_all_hallucinated", "_f3_only_write_report",
            "_f3_partial_hallucinated", "_f3_drop_http_status",
            "_f3_wrong_http_status", "_f3_null_error_http_status",
            "_f3_limit_out_of_range", "_f3_null_offset_first",
            "_f3_delete_offset_first"]
for v in variants:
    fn = getattr(t, v, None)
    if fn is not None:
        raws["f3_" + v] = t._f3_raw(fn)
raws["f3_none"] = t._f3_raw(None)
for s in ["_bad_index", "_bad_status", "_bad_params", "_no_final", "_calls_not_array"]:
    fn = getattr(t, s, None)
    if fn is not None:
        d = copy.deepcopy(raws["fixture_raw-perfect"])
        fn(d)
        raws["struct_" + s] = d


def summarize(raw: dict) -> dict:
    try:
        rep = dr.build_report(TASK, tp.recompute_all(raw, task=TASK))
        dr.validate_report(rep)
        return {"ok": True,
                "schema_valid": rep["trace_audit"]["schema_valid"],
                "invalid_reasons": rep["trace_audit"]["invalid_reasons"],
                "call_count": rep["trace_audit"]["call_count"],
                "ratio": rep["score"]["ratio"],
                "assertions": [(a["id"], a["state"], a.get("reason_code"),
                                a.get("error_patterns")) for a in rep["assertions"]]}
    except Exception as e:
        return {"ok": False, "exc": type(e).__name__, "msg_len": len(str(e))}


use("new")
new_res = {k: summarize(v) for k, v in raws.items()}
use("old")
old_res = {k: summarize(v) for k, v in raws.items()}
use("new")
l2_diffs = [(k, old_res[k], new_res[k]) for k in raws if old_res[k] != new_res[k]]
print("L2_CASES=", len(raws), "L2_DIFFS=", len(l2_diffs))
for k, a, b in l2_diffs[:5]:
    print("L2_DIFF", k, ascii(a)[:140], ascii(b)[:140])
match = not l1_diffs and not l2_diffs
print("FINAL_MATCH=", match)
sys.exit(0 if match else 1)
