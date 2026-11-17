"""把处置计划排版成「一页纸」：给维护者看的一份，不是给学生的。

跑法：
    python scripts/notices_page.py --db <库> --out <html> \
        --changed-on 2026-11-10 --what "改了一条判据的期望值"

为什么单独一个脚本：`plan_notices()` 能被程序消费（dict / 断言），但**人要看的是分布和原文**——
"这一批里几个人会被通知、他们看到的那句话长什么样、哪些还待定"。
把这四件事排在一页上，比让人翻 JSON 更容易发现"这句话不对劲"。

刻意不做：不发送、不落库、不生成学生可见的页面。这里输出的是一份内部核对件。
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import notices as nt  # noqa: E402
from arena import store  # noqa: E402

STATE_LABEL = {
    "recalculated": ("已重算", "分数变了 → 页面上说一句"),
    "unchanged": ("未受影响", "只留档案：已复核，结论不变"),
    "legacy_scored": ("旧标准有效、判不了", "原分 + 可见标注"),
    "legacy_unscored": ("当年未通过、判不了", "无分，诊断保留，不告知"),
    "pending_mastery": ("待定", "掌握度口径未定，先标出来"),
}


def _row(notice: nt.Notice) -> str:
    cells = "".join(
        f"<div class='el'><span class='k'>{html.escape(k)}</span>"
        f"<span class='v'>{html.escape(t)}</span></div>"
        for k, t in notice.elements
    ) or "<div class='el muted'>（不告知）</div>"
    flag = "<span class='tag warn'>待拍</span>" if notice.needs_decision else ""
    # 已标「确认不可回溯」的行照常出现在表里（标记只停重试，不停交代），
    # 这里加一个中性标记，免得看的人以为"怎么还在"。
    flag += "<span class='tag'>已确认不可回溯</span>" if notice.retired else ""
    return (f"<tr><td><code>{html.escape(notice.submission_id)}</code></td>"
            f"<td>{html.escape(notice.user_id)}</td>"
            f"<td>{html.escape(STATE_LABEL[notice.state][0])}{flag}</td>"
            f"<td><code>{html.escape(notice.disclosure)}</code></td>"
            f"<td>{cells}</td></tr>")


def render(plan: nt.NoticePlan, *, task_id: str, changed_on: str, what: str) -> str:
    counted = plan.counted()
    cards = "".join(
        f"<div class='card'><div class='n'>{counted[name]}</div>"
        f"<div class='t'>{STATE_LABEL[name][0]}</div>"
        f"<div class='d'>{STATE_LABEL[name][1]}</div></div>"
        for name in nt.RECORD_STATES
    )
    rows = "".join(_row(n) for n in plan.notices)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>处置计划一页纸 · {html.escape(task_id)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font: 14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         margin: 0 auto; max-width: 1080px; padding: 32px 24px 56px; color: #1f2328; background: #fff; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
  .cards {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 26px; }}
  .card {{ border: 1px solid #e3e6ea; border-radius: 10px; padding: 12px 14px; background: #fafbfc; }}
  .card .n {{ font-size: 24px; font-weight: 700; color: #111; }}
  .card .t {{ font-weight: 600; margin-top: 2px; }}
  .card .d {{ color: #6b7280; font-size: 12px; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; vertical-align: top; padding: 10px 8px; border-bottom: 1px solid #eceff2; }}
  th {{ font-size: 12px; color: #6b7280; font-weight: 600; }}
  code {{ font: 12px ui-monospace, Consolas, monospace; color: #444; }}
  .el {{ margin: 2px 0; }}
  .el .k {{ display: inline-block; min-width: 92px; color: #8a6d3b; font: 11px ui-monospace, monospace; }}
  .el .v {{ }}
  .muted {{ color: #9aa0a6; }}
  .tag {{ font-size: 11px; padding: 1px 6px; border-radius: 999px; margin-left: 6px; }}
  .tag.warn {{ background: #fff4e5; color: #95590b; border: 1px solid #f3d4a4; }}
  footer {{ margin-top: 24px; color: #6b7280; font-size: 12px; }}
</style></head><body>
<h1>处置计划一页纸</h1>
<div class="meta">任务 <code>{html.escape(task_id)}</code> · 判据修订 {html.escape(changed_on)}
 · 改了什么：{html.escape(what)} · 本次改到的规则：{html.escape(', '.join(plan.changed_rules) or '（无）')}</div>
<div class="cards">{cards}</div>
<table>
<thead><tr><th>提交</th><th>学生</th><th>记录状态</th><th>谁会看到</th><th>要说的原文（按「件」）</th></tr></thead>
<tbody>{rows}</tbody></table>
<footer>本文件由 <code>scripts/notices_page.py</code> 生成，只读库里现有数据；它不发通知、不改分数。
「待拍」那一格是掌握度口径（B-04h），在拍之前不会有任何对外说法。</footer>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把处置计划排成一页纸（只读）")
    ap.add_argument("--db", required=True)
    ap.add_argument("--task", default=str(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    ap.add_argument("--changed-on", required=True)
    ap.add_argument("--what", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    conn = store.connect(Path(args.db))
    try:
        task = dr.load_task(args.task)
        plan = nt.plan_notices(conn, task, changed_on=args.changed_on,
                               what_changed=args.what)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(plan, task_id=plan.task_id, changed_on=args.changed_on,
                              what=args.what), encoding="utf-8")
        print(f"OK {out}")
        print(f"   {plan.counted()}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
