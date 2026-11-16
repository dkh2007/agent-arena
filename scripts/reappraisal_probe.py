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

## 它不是产品代码

不落库、不改任何东西、只打印。正式的重评入口要看这一步的结论再定形态。
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


def _had_artifacts_at_the_time(report: dict) -> bool:
    """当时到底有没有产物？看 R1 门禁断言的判定，而不是看列里有没有值。

    这一条区分了两种完全不同的"库里没有产物"：
      - 当时有产物、只是**没存下来** ⇒ 这份提交真的不可重评（判定依赖产物）；
      - 当时就没交产物（R1 当时就 fail）⇒ 用空对象重评，结论与当时一致，可以重评。
    混成一种，会把"没交产物"的提交永远排除在重评之外——那类提交恰恰是
    判据改动后最需要复核的一批。
    """
    for item in report.get("assertions") or []:
        if item.get("id") == "R1_file_exists":
            return item.get("state") == "pass"
    return False


def _reappraise(conn, submission_id: str, task: dict) -> dict:
    """拿库里的 trace + artifacts，按传入的 task（可能是改过的 rubric）重跑一遍。

    返回 `{"state": ..., "report": ...}`。`state != "ok"` 时**不许**拿空产物顶上去算——
    那会把"这份不可回溯"算成"这份没交产物"，凭空给学生扣分。
    """
    row = store.load_submission(conn, submission_id)
    if row.get("trace") is None:
        return {"state": "missing_trace"}
    artifacts = row.get("artifacts")
    if artifacts is None and _had_artifacts_at_the_time(row["diagnosis"]):
        return {"state": "missing_artifacts"}
    if artifacts is None:
        artifacts = {}   # 当时就没交产物；空对象重评得到的是同一个结论
    raw = tp.assemble_raw(row["diagnosis"]["submission"], task,
                          trace=row["trace"], artifacts=artifacts)
    raw = tp.recompute_all(raw, task=task)
    return {"state": "ok", "report": dr.build_report(task, raw)}


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
        print(f"   {sid}: trace={'有' if row['trace'] else '无'}  "
              f"artifacts={'有' if row['artifacts'] else '无'}  "
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
    new_reports = []
    for sid in ("s-perfect", "s-nofile"):
        got = _reappraise(c, sid, changed)
        if got["state"] != "ok":
            print(f"   {sid}: **不可重评**（{got['state']}）—— 拿空产物顶上会凭空扣分")
            new_reports.append(old_reports[("s-perfect", "s-nofile").index(sid)])
            continue
        rep = got["report"]
        new_reports.append(rep)
        old_ratio = old_reports[("s-perfect", "s-nofile").index(sid)]["score"]["ratio"]
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
    print("4. 没有产物的提交能不能重评（历史数据补不回来的证据）")
    rows = c.execute("SELECT submission_id, artifacts, diagnosis FROM submissions").fetchall()
    blocked = [r["submission_id"] for r in rows
               if r["artifacts"] is None and _had_artifacts_at_the_time(json.loads(r["diagnosis"]))]
    truly_bare = [r["submission_id"] for r in rows
                  if r["artifacts"] is None and not _had_artifacts_at_the_time(json.loads(r["diagnosis"]))]
    print(f"   有产物但没存下来（真不可重评）：{blocked or '无'}")
    print(f"   当时就没交产物（可重评，结论不变）：{truly_bare or '无'}")

    c.close()
    print("=" * 74)
    print("结论（由本次实测生成，不是预先写好的）：")
    if delta is not None and abs(delta) >= 0.02:
        print(f"  · 有 trace + artifacts 的提交能按新判据重评，且差异传到了 P（ΔP={delta:+.4f}）；")
    elif delta is not None:
        print(f"  · 能重评，但本例差异 {delta:+.4f} 小于 0.02，不足以改变名次。")
    else:
        print("  · 本例无法比较（缺 P）——不要据此说「可回溯」。")
    print(f"  · v4 之前落库、当时有产物的提交共 {len(blocked)} 份不可重评；")
    print("  · 所以「标准改完后批量回溯重算」只在 v4 之后的新提交上完整成立，")
    print("    旧提交只能用「让学生重做」或「承认不可回溯」，不许拿空产物冒充重评。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
