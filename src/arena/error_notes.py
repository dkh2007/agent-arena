"""把错误模式拼成人话：一条模式一句话，句子里的每个数字都来自本次证据。

## 为什么要有这一层

`docs/error-patterns-v0.yaml` 已经给了每条模式的 `label`（四个字）和 `description`
（一句书面解释，斜杠后面常带 `valid_count` 这种字段名）。这两样够判分、够归档，
但学生看到的是一句「总额算错」了事，不知道自己填了多少、正确的是多少。
实测证据里本来就有这些数字（`diffs` 里有 `expected/actual`，`violations` 里有
`param/detail`），缺的只是拼接规则——本模块就是那套规则。

## 纪律（破坏其中一条，这层就退化成第二真相源）

1. **模板挂在模式上，不挂在题目上。** 19 条模式 = 19 条模板，写一次；
   挂题目会随题量线性增长（24 题 → 40 题就要写 40 遍）。
2. **只许拼证据里有的字段。** 模板槽填不满就抛 `NoteError`，由调用方降级到词表
   `description`。**绝不**给槽补一个「大概是这样」的默认值——那是在报告里编数。
   某一类错误的明细确实拼不出人话时，正确动作是回执行器把那部分数据结构化
   （例如参数越界原先只有 `"20 > maximum 10"` 一句字符串，现已补出
   `value`/`bound`/`bound_kind`），不是在模板里凑字。
3. **一句事实 + 一句动作。** 事实句必须能在证据里逐字找到；动作句是教学口径，
   可以不带数据，但不许承诺「这样就对了」。
4. **不出现内部词。** 断言 id（`R2_report_correct`）、字段名（`valid_count`）、
   分类来源（`case:missing_fields`）都不进句子。字段名经 `FIELD_LABELS` 翻成中文，
   翻不到就抛错——回退成英文 id 等于把技术口径又漏回页面。

## 证据形状依赖

模板槽依赖执行器 `evidence` 的**当前**形状（`diffs` / `violations` / `extra.matched_steps`
等）。`tests/test_error_notes.py` 用真实执行器把 19 条模式逐条跑出来再渲染，
任何一条填不满即测试失败——这就是「模板与执行器不同步」的报警器。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[2]
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"

_MISSING = object()


class NoteError(ValueError):
    """这条模式的人话拼不出来：槽没有对应证据，或模板本身破损。"""


# 报告里出现的 JSON 字段的中文叫法。**这是唯一一份**：报告通过
# `display_labels.fields` 下发，Android 侧只查表，不再自带第二份映射。
FIELD_LABELS: dict[str, str] = {
    "valid_count": "有效条数",
    "total_amount": "合计金额",
    "offset": "翻页游标",
    "root_cause": "根因",
    "offending_line": "出错行号",
}

# 断言类型的中文叫法。页面用它当每一条的标题——原来的标题是 `R2_report_correct`，
# 学生读不懂，也违反「页面上不许出现英文 id」那条红线。
TYPE_LABELS: dict[str, str] = {
    "file_exists": "交付物是否存在",
    "trace_final_status": "流程是否正常结束",
    "json_field_equals": "结论是否与判据一致",
    "trace_sequence": "关键步骤是否走到",
    "trace_param_schema_valid": "工具调用参数是否合规",
}

# 报告解析失败的三种形状（`evidence.extra.parse_error.kind`）。
PARSE_KIND_LABELS: dict[str, str] = {
    "syntax_error": "语法有错",
    "empty_content": "内容是空白",
    "not_json_object": "顶层不是对象",
}

# 参数类型的中文叫法（`_check_call_params` 的 `expected_type` / `actual_type`）。
PARAM_TYPE_LABELS: dict[str, str] = {
    "integer": "整数",
    "string": "字符串",
    "int": "整数",
    "str": "字符串",
    "float": "小数",
    "bool": "布尔值",
    "NoneType": "空值",
    "list": "数组",
    "dict": "对象",
}


@dataclass(frozen=True)
class Template:
    """一条模板分支。`when` 为空表示兜底，必须是最后一条。"""

    text: str
    hint: str | None
    when: Mapping[str, Any] | None


# ---------------------------------------------------------------------------
# 证据 -> 模板槽
# ---------------------------------------------------------------------------


def _evidence(a: Mapping[str, Any]) -> Mapping[str, Any]:
    ev = a.get("evidence")
    return ev if isinstance(ev, Mapping) else {}


def _extra(a: Mapping[str, Any]) -> Mapping[str, Any]:
    ex = _evidence(a).get("extra")
    return ex if isinstance(ex, Mapping) else {}


def _observed(a: Mapping[str, Any]) -> Mapping[str, Any]:
    ob = _evidence(a).get("observed")
    return ob if isinstance(ob, Mapping) else {}


def _fmt(value: Any) -> str:
    """数字按原样显示：17 不写成 17.00（条数带两位小数等于告诉读者这数是编的）。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if float(value) == int(value):
            return str(int(value))
        return f"{round(float(value), 2):.2f}"
    return str(value)


