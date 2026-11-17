"""探查：「确认不可回溯」终止标记到底改了什么（B-04h ② 的验收）。

## 这个脚本要证的四件事

1. **不再重试**：标记之后，同一批行不再出现在每一轮跑批报告里（报告停止线性变长）；
2. **不再重试 ≠ 不用解释**：补不上的名单、T1/T2/T3 分档、对外文案一个字不变；
3. **前提会被推翻**：产物补回来之后标记立刻失效，并在 `ignored_marks` 里显名；
4. **撤销回到范围**：撤销不是删行，判断与人名都还在。

## 为什么留一条不标记

只演示"都标上、于是都消失了"会给出一个过于漂亮的结论：真正要证明的是
**标记过的那两条消失、没标的那条还在报**。所以本脚本刻意留一个对照组。

用法：`python scripts/retirement_probe.py`（任意工作目录）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import baseline as bl  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import notices as nt  # noqa: E402
from arena import reappraisal as rap  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK = "dbg-001-order-report"
AT = "2026-11-17T09:00:00Z"
REASON = "probe: 终止标记演示"
HISTORY = ("s-perfect", "s-nofile", "s-empty")


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(fixture: str, submission_id: str, user_id: str) -> dict:
    raw = _fixture(fixture)
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": user_id,
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


def _historicize(c, submission_id: str) -> None:
    """v5 之前 + v4 之前的形态：报告没有指纹、产物列是 NULL。"""
    report = store.load_report(c, submission_id)
    report.pop("rubric_fingerprint", None)
    report.pop("rubric_rule_fingerprints", None)
    c.execute(
        "UPDATE submissions SET diagnosis = ?, artifacts = NULL WHERE submission_id = ?",
        (json.dumps(report, ensure_ascii=False, sort_keys=True), submission_id),
    )
    c.commit()


def _changed_task() -> dict:
    """模拟"人把 R2 的期望值改了"：这样曝光表才肯判"这条规则当年真的判过"。"""
    task = dr.load_task(ROOT / "tasks" / f"{TASK}.yaml")
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
            return task
    raise AssertionError("夹具变了：R2_report_correct 不在 rubric 里")


def main() -> int:
    db = Path(tempfile.mkdtemp(prefix="retire-")) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    print("=" * 78)
    print("0. 造三份「补不上」的历史提交（无指纹、无产物）+ 一份当代提交")
    api.submit(_payload("raw-perfect", "s-perfect", "u-alice"), registry=registry,
               conn_factory=conn)
    api.submit(_payload("raw-no-file", "s-nofile", "u-bob"), registry=registry,
               conn_factory=conn)
    api.submit(_payload("raw-empty", "s-empty", "u-carol"), registry=registry,
               conn_factory=conn)
    api.submit(_payload("raw-perfect", "s-live", "u-dave"), registry=registry,
               conn_factory=conn)

    c = conn()
    for sid in HISTORY:
        _historicize(c, sid)
    for sid in HISTORY + ("s-live",):
        row = store.load_submission(c, sid)
        print(f"   {sid:10s} user={row['user_id']:8s} "
              f"artifacts={'NULL' if row['artifacts'] is None else '有'} "
              f"ratio={row['assert_score']:.2f}")

    tasks = [_changed_task()]
    tid = tasks[0]["task_id"]

    print("=" * 78)
    print("1. 第一次跑批：三份补不上的都被重新试一遍、各报一次")
    first = bl.run_baseline(c, tasks, reason=REASON, apply=True, created_at=AT)
    print(f"   {first.counted()}")
    print(f"   被报为补不上的：{[o.submission_id for o in first.blocked()]}")

    print("=" * 78)
    print("2. 人确认过之后，给其中两份打「确认不可回溯」；第三份刻意不标（对照组）")
    for sid, reason in (("s-perfect", "v4 之前提交，产物未随trace落盘，旧备份里也没有"),
                        ("s-nofile", "v4 之前提交，当年就没保留产物")):
        row = store.mark_unrecoverable(c, sid, reason=reason, marked_by="课程助教",
                                       created_at=AT)
        print(f"   {sid:10s} marked_at={row['marked_at']} by={row['marked_by']}")
    print(f"   未撤销的标记：{sorted(store.unrecoverable_marks(c, tid))}")

    print("=" * 78)
    print("3. 再跑一次：标过的不再出现，没标的那份仍在报（这就是「报告不再线变长」）")
    second = bl.run_baseline(c, tasks, reason=REASON, apply=True, created_at=AT)
    print(f"   {second.counted()}")
    print(f"   这次被报为补不上的：{[o.submission_id for o in second.blocked()]}")
    print(f"   —— 少了的正是标过的那两份；剩下的 {[o.submission_id for o in second.blocked()]}"
          " 是刻意留下的对照组")
    stopped = ({o.submission_id for o in first.blocked()}
               - {o.submission_id for o in second.blocked()})
    print(f"   因标记而停止重试：{sorted(stopped)}")

    print("=" * 78)
    print("4. 但交代一条都没少：分档、曝光、对外文案都还在")
    split = bl.tier_split(c)
    print(f"   补不上总数仍为 {len(split.blocked)}（T1={len(split.tier('T1'))} "
          f"T2={len(split.tier('T2'))} T3={len(split.tier('T3'))}）")
    plan = nt.plan_notices(c, tasks[0], changed_on="2026-11-10",
                           what_changed="改了 R2 的期望值")
    for notice in plan.notices:
        if notice.retired or notice.state == "legacy_scored":
            head = notice.elements[0][1] if notice.elements else "（不告知）"
            print(f"   [{notice.state:<14}] {notice.submission_id:10s} "
                  f"{notice.disclosure:<6} 标记={notice.retired}  {head[:46]}…")
    # 两份标过的提交处境不同，**不能要求同一句文案**：
    # s-perfect 当年有分 ⇒ T1 ⇒ 本人页面上一句标注；
    # s-nofile 当年 0 分但证据进了 P ⇒ T3 ⇒ 口径未拍，先标成待定、不告知。
    # 这里要证的是"两条都还在计划里"，不是"两条说法一样"——
    # 后者会逼着把 T3 也推上页面，而那是一件还没拍的事。
    by_id = {n.submission_id: n for n in plan.notices}
    still_explained = (
        by_id["s-perfect"].disclosure == "page"
        and by_id["s-nofile"].state == "pending_mastery"
        and by_id["s-nofile"].needs_decision
    )
    print(f"   标过的两份都还在计划里（T1 上页面、T3 标待定）⇒ {still_explained}")

    print("=" * 78)
    print("5. 反证一：把 s-nofile 的产物补回来 ⇒ 标记的前提没了，它回到重试范围")
    c.execute("UPDATE submissions SET artifacts = ? WHERE submission_id = 's-nofile'",
              (json.dumps({"report.md": "从旧备份恢复"}, ensure_ascii=False),))
    c.commit()
    scope = rap.scope_for_task(c, tasks[0])
    print(f"   仍在 retired：{list(scope.retired)}")
    print(f"   被现实推翻的标记：{list(scope.ignored_marks)}")
    print(f"   重新进入待重评：{'s-nofile' in scope.all_ids}")
    premise_rechecked = (scope.ignored_marks == ("s-nofile",)
                         and "s-nofile" not in scope.retired
                         and "s-nofile" in scope.all_ids)

    print("=" * 78)
    print("6. 反证二：撤销 s-perfect 的标记 ⇒ 它也回到范围，而那句话和人名都还在")
    kept = store.revoke_unrecoverable(c, "s-perfect", reason="产物其实还在另一个备份里",
                                      revoked_at=AT)
    print(f"   撤销记录：marked_at={kept['marked_at']} marked_by={kept['marked_by']} "
          f"revoked_at={kept['revoked_at']}")
    print(f"   撤销理由：{kept['revoked_reason']}")
    scope = rap.scope_for_task(c, tasks[0])
    print(f"   未撤销的标记：{sorted(store.unrecoverable_marks(c, tid))}")
    print(f"   retired={list(scope.retired)}  待重评含 s-perfect={'s-perfect' in scope.all_ids}")
    revoke_works = ("s-perfect" in scope.all_ids and scope.retired == ()
                    and kept["marked_by"] == "课程助教")

    print("=" * 78)
    print("7. 只读复核：连跑两次范围计算结果一致（标记不是一次性的运气）")
    a = rap.scope_for_task(c, tasks[0]).to_dict()
    b = rap.scope_for_task(c, tasks[0]).to_dict()
    print(f"   {a == b}")

    print()
    print(f"RETIREMENT_OK={bool(stopped)} "
          f"NO_RETRY_AFTER_MARK={not (set(stopped) & {o.submission_id for o in second.blocked()})} "
          f"STILL_EXPLAINED={still_explained} "
          f"PREMISE_RECHECKED={premise_rechecked} "
          f"REVOKE_KEEPS_RECORD={revoke_works}")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
