"""复习提醒页的命令行入口（B-10，2026-11-22）。

    python scripts/review_page.py                    # 内置演示数据（不联网、不留库）
    python scripts/review_page.py --db arena.db --user s1 --at 2026-11-22T21:00+08:00

为什么默认跑演示：这条链路需要"到期的复习项"才看得见东西，而真实库里的到期项
取决于谁在什么时候练过。演示数据固定三份，页面长什么样可以对着看、对着改。

## `--json` / `--out`：给 Android 页供数（2026-11-24）

    python scripts/review_page.py --at 2026-11-24T21:00:00+08:00 \\
        --out android/app/src/main/assets/reminder.json

输出的就是 `build_reminder()` 的返回值，**一个字段都不加、一个字段都不减**。
页面的排序、上限、`cap=0` 时该说哪句话，全部在 Python 侧算完；Kotlin 只负责摆位置。
把判断抄进 Kotlin 会变成第二真相源——两处各写一份"今天该给几项"，
迟早有一处先改，然后页面上出现"昨天该看的今天还在"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import reminder  # noqa: E402
from arena import store  # noqa: E402
from arena.diagnosis_report import load_skill_labels  # noqa: E402

TZ = timezone(timedelta(hours=8))


def _demo_items(at: datetime) -> list[dict]:
    """三份到期项 + 一份未到期项：够看出"逾期 / 今天到期 / 排到明天 / 还没到"四种情况。"""
    def iso(delta_days: float) -> str:
        return (at + timedelta(days=delta_days)).isoformat(timespec="seconds")

    return [
        {"review_id": "r-1", "user_id": "demo", "skill_id": "tool_param_validation",
         "kind": "fault_injection", "task_id": "dbg-001-order-report",
         "due": iso(-2.2), "status": "pending"},
        {"review_id": "r-2", "user_id": "demo", "skill_id": "multi_step_planning",
         "kind": "variant", "task_id": "dbg-003-purchase-report",
         "due": iso(-0.1), "status": "pending"},
        {"review_id": "r-3", "user_id": "demo", "skill_id": "failure_retry",
         "kind": "fault_injection", "task_id": "ret-002-second-page-fault",
         "due": iso(-0.02), "status": "pending"},
        {"review_id": "r-4", "user_id": "demo", "skill_id": "tool_param_validation",
         "kind": "variant", "task_id": "prm-002-ceiling-5-long",
         "due": iso(3), "status": "pending"},
    ]


def _task_titles(folder: Path) -> dict[str, str]:
    """从任务库里取 task_id → 标题。取不到就留空，页面只少一行括号。"""
    titles: dict[str, str] = {}
    for path in sorted(folder.glob("*.yaml")):
        task_id = title = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("task_id:") and task_id is None:
                task_id = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("title:") and title is None:
                title = line.split(":", 1)[1].strip().strip("'\"")
        if task_id and title:
            titles[task_id] = title
    return titles


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打印「今天该复习什么」")
    ap.add_argument("--db", help="数据库文件；不给就跑内置演示数据")
    ap.add_argument("--user", default="demo", help="用户 id（默认 demo）")
    ap.add_argument("--at", help="把「现在」固定成某个时刻（ISO，带时区）")
    ap.add_argument("--cap", type=int, help="覆盖每日上限（默认读用户设置）")
    ap.add_argument("--tasks", default=str(ROOT / "tasks"), help="任务目录，用来显示题目名")
    ap.add_argument("--json", action="store_true",
                    help="把 build_reminder() 的返回值原样打成 JSON（Android 页供数用）")
    ap.add_argument("--out", metavar="PATH",
                    help="把 JSON 写进文件（隐含 --json），例如 assets/reminder.json")
    args = ap.parse_args(argv)

    at = args.at or datetime.now(TZ).isoformat(timespec="seconds")
    titles = _task_titles(Path(args.tasks))
    try:
        if args.db:
            conn = store.connect(args.db)
            items = store.list_review_items(conn, args.user)
            cap = args.cap if args.cap is not None else store.daily_review_cap(conn, args.user)
        else:
            items = _demo_items(datetime.fromisoformat(at))
            cap = 2 if args.cap is None else args.cap
        data = reminder.build_reminder(
            items, at=at, cap=cap, skill_names=load_skill_labels(), task_titles=titles
        )
    except (reminder.ReminderError, store.StoreError) as exc:
        print(str(exc), flush=True)
        return 2

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 换行固定成 \n：这份 JSON 要进版本库，CRLF 会让每次 diff 多出一堆假改动。
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"已写入 {path}（今日 {len(data['today'])} 项 / 排队 {data['due_total']} 项）", flush=True)
        return 0

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
        return 0

    print(reminder.format_reminder(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
