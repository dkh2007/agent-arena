"""把诊断报告导成一个自包含的 HTML 页（B-48）。

## 它替代的是什么

到现在为止，报告只有两种形态：一份 JSON（人读不了）、一个安卓页面（要装 APK、
数据还是打包死的）。试用要给同学"看完能说话"的东西，而他们手上只有浏览器。

所以这里只做一件事：**报告 JSON → 一个 .html，双击就能看，不联网、没有外链**。

## 一条硬规则：不出现机器码

页面里不许出现断言 id（`R2_report_correct`）、字段名（`valid_count`）、
`expected` / `observed` 这类词。这些在 `docs/android-ui-handoff.md` §4.6 已经定过：
学习者看到的应该是「结论是否与判据一致」「有效条数你填了 9，按剔除规则应为 17」，
而不是"哪条断言挂了"。`tests/test_report_html.py` 把这条锁死。

工具名（`fetch_orders`）与参数名（`offset` / `limit`）**照原样显示**——题面里就是这些词，
翻译反而对不上。这与"不出现断言 id"不矛盾：前者是学习者要调的东西，后者是内部判分标识。

## 为什么不用模板引擎

一页、五个区块、没有循环嵌套的样式切换。引 Jinja 等于为一个文件加一条依赖，
而这套东西的部署前提是"标准库就能跑"（外部回件 Q6 的结论）。
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

STATE_LABEL = {
    "pass": "通过",
    "fail": "没过",
    "blocked": "没法判定",
    "unscored": "没算分",
}
STATE_CLASS = {
    "pass": "ok",
    "fail": "bad",
    "blocked": "warn",
    "unscored": "warn",
}

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 18px 16px 40px;
  font: 16px/1.65 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: #1c1f23; background: #f6f7f9;
  -webkit-text-size-adjust: 100%;
}
main { max-width: 720px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 10px; color: #3a4048; }
.sub { color: #6b7280; font-size: 13px; margin: 0 0 18px; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 14px 16px; margin: 10px 0; }
.score { display: flex; align-items: baseline; gap: 10px; }
.score b { font-size: 34px; line-height: 1; }
.score span { color: #6b7280; font-size: 13px; }
.chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 999px;
        background: #f1f2f4; color: #4b5563; }
.tag { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 999px;
       margin-bottom: 6px; }
.tag.ok { background: #e8f5ec; color: #1f7a3d; }
.tag.bad { background: #fdeaea; color: #b3261e; }
.tag.warn { background: #fdf3e2; color: #8a5a00; }
.ttl { font-weight: 600; margin: 2px 0 6px; }
.fact { margin: 4px 0; }
.hint { color: #4b5563; font-size: 14px; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 3px 0; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #eef0f2; }
th { color: #6b7280; font-weight: 500; }
code { font-family: ui-monospace, Consolas, monospace; font-size: 13px;
       background: #f3f4f6; padding: 1px 4px; border-radius: 4px; }
.foot { color: #6b7280; font-size: 12px; margin-top: 26px; }
textarea { width: 100%; min-height: 120px; padding: 10px 12px; font: inherit; font-size: 15px;
           border: 1px solid #d6d9dd; border-radius: 10px; resize: vertical; background: #fff; }
.q { margin: 10px 0 4px; font-weight: 600; }
.note { color: #6b7280; font-size: 13px; }
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _label(mapping: dict, key: str) -> str:
    """有中文就用中文，没有就退回原文——但绝不退回断言 id（调用方保证不把 id 传进来）。"""
    return str(mapping.get(key) or key)


def _score_block(report: dict) -> str:
    score = report.get("score") or {}
    ratio = float(score.get("ratio") or 0.0)
    by_state = score.get("by_state") or {}
    pieces = [
        f'<div class="score"><b>{ratio * 100:.0f}%</b>'
        f'<span>这道题这次拿到的分</span></div>'
    ]
    chips = []
    for state in ("pass", "fail", "blocked", "unscored"):
        count = int(by_state.get(state) or 0)
        if count:
            chips.append(f'<span class="chip">{_esc(STATE_LABEL[state])} {count} 项</span>')
    if score.get("voided"):
        chips.append('<span class="chip">本次不算数（按旧版本交的，需重交）</span>')
    if score.get("disqualified"):
        chips.append('<span class="chip">有一项硬性要求没过</span>')
    if chips:
        pieces.append('<div class="chips">' + "".join(chips) + "</div>")
    return '<div class="card">' + "".join(pieces) + "</div>"


def _assertion_card(assertion: dict) -> str:
    state = str(assertion.get("state") or "unscored")
    tag = STATE_CLASS.get(state, "warn")
    # 标题缺失时不能拿 id 顶——那正是要藏起来的东西
    title = assertion.get("title") or "这项检查"
    out = [f'<div class="card"><span class="tag {tag}">{_esc(STATE_LABEL.get(state, state))}</span>',
           f'<div class="ttl">{_esc(title)}</div>']

    notes = assertion.get("pattern_notes") or []
    facts = [n for n in notes if (n or {}).get("text")]
    if facts:
        for note in facts:
            out.append(f'<div class="fact">{_esc(note["text"])}</div>')
            if note.get("hint"):
                out.append(f'<div class="hint">下一步：{_esc(note["hint"])}</div>')
    elif state != "pass":
        out.append('<div class="hint">这一项没有更细的说明——把过程发回给我，我来补。</div>')
    out.append("</div>")
    return "".join(out)


def _skill_block(report: dict) -> str:
    gaps = report.get("skill_gaps") or []
    if not gaps:
        return ""
    skills = (report.get("display_labels") or {}).get("skills") or {}
    rows = []
    for gap in gaps:
        skill = _label(skills, str(gap.get("skill_id") or ""))
        lost = gap.get("weight_lost")
        lost_text = f"，掉了 {lost} 分" if isinstance(lost, (int, float)) else ""
        rows.append(f"<li>{_esc(skill)}{_esc(lost_text)}</li>")
    return '<h2>这次暴露的薄弱点</h2><div class="card"><ul>' + "".join(rows) + "</ul></div>"


def _short(value: object, limit: int = 48) -> str:
    """参数值太长就截断——`write_report` 的 content 是整份产物，塞进表格会把页面撑坏。"""
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（共 {len(text)} 字）"


def _trace_block(report: dict) -> str:
    view = report.get("trace_view") or []
    if not view:
        return ""
    rows = []
    for item in view:
        index = item.get("call_index")
        name = item.get("name")
        params = item.get("params") or {}
        status = item.get("status")
        detail = ", ".join(
            f"{k}=内容（{len(str(v))} 字）" if k == "content" else f"{k}={_short(v)}"
            for k, v in params.items()
        )
        if status == "error":
            result = f"失败（{item.get('http_status') or '连接不上'}）"
        elif status == "timeout":
            result = "超时"
        else:
            result = "成功"
        rows.append(
            f"<tr><td>{_esc(index)}</td><td><code>{_esc(name)}</code></td>"
            f"<td>{_esc(detail)}</td><td>{_esc(result)}</td></tr>"
        )
    return ('<h2>你实际发出去的过程</h2><div class="card"><table>'
            "<tr><th>第几次</th><th>工具</th><th>参数</th><th>结果</th></tr>"
            + "".join(rows) + "</table></div>")


def _all_pass_block(report: dict) -> str:
    """全过的时候说点具体的话。

    第一份真实提交就是满分（学弟让 AI 写的脚本），页面当时只有五张"通过"卡片——
    等于什么都没告诉他。这里把**通过的技能名**列出来，再给一句下一步。
    技能名来自 `display_labels.skills`，不是写死在页面里的。
    """
    assertions = report.get("assertions") or []
    if not assertions or any(a.get("state") != "pass" for a in assertions):
        return ""
    if report.get("skill_gaps"):
        return ""
    skills = (report.get("display_labels") or {}).get("skills") or {}
    names: list[str] = []
    for assertion in assertions:
        skill_id = assertion.get("skill_id")
        if not skill_id:
            continue
        label = _label(skills, str(skill_id))
        if label not in names:
            names.append(label)
    detail = f"这次没扣分的是：{'、'.join(names)}。" if names else "这一题的检查全过了。"
    return ('<div class="card"><span class="tag ok">全过了</span>'
            f'<div class="ttl">{_esc(detail)}</div>'
            '<div class="hint">下一步：换一道同族更难的，或者把这次的做法用自己的话写三行——'
            "讲得清才算真会。</div></div>")


def _missing_block(title: str, reason: str) -> str:
    """某块没生成出来时说一句，而不是整块消失。

    静默消失最坏：用户以为"这产品本来就没有训练单"，而我这边看不到任何信号。
    """
    short = reason if len(reason) <= 90 else reason[:90] + "…"
    return (f'<h2>{_esc(title)}</h2><div class="card"><div class="note">'
            f"这块这次没生成出来（{_esc(short)}）。把报告连同这一行一起发回给我。</div></div>")


def _training_block(report: dict, task: dict, catalog_path: str | None) -> str:
    """训练单：下一步做哪道题、什么算练会、练完回来看哪几条。

    数据来自 `training.drill_brief()`，这里只负责排版。生成不出来就整块不出现，
    不写"暂无数据"——那会让人以为训练单从来没有。
    """
    from . import training

    try:
        failed = [str(a["id"]) for a in report.get("assertions") or []
                  if a.get("id") is not None and a.get("state") != "pass"]
        titles = {str(a["id"]): str(a.get("title") or "")
                  for a in report.get("assertions") or [] if a.get("id") is not None}
        brief = training.drill_brief(
            task, training.load_catalog(catalog_path or training.DEFAULT_CATALOG),
            focus_assertions=failed, assertion_titles=titles,
        )
    except Exception as exc:  # 训练单是附加信息：不能连累整页，但也不能装作没问题
        return _missing_block("接下来练什么", f"{type(exc).__name__}: {exc}")

    if brief.get("status") == "no_family":
        return ""
    parts = ['<h2>接下来练什么</h2><div class="card">']
    nxt = brief.get("next")
    if brief.get("status") == "has_next" and isinstance(nxt, dict):
        level = nxt.get("difficulty")
        level_text = f"（第 {level} 档）" if isinstance(level, int) else ""
        parts.append(f'<div class="ttl">下一道：{_esc(nxt.get("title"))}{_esc(level_text)}</div>')
        asks = [a for a in (nxt.get("asks") or []) if isinstance(a, str)]
        if asks:
            parts.append('<div class="note">它会要求你：</div><ul>')
            parts.extend(f"<li>{_esc(item)}</li>" for item in asks)
            parts.append("</ul>")
    else:
        parts.append('<div class="ttl">这一族你已经到最高档了</div>')

    if brief.get("criterion"):
        parts.append(f'<div class="hint">什么算练会：{_esc(brief["criterion"])}</div>')

    focus = [str(row.get("title") or "") for row in (brief.get("focus_assertions") or [])
             if isinstance(row, dict) and row.get("title")]
    if focus:
        parts.append('<div class="note">练完回来看这份报告里的：' + _esc("、".join(focus)) + "</div>")
    parts.append("</div>")
    return "".join(parts)


def _recall_block(report: dict, task: dict) -> str:
    """复述：三个问题 + 一个输入框。

    为什么把输入框放进报告页：一份满分报告此前"没什么可说的"，而满分恰恰是最需要
    复述的时刻——讲得出来才算真会。桌面端和手机浏览器的**语音输入都走系统输入法**
    （Windows 是 Win+H，手机是键盘上的麦克风键），所以这里只要有一个真正的文本框。

    **不渲染 `hints`**：「看提示要不要降权」还没拍板（BACKLOG B-32），提示一律不进页面。
    """
    from . import feynman

    try:
        prompt = feynman.build_prompt(report, task)
    except Exception as exc:
        return _missing_block("讲一遍（复述）", f"{type(exc).__name__}: {exc}")
    questions = [q for q in (prompt.get("questions") or []) if isinstance(q, dict) and q.get("text")]
    if not questions:
        return ""
    parts = ['<h2>讲一遍（复述）</h2><div class="card">',
             '<div class="note">念出来或打出来都行：桌面上按 Win+H 用系统语音输入，'
             '手机上用输入法空格键旁边那个麦克风。</div>']
    for question in questions:
        parts.append(f'<div class="q">{_esc(question["text"])}</div>')
    parts.append('<textarea placeholder="在这里写。写完把这段文字发回给我。"></textarea>')
    parts.append('<div class="note">这一步没有自动判分——它要的是你的话，不是分数。</div>')
    parts.append("</div>")
    return "".join(parts)


def render_html(report: dict, *, task_title: str = "", task_id: str = "",
                task: dict | None = None, catalog_path: str | None = None) -> str:
    """报告 dict → 完整 HTML 字符串。**不读磁盘、不联网**，纯函数好测。"""
    submission = report.get("submission") or {}
    tid = task_id or str(submission.get("task_id") or "")
    title = task_title or "这次提交的诊断报告"

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head><body><main>",
        f"<h1>{_esc(title)}</h1>",
        f'<p class="sub">题目 <code>{_esc(tid)}</code> 的诊断结果 · 这份文件可以离线打开</p>',
        _score_block(report),
        _all_pass_block(report),
        "<h2>逐项看</h2>",
    ]
    for assertion in report.get("assertions") or []:
        parts.append(_assertion_card(assertion))
    parts.append(_skill_block(report))
    parts.append(_trace_block(report))
    if task is not None:
        parts.append(_training_block(report, task, catalog_path))
        parts.append(_recall_block(report, task))
    parts.append(
        '<p class="foot">这不是成绩单：它只说明这一次提交里哪些要求没满足、下一步可以先改什么。'
        "页面不联网，也不会上传任何东西。</p>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="诊断报告 → 自包含 HTML（B-48）")
    parser.add_argument("--report", required=True, help="报告 JSON 路径")
    parser.add_argument("--out", required=True, help="输出的 .html 路径")
    parser.add_argument("--task", help="任务 YAML 或 task_id；给了就带出标题、训练单与复述题")
    parser.add_argument("--catalog", help="任务目录（默认仓库的 tasks/）")
    args = parser.parse_args(argv)

    path = Path(args.report)
    if not path.is_file():
        print(f"找不到报告：{path}", flush=True)
        return 2
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"{path} 不是合法 JSON（{exc}）", flush=True)
        return 2
    if not isinstance(report, dict):
        print(f"{path} 顶层应是对象，实际 {type(report).__name__}", flush=True)
        return 2

    task: dict | None = None
    task_title = ""
    if args.task:
        from .diagnosis_report import load_task
        from .lab import DEFAULT_CATALOG, resolve_task_file

        task = load_task(resolve_task_file(args.task, args.catalog or DEFAULT_CATALOG))
        task_title = str(task.get("title") or "")

    out = Path(args.out)
    out.write_text(
        render_html(report, task_title=task_title, task=task, catalog_path=args.catalog),
        encoding="utf-8",
    )
    print(f"已写出 {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
