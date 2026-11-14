"""B-07 的十分钟验证：造三次提交 → 打印 P 序列 → 与手算并排。

用法（不需要密钥、不联网、不往仓库以外写字）：

    python scripts/mastery_walk.py
    python scripts/mastery_walk.py --skill failure_retry --lam 0.5

做完这一步要能回答三件事：
  ① 同一用户同一技能的连续三次提交，P 是否按 `P_k=(1-λ)P_{k-1}+λc_k` 变化；
  ② `unscored` / `blocked` 的提交是否**原地不动**（决策 7）；
  ③ λ 换一个值，结论会移动多少（"参数是假设"的可操作表达）。

第三件事是刻意加的：λ 现在没有数据可标定，任何人问"凭什么是 0.3"，
这个脚本能立刻说清"换成 0.5 会变成多少"，而不是只给一个信心满满的数字。
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

DB_PATH = ROOT / ".git" / "mastery-demo.db"
TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "demo-user"

# 三次提交的剧本：满分 → 假重试 → 过程缺失。
# 选这三个形态是因为它们分别覆盖"证据正常 / 真失败 / 缺数据"三类，缺一样就看不出决策 7。
PLAN = [
    ("raw-perfect", "满分（三条断言全过）"),
    ("raw-fake-retry", "假重试（R2/R3 失败，R4 通过）"),
    ("raw-no-trace", "没交过程记录（R3/R4 判 unscored）"),
]


def _report(name: str, submission_id: str, at: str) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="掌握度 P 的可复核演示")
    ap.add_argument("--skill", default="multi_step_planning",
                    help="技能 id（默认 multi_step_planning）")
    ap.add_argument("--lam", type=float, default=m.LAMBDA_DEFAULT, help="平滑系数 λ")
    args = ap.parse_args()
    skill, lam = args.skill, args.lam

    DB_PATH.unlink(missing_ok=True)
    conn = store.connect(DB_PATH)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        store.register_task(conn, TASK_YAML)

        base = datetime(2026, 11, 1, tzinfo=timezone.utc)
        print(f"用户 {USER} / 技能 {skill} / λ={lam} / 初值=首次观测播种")
        print()
        print("=== 三次提交（真落进 SQLite，再从库里读回来折 P） ===")
        print(f"{'#':<3}{'夹具':<18}{'c':<12}{'计入权重':<10}{'P 前':<12}{'P 后':<10}说明")

        for i, (fixture, note) in enumerate(PLAN, start=1):
            at = (base + timedelta(days=i)).isoformat()
            sid = f"demo-{i}"
            report = _report(fixture, sid, at)
            store.save_submission(conn, report, user_id=USER, created_at=at)

            ev = m.skill_evidence(report, skill, at=at)
            series = m.build_series(conn, USER, skill, lam=lam)
            step = next((s for s in series.steps if s.submission_id == sid), None)
            c_text = f"{ev.c:.2f}" if ev.counts else "无证据"
            weight = f"{ev.counted_weight:g}"
            if step is None:
                print(f"{i:<3}{fixture:<18}{c_text:<12}{weight:<10}{'—':<12}{'—':<10}"
                      f"{note}　⇒ 无计入断言，P 原地不动（决策 7）")
            else:
                before = "播种" if step.seeded else f"{step.p_before:.4f}"
                print(f"{i:<3}{fixture:<18}{c_text:<12}{weight:<10}{before:<12}"
                      f"{step.p_after:.4f}     {note}")

        series = m.build_series(conn, USER, skill, lam=lam)
        print()
        print("=== 手算对照（与上面最后一列必须逐位相同） ===")
        p: float | None = None
        for i, step in enumerate(series.steps, start=1):
            if step.seeded:
                p = step.c
                print(f"  P_1 = c_1 = {step.c:.4f}（初值播种，不发明先验）")
            else:
                p_prev = p
                assert p_prev is not None
                p = (1 - lam) * p_prev + lam * step.c
                print(f"  P_{i} = (1-{lam})·{p_prev:.4f} + {lam}·{step.c:.4f} = {p:.4f}")
        if series.skipped:
            names = ", ".join(s.submission_id for s in series.skipped)
            print(f"  跳过 {names}（{series.skipped[0].reason}）——不推进 P")
        print(f"  终值 P = {series.p:.4f}" if series.p is not None else "  终值 P = None（无任何证据）")
        print(f"  被排除的断言：{series.ignored or '无'}")

        print()
        print("=== λ 敏感性（λ 尚未标定，这行是唯一诚实的回答方式） ===")
        print("  打出整条序列而不是终值：λ 大小与终值**不是单调关系**——")
        print("  λ 大时最近一次高分拉回得更猛，只看终值会得出「λ 越大越保守」这种错印象。")
        evidence = [
            m.skill_evidence(_report(f, f"x{i}", (base + timedelta(days=i)).isoformat()),
                             skill, at=(base + timedelta(days=i)).isoformat())
            for i, (f, _) in enumerate(PLAN, start=1)
        ]
        for lam_value, s in m.series_for_lambdas(evidence, user_id=USER, skill_id=skill).items():
            path = " → ".join(f"{step.p_after:.4f}" for step in s.steps) or "None"
            marker = "  ← 当前" if abs(lam_value - lam) < 1e-9 else ""
            print(f"  λ={lam_value:<4} {path}{marker}")

        print()
        print("说明：λ 的具体数值属**假设**（无答题数据可标定，见 docs/BACKLOG.md B-07b）；")
        print("      本脚本证明的是「公式落地正确」，不是「λ=0.3 是对的」。")
        print("      另：衰减尚未实现（依赖复习间隔 B-08），所以 P 不随自然时间下降。")
        return 0
    finally:
        conn.close()
        DB_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
