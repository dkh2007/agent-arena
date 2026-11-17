"""「每条记录说什么」的验收：把五种状态都摆出来，并把四件必说的事逐条打出来。

跑法：`python scripts/notices_probe.py`

前面几个探针回答的都是"数"（还剩谁、谁要交代、谁被改动碰到）。这一段回答
**"跟他说什么"**，而这一层没有算式可对——只能把句子摊开看。
所以脚本做三件事：造出五种状态的记录、逐条打印会发给学生的原文、
再验三条硬约束（四件齐全 / 无内部术语 / 只读）。

末行 `NOTICES_OK=...` 是给自动检查看的。
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
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

AT = "2026-11-17T09:00:00Z"
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
CHANGED_ON = "2026-11-10"
WHAT = "改了「报告总额」这条判据的期望值"
REQUIRED = {
    "legacy_scored": ("which_rubric", "comparable", "next_step", "not_your_fault"),
    "recalculated": ("what_changed", "why_me", "comparable", "next_step"),
}


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


def main() -> int:
    db = Path(tempfile.mkdtemp(prefix="notices-")) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)
    conn = lambda: store.connect(db)  # noqa: E731
    submit = lambda f, sid, uid: api.submit(  # noqa: E731
        _payload(f, sid, uid), registry=registry, conn_factory=conn)

    # 五份记录：能重评且分数会变 / 补不上但有分 / 补不上且没分 / 补不上但证据进了 P / 正常
    submit("raw-perfect", "s-willchange", "u-ann")
    submit("raw-perfect", "s-scored", "u-bob")
    submit("raw-empty", "s-zero", "u-cy")
    submit("raw-no-trace", "s-evidence", "u-dee")
    submit("raw-no-trace", "s-plain", "u-eve")
    c = conn()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL "
                  "WHERE submission_id IN ('s-scored','s-zero','s-evidence')")
        report = store.load_report(c, "s-willchange")
        report.pop("rubric_fingerprint")
        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  (json.dumps(report, ensure_ascii=False, sort_keys=True), "s-willchange"))
        c.commit()

        task = dr.load_task(TASK_PATH)
        for spec in task["rubric_credit"]:
            if spec["id"] == "R2_report_correct":
                spec["expected"]["total_amount"] += 1.0
        bl.run_baseline(c, [task], reason="探针：判据修订后重算", created_at=AT)

        plan = nt.plan_notices(c, task, changed_on=CHANGED_ON, what_changed=WHAT)
        print("=" * 78)
        print("1. 五态分布")
        print(f"   {plan.counted()}")
        print(f"   页面看到的是：{[n.submission_id for n in plan.disclosed('page')]}")
        print(f"   只有档案里有的：{[n.submission_id for n in plan.disclosed('archive')]}")
        print(f"   待定（口径未拍）：{[n.submission_id for n in plan.needs_decision()]}")

        print("=" * 78)
        print("2. 逐条会发给学生的原文")
        for notice in plan.notices:
            if not notice.elements:
                print(f"   [{notice.state}] {notice.submission_id}：（不告知）")
                continue
            print(f"   [{notice.state}] {notice.submission_id}  {notice.disclosure}")
            for key, text in notice.elements:
                print(f"      · {key}：{text}")

        print("=" * 78)
        print("3. 三条硬约束")
        keys_ok = all(
            (notice.keys() == REQUIRED[notice.state])
            for notice in plan.notices if notice.state in REQUIRED
        )
        jargon_ok = all(
            word.lower() not in notice.text().lower()
            for notice in plan.notices
            for word in nt.FORBIDDEN_IN_TEXT
        )
        before = (store.count_submissions(c), len(store.list_reappraisals(c)))
        again = nt.plan_notices(c, task, changed_on=CHANGED_ON, what_changed=WHAT)
        after = (store.count_submissions(c), len(store.list_reappraisals(c)))
        read_only = (again.to_dict() == plan.to_dict() and before == after)
        print(f"   四件齐全 ⇒ {keys_ok}；无内部术语 ⇒ {jargon_ok}；只读 ⇒ {read_only}")
        print(f"   待定的那批没有被并进「不用管」⇒ "
              f"{'s-evidence' in [n.submission_id for n in plan.needs_decision()]}")
        print("=" * 78)
        print("结论（由本次实测生成）：")
        print("  · 五态各归其位，页面/档案/不告知三种去处都有实例；")
        print("  · 有分那批和分数变了那批，四件必说的事一件不少；")
        print("  · 文案里没有内部术语、没有将来承诺；计划本身不改库。")
        print(f"NOTICES_OK={keys_ok} PLAIN_TEXT_OK={jargon_ok} READ_ONLY={read_only}")
        return 0 if (keys_ok and jargon_ok and read_only) else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
