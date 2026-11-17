"""探查：历史提交没有指纹，跑一次全库基线之后会怎样（B-04f 的验收）。

## 为什么要有这个脚本

`scope_for_task()` 能回答"这个任务里谁对不上"，但 v5 之前的报告**没有**
`rubric_fingerprint`（当时没这个概念），于是它们全部落在 `unfingerprinted` 桶里——
意思是：从现在起的新提交能按指纹只跑增量，而**全部历史每次都得整批重评**。

本脚本要实测的就一件事：**跑完这一次之后，剩下的是什么**。四问：

1. 能重评的历史提交，指纹补上了吗（`unfingerprinted` 是否变空）？
2. 补不上的（缺 trace / 缺产物）有没有被**报出来**，还是被算进了"已处理"？
3. 再跑一次会不会重复干活（幂等）？
4. 分数变了的那些人，能不能**指名**（`user_id` + `delta`）？

## 它与产品代码的关系

逻辑都在 `src/arena/baseline.py`。本脚本**不自带实现**——只负责造一批历史形态的数据、
调产品函数、把结果打成可复核的输出。判据只有一处：`baseline.run_baseline()`。

用法：`python scripts/baseline_probe.py`（任意工作目录）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import baseline as bl  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK = "dbg-001-order-report"
AT = "2026-11-17T09:00:00Z"
REASON = "baseline: 全库按现行判据打平（历史报告缺 rubric_fingerprint）"
SUBS = ("s-perfect", "s-nofile", "s-empty")


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


def _strip(c, submission_id: str) -> None:
    """把一份报告改回 v5 之前的样子：证据全在，只少一个键。"""
    report = store.load_report(c, submission_id)
    report.pop("rubric_fingerprint", None)
    c.execute(
        "UPDATE submissions SET diagnosis = ? WHERE submission_id = ?",
        (json.dumps(report, ensure_ascii=False, sort_keys=True), submission_id),
    )
    c.commit()


def main() -> int:
    db = Path(tempfile.mkdtemp(prefix="baseline-")) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    print("=" * 78)
    print("0. 造一批「v5 之前」的提交：证据都在，只是报告里没有指纹")
    api.submit(_payload("raw-perfect", "s-perfect", "u-alice"),
               registry=registry, conn_factory=conn)
    api.submit(_payload("raw-no-file", "s-nofile", "u-bob"),
               registry=registry, conn_factory=conn)
    api.submit(_payload("raw-empty", "s-empty", "u-carol"),
               registry=registry, conn_factory=conn)

    c = conn()
    for sid in SUBS:
        _strip(c, sid)
    # v4 之前连 `artifacts` 这一列都没有 ⇒ 模拟"当时确实交了、但没存下来"。
    # 这正是补不上的那类：拿空对象顶上会把平台的缺陷算成他没做。
    c.execute("UPDATE submissions SET artifacts = NULL WHERE submission_id = 's-empty'")
    c.commit()

    for sid in SUBS:
        row = store.load_submission(c, sid)
        print(f"   {sid:10s} user={row['user_id']:8s} "
              f"trace={'有' if row['trace'] is not None else '无'} "
              f"artifacts={'无(NULL)' if row['artifacts'] is None else '有'} "
              f"ratio={row['assert_score']:.2f} "
              f"指纹={'有' if 'rubric_fingerprint' in row['diagnosis'] else '无'}")

    tasks = [dr.load_task(ROOT / "tasks" / f"{TASK}.yaml")]
    tid = tasks[0]["task_id"]

    print("=" * 78)
    print("1. 跑之前的范围（只按现行判据算，人不需要报任何 user_id）")
    for task_id, scope in bl.all_task_scopes(c, tasks).items():
        print(f"   {task_id}: 过期={len(scope.stale)}  无指纹={len(scope.unfingerprinted)}"
              f"  已符合={len(scope.current)}")
        print(f"   无指纹的是：{list(scope.unfingerprinted)}")

    print("=" * 78)
    print("2. 演练（apply=False）：只算不写。这是「动手前先看一眼」的正规做法")
    dry = bl.run_baseline(c, tasks, reason=REASON, apply=False, created_at=AT)
    print(f"   {dry.counted()}")
    unchanged = all(
        (a.stale, a.unfingerprinted) == (b.stale, b.unfingerprinted)
        for a, b in zip(dry.after.values(), dry.before.values())
    )
    print(f"   演练后范围与之前一致 ⇒ {unchanged}（一致正是「确实没写」的证据）")

    print("=" * 78)
    print("3. 真跑（apply=True）")
    started = time.perf_counter()
    real = bl.run_baseline(c, tasks, reason=REASON, apply=True, created_at=AT)
    elapsed = time.perf_counter() - started
    counted = real.counted()
    print(f"   {counted}")
    print(f"   耗时 {elapsed:.2f}s / {counted['scanned']} 份"
          f"（每份各自刷一次该用户快照；演示规模可接受，上千份要改成按用户合并）")

    print("=" * 78)
    print("4. 补不上的那批：必须被点名，不许并进「已处理」")
    for out in real.blocked():
        print(f"   {out.submission_id:10s} user={out.user_id} state={out.state}"
              f"  ← 成绩原地不动，也不冒算")
    if not real.blocked():
        print("   （本例没有补不上的）")

    print("=" * 78)
    print("5. 分数变了的名单（要通知的是这些人）")
    for out in real.changed():
        print(f"   {out.user_id:8s} {out.submission_id:10s} "
              f"{out.old_ratio:.2f} → {out.new_ratio:.2f}  Δ={out.delta:+.2f}"
              f"{'  需人工复核' if out.needs_manual_review else ''}")
    if not real.changed():
        print("   （本例判据没改，基线只补指纹，因此没有分数变化）")

    print("=" * 78)
    print("6. 跑完剩什么（这才是「我们回溯过了」能不能说出口的依据）")
    after = real.after[tid]
    print(f"   过期={list(after.stale)}  无指纹={list(after.unfingerprinted)}"
          f"  已符合={len(after.current)}")
    legacy_blocked = sorted(
        row["submission_id"]
        for row in c.execute("SELECT submission_id, artifacts FROM submissions").fetchall()
        if row["artifacts"] is None
    )
    print(f"   原始产物为 NULL（补不上）：{legacy_blocked or '无'}")
    print("   两者相等 ⇒ 「还剩谁」有确定答案；不等 ⇒ 有提交既没补上、也没被报出来")

    print("=" * 78)
    print("7. 再跑一次：不许重复改分，但补不上的仍会被重新尝试并再报一次")
    second = bl.run_baseline(c, tasks, reason=REASON, apply=True, created_at=AT)
    chains = {sid: len(store.list_reappraisals(c, sid)) for sid in SUBS}
    print(f"   第二次：{second.counted()}")
    print(f"   每份的重评链长度：{chains}")
    print("   ↑ 补上指纹的那两份都停在 1（没有第 2 条修订）；补不上的那份是 0，")
    print("     但它仍在范围里——每次跑都会再试一遍、再报一次，这是刻意的：")
    print("     把它剔出范围就等于让「还剩谁」从报告里消失。")

    ok_backfill = sorted(after.unfingerprinted) == legacy_blocked
    ok_idempotent = (
        all(o.state.startswith("missing_") for o in second.outcomes)
        and all(v <= 1 for v in chains.values())
    )
    ok_reportable = all(o.state.startswith("missing_") for o in real.blocked()) \
        and len(real.blocked()) == len(legacy_blocked)
    ok = ok_backfill and ok_idempotent and ok_reportable

    c.close()
    print("=" * 78)
    print("结论（由本次实测生成，不是预先写好的）：")
    print(f"  · 能重评的历史提交都补上了指纹，剩下的正好是补不上的那批 ⇒ {ok_backfill}；")
    print(f"  · 第二次没有新增任何修订，也没有重复改分 ⇒ {ok_idempotent}；")
    print(f"  · 补不上的被单独报出来、且数量对得上，没并进「已处理」⇒ {ok_reportable}；")
    print("  · 三条同时成立，「全库基线跑过了」才是一句有根据的话；")
    print("    第一条成立而第二条不成立 ⇒ 范围没收敛，每跑一次都在重复改分；")
    print("    把补不上的剔出范围来凑第二条（让它变成「没有活干」）⇒ 报告会变漂亮，")
    print("    而那些人从此再也不会被提起。")
    print(f"BACKFILLED={ok_backfill} IDEMPOTENT={ok_idempotent} REPORTED={ok_reportable}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
