"""B-04b 演示：一次提交怎么带上双评，低一致率怎么进队列、怎么被裁决。

**不联网、不花钱**：judge 用 `StubJudge`（按预设返回），只为把接线路径走给人看。
真模型的一致性分布见 `docs/judge-agreement-distribution.json`（17 次真调用）。

跑法（在哪个目录都行，脚本自己找仓根）：

    python scripts/judge_queue_walk.py

五段：
  1. 不传 judge —— 报告没有新字段，两个派生列是 NULL，队列空
  2. 传 judge、两次同档 —— 报告带字段、列被填，但**不入队**（一致）
  3. 传 judge、两次不同档 —— 入队，队列行里记着当时的一致率与阈值
  4. 人工裁决 —— 必须写理由；裁决后再裁一次会被拒
  5. 对撞 —— 上面第 2、3 段的分数与断言必须与第 1 段逐项相同（judge 不碰判定）

临时库落在系统临时目录，跑完即删，不在仓库留文件。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_judge as lj  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

AT = "2026-11-16T09:00:00Z"
FIXTURE = ROOT / "fixtures" / "raw-perfect.json"


def banner(text: str) -> None:
    print(f"\n=== {text} ===")


def payload(submission_id: str) -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sub = raw["submission"]
    return {
        "task_id": sub["task_id"],
        "task_version": "2026-11-13.1",
        "user_id": "u-demo",
        "agent_id": sub["agent_id"],
        "submission_id": submission_id,
        "task_git_hash": sub["task_git_hash"],
        "evaluated_at": AT,
        "trace": raw.get("trace"),
        "artifacts": raw.get("artifacts"),
    }


def submit(db: Path, registry, sid: str, **kw) -> dict:
    return api.submit(payload(sid), registry=registry,
                      conn_factory=lambda: store.connect(db), **kw)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        registry = api.TaskRegistry()
        api.bootstrap(db, registry)

        banner("1 不传 judge（默认）")
        plain = submit(db, registry, "demo-plain")
        print(f"  报告里有 judge 字段？{'judge_scores' in plain}")
        conn = store.connect(db)
        row = store.load_submission(conn, "demo-plain")
        print(f"  库里 judge_agreement = {row['judge_agreement']!r}，队列条数 = "
              f"{len(store.list_arbitration(conn, status=None))}")
        conn.close()

        banner("2 双评一致（两次都 3 档）")
        same = submit(db, registry, "demo-same",
                      judge=lj.StubJudge([lj.JudgeVerdict(rating=3),
                                          lj.JudgeVerdict(rating=3)]),
                      arbitration_threshold=0.5)
        print(f"  agreement = {same['judge_agreement']}")
        conn = store.connect(db)
        print(f"  入队？{'是' if store.list_arbitration(conn, status=None) else '否'}；"
              f"两个派生列已填 = {store.load_submission(conn, 'demo-same')['judge_agreement']}")
        conn.close()

        banner("3 双评不一致（3 档 / 2 档）")
        diff = submit(db, registry, "demo-diff",
                      judge=lj.StubJudge([lj.JudgeVerdict(rating=3),
                                          lj.JudgeVerdict(rating=2)]),
                      arbitration_threshold=1.0)
        print(f"  agreement = {diff['judge_agreement']}，ratings = "
              f"{[s['rating'] for s in diff['judge_scores']]}")
        conn = store.connect(db)
        pending = store.list_arbitration(conn)
        for r in pending:
            print(f"  队列：{r['submission_id']}  status={r['status']}  "
                  f"agreement={r['judge_agreement']}  threshold={r['threshold']}")
        print(f"  待办 {len(pending)} 条")

        banner("4 人工裁决（必须写理由）")
        try:
            store.resolve_arbitration(conn, "demo-diff", note="  ")
        except store.StoreError as exc:
            print(f"  空理由被拒：{exc}")
        done = store.resolve_arbitration(conn, "demo-diff", note="R2 的『合格』要写成数值区间")
        print(f"  裁决后 status={done['status']}  resolved_at={done['resolved_at']}")
        print(f"  理由：{done['resolution_note']}")
        try:
            store.resolve_arbitration(conn, "demo-diff", status="dismissed", note="再改一次")
        except store.StoreError as exc:
            print(f"  改判被拒：{exc}")
        print(f"  仍待办 {len(store.list_arbitration(conn))} 条")
        conn.close()

        banner("5 对撞：judge 有没有碰判定")
        # 只比判定相关字段：三份提交的 submission_id 不同，`submission` 段必然不同。
        judged = ("score", "assertions", "report_attribution", "attribution_policy",
                  "skill_gaps", "unattributed_patterns", "trace_audit", "process_fact")
        identical = all(same[k] == plain[k] for k in judged) and \
            all(diff[k] == plain[k] for k in judged)
        print(f"  score.ratio 三次 = {plain['score']['ratio']} / "
              f"{same['score']['ratio']} / {diff['score']['ratio']}")
        print(f"  八个判定字段逐项相同 = {identical}")
        print(f"  （不一致时 agreement={diff['judge_agreement']}，但分数一个字没变）")

    # 这一行必须**由上面算出来**，不能写死 True——写死就是恒真断言，演示就成了摆设。
    print(f"\nINTEGRITY_OK={identical}")
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
