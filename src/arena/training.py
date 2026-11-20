"""训练单：从「当前任务在族内的位置」推出下一档练什么（口径 B）。

## 为什么按族 + 难度，而不是按技能

12 个任务里 `secondary_skill_ids` 有 11 个贴同一组标签（多步任务分解 12/12、
工具参数校验 11/12、失败重试策略 11/12），按技能筛不出「该做哪道」。
`variant_meta.family_id` / `variant_meta.difficulty` 是任务 YAML 里**已经存在**的字段
（B-05 变体机制），本模块只读它，不新增标签、不改任务语义。

## 口径边界（写进注释，防止后人当成算法）

- 只向下走**一档**：`difficulty = d + 1`。不跳档、不做「薄弱点加权」——
  加权需要真实使用数据才能定权重，现在没有，硬定等于编。
- 同族同档有多道题时取 `task_id` 最小的一道，保证同一输入两次运行结果一致。
  （这是**确定性**要求，不是「更推荐」的理由。）
- 「练会」的判据固定为：在下一档任务上**门禁项全过、积分项全过**（得分率 1.00 且不合格=否）。
  这是一条项目规则，不是实测出来的阈值；它可判定，但不含任何学习科学依据。
- 族内已是最高档时不编造「再加难」，返回 `status="top_of_family"`，
  由调用方决定是换族还是收工——本模块不替产品做这个决定。

## 用法

    python -m arena.training --task tasks/dbg-001-order-report.yaml --catalog tasks
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "tasks"

# 「练会」的判据。改这句等于改产品承诺，须同步 docs/android-ui-handoff.md 与计划书。
# 中文标点后的空格：跨行拼接的副产物，渲染前统一去掉。
_CJK_PUNCT_SPACE = re.compile(r"(?<=[，。：；！？、）])\s+")

CRITERION = "在下一档任务上，门禁项全过且积分项全过（得分率 1.00、未判不合格）才算练会；只过一部分，回来看这份报告里没消掉的缺口。"


def _meta(task: Mapping[str, Any]) -> dict[str, Any]:
    meta = task.get("variant_meta")
    return meta if isinstance(meta, Mapping) else {}


def family_position(task: Mapping[str, Any]) -> tuple[str, int] | None:
    """返回 `(family_id, difficulty)`；缺字段返回 None（不猜、不默认成 1）。

    缺字段就返回 None 是刻意的：把「没标族」当成「第 1 档」会让推荐链凭空长出一档，
    而那一档并不存在——这正是本项目一直防的「看起来像真的」。
    """
    meta = _meta(task)
    family = meta.get("family_id")
    difficulty = meta.get("difficulty")
    if not isinstance(family, str) or not family.strip():
        return None
    if isinstance(difficulty, bool) or not isinstance(difficulty, int) or difficulty < 1:
        return None
    return family, difficulty


def load_catalog(directory: str | Path = DEFAULT_CATALOG) -> list[dict[str, Any]]:
    """读目录下全部 `*.yaml`。只取本模块要用的键，不做 rubric 深合并。

    变体的 `title` / `scenario` / `variant_meta` 都是各文件自己声明的（rubric 才靠
    `base_task` 继承），所以这里不必复用 `diagnosis_report.load_task()` 的深合并——
    少一层依赖，也少一份可能漂移的实现。
    """
    out: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        task = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(task, dict) or not task.get("task_id"):
            continue
        out.append(task)
    return sorted(out, key=lambda t: t["task_id"])


def levels_in_family(family: str, catalog: Iterable[Mapping[str, Any]]) -> list[int]:
    levels = {
        pos[1]
        for task in catalog
        if (pos := family_position(task)) is not None and pos[0] == family
    }
    return sorted(levels)


def next_in_family(
    task: Mapping[str, Any],
    catalog: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """同族、档位恰好 +1 的下一道题；没有则 None。"""
    pos = family_position(task)
    if pos is None:
        return None
    family, difficulty = pos
    candidates = [
        t
        for t in catalog
        if t.get("task_id") != task.get("task_id")
        and family_position(t) == (family, difficulty + 1)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda t: t["task_id"])[0]


def asks_of(task: Mapping[str, Any], limit: int = 4) -> list[str]:
    """把 `scenario` 里编号步骤摘成短句。

    只摘「1. / 2. …」这种已编号的条目：它们是任务自述的要求清单，不是我替它总结的。
    没有编号条目时返回空列表——不拿标题硬凑成「练什么」。

    跨行要接起来：YAML 块标量的软换行会把一句要求劈成两行，逐行取会得到
    「调用 write_report 将结果写入 /workspace/report.json，」这种半句（2026-11-20 实测到的）。
    续行以「下一行不是编号开头」为界，拼接后压成单空格。
    """
    scenario = task.get("scenario")
    if not isinstance(scenario, str):
        return []

    items: list[list[str]] = []
    for raw in scenario.splitlines():
        line = raw.strip()
        if not line:
            continue
        head, sep, rest = line.partition(".")
        if sep and head.strip().isdigit():
            items.append([rest.strip()])
        elif items:
            items[-1].append(line)
    out: list[str] = []
    for item in items[:limit]:
        text = " ".join(part for part in item if part).strip()
        # YAML 里的子项写成「- xxx」，拼成一行后会变成「… ：- a - b」；换成中文分号，
        # 让它在手机上是一句可读的话，而不是一串横线。
        text = "；".join(piece.strip() for piece in text.split(" - ") if piece.strip())
        text = text.replace("：；", "：").replace("，；", "，")
        # 跨行拼接会在中文标点后留下一个空格（「report.json， 内容」），中文排版里不该有。
        text = _CJK_PUNCT_SPACE.sub("", text)
        out.append(" ".join(text.split()))
    return out


def drill_brief(
    task: Mapping[str, Any],
    catalog: Iterable[Mapping[str, Any]],
    *,
    focus_assertions: Iterable[str] = (),
) -> dict[str, Any]:
    """组装一张训练单。返回的每个字段都能追到任务 YAML 或本模块的显式规则。"""
    catalog = list(catalog)
    positions = {t["task_id"]: family_position(t) for t in catalog}
    pos = family_position(task)

    brief: dict[str, Any] = {
        # 与报告对账用：页面必须确认「训练单讲的任务」就是「报告判的任务」，
        # 两张数据来自不同文件，对不上时宁可报错也不显示一张错位的训练单。
        "task_id": task.get("task_id"),
        "focus_assertions": [a for a in focus_assertions if isinstance(a, str)],
        "criterion": CRITERION,
    }
    if pos is None:
        brief.update(status="no_family", family_id=None, level=None, levels_total=0, next=None)
        return brief

    family, level = pos
    levels = levels_in_family(family, catalog)
    brief.update(
        family_id=family,
        level=level,
        levels_total=len(levels),
        levels=levels,
    )

    nxt = next_in_family(task, catalog)
    if nxt is None:
        brief.update(status="top_of_family", next=None)
        return brief

    brief.update(
        status="has_next",
        next={
            "task_id": nxt["task_id"],
            "title": nxt.get("title"),
            "difficulty": positions[nxt["task_id"]][1],
            "asks": asks_of(nxt),
        },
    )
    return brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打印某道任务的下一档训练单")
    parser.add_argument("--task", required=True, help="任务 YAML 路径")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="任务目录")
    parser.add_argument("--out", help="写入文件（不填则打印到 stdout）")
    parser.add_argument(
        "--focus",
        default="",
        help="逗号分隔的断言 id，写进训练单的「练完回来看哪条」",
    )
    parser.add_argument(
        "--from-report",
        help="从诊断报告取 focus（缺口里的 failed_assertions 并集）并核对 task_id，避免手抄",
    )
    args = parser.parse_args(argv)

    task = yaml.safe_load(Path(args.task).read_text(encoding="utf-8"))
    focus = [x.strip() for x in args.focus.split(",") if x.strip()]

    if args.from_report:
        report = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
        reported = (report.get("submission") or {}).get("task_id")
        if reported != task.get("task_id"):
            sys.stderr.write(
                f"报告判的是 {reported!r}，训练单却按 {task.get('task_id')!r} 生成：两份数据对不上，拒绝产出\n"
            )
            return 2
        for gap in report.get("skill_gaps") or ():
            for assertion_id in gap.get("failed_assertions") or ():
                if assertion_id not in focus:
                    focus.append(assertion_id)

    brief = drill_brief(task, load_catalog(args.catalog), focus_assertions=focus)
    text = json.dumps(brief, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if brief["status"] != "no_family" else 1


if __name__ == "__main__":
    raise SystemExit(main())