def _diffs(a: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [d for d in (_extra(a).get("diffs") or ()) if isinstance(d, Mapping)]


def _diff_facts(a: Mapping[str, Any], field: str) -> dict[str, Any]:
    """按字段名取一条 diff。缺了就是证据形状变了，抛错而不是给空串。"""
    for d in _diffs(a):
        if d.get("field") == field:
            want, got = d.get("expected"), d.get("actual")
            facts: dict[str, Any] = {"expected": _fmt(want), "actual": _fmt(got)}
            if isinstance(want, (int, float)) and isinstance(got, (int, float)):
                facts["delta"] = _fmt(round(abs(float(want) - float(got)), 2))
            return facts
    raise NoteError(f"证据里没有 {field} 的差异明细，拼不出「{field} 算错」这一句")


def _missing_facts(a: Mapping[str, Any]) -> dict[str, Any]:
    """缺字段类：给中文的缺失项列表；若是整份解析失败，另给原因。"""
    fields = [d.get("field") for d in _diffs(a) if d.get("missing")]
    if not fields:
        raise NoteError("证据里没有 missing 标记的差异项，拼不出「缺字段」这一句")
    labels = []
    for f in fields:
        if f not in FIELD_LABELS:
            raise NoteError(f"字段 {f!r} 没有中文叫法，不许把英文名漏到页面上")
        labels.append(FIELD_LABELS[f])
    facts: dict[str, Any] = {"fields": "、".join(labels), "count": len(labels)}
    parse = _extra(a).get("parse_error")
    if isinstance(parse, Mapping):
        kind = str(parse.get("kind") or "")
        facts["parse_error"] = True
        facts["parse_kind"] = PARSE_KIND_LABELS.get(kind, "格式不对")
    else:
        facts["parse_error"] = False
    return facts


def _file_facts(a: Mapping[str, Any]) -> dict[str, Any]:
    return {"size": _fmt(_observed(a).get("size_bytes"))}


def _step_facts(a: Mapping[str, Any]) -> dict[str, Any]:
    """轨迹类：走了几步、碰到问题点几次。

    刻意只用 `matched_steps`/`total_steps` 与两次投影的长度，**不碰 offsets 的取值**：
    同一个执行器同时服务「翻页游标」和「文件路径」两种骨架，读值就会把
    `offset=10` 这类骨架细节写进人话。
    """
    obs = _observed(a)
    extra = _extra(a)
    matched, total = extra.get("matched_steps"), extra.get("total_steps")
    if matched is None or total is None:
        raise NoteError("证据里没有 matched_steps/total_steps，拼不出「走了几步」这一句")
    return {
        "matched": _fmt(matched),
        "total": _fmt(total),
        "calls": len(obs.get("offsets") or ()),
        "fault_hits": len(obs.get("statuses") or ()),
    }


def _param_facts(a: Mapping[str, Any]) -> dict[str, Any]:
    """参数类：取第一条违规（报告与页面都只讲最靠前那一条），另给总条数。"""
    violations = _observed(a).get("violations") or ()
    first = next((v for v in violations if isinstance(v, Mapping)), None)
    if first is None:
        raise NoteError("证据里没有 violations，拼不出「参数不对」这一句")
    facts: dict[str, Any] = {
        "call_index": _fmt(first.get("call_index")),
        "tool": _fmt(first.get("tool")),
        "param": _fmt(first.get("param")),
        "count": len([v for v in violations if isinstance(v, Mapping)]),
    }
    if first.get("expected_type") is not None:
        raw = str(first["expected_type"])
        facts["expected_type"] = PARAM_TYPE_LABELS.get(raw, raw)
    if first.get("actual_type") is not None:
        raw = str(first["actual_type"])
        facts["actual_type"] = PARAM_TYPE_LABELS.get(raw, raw)
    if first.get("bound") is not None:
        facts["bound"] = _fmt(first["bound"])
        facts["bound_kind"] = str(first.get("bound_kind") or "maximum")
        if "value" in first:
            facts["value"] = _fmt(first["value"])
    return facts


def _no_facts(a: Mapping[str, Any]) -> dict[str, Any]:
    return {}


_FACTS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "no_output_file": _no_facts,
    "empty_output_file": _file_facts,
    "wrong_valid_count": lambda a: _diff_facts(a, "valid_count"),
    "wrong_total_amount": lambda a: _diff_facts(a, "total_amount"),
    "report_missing_field": _missing_facts,
    "wrong_root_cause": lambda a: _diff_facts(a, "root_cause"),
    "wrong_offending_line": lambda a: _diff_facts(a, "offending_line"),
    "diagnosis_missing_field": _missing_facts,
    "no_retry": _step_facts,
    "retry_wrong_params": _step_facts,
    "retry_exhausted": _step_facts,
    "no_clarification": _step_facts,
    "clarified_too_late": _step_facts,
    "param_out_of_schema": _param_facts,
    "param_missing_required": _param_facts,
    "param_type_mismatch": _param_facts,
    "param_out_of_range": _param_facts,
    "task_crashed": _no_facts,
    "task_timeout": _no_facts,
}


