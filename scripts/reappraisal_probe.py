"""探查：判据改了之后，能不能按新标准把历史提交重算一遍（B-04d 甲+护栏的第三根柱子）。

## 为什么要有这个脚本

人拍的口径是「甲 + 护栏」，其中一句是**「标准改完后批量回溯重算」**。
这句话能不能兑现，取决于一件很朴素的事：**库里的历史提交，够不够当重评的输入。**

`recompute_all()` 只是把已有报告重放一遍——报告没变，算出来还是同一个数。
真要按新判据重算，必须用 **原始 trace + 原始产物** 重新跑一遍断言执行器。
所以本脚本要实测三件事，缺一件"回溯"就是空话：

1. 库里存的 trace + artifacts 足以重建一份 raw；
2. 用改写过的 rubric 重评这份 raw，会得到**不一样**的结论；
3. 这个不一样能一路传到 P（不是只改了个报告字段）。

另外实测第四个：**没有产物的旧提交不可重评**——这一条是"历史数据补不回来"的证据，
不是猜测。

## 它与产品代码的关系（round-92 起）

重评逻辑已经落成 `src/arena/reappraisal.py`。本脚本**不再自带一份重评实现**——
那会变成两套判据，一处改了另一处不知道。现在它只做验收：调产品函数、把结果打出来、
把"能不能回溯"这件事变成可复核的输出。

判据只有一处：`reappraisal.rebuild_report()`。它对缺产物的提交一律拒绝
（`missing_artifacts`），不用空对象顶替——理由见该模块文档。

用法：`python scripts/reappraisal_probe.py`（任意工作目录，绝对路径可调）。
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mastery  # noqa: E402
from arena import reappraisal as rap  # noqa: E402
from arena import store  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
SKILL = "multi_step_planning"
AT = "2026-11-16T09:00:00Z"


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _payload(fixture: str, submission_id: str) -> dict:
    raw = _fixture(fixture)
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": "u-probe",
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


def _p_of(reports: list[dict], skill_id: str) -> float | None:
    evidence = [mastery.skill_evidence(r, skill_id) for r in reports]
    return mastery.replay(evidence, user_id="u-probe", skill_id=skill_id).p


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="reappraise-")
    db = Path(tmp) / "probe.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    print("=" * 74)
    print("0. 交两份提交（一份满分、一份没交产物），记录现在的 P")
    api.submit(_payload("raw-perfect", "s-perfect"), registry=registry, conn_factory=conn)
    api.submit(_payload("raw-no-file", "s-nofile"), registry=registry, conn_factory=conn)

    c = conn()
    old_reports = [store.load_report(c, sid) for sid in ("s-perfect", "s-nofile")]
    old_p = _p_of(old_reports, SKILL)
    print(f"   旧 P（{SKILL}）= {old_p:.4f}")
    for sid in ("s-perfect", "s-nofile"):
        row = store.load_submission(c, sid)
        # 用 `is None` 判而不是真假值：`{}` 是"交了但为空"，与"没交（NULL）"是两件事，
        # 混起来会把 s-nofile 显示成"没交产物"，而它其实交了个空对象。
        print(f"   {sid}: trace={'有' if row['trace'] is not None else '无'}  "
              f"artifacts={'无(NULL)' if row['artifacts'] is None else ('空({})' if not row['artifacts'] else '有')}  "
              f"ratio={row['assert_score']:.2f}")

    print("=" * 74)
    print("1. 模拟「判据改了」：R2 的标准答案 total_amount 从 1837.99 改成 1838.99")
    task = dr.load_task(TASK_PATH)
    changed = copy.deepcopy(task)
    for spec in changed["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] = 1838.99
    print(f"   原标准：{task['rubric_credit'][0]['expected']}")
    print(f"   新标准：{changed['rubric_credit'][0]['expected']}")

    print("=" * 74)
    print("2. 按新标准重评历史提交（用库里存的 trace + artifacts，不碰磁盘夹具）")
    order = ("s-perfect", "s-nofile")
    new_reports = []
    for sid in order:
        rep, state = rap.rebuild_report(c, sid, changed)
        if rep is None:
            print(f"   {sid}: **不可重评**（{state}）—— 拿空产物顶上会凭空扣分")
            new_reports.append(old_reports[order.index(sid)])
            continue
        new_reports.append(rep)
        old_ratio = old_reports[order.index(sid)]["score"]["ratio"]
        print(f"   {sid}: ratio {old_ratio:.2f} → {rep['score']['ratio']:.2f}"
              f"   ({'变了' if rep['score']['ratio'] != old_ratio else '没变'})")

    print("=" * 74)
    print("3. 重放：新 P 与旧 P 的差，能不能传到掌握度")
    new_p = _p_of(new_reports, SKILL)
    delta = None if (old_p is None or new_p is None) else new_p - old_p
    print(f"   旧 P = {old_p:.4f}")
    print(f"   新 P = {new_p:.4f}")
    print(f"   ΔP  = {delta:+.4f}" if delta is not None else "   ΔP  = 不可比")
    needs_review = delta is not None and abs(delta) >= 0.02
    print(f"   超过人工复核阈值 0.02 ⇒ {'是（要人看一眼）' if needs_review else '否（差异不足以改变名次）'}")

    print("=" * 74)
    print("4. 缺产物的提交一律不重评（保守判据，见 reappraisal 模块文档）")
    rows = c.execute("SELECT submission_id, artifacts FROM submissions").fetchall()
    blocked = [r["submission_id"] for r in rows if r["artifacts"] is None]
    print(f"   artifacts 为 NULL 的提交：{blocked or '无'} —— 这些不重评，成绩原地不动")
    print("   为什么不用空对象顶替：那会把「我们没存下来」算成「他没交」，")
    print("   而两份在报告里长得一模一样（决策 7 要挡的正是这个）。")

    print("=" * 74)
    print("5. 改完标准，系统能不能自己算出「谁受影响」，算完还收得回来")
    # 这一段是 B-04e ⓑ 的验收：范围必须**自己**算出来（不再靠人报 user_id），
    # 而且批量重评完必须自己变空——否则同一条判据改动每跑一次就把同一批再评一次。
    orig_scope = rap.scope_for_task(c, task)
    print(f"   判据没改时的范围：current={list(orig_scope.current)}"
          f"  stale={list(orig_scope.stale)}")
    assert orig_scope.is_empty, "判据没改却说有人「过期」——范围算错了"

    pending = rap.scope_for_task(c, changed)
    print(f"   判据改后自动算出的范围：stale={list(pending.stale)}"
          f"  ← 人只给了任务和新判据，没报任何 user_id")

    outs = rap.reappraise_user(c, "u-probe", changed, reason="probe: R2 标准答案 +1",
                               created_at=AT)
    states = {o.submission_id: o.state for o in outs}
    print(f"   批量重评结果：{states}")

    after = rap.scope_for_task(c, changed)
    print(f"   重评后的范围：stale={list(after.stale)}  current={list(after.current)}")
    converged = after.stale == () and set(after.current) == set(states)
    print(f"   范围收敛 ⇒ {converged}"
          f"{'' if converged else '  ← 同一批会被反复重评，机制等于没有'}")

    back = rap.scope_for_task(c, task)
    print(f"   把标准改回原样后：stale={list(back.stale)}"
          f"  ← 范围跟着判据走，不跟着「重评过没有」走")
    track_rubric = set(back.stale) == set(states)

    c.close()
    print("=" * 74)
    print("结论（由本次实测生成，不是预先写好的）：")
    if delta is not None and abs(delta) >= 0.02:
        print(f"  · 有 trace + artifacts 的提交能按新判据重评，且差异传到了 P（ΔP={delta:+.4f}）；")
    elif delta is not None:
        print(f"  · 能重评，但本例差异 {delta:+.4f} 小于 0.02，不足以改变名次。")
    else:
        print("  · 本例无法比较（缺 P）——不要据此说「可回溯」。")
    print(f"  · 库里有 {len(blocked)} 份提交因缺产物不可重评（成绩不动，不冒算）；")
    print("  · 所以「标准改完后批量回溯重算」只在 v4 之后的新提交上完整成立，")
    print("    旧提交只能用「让学生重做」或「承认不可回溯」，不许拿空产物冒充重评。")
    print("  · 受影响范围由 scope_for_task() 按任务自己算出（ⓑ），"
          f"重评后收敛={converged}、跟判据走={track_rubric}：")
    print("    这两条同时成立，第四句护栏「标准改完后批量回溯重算」才算兑现——")
    print("    只有前者不成立（范围不收），同一批会被每扫一次重评一次；")
    print("    只有后者不成立（范围不跟判据走），改了标准也不会有人进范围。")
    print("  · 产品入口：reappraisal.reappraise() / reappraise_user() / scope_for_task()"
          "（都不是只算不写的脚本）。")
    ok = converged and track_rubric and (delta is None or abs(delta) >= 0.02)
    print(f"SCOPE_CONVERGED={converged} TRACKS_RUBRIC={track_rubric} "
          f"DELTA_MEASURED={delta is not None}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
