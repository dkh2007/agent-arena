"""学习者视角的「成绩单」：给自己看的那一面（round-82）。

## 这一步为什么值得单独写

产品口径已定（2026-11-15 人拍板）：**成绩先给自己看，用于督促；排名是后继的、有条件的**。
这句话在代码上有一个直接推论，容易被漏掉：

> **要"看进步曲线"，不需要把 P 存进数据库。**
> 每份诊断报告都已经落库（`submissions.diagnosis`），P 可以随时按时间重放出来——
> `mastery.build_series()` 就是干这个的。存一份 P 快照只是省 CPU，换来的却是
> 破坏性 schema 变更（B-07c）与"快照与报告不一致"的第二真相源。

所以本脚本不改任何表、不加任何字段，只回答一个问题：
**一个学生打开自己的页面，应该看到什么。**

## 它刻意做对的两件事

1. **分数列与技能列分开写，且各自带限定词**（外部评审 round-81 的 A1）：
   一份"没交产物"的提交会让技能涨分，如果并排写成「0 分 / 掌握度 ↑」，学生只会觉得系统在骗他。
   所以这里技能行带 `⚠ 其中有 N 份整份不合格`，分数不在同一行出现。
2. **不出现名次、不出现他人数据**：排名没开，脚本就不许先长出这个字段——
   否则界面上先有了名次，口径就被界面倒逼了。

## 用法

    python scripts/mastery_scorecard.py            # 内置三份演示提交（不联网、不留库）
    python scripts/mastery_scorecard.py --db x.db  # 读真实库（需已有 submissions）
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import mastery as m  # noqa: E402
from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
DEMO_DB = ROOT / ".git" / "scorecard-demo.db"
USER = "demo-user"

# 技能 id → 学生看得懂的名字。技能词表在 docs/skill-taxonomy-v0.yaml，这里只是展示名。
SKILL_LABELS = {
    "multi_step_planning": "多步任务规划",
    "failure_retry": "失败后重试",
    "tool_param_validation": "工具参数不瞎猜",
}

PLAN = [
    ("raw-perfect", "满分"),
    ("raw-fake-retry", "假重试"),
    ("raw-no-file", "没交产物（门禁不合格）"),
    ("raw-no-trace", "没交过程记录"),
]


def _report(name: str, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def _trend(series: m.MasterySeries) -> str:
    """趋势用整条序列的箭头表示；只有一步时不给箭头（一步看不出趋势，别编）。"""
    if not series.steps:
        return "还没有可用的证据"
    if len(series.steps) == 1:
        return "首次观测"
    first, last = series.steps[0].p_after, series.steps[-1].p_after
    arrow = "↑" if last > first else ("↓" if last < first else "→")
    return f"{arrow} {first:.2f} → {last:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="学习者视角的成绩单（不排名、不展示他人）")
    ap.add_argument("--db", help="读真实库；不给则用内置演示数据")
    ap.add_argument("--user", default=USER)
    ap.add_argument("--as-of", dest="as_of", default=None,
                    help="按这个时刻读衰减后的掌握度（不给＝只看原始 P，行为与以前一致）")
    args = ap.parse_args()

    if args.db:
        conn = store.connect(Path(args.db))
        db_path = None
    else:
        DEMO_DB.unlink(missing_ok=True)
        conn = store.connect(DEMO_DB)
        db_path = DEMO_DB
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        store.register_task(conn, TASK_YAML)
        base = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for i, (fixture, _) in enumerate(PLAN, start=1):
            at = (base + timedelta(days=i)).isoformat()
            store.save_submission(conn, _report(fixture, f"demo-{i}", at), user_id=args.user, created_at=at)

    try:
        print(f"=== 成绩单（{args.user}）：只给你自己看，不参与任何排名 ===")
        print()
        if args.as_of:
            header = (f"{'技能':<14}{'掌握度(原始)':<14}{'现在(' + args.as_of[:10] + ')':<18}"
                      f"{'次数':<6}{'趋势':<18}备注")
        else:
            header = f"{'技能':<14}{'当前掌握度':<12}{'观察次数':<10}{'趋势':<18}备注"
        print(header)
        print("-" * 78)
        for skill_id, label in SKILL_LABELS.items():
            series = m.build_series(conn, args.user, skill_id)
            if series.p is None:
                print(f"{label:<14}{'—':<12}{0:<10}{'还没有可用的证据':<18}")
                continue
            gate_bad = sum(1 for s in series.steps if s.gate_status != "ok")
            note = f"⚠ 其中有 {gate_bad} 份整份不合格（证据仍计入）" if gate_bad else ""
            if args.as_of:
                try:
                    _, dec = m.decayed_at(conn, args.user, skill_id, as_of=args.as_of)
                except Exception as exc:      # 时间倒流等：如实说，不猜一个数
                    print(f"{label:<14}{series.p:<14.2f}{'读不出':<18}{len(series.steps):<6}"
                          f"{_trend(series):<18}{note} · {exc}")
                    continue
                extra = "" if dec.applied else f"（未衰减：{dec.reason}）"
                print(f"{label:<14}{series.p:<14.2f}{dec.p_effective:<18.2f}"
                      f"{len(series.steps):<6}{_trend(series):<18}{note}{extra}")
            else:
                print(f"{label:<14}{series.p:<12.2f}{len(series.steps):<10}{_trend(series):<18}{note}")

        print()
        print("说明：")
        print("  · 掌握度 = 最近表现估计，不是分数。分数看单次提交，掌握度看长期。")
        print("  · 「整份不合格」的提交仍会提供技能证据（数据缺失 ≠ 能力不足），")
        print("    但分数是 0——所以两列分开写，不要并成一句读。")
        print("  · 本表不存快照：每次都从已落库的报告重放，所以不会出现「表和报告不一致」。")
        print("  · 排名未开。开排名前必须先切口径（否则交白卷能把掌握度顶满），见 B-07b。")
        if args.as_of:
            if not args.db:
                print("  · 注意：内置演示数据**不建卡片**，所以这一列会全部显示「未衰减：no_card」。")
                print("    想看真实衰减用 `python scripts/decay_walk.py`，或对真库加 --db。")
            print("  · 「现在」这一列是**读法**：P 乘上遗忘曲线 R(t,S)，库里什么都没改。")
            print("    没有卡片的技能不会衰减（显示「未衰减：no_card」）——")
            print("    「从没失败过所以没建卡」不等于「忘光了」。")
        else:
            print("  · 想看你多久没练会掉多少：加 --as-of 2026-12-31（不给就只看原始 P）。")
        return 0
    finally:
        conn.close()
        if db_path is not None:
            db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
