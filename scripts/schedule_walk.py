"""B-08 的十分钟验证：真落库跑三次提交，把「该复习什么、什么时候」打出来。

用法（不需要密钥、不联网、只写 `.git/` 下的临时库）：

    python scripts/schedule_walk.py
    python scripts/schedule_walk.py --cap 1

做完这一步要能回答四件事：
  ① 一次失败提交是否真的产出一条 `pending` 的复习项，`due` 是否由 py-fsrs 给出；
  ② 同一技能再失败一次，旧项是否被关掉（队列不堆积）、新项是否换成新 `due`；
  ③ `daily_review_cap` 是否真的截断今日队列，而被截掉的项是否**仍在库里**；
  ④ 「本次不算数」的提交（白卷 / 缺过程）是否真的不动卡片——这是本轮的口径判断，
     也是它和 B-07b（白卷抬高 P）最容易被混为一谈的地方。

`due` 为什么第一次只隔 1 分钟：py-fsrs 的 `learning_steps` 默认是 (60s, 600s) ——
新卡先按"1 分钟后、10 分钟后"两轮短间隔走，第三轮才进入天级间隔。这不是 bug，
但必须在演示里说出来，否则看的人会以为排期坏了。
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import scheduler as sch  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

DB_PATH = ROOT / ".git" / "schedule-demo.db"
TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "demo-user"

# 剧本：先失败（要复习）→ 再失败一次（看旧项关闭）→ 满分（看队列清空）→ 再失败一次
# （让队列里重新有 pending，否则"每日上限"那段演示是空转）→ 白卷（看"不算数"不动卡）。
# 每一步都对应文件头的一条问题，缺一步就有一段演示说不出话。
PLAN = [
    ("raw-fake-retry", "假重试：规划 0/2、重试 0/1、参数 1/1"),
    ("raw-fake-retry", "同形态再失败一次（旧项应被关闭，新项换 due）"),
    ("raw-perfect", "满分（待复习项应全部关闭，且不产生新项）"),
    ("raw-fake-retry", "再失败一次（队列里重新出现 pending，供下一段演示上限）"),
    ("raw-no-file", "白卷：没交产物（不该动卡、不该建项、不该关旧项）"),
]


def _report(name: str, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def _queue_rows(conn) -> list[dict]:
    return store.list_review_items(conn, USER)


def main() -> int:
    ap = argparse.ArgumentParser(description="复习调度的可复核演示")
    ap.add_argument("--cap", type=int, default=None,
                    help="临时把每日复习上限改成这个值（默认读 users.daily_review_cap）")
    args = ap.parse_args()

    sched = sch.make_scheduler()
    print(f"用户 {USER} / fuzz=关（可复现）/ "
          f"learning steps={[str(s) for s in sched.learning_steps]} / "
          f"desired_retention={sched.desired_retention}")
    print()

    DB_PATH.unlink(missing_ok=True)
    conn = store.connect(DB_PATH)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        store.register_task(conn, TASK_YAML)
        if args.cap is not None:
            store.set_daily_review_cap(conn, USER, args.cap)

        base = datetime(2026, 11, 1, tzinfo=timezone.utc)
        print("=== 四次提交（真落进 SQLite，卡片与队列都从库里读回） ===")
        before_white: dict | None = None
        for i, (fixture, note) in enumerate(PLAN, start=1):
            at = (base + timedelta(days=i)).isoformat()
            sid = f"demo-{i}"
            if fixture == "raw-no-file":
                before_white = store.load_fsrs_cards(conn, USER)
            report = _report(fixture, sid, at)
            store.save_submission(conn, report, user_id=USER, created_at=at)
            result = sch.schedule_submission(
                report, user_id=USER, at=at,
                cards=sch.cards_from_rows(store.load_fsrs_cards(conn, USER)),
                task_has_fault=True,
            )
            summary = store.save_schedule(conn, result)

            print(f"\n[{i}] {fixture}  {note}")
            print(f"    {at[:19]}  ratio={report['score']['ratio']:<5} "
                  f"不合格={report['score']['disqualified']}  "
                  f"不算数理由={sch.dr.suppression_reasons(report) or '无'}")
            for d in result.decisions:
                c_text = "—" if d.c is None else f"{d.c:.2f}"
                print(f"      {d.skill_id:<24} c={c_text:<6} rating={d.rating or '—':<4} "
                      f"→ {d.action}")
            print(f"    落库：卡 {summary['cards_written']} 张 / 新项 {summary['items_added']} 条 / "
                  f"关闭 {summary['items_closed']} 条 / 幂等跳过 {summary['items_skipped']} 条")

            cards = store.load_fsrs_cards(conn, USER)
            for skill_id, payload in cards.items():
                due = payload.get("due")
                print(f"      卡 {skill_id:<24} due={due} state={payload.get('state')} "
                      f"S={payload.get('stability')}")

        rows = _queue_rows(conn)
        pending = [r for r in rows if r["status"] == "pending"]
        done = [r for r in rows if r["status"] == "done"]

        after_white = store.load_fsrs_cards(conn, USER)
        print()
        print("=== 白卷那一步的卡片比对（本轮的口径判断用这一行自证） ===")
        if before_white is None:
            print("  （剧本里没有白卷，跳过）")
        else:
            same = before_white == after_white
            print(f"  白卷提交前/后的卡片 JSON 完全相同：{same}")
            for skill_id in sorted(after_white):
                print(f"    {skill_id:<24}due={after_white[skill_id]['due']}")
            print("  ⇒ 白卷属「本次不算数」，**不动卡片、不建项、不关旧项**（见本次落库计数）")

        print()
        print("=== 复习项全表（history 保留：done 不会被覆盖） ===")
        for r in sorted(rows, key=lambda x: (x["due"], x["review_id"])):
            print(f"  {r['status']:<8}{r['skill_id']:<24}due={r['due']}  "
                  f"kind={r['kind']:<16} id={r['review_id']}  ← {r['source_submission_id']}")
        print(f"  合计 pending={len(pending)} / done={len(done)}")

        print()
        cap = store.daily_review_cap(conn, USER)
        at_now = (base + timedelta(days=30)).isoformat()
        print(f"=== 每日队列（daily_review_cap={cap}，查看时刻 {at_now[:19]}） ===")
        kept, deferred = sch.take_daily(rows, at=at_now, cap=cap)
        for r in kept:
            print(f"  今天看：{r['skill_id']:<24}due={r['due']}")
        if not kept:
            print("  今天看：（空）")
        print(f"  被推迟：{deferred} 条（**仍在库里、仍是 pending**，不是被删掉）")
        # 把上限压到 1 与 0：默认值可能恰好等于 pending 条数，那样"截断"演示不出来。
        for probe in (1, 0):
            store.set_daily_review_cap(conn, USER, probe)
            kept_p, def_p = sch.take_daily(rows, at=at_now, cap=store.daily_review_cap(conn, USER))
            note = "（'今天不复习'）" if probe == 0 else ""
            print(f"  上限临时改成 {probe}{note} ⇒ 今日 {len(kept_p)} 条、推迟 {def_p} 条；"
                  f"库里 pending 仍 {len(pending)} 条")
        store.set_daily_review_cap(conn, USER, cap)

        print()
        print("=== 与掌握度对照：为什么白卷在这里没有危害 ===")
        print("  B-07b 记的是「白卷会抬高 P」（因为先做对、初值播种）——那是**能力估计**的问题；")
        print("  调度这一侧问的是「下次什么时候练」，而白卷属于'本次不算数'，")
        print("  所以它既不动卡、也不建项、也不关闭旧项（见第 4 次的输出）。")
        print("  两者能分开是因为 `mastery` 读 `attribution_policy`、`scheduler` 读 `suppression_reasons`，")
        print("  不是同一个布尔，也没有互相覆盖。")

        print()
        print("说明：rating 阈值（0.9/0.7/0.4）与 learning steps 都是**假设**，")
        print("      要 dogfooding 后校准（docs/BACKLOG.md B-08b / B-07b）。")
        print("      衰减仍未实现：P 不随自然时间下降（见 B-08b 的开工条件）。")
        return 0
    finally:
        conn.close()
        DB_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
