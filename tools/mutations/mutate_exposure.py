"""B-04g「这次改过的那条规则，谁真的用到了」的变异检验（round-99）。

判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：

    DETECTED  变异后 pytest 退出码 **1** —— 真被抓住
    MISSED    变异后仍全绿 —— 这条测试没在管这事
    INVALID   锚点没命中 / 收集失败 —— **不算抓住**

## 这一层的失效方式

分档错了会让"要交代的人"多一个或少一个；曝光错了会让**解释一句话说错**，
而错的形态是"听起来很有道理"：

- M1 打"单条指纹按整份算"（改 R2 连 R3 都报变了 ⇒ 所有人都成了受影响者）；
- M2 打"单条指纹带数组位置"（换位置也报"判据改了"⇒ 解释变成假的）；
- M3 打"读不到指纹当没变"（历史报告全被当成"不受影响"，静默跳过）；
- M4 打"空指纹对象当没改"（坏数据被降级成"一切正常"）；
- M5 打"判过没判过不分"（当年被门禁拦住、R2 只是 blocked 的提交也算"受 R2 改动影响"）；
- M6 打"不知道当没碰"（缺指纹的历史直接消失）；
- M7 打"新增规则不算影响"（新判据多了一条，老提交却声称"与我无关"）；
- M8 打"丢 task_id 过滤"（别的任务的改动算到这个任务的人头上）；
- M9 打"只看档位不看状态"（判据没改也把人塞进交代名单）；
- M10 打"只报 T1"（0 分但证据进了 P 的那批从视野里消失）；
- M11 打"带不出当年状态"（说是"受影响"，却说不清当年这条怎么判的）；
- M12 打"报告不写单条指纹"（从此没有任何东西能回答"哪条规则变了"）。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

DR = Path("src/arena/diagnosis_report.py")
BL = Path("src/arena/baseline.py")
T = "tests/test_rule_exposure.py"

MUTATIONS = [
    ("M1 单条指纹按整份算（改一条规则，全体都报变了）",
     DR,
     '            out[rid] = hashlib.sha256(payload.encode("utf-8")).hexdigest()',
     "            out[rid] = rubric_fingerprint(task)  # 变异：每条都等于整份",
     f"{T}::test_editing_one_rule_moves_only_that_rules_fingerprint"),

    ("M2 单条指纹带数组位置（换位置也报「判据改了」）",
     DR,
     '                {"version": RULE_FINGERPRINT_VERSION, "layer": layer, "rule": rule},',
     '                {"version": RULE_FINGERPRINT_VERSION, "layer": layer, "rule": rule,\n'
     '                 "i": rules.index(rule)},  # 变异：把位置算进指纹',
     f"{T}::test_reordering_rules_moves_the_whole_fingerprint_but_no_single_rule_one"),

    ("M3 读不到指纹当「没变」（历史报告全部静默跳过）",
     BL,
     "    if before is None:\n        return None",
     "    if before is None:\n        return RuleDiff((), (), ())  # 变异：不认识当没变",
     f"{T}::test_legacy_report_without_rule_fingerprints_is_unknown_not_unchanged"),

    ("M4 空指纹对象当「没改」（坏数据被降级成一切正常）",
     BL,
     '    if not before:\n        raise BaselineError(',
     "    if not before:\n        return RuleDiff((), (), ())  # 变异：空对象当没改\n"
     "    if False:\n        raise BaselineError(",
     f"{T}::test_empty_rule_fingerprint_map_is_bad_data_not_no_change"),

    ("M5 判过没判过不分（blocked 的也算「受影响」）",
     BL,
     "            judged = any(t.state in JUDGED_STATES for t in touches)",
     "            judged = bool(touches)  # 变异：只要命中就算判过",
     f"{T}::test_rule_that_never_ran_is_not_applied_not_touched"),

    ("M6 「不知道」当「没碰」（缺指纹的历史直接消失）",
     BL,
     '        if diff is None:\n            status, touches = "unknown", ()',
     '        if diff is None:\n            status, touches = "untouched", ()  # 变异：不认识当没碰',
     f"{T}::test_new_rule_and_missing_fingerprints_stay_on_the_list"),

    ("M7 新增规则不算影响（老提交声称「与我无关」）",
     BL,
     '            elif diff.added:\n                status = "added_only"',
     '            elif False:\n                status = "added_only"  # 变异：不认新增规则',
     f"{T}::test_new_rule_and_missing_fingerprints_stay_on_the_list"),

    ("M8 丢 task_id 过滤（别的任务的改动算到这批人头上）",
     BL,
     '    rows = [row for row in store.blocked_submissions(conn) if row["task_id"] == tid]',
     "    rows = list(store.blocked_submissions(conn))  # 变异：不收范围",
     f"{T}::test_exposure_is_scoped_to_one_task"),

    ("M9 只看档位不看状态（判据没改也把人塞进交代名单）",
     BL,
     '                     if row.tier == "T1" and row.status in ("touched", "added_only", "unknown"))',
     '                     if row.tier == "T1")  # 变异：状态不看',
     f"{T}::test_unchanged_task_marks_everyone_untouched"),

    ("M10 只报 T1（0 分但证据进了 P 的那批消失）",
     BL,
     '    return ExposureReport(task_id=tid, changed_rules=tuple(sorted(union)),\n'
     '                          rows=tuple(exposures))',
     '    return ExposureReport(task_id=tid, changed_rules=tuple(sorted(union)),\n'
     '                          rows=tuple(r for r in exposures if r.tier == "T1"))'
     '  # 变异：只报有分的那档',
     f"{T}::test_zero_score_submissions_are_reported_too_not_only_the_scored_ones"),

    ("M11 带不出当年状态（说「受影响」却说不清当年怎么判的）",
     BL,
     "            touches = tuple(RuleTouch(rid, seen[rid]) for rid in sorted(seen))",
     "            touches = tuple(RuleTouch(rid, '?') for rid in sorted(seen))  # 变异：丢状态",
     f"{T}::test_edited_rule_that_was_really_judged_lands_in_touched_with_its_state"),

    ("M12 报告不写单条指纹（从此没人能回答「哪条规则变了」）",
     DR,
     '    report["rubric_rule_fingerprints"] = rubric_rule_fingerprints(task)',
     '    # 变异：不写单条指纹',
     f"{T}::test_every_report_carries_rule_fingerprints"),
]

def _run(node: str) -> tuple[int, str]:
    env = {**os.environ, "PYTEST_DEBUG_TEMPROOT": tempfile.mkdtemp(prefix="pt-exp-")}
    r = subprocess.run([PYEXE, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-exposure-")

if __name__ == "__main__":
    raise SystemExit(main())