# ---------------------------------------------------------------------------
# 载入 / 渲染
# ---------------------------------------------------------------------------


def load_templates(path: str | Path = VOCAB_PATH) -> dict[str, tuple[Template, ...]]:
    """读词表里的 `human`。结构破损在这里就抛，不留到渲染时才发现。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    patterns = data.get("patterns") or []
    out: dict[str, tuple[Template, ...]] = {}
    for p in patterns:
        pid = p.get("pattern_id")
        blocks = p.get("human")
        if not isinstance(blocks, list) or not blocks:
            raise NoteError(f"错误模式 {pid} 没有 human 模板：19 条一条都不能少")
        items = []
        for i, b in enumerate(blocks, 1):
            if not isinstance(b, Mapping) or not str(b.get("text") or "").strip():
                raise NoteError(f"{pid} 的第 {i} 条 human 缺 text")
            when = b.get("when")
            if when is not None and not isinstance(when, Mapping):
                raise NoteError(f"{pid} 的第 {i} 条 human 的 when 必须是对象")
            items.append(Template(text=str(b["text"]), hint=b.get("hint"), when=when))
        if items[-1].when is not None:
            raise NoteError(f"{pid} 的最后一条 human 必须是无条件兜底（缺兜底会在某些证据上无话可说）")
        for t in items[:-1]:
            if t.when is None:
                raise NoteError(f"{pid} 的兜底不在最后一条：它会把后面的分支全挡住")
        out[pid] = tuple(items)
    missing = sorted(set(_FACTS) - set(out))
    if missing:
        raise NoteError(f"这些错误模式缺人话模板：{missing}")
    extra = sorted(set(out) - set(_FACTS))
    if extra:
        raise NoteError(f"这些模式在人话模板里存在，但代码不知道它们要哪些槽：{extra}")
    return out


def _match(facts: Mapping[str, Any], want: Mapping[str, Any]) -> bool:
    """分支条件：逐键严格相等。证据里没有这个键 ⇒ 不命中（走兜底，不猜）。"""
    for key, expected in want.items():
        got = facts.get(key, _MISSING)
        if got is _MISSING:
            return False
        if type(got) is not type(expected) or got != expected:
            return False
    return True


def _pick(items: tuple[Template, ...], facts: Mapping[str, Any]) -> Template:
    for t in items:
        if t.when is None or _match(facts, t.when):
            return t
    raise NoteError("没有命中任何模板分支，也没有兜底")


def render(pattern_id: str, assertion: Mapping[str, Any],
           templates: Mapping[str, tuple[Template, ...]] | None = None) -> dict[str, Any]:
    """一条错误模式 -> 一句人话（+ 一句动作）。填不满槽就抛 `NoteError`。"""
    templates = templates if templates is not None else load_templates()
    if pattern_id not in templates:
        raise NoteError(f"错误模式 {pattern_id} 词表里没有 human 模板")
    facts = _FACTS[pattern_id](assertion)
    tpl = _pick(templates[pattern_id], facts)
    try:
        text = tpl.text.format(**facts)
    except KeyError as exc:
        raise NoteError(f"{pattern_id}: 模板占位符 {exc.args[0]!r} 在本次证据里没有对应值") from exc
    note: dict[str, Any] = {"pattern_id": pattern_id, "text": text}
    if tpl.hint:
        note["hint"] = tpl.hint
    return note


def load_pattern_descriptions(path: str | Path = VOCAB_PATH) -> dict[str, str]:
    """降级用：拼不出人话时退回词表原话，绝不现编一句。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for p in data.get("patterns") or []:
        desc = str(p.get("description") or "").strip()
        if desc:
            out[str(p["pattern_id"])] = desc
    return out


def notes_for(assertion: Mapping[str, Any],
              templates: Mapping[str, tuple[Template, ...]] | None = None,
              descriptions: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """一条断言的 `error_patterns` -> 若干条人话。

    拼不出来的那条**降级**成词表 description 并标 `degraded: true`，不静默丢弃：
    页面上仍有一句真话，测试则能盯住「有几条降级了」。零降级是发布条件。
    """
    templates = templates if templates is not None else load_templates()
    descriptions = descriptions if descriptions is not None else load_pattern_descriptions()
    out: list[dict[str, Any]] = []
    for pid in assertion.get("error_patterns") or ():
        try:
            out.append(render(pid, assertion, templates))
        except NoteError:
            out.append({
                "pattern_id": pid,
                "text": descriptions.get(pid, ""),
                "degraded": True,
            })
    return out
