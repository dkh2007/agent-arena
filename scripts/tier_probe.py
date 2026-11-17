"""「补不上」那批分档的验收：把三档都摆出来，并展示"只看总数"会错在哪。

跑法：`python scripts/tier_probe.py`

这一段回答的不是"还剩谁"（那是 `baseline_probe.py`），而是**"剩下来的这些人，
谁真的需要跟他解释"**。两者的区别是政策前提：拿一个总数去设计交代方案，
等于为一个还没被数过的规模拍板。

脚本自己造库、自己跑，不改仓库里任何数据。末行 `TIERS_OK=...` 是给自动检查看的。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import baseline as bl  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

AT = "2026-11-17T09:00:00Z"


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


def _new_db(prefix: str):
    db = Path(tempfile.mkdtemp(prefix=prefix)) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)
    return db, registry, (lambda: store.connect(db))


def main() -> int:
    db, registry, conn = _new_db("tier-")
    submit = lambda fixture, sid, uid: api.submit(  # noqa: E731
        _payload(fixture, sid, uid), registry=registry, conn_factory=conn)

    print("=" * 78)
    print("0. 造四份样本：三份补不上（处境各不相同），一份补得上作对照")
    # 关键：「产物没存下来」和「当年就没交产物」，在库里长得一模一样（都是 NULL），
    # 但分数档位完全不同。前者可以带着 1.0 分进档案，后者基本只能 0 分。
    submit("raw-perfect", "s-scored", "u-alice")     # 判过、给了 1.00 分
    submit("raw-no-file", "s-zero-ok", "u-bob")      # 门禁没过，但 credit 有通过
    submit("raw-empty", "s-zero-none", "u-carol")    # 什么都没留下
    submit("raw-perfect", "s-fine", "u-dave")        # 有产物 ⇒ 不在这一批

    c = conn()
    # 三份「补不上」是同一个动作造成的：`artifacts` 这一列 v4 才有，之前根本没存。
    # 唯一例外是 s-zero-ok / s-zero-none——它们交的时候就没有产物，所以判出来是 0 分。
    c.execute("UPDATE submissions SET artifacts = NULL "
              "WHERE submission_id IN ('s-scored', 's-zero-ok', 's-zero-none')")
    c.commit()
    for sid in ("s-scored", "s-zero-ok", "s-zero-none", "s-fine"):
        row = store.load_submission(c, sid)
        print(f"   {sid:11s} {row['user_id']:8s} ratio={row['assert_score']:.2f}  "
              f"artifacts={'无(NULL)' if row['artifacts'] is None else '有'}")

    print("=" * 78)
    print("1. 分档（只读，人不需要报任何 id）")
    split = bl.tier_split(c)
    print(f"   全库 {split.counted()['total']} 份，补不上 {split.counted()['blocked']} 份")
    for name in bl.TIERS:
        items = split.tier(name)
        print(f"\n   {name} {bl.TIER_TITLES[name]}：{len(items)} 份 / {len(split.users(name))} 人")
        for item in items:
            extra = f"  计入技能={','.join(item.positive_skills)}" if item.positive_skills else ""
            print(f"      {item.submission_id:11s} {item.user_id:8s} 原分={item.ratio:.2f}{extra}")

    print("=" * 78)
    print("2. 只看总数的代价")
    explain = split.needs_explanation()
    print(f"   「补不上」= {split.counted()['blocked']} 份 —— 拿这个数去拍政策，就有 3 个人要交代。")
    print(f"   按档切开：真的要交代的是 T1 = {len(explain)} 份 "
          f"（{', '.join(i.submission_id for i in explain) or '无'}）。")
    print("   T2 本来就是 0 分，没什么可解释；T3 的分数也是 0，但它的过程证据进了 P，")
    print("   所以它的问题不在分数上，而在 B-07b（门禁口径）——两件事不能一起拍。")

    print("=" * 78)
    print("3. 用另一种构造方式模拟历史，会得到什么结论")
    # 这一节是脚本自己的反证：如果只造「学生没交产物」这一种来源，
    # 「当年有分」那档会恒为 0，于是"没有人需要解释"这个结论是被构造方式制造出来的。
    _, registry2, conn2 = _new_db("tier2-")
    for fixture, sid, uid in (("raw-no-file", "s-x", "u-bob"), ("raw-empty", "s-y", "u-carol")):
        naive_payload = _payload(fixture, sid, uid)
        naive_payload["artifacts"] = None        # 只有这一种来源：当年就没交产物
        api.submit(naive_payload, registry=registry2, conn_factory=conn2)
    c2 = conn2()
    naive = bl.tier_split(c2)
    print(f"   只造「没交产物」：{naive.counted()}")
    print(f"   结论会变成「补不上 {naive.counted()['blocked']} 份、其中要交代的 T1=0 ⇒ 没人需要解释」")
    print("   ⇒ 这正是我第一版测试犯过的错：模拟方式决定了结论，不是库里的情况。")
    c2.close()

    print("=" * 78)
    before = (store.count_submissions(c), sum(
        len(store.list_reappraisals(c, sid)) for sid in ("s-scored", "s-zero-ok", "s-zero-none")))
    again = bl.tier_split(c)
    after = (store.count_submissions(c), sum(
        len(store.list_reappraisals(c, sid)) for sid in ("s-scored", "s-zero-ok", "s-zero-none")))
    read_only = before == after and again.to_dict() == split.to_dict()
    print("4. 只读：两次分档逐字段相同、库里的行数没动")
    print(f"   {read_only}  ({before} → {after})")
    c.close()

    tiers_ok = (split.counted()["T1"] == 1 and split.counted()["T2"] == 1
                and split.counted()["T3"] == 1 and split.counted()["total"] == 4)
    only_t1 = [i.submission_id for i in explain] == ["s-scored"]
    naive_t1_zero = naive.counted()["T1"] == 0
    print("=" * 78)
    print("结论（由本次实测生成）：")
    print(f"  · 三档各归其位、全库总数取全库 ⇒ {tiers_ok}；")
    print(f"  · 交代名单只有 T1，没有把 T2/T3 混进来 ⇒ {only_t1}；")
    print(f"  · 换一种模拟来源，T1 会变成 0（说明这个数不能靠猜）⇒ {naive_t1_zero}；")
    print(f"  · 分档是只读的 ⇒ {read_only}")
    print(f"TIERS_OK={tiers_ok} EXPLAIN_ONLY_T1={only_t1} "
          f"SIMULATION_CHANGES_ANSWER={naive_t1_zero} READ_ONLY={read_only}")
    return 0 if all((tiers_ok, only_t1, naive_t1_zero, read_only)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
