"""B-04d 接线的端到端演示：一份争议提交，从"被标待复核"到"成绩按新判据改掉"。

前几轮分别做完了链上的四块，本脚本把它们串起来跑给人看——**不联网、不花钱**：

    judge 用 StubJudge（预设两次不同档）→ 入队 → 人工裁决 → 重评 → P 变

跑法（在哪个目录都行，脚本自己找仓根）：

    python scripts/reappraisal_walk.py

五段：
  1. 交一份满分作答，记下 P 与生效分数
  2. 双评不一致 ⇒ 进待办，成绩挂「标准待复核」，但**照算**（甲不是乙）
  3. 人工裁决"判据要改"，写理由
  4. 按改后的判据重评 ⇒ 新报告落 `reappraisals`，`submissions` 一行不动，P 跟着变
  5. 对撞：`submissions` 行数不变、原始诊断逐字节相同、P 的步数不变

最后一行打 `INTEGRITY_OK=True/False`——它由第 5 段的实际比对算出，不是写死的。
临时库落在系统临时目录，跑完即删，不在仓库留文件。
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import llm_judge as lj  # noqa: E402
from arena import mastery as ms  # noqa: E402
from arena import mastery_snapshot as snap  # noqa: E402
from arena import reappraisal as rap  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

AT = "2026-11-16T09:00:00Z"
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
SKILL = "multi_step_planning"
FIXTURE = ROOT / "fixtures" / "raw-perfect.json"


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def payload(submission_id: str) -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": "u-walk",
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "invoke": sub.get("invoke"),
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


def changed_rubric() -> dict:
    """人把标准答案改掉：R2 期望总额 1837.99 → 1838.99。"""
    task = copy.deepcopy(dr.load_task(TASK_PATH))
    for spec in task["rubric_credit"]:
        if spec["id"] == "R2_report_correct":
            spec["expected"]["total_amount"] += 1.0
    return task


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="reap-walk-")
    db = Path(tmp) / "walk.db"
    registry = api.TaskRegistry()
    api.bootstrap(db, registry)

    def conn():
        return store.connect(db)

    # 两次给不同档 ⇒ 一致率低于阈值 ⇒ 进队列。StubJudge 的结果是 pop(0) 消耗的，
    # 必须现造，不能模块级共享。
    disagree = lj.StubJudge([lj.JudgeVerdict(rating=4, patterns=()),
                             lj.JudgeVerdict(rating=1, patterns=())])

    banner("1. 交一份满分作答（带 judge 双评，两次不同档）")
    api.submit(payload("s-walk"), registry=registry, conn_factory=conn,
               judge=disagree, arbitration_threshold=0.8)

    c = conn()
    original = store.load_submission(c, "s-walk")
    original_report = store.load_report(c, "s-walk")
    p_before = ms.build_series(c, "u-walk", SKILL).p
    steps_before = len(ms.build_series(c, "u-walk", SKILL).steps)
    print(f"   提交后 P = {p_before:.4f}，生效分数 = {store.effective_ratio(c, 's-walk'):.2f}")

    banner("2. 争议被发现：进待办 + 挂「标准待复核」，但成绩照算")
    queue = store.load_arbitration(c, "s-walk")
    print(f"   队列状态 = {queue['status']}   一致率 = {queue['judge_agreement']:.2f}"
          f"   阈值 = {queue['threshold']:.2f}")
    marked = snap.is_provisional(c, "u-walk", SKILL)
    print(f"   成绩挂待复核 = {marked}（进榜时会被默认排除）")
    print(f"   但 P 仍是 {ms.build_series(c, 'u-walk', SKILL).p:.4f}——甲不是乙，学习者没被冻住")

    banner("3. 人工裁决：判据要改")
    note = "R2 的标准答案写错了（1837.99 应为 1838.99），先改判据再重评受影响的提交"
    entry = store.resolve_arbitration(c, "s-walk", status="resolved", note=note, at=AT)
    print(f"   状态 = {entry['status']}   理由 = {entry['resolution_note'][:28]}…")
    print(f"   裁决后成绩还挂待复核吗 = {snap.is_provisional(c, 'u-walk', SKILL)}"
          "（标记是派生的，裁决一落就消失）")
    print(f"   但分数还是 {store.effective_ratio(c, 's-walk'):.2f}——"
          "裁决只写了个备注，没改任何数字（这就是「不回流」）")

    banner("4. 按改后的判据重评（这一步是本轮接上的）")
    out = rap.reappraise(c, "s-walk", changed_rubric(),
                         reason="rubric: R2.expected.total_amount 1837.99 -> 1838.99",
                         created_at=AT)
    print(f"   结论 = {out.state}   revision = {out.revision}")
    print(f"   分数 {out.old_ratio:.2f} → {out.new_ratio:.2f}   Δ = {out.delta:+.2f}"
          f"   需人工复核 = {out.needs_manual_review}")
    p_after = ms.build_series(c, "u-walk", SKILL).p
    print(f"   P {p_before:.4f} → {p_after:.4f}（{p_after - p_before:+.4f}）")

    banner("5. 对撞：重评换的是结论，不是证据")
    rows = store.count_submissions(c)
    same_report = store.load_report(c, "s-walk") == original_report
    same_score = store.load_submission(c, "s-walk")["assert_score"] == original["assert_score"]
    steps_after = len(ms.build_series(c, "u-walk", SKILL).steps)
    effective_moved = store.effective_ratio(c, "s-walk") == out.new_ratio
    revisions = len(store.list_reappraisals(c, "s-walk"))

    print(f"   submissions 行数        {rows}（原 1）                 -> {rows == 1}")
    print(f"   原始 diagnosis 逐字节同  {'是' if same_report else '否'}"
          f"                              -> {same_report}")
    print(f"   assert_score 未被改写    {'是' if same_score else '否'}"
          f"                              -> {same_score}")
    print(f"   P 的步数不变             {steps_before} -> {steps_after}"
          f"                          -> {steps_before == steps_after}")
    print(f"   生效分数 = 新结论        {'是' if effective_moved else '否'}"
          f"                              -> {effective_moved}")
    print(f"   重评记录条数             {revisions}（append-only）          -> {revisions == 1}")

    ok = all([rows == 1, same_report, same_score, steps_before == steps_after,
              effective_moved, revisions == 1])
    c.close()
    print(f"\nINTEGRITY_OK={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
