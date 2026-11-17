"""「这次改过的那条规则，谁的提交真的用到了」的验收（B-04g 取数层第二段）。

跑法：`python scripts/exposure_probe.py`

`tier_probe.py` 回答"补不上的这批里谁当年有分、要解释"；本脚本回答**它的下一句**：
"其中谁的提交真的用到了这次改过的那条规则"。两句都要，因为第一句只是把
"补不上的全部"换成"补不上且有分的那部分"，第二句才把它换成"**被这次改动碰到的那部分**"。

四段，每段都在证一件事：
  1. 全库补不上 3 份 ⇒ 按档位要交代的只有 T1 的 1 份；
  2. 改一条**当年真的判过**的规则（R2）⇒ 1 份 `touched`，名单还是 1 份，但话说得清了；
  3. 改一条**当年没参与判定**的规则（R2 在这份提交里是 `blocked`，被门禁拦住）
     ⇒ `not_applied`，名单变 0 份——这就是"这次练习到底有没有触发那条写错的规则"
     的否定答案；
  4. 一份缺单条指纹的历史（v5 之前的报告）必须落 `unknown` 并**留在名单里**，
     哪怕同一份任务这次一条规则都没改——"不知道"不能当"没碰"。

脚本自己造库、自己跑，不改仓库里任何数据。末行 `EXPOSURE_OK=...` 是给自动检查看的。
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
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

AT = "2026-11-17T09:00:00Z"
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


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


def _bump(task: dict, rule_id: str) -> dict:
    for spec in task["rubric_gate"] + task["rubric_credit"]:
        if spec["id"] == rule_id:
            for key, value in spec["expected"].items():
                if isinstance(value, (int, float)):
                    spec["expected"][key] = value + 1
                    return task
    raise AssertionError(f"{rule_id} 不在 rubric 里")


def main() -> int:
    db = Path(tempfile.mkdtemp(prefix="exposure-")) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)
    conn = lambda: store.connect(db)  # noqa: E731
    submit = lambda f, sid, uid: api.submit(  # noqa: E731
        _payload(f, sid, uid), registry=registry, conn_factory=conn)

    submit("raw-perfect", "s-scored", "u-ann")     # 当年 1.0 分，产物没存下来 ⇒ T1
    submit("raw-empty", "s-gate", "u-bob")         # 当年门禁就挂了 ⇒ 0 分
    submit("raw-no-trace", "s-notrace", "u-cy")    # 当年 0 分，但有过程证据 ⇒ T3
    c = conn()
    try:
        c.execute("UPDATE submissions SET artifacts = NULL")
        c.commit()

        base_task = dr.load_task(TASK_PATH)
        print("=" * 78)
        print("1. 只看档位：补不上的里面，谁当年有分")
        split = bl.tier_split(c)
        print(f"   补不上 {split.counted()['blocked']} 份，T1/T2/T3 = "
              f"{split.counted()['T1']}/{split.counted()['T2']}/{split.counted()['T3']}"
              f"；按档位要交代 = {[i.submission_id for i in split.needs_explanation()]}")

        print("=" * 78)
        print("2. 改一条当年真的判过的规则（R2_report_correct）")
        edited = _bump(dr.load_task(TASK_PATH), "R2_report_correct")
        rep = bl.exposure_split(c, edited)
        for row in rep.rows:
            states = ",".join(f"{t.rule_id}={t.state}" for t in row.touches) or "-"
            print(f"   {row.submission_id:<10} {row.tier}  {row.status:<11} {states}")
        print(f"   交代名单（缩窄后）= {[r.submission_id for r in rep.must_explain()]}")
        narrowed_t2 = [r.submission_id for r in rep.must_explain()]

        print("=" * 78)
        print("3. 换一条「当年没走到判据上」的规则：R2 在 s-gate 里是 blocked")
        print("   （门禁 R1 先挂了，R2 的判据根本没参与 ⇒ 改它改不动这份分）")
        s_gate = [r for r in rep.rows if r.submission_id == "s-gate"][0]
        print(f"   s-gate 状态 = {s_gate.status}；touches = "
              f"{[(t.rule_id, t.state) for t in s_gate.touches]}")
        not_applied_ok = s_gate.status == "not_applied"

        print("=" * 78)
        print("4. 缺单条指纹的历史（v5 之前的报告）必须留在名单里")
        report = store.load_report(c, "s-notrace")
        report.pop("rubric_rule_fingerprints")
        c.execute("UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
                  (json.dumps(report, ensure_ascii=False, sort_keys=True), "s-notrace"))
        c.commit()
        plain = bl.exposure_split(c, base_task)   # 这次**一条规则都没改**
        legacy = [r for r in plain.rows if r.submission_id == "s-notrace"][0]
        print(f"   任务未改动时：s-notrace 状态 = {legacy.status}（其余 "
              f"{[r.status for r in plain.rows if r.submission_id != 's-notrace']}）")
        print("   ⇒ 没改规则的那批可以落 untouched，但缺指纹的那份只能落 unknown；"
              "\n     把它当 untouched 就是「不认识当合格」")

        print("=" * 78)
        print("5. 只读：同一状态下连跑两次，结果逐字段相同，库里的行数没动")
        first = bl.exposure_split(c, edited)
        second = bl.exposure_split(c, edited)
        same = first.to_dict() == second.to_dict()
        before = store.count_submissions(c)
        print(f"   {same}  （提交数 {before}，两次都是 {first.counted()['rows']} 行）")
        print("=" * 78)
        print("结论（由本次实测生成）：")
        print(f"  · 档位给的名单 = {[i.submission_id for i in split.needs_explanation()]}；"
              f"缩窄后 = {narrowed_t2}")
        print(f"  · 「当年没走到判据上」能识别出来 ⇒ {not_applied_ok}")
        print(f"  · 缺指纹的历史落 unknown 而不是 untouched ⇒ {legacy.status == 'unknown'}")
        print(f"  · 只读 ⇒ {same}")
        print(f"EXPOSURE_OK={narrowed_t2 == ['s-scored']} "
              f"NOT_APPLIED_DETECTED={not_applied_ok} "
              f"UNKNOWN_NOT_DROPPED={legacy.status == 'unknown'} READ_ONLY={same}")
        return 0 if (narrowed_t2 == ["s-scored"] and not_applied_ok
                     and legacy.status == "unknown" and same) else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
