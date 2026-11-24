"""费曼复述页的数据包：把「这题要什么 / 你哪里没说对 / 拿什么对照」摊在眼前。

## 这一层为什么存在

费曼复述的价值全在「不看材料，自己讲一遍，再拿材料对照」。所以页面必须同时有：

1. **要复述什么**（任务自述里的编号要求，逐字）；
2. **讲完拿什么对照**（这次没通过的断言、期望值 vs 实际值）；
3. **卡住时能点开的提示**（错法叫什么、当时现场什么样）——全部逐字取自报告与词表。

页面自己一个字都不编。判定、算分、归因都在评测层做完了，这里只做「搬运 + 排版」。

## 不许做的事

- 不许给复述打分。没有第二真相源（模型判定）之前，任何「你讲得 80 分」都是编的。
  本模块只产出材料和自查清单，用户在页面上自己勾。
- 不许在提交前泄露对照材料。费曼的前提是「先自己想」，所以清单与对照由页面在
  提交后解锁；数据包里两者都在，**锁是页面的责任**，见 `FeynmanScreen` 的 committed。
- 不许把 `**` 之外的 markdown 塞进来：手机端只渲染加粗，其余符号会原样露出来。
- 不许在缺词表条目时糊一个中文名：`display_labels` 的纪律同样适用于这一层。

## 用法

    python -m arena.feynman --task tasks/dbg-001-order-report.yaml \\
        --from-report android/app/src/main/assets/sample-report.json \\
        --out android/app/src/main/assets/feynman-prompt.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from . import training
from .diagnosis_report import (
    ReportError,
    load_pattern_labels,
    load_skill_labels,
    VOCAB_PATH,
)
from .error_notes import FIELD_LABELS


class FeynmanError(ValueError):
    """材料凑不齐——宁可不出这一页，也不给一份「看起来像提示」的空壳。"""


# 三条复述题。这是产品文案（不是数据推断），改它等于改这一页的教学口径。
# 为什么是这三条：对应「任务理解 → 失败定位 → 修复动作」，
# 与训练单的「练什么 / 怎么算练会 / 回来看哪条」一一对应，不引入第四种框架。
QUESTIONS: tuple[dict[str, str], ...] = (
    {"key": "task", "text": "这题要你交什么？用你自己的话说，不要念题目。"},
    {"key": "gap", "text": "你这次卡在哪一步？说清「本来应该是什么、你实际做了什么」。"},
    {"key": "fix", "text": "下次具体改哪一处？给一个能照着做的动作，别写「更仔细一点」。"},
)

SOURCE_NOTE = (
    "本页材料由本地评测管线产出：要求清单逐字取自任务定义，"
    "对照项与差异取自这份诊断报告，错法解释取自错误模式词表。页面不生成任何新判断。"
)


def load_pattern_descriptions(path: str | Path = VOCAB_PATH) -> dict[str, str]:
    """`pattern_id -> description`。词表里每条都有 description，缺失即为词表破损。"""
    out: dict[str, str] = {}
    for pattern in yaml.safe_load(Path(path).read_text(encoding="utf-8"))["patterns"]:
        desc = (pattern.get("description") or "").strip()
        if not desc:
            raise FeynmanError(f"错误模式 {pattern['pattern_id']} 在词表里没有 description")
        out[pattern["pattern_id"]] = desc
    return out


def _fmt(value: Any) -> str:
    """把期望/实际值渲染成人能读的一行。

    数字走 `str()` 而不是 `%.2f`：报告里的 17 是条数、1837.99 是金额，
    统一两位小数会把「17 条」写成「17.00 条」，读者立刻知道这个数字是编的。
    """
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "（空）"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _diff_lines(assertion: Mapping[str, Any]) -> list[dict[str, str]]:
    """一条失败断言的「期望 vs 实际」。只取报告里已有的 diffs，不自己比较。

    字段名在这里就翻成中文（`valid_count` → 有效条数）：词的来源只有
    `error_notes.FIELD_LABELS` 一处，页面侧（含 Android）不再自带映射表。
    """
    evidence = assertion.get("evidence") or {}
    extra = evidence.get("extra") or {}
    rows: list[dict[str, str]] = []
    for diff in extra.get("diffs") or ():
        if not isinstance(diff, Mapping):
            continue
        # 不再往每行里塞 assertion_id：外层 check 已经带着它，同一事实存两处必然漂移。
        field = str(diff.get("field"))
        rows.append(
            {
                "field": FIELD_LABELS.get(field, field),
                "expected": _fmt(diff.get("expected")),
                "actual": _fmt(diff.get("actual")),
            }
        )
    return rows


def _scene_lines(assertion: Mapping[str, Any]) -> list[str]:
    """「当时的现场」：断言的 note 与 evidence.extra.why，原样搬，不改写。

    这两处是评测管线写给人看的解释，是本项目最接近「老师点评」的材料；
    改写一句就可能把「你这一步没做」变成「你能力不行」。
    """
    lines: list[str] = []
    note = assertion.get("note")
    if isinstance(note, str) and note.strip():
        lines.append(note.strip())
    extra = (assertion.get("evidence") or {}).get("extra") or {}
    why = extra.get("why")
    if isinstance(why, str) and why.strip():
        lines.append(why.strip())
    return lines


def _human_note(assertion: Mapping[str, Any]) -> str:
    """「这次没过」那句话：优先 `pattern_notes`（人话模板产出），退回 `note`。

    两句都不带时返回空串，由调用方回退成断言 id——不在这里补一句解释。
    """
    texts = [
        str(n.get("text")).strip()
        for n in (assertion.get("pattern_notes") or ())
        if isinstance(n, Mapping) and str(n.get("text") or "").strip()
    ]
    if texts:
        return "；".join(texts)
    note = assertion.get("note")
    return note.strip() if isinstance(note, str) else ""


def build_prompt(
    report: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    skill_labels: Mapping[str, str] | None = None,
    pattern_labels: Mapping[str, str] | None = None,
    pattern_descriptions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """组装费曼复述页的数据包。每个字段都能追到报告、任务 YAML 或词表。"""
    skill_labels = load_skill_labels() if skill_labels is None else skill_labels
    pattern_labels = load_pattern_labels() if pattern_labels is None else pattern_labels
    pattern_descriptions = (
        load_pattern_descriptions() if pattern_descriptions is None else pattern_descriptions
    )

    submission = report.get("submission") or {}
    reported = submission.get("task_id")
    declared = task.get("task_id")
    if reported != declared:
        # 与训练单同一条纪律：两份数据对不上时宁可报错。讲错题比不给材料更伤。
        raise FeynmanError(
            f"报告判的是 {reported!r}，任务定义却是 {declared!r}：对不上，拒绝产出复述材料"
        )

    assertions = [a for a in (report.get("assertions") or ()) if isinstance(a, Mapping)]
    failed = [a for a in assertions if a.get("state") != "pass"]

    used_patterns: list[str] = []
    for assertion in assertions:
        for pattern_id in assertion.get("error_patterns") or ():
            if pattern_id not in used_patterns:
                used_patterns.append(pattern_id)

    missing = [p for p in used_patterns if p not in pattern_labels or p not in pattern_descriptions]
    if missing:
        raise FeynmanError(f"错误模式 {missing} 出现在报告里，但 {VOCAB_PATH.name} 里查不到")

    gaps: list[dict[str, Any]] = []
    for gap in report.get("skill_gaps") or ():
        skill_id = gap.get("skill_id")
        if skill_id not in skill_labels:
            raise ReportError(f"缺口里的技能 {skill_id!r} 在技能词表里没有 name")
        patterns = [
            {
                "id": pid,
                "label": pattern_labels[pid],
                "description": pattern_descriptions[pid],
            }
            for pid in gap.get("patterns") or ()
        ]
        gaps.append(
            {
                "skill_label": skill_labels[skill_id],
                "failed_assertions": list(gap.get("failed_assertions") or ()),
                "weight_lost": gap.get("weight_lost"),
                "patterns": patterns,
            }
        )

    checks: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for assertion in failed:
        lines = _diff_lines(assertion)
        # 标题取报告下发的中文名（`title`）；老报告没有这个键时才退回断言 id。
        # 页面上的字一律是给人读的，`R2_report_correct` 只在没有中文名时出现。
        title = str(assertion.get("title") or assertion.get("id"))
        checks.append(
            {
                "assertion_id": assertion.get("id"),
                "title": title,
                "diffs": lines,
                "scene": _scene_lines(assertion),
            }
        )
        # 清单条目：报告给了人话说明就用那句（逐字），没给就只留断言 id。
        # 不在这一层替失败项写解释——写了就等于页面自己下了结论。
        # 2026-11-24 起优先取 `pattern_notes`：那是按错误模式拼出来的、带本次数字的句子
        # （报告页与这里读同一份），比管线偶尔才写的 `note` 稳得多——实测按当前链路重算的
        # 报告一个 `note` 都不带，只认 `note` 会让这一栏退化成一行机器码。
        note = _human_note(assertion)
        missed.append(
            {
                "key": str(assertion.get("id")),
                "kind": "missed",
                "text": note if note else str(assertion.get("id")),
                "diffs": lines,
            }
        )

    hints: list[dict[str, Any]] = []

    asks = training.asks_of(task)
    if asks:
        hints.append(
            {"key": "asks", "label": "题目到底要几步", "lines": [f"{i + 1}. {a}" for i, a in enumerate(asks)]}
        )

    diff_rows = [
        (check["title"], row) for check in checks for row in check["diffs"]
    ]
    if diff_rows:
        hints.append(
            {
                "key": "diffs",
                "label": "把期望和实际念一遍",
                "lines": [
                    f"{title} · {row['field']}：期望 {row['expected']}，实际 {row['actual']}"
                    for title, row in diff_rows
                ],
            }
        )

    if used_patterns:
        hints.append(
            {
                "key": "patterns",
                "label": "这个错法叫什么",
                "lines": [
                    f"{pattern_labels[p]}：{pattern_descriptions[p]}" for p in used_patterns
                ],
            }
        )

    scene_lines = [line for check in checks for line in check["scene"]]
    if scene_lines:
        hints.append({"key": "scene", "label": "当时现场是什么样", "lines": scene_lines})

    # 提交后才解锁的自查清单：先「这题该说哪几步」（逐字任务自述），
    # 再「哪里说漏了」（报告原文）。两段都不新写句子，页面只负责排版与勾选。
    checklist = [
        {"key": f"step-{index}", "kind": "step", "text": ask, "diffs": []}
        for index, ask in enumerate(asks, start=1)
    ] + missed

    return {
        "task_id": declared,
        "submission_id": submission.get("submission_id"),
        "task_title": task.get("title"),
        "asks": asks,
        "questions": [dict(q) for q in QUESTIONS],
        "gaps": gaps,
        "checks": checks,
        "checklist": checklist,
        "hints": hints,
        "source_note": SOURCE_NOTE,
    }


def _find_task_file(task_id: str, folder: str | Path) -> Path:
    for path in sorted(Path(folder).glob("*.yaml")):
        task = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(task, dict) and task.get("task_id") == task_id:
            return path
    raise FeynmanError(f"{folder} 下找不到 task_id={task_id!r} 的任务定义")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成费曼复述页的数据包")
    parser.add_argument("--from-report", required=True, help="诊断报告 JSON")
    parser.add_argument("--task", help="任务 YAML（不填则按报告里的 task_id 在 --catalog 下找）")
    parser.add_argument("--catalog", default=str(training.DEFAULT_CATALOG), help="任务目录")
    parser.add_argument("--out", help="写入文件（不填则打印到 stdout）")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
    task_id = (report.get("submission") or {}).get("task_id")
    task_path = Path(args.task) if args.task else _find_task_file(task_id, args.catalog)
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))

    try:
        prompt = build_prompt(report, task)
    except (FeynmanError, ReportError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    text = json.dumps(prompt, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
