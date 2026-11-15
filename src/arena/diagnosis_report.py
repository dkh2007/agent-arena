"""诊断报告 DTO 的组装器（评测层出口，见 docs/diagnosis-report-schema.md）。

设计原则（四条，都是踩过的坑换来的）：
1. **只装可判定的观测，不装推断**：复习建议、掌握度都在状态层产生，进了报告就是两套口径。
2. **契约自解释**：报告自带 attribution_policy 与 denominator_policy，状态层不必回读 YAML 才知道怎么算。
3. **宁可报错也不静默降级**：A2E 的教训是「读不到 trace 就静默给满分」，所以 trace 不可用时强制
   把轨迹断言改写成 unscored，reasons 写进 trace_audit；任何不自洽直接抛 ReportError，不产出报告。
4. **门禁只管分数，不管观测**：gate 失败时 credit 断言照常判定并记录（诊断信息不因 0 分而丢弃），
   只是不计入分数。反过来，若 gate 失败就把 credit 全标 blocked，等于用评分逻辑吞掉诊断数据。

两段式计分（P6 拍板）：
    rubric_gate    R1 交产物 / R5 正常退出    —— 不计分，任一门禁 fail ⇒ disqualified，ratio=0
    rubric_credit  R2 / R3 / R4              —— 分母恒为 credit 权重之和（本任务 4）

调用关系：执行器（S3）逐条跑断言拿到 assertion_results -> build_report() 组装 -> validate_report() 过 schema。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

STATES = ("pass", "fail", "blocked", "unscored")
GATE, CREDIT = "gate", "credit"
TRACE_TYPE_PREFIX = "trace_"
REPORT_VERSION = "v2"
DENOMINATOR_POLICY = "fixed_at_credit_total_weight"
# TD-034 (C)：评分策略声明必须与 builder 硬编码一致，见 _check_scoring_policy()。
# 只锁影响分数的 5 组；score.formula 与 gate.source / credit.source 是纯说明字段，不参与判定。
GATE_DISQUALIFIED_IF = "any_gate_failed"
GATE_DISQUALIFY_RATIO = 0
GATE_ON_UNSCORED = "do_not_disqualify"
CREDIT_CONTRIBUTION = {"pass": "weight", "fail": 0, "blocked": 0, "unscored": 0}

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "diagnosis-report.schema.json"
VOCAB_PATH = ROOT / "docs" / "error-patterns-v0.yaml"


class ReportError(ValueError):
    """报告不自洽——必须停下来修，不允许产出一份"看起来像报告"的坏数据。"""


# 变体继承（B-05；采纳项见 review-response-v1.md 第 1 节第 3 行「基础任务 + 补丁」）
#
# 10 个任务各写完整 YAML，重复配置会超过一半，而且改一处口径要改十处、漏一处就漂移。
# 所以变体只写**差异**：`base_task: dbg-001-order-report` + 要改的字段，加载时深合并。
#
# 合并规则只有三条，都能一眼说清（规则越"聪明"，越没人敢用）：
#   1. 字典：递归合并。子文件写了的键覆盖，没写的继承。
#   2. rubric_gate / rubric_credit（按 `id`）、tools（按 `name`）：**按 id 合并**——
#      子文件只写要改的那一条断言/工具，其余从母本继承。出现母本没有的 id ⇒ 报错，
#      不让一个拼错的 id 静默变成"新增了一条断言"。
#   3. 其余列表（如 `test_inputs.orders_fixture`）：**整体替换**。测试数据不做元素级合并——
#      两份数据按下标混起来，期望值就对不上了，而且没人能一眼看出结果是什么。
_ID_MERGE_KEYS = {"rubric_gate": "id", "rubric_credit": "id", "tools": "name"}


def _find_task_file(task_id: str, folder: Path) -> Path:
    """按 task_id 找同目录下的任务文件（先按同名约定，再退化为扫目录）。"""
    direct = folder / f"{task_id}.yaml"
    if direct.exists():
        got = yaml.safe_load(direct.read_text(encoding="utf-8")).get("task_id")
        if got != task_id:
            raise ReportError(
                f"{direct.name} 的文件名暗示 task_id={task_id}，但文件里写的是 {got!r}"
                "——两者不一致时按谁走都会有歧义"
            )
        return direct
    for cand in sorted(folder.glob("*.yaml")):
        if yaml.safe_load(cand.read_text(encoding="utf-8")).get("task_id") == task_id:
            return cand
    raise ReportError(f"base_task={task_id} 在 {folder} 下找不到")


def _merge_by_id(base_list: list, patch_list: list, id_key: str, trail: str) -> list:
    index = {item[id_key]: i for i, item in enumerate(base_list) if isinstance(item, dict) and id_key in item}
    out = copy.deepcopy(base_list)
    for item in patch_list:
        if not isinstance(item, dict) or id_key not in item:
            raise ReportError(f"{trail} 的补丁项必须带 {id_key}，否则无法判断它在改哪一条：{item!r}")
        key = item[id_key]
        if key not in index:
            raise ReportError(
                f"{trail} 里没有 {id_key}={key}。补丁只改已有条目，不能新增"
                "（要新增就整份替换这个列表）——先确认是不是拼错了"
            )
        i = index[key]
        out[i] = _merge_dict(out[i], item, f"{trail}[{key}]")
    return out


def _merge_dict(base: dict, patch: dict, trail: str) -> dict:
    out = dict(base)
    for key, val in patch.items():
        here = f"{trail}.{key}" if trail else str(key)
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _merge_dict(out[key], val, here)
        elif key in _ID_MERGE_KEYS and isinstance(out.get(key), list) and isinstance(val, list):
            out[key] = _merge_by_id(out[key], val, _ID_MERGE_KEYS[key], here)
        else:
            out[key] = copy.deepcopy(val)
    return out


def load_task(path: str | Path, *, _chain: tuple[Path, ...] = ()) -> dict:
    """读任务 YAML；带 `base_task` 时先加载母本再深合并（见上方三条规则）。

    `_chain` 只为检测循环继承（A 继承 B、B 又继承 A），外部调用不要传。
    """
    p = Path(path).resolve()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ReportError(f"{p.name} 不是一个 YAML 映射（顶层写成了 {type(raw).__name__}）")
    base_id = raw.get("base_task")
    if not base_id:
        return raw
    if p in _chain:
        raise ReportError(f"变体继承成环：{' -> '.join(x.name for x in (*_chain, p))}")
    base = load_task(_find_task_file(str(base_id), p.parent), _chain=(*_chain, p))
    if base.get("task_id") == raw.get("task_id"):
        raise ReportError(
            f"{p.name} 的 task_id 与母本相同（{raw.get('task_id')}）——"
            "注册表按 task_id 索引，同 id 会互相覆盖"
        )
    patch = {k: v for k, v in raw.items() if k != "base_task"}
    merged = _merge_dict(base, patch, "")
    merged["base_task"] = base_id          # 保留来源，便于审计"这个变体继承了谁"
    return merged


def load_vocabulary(path: str | Path = VOCAB_PATH) -> set[str]:
    return {p["pattern_id"] for p in yaml.safe_load(Path(path).read_text(encoding="utf-8"))["patterns"]}


def load_pattern_skills(path: str | Path = VOCAB_PATH) -> dict[str, tuple[str, ...]]:
    """`pattern_id -> mapped_skill_ids`（B-06 / ROADMAP R-2）。

    词表里 `mapped_skill_ids` 自第 6 轮评审起就写好了，但 `src/` 长期没有消费者：
    报告里的 `skill_gaps` 走的是断言自带的 `skill_id`（能一对一，不能一对多）。
    本函数把词表变成代码可查的映射，供 `build_report()` 在断言**未声明** skill_id 时兜底。
    """
    out: dict[str, tuple[str, ...]] = {}
    for pattern in yaml.safe_load(Path(path).read_text(encoding="utf-8"))["patterns"]:
        out[pattern["pattern_id"]] = tuple(pattern.get("mapped_skill_ids") or ())
    return out


def _clean_number(value: float) -> int | float:
    """整数值收成 int：报告是给人看的，别让 1 变 1.0，也免掉逐字比对时的浮点噪声。"""
    value = round(float(value), 6)
    return int(value) if value == int(value) else value


def _split_weight(weight: int | float, n: int) -> int | float:
    return _clean_number(weight / n) if n else weight


def _attribute(
    assertion: dict, pattern_skills: dict[str, tuple[str, ...]]
) -> tuple[list[str], str]:
    """一条失败断言 -> (归因到的技能列表, 归因来源)。

    来源只有两种，且**互斥**：`assertion`（断言自己声明了 skill_id）/
    `mapping`（没声明，靠词表兜底）。断言声明了就不查词表 —— 这是 Q10=A 的边界，
    否则「按 spec.skill_id 归因」就变成了「按词表归因」（TD-001 未销项）。

    词表里查不到该 pattern 时**报错**而不是返回空：返回空会把兜底悄悄变成
    "这条失败没有技能归属"，映射表失效却没有任何报错，正是 TD-001 想避免的那类静默。
    """
    declared = assertion.get("skill_id")
    if declared:
        return [declared], "assertion"
    mapped: list[str] = []
    for code in assertion["error_patterns"]:
        if code not in pattern_skills:
            raise ReportError(
                f"{assertion['id']}: 错误模式 {code} 不在归因映射里——兜底归因会静默失效，拒绝产出报告"
            )
        for skill_id in pattern_skills[code]:
            if skill_id not in mapped:
                mapped.append(skill_id)
    return mapped, "mapping"


def load_schema(path: str | Path = SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_report(report: dict, schema: dict | None = None) -> None:
    """过 JSON Schema。schema 只管结构；语义不自洽由 build_report 内的断言负责。"""
    schema = schema or load_schema()
    try:
        jsonschema.Draft202012Validator(schema).validate(report)
    except jsonschema.ValidationError as exc:
        raise ReportError(f"报告不符合 schema：{exc.message}（路径 {'/'.join(map(str, exc.path))}）") from exc


# ---------------------------------------------------------------------------
# rubric 分层
# ---------------------------------------------------------------------------


def _policy_value_matches(value: Any, want: Any) -> bool:
    """严格比较：0 与 False 不能混过对账（bool 是 int 子类，== 会漏）。"""
    if isinstance(want, dict):
        return (
            isinstance(value, dict)
            and set(value) == set(want)
            and all(_policy_value_matches(value[k], want[k]) for k in want)
        )
    return type(value) is type(want) and value == want


def _check_scoring_policy(task: dict) -> None:
    """TD-034 (C)：YAML 评分策略声明 vs builder 硬编码，逐项对账，不一致即拒绝。

    放在 rubric_layers 这道公共门：执行器、CLI、未来的 API 都必经，改了 YAML 不改
    builder 会立刻得到可读错误，不会悄悄漂移。builder 对应实现见 build_report()：
    disqualified = bool(disqualifying)、不合格 ratio 固定 0、gate unscored 不拦、
    credit_total = sum(weight)、分子只累加 state == "pass"。
    本函数不改任何分数；不读 formula 与两个 source（纯说明字段，锁了误报高，见 §38.5）。
    """
    score = (task.get("scoring") or {}).get("score") or {}
    gate_cfg = score.get("gate") or {}
    credit_cfg = score.get("credit") or {}
    expected = (
        ("scoring.score.gate.disqualified_if", gate_cfg.get("disqualified_if"), GATE_DISQUALIFIED_IF),
        ("scoring.score.gate.disqualify_ratio", gate_cfg.get("disqualify_ratio"), GATE_DISQUALIFY_RATIO),
        ("scoring.score.gate.on_unscored", gate_cfg.get("on_unscored"), GATE_ON_UNSCORED),
        ("scoring.score.credit.denominator", credit_cfg.get("denominator"), DENOMINATOR_POLICY),
        ("scoring.score.credit.contribution", credit_cfg.get("contribution"), CREDIT_CONTRIBUTION),
    )
    drift = [
        f"{path}={value!r}（builder 固定为 {want!r}）"
        for path, value, want in expected
        if not _policy_value_matches(value, want)
    ]
    if drift:
        raise ReportError(
            "YAML 评分策略与 builder 硬编码不一致：" + "；".join(drift)
            + "——改评分规则须同步改 builder 与本节常量（见 review-response-v1.md §38）"
        )


def _check_rubric_item_shapes(task: dict) -> None:
    """TD-033 第 4 子项：rubric 条目必须是映射，坏条目在加载期给可读错误。

    抽成公共函数：执行路径与后续可能接入的生成路径共用同一份判据，
    避免把形状规则复制成第二真相源（round-39 / §41 同源口径）。
    """
    for layer in (GATE, CREDIT):
        rows = list(task.get(f"rubric_{layer}") or [])
        for idx, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ReportError(
                    f"rubric_{layer} 第 {idx} 条断言不是映射"
                    f"（实际 {type(row).__name__}: {row!r}）：条目必须是含 id / type / depends_on 的对象"
                )


def _check_dependency_order(gate: list[dict], credit: list[dict]) -> None:
    """TD-035 ② 案 A：被依赖项必须在全局 gate+credit 序列里排在引用方之前。

    契约依据（仓内）：docs/diagnosis-report-schema.md「先 gate 后 credit，与执行顺序一致」、
    trace_pipeline.py「gate 在前，R1 一定先于 R2 算出」、review-response-v1.md §6.2
    「断言数组顺序 = 执行顺序」。执行器按数组顺序累积 dependency_results，后置断言
    尚未算出，会出现报告层 ratio=1.0 与执行器 blocked 结论相反；本校验在唯一配置入口
    挡住非法配置（见 review-response-v1.md §44 / §46）。

    边界：① 引用不存在 / 自依赖 / 类型不支持由 Step1 先报，未知 dep 在此跳过，
    不抢错误优先级；② 只做 index 比较，环检查由 tests/test_task_yaml.py 静态负责，
    不在此重复实现（§45.4 同源口径）。
    """
    order = [r["id"] for r in gate + credit]
    position = {rid: i for i, rid in enumerate(order, 1)}
    for idx, r in enumerate(gate + credit, 1):
        for dep in r["depends_on"]:
            at = position.get(dep)
            if at is None:
                continue  # Step1 已报「引用不存在」
            if at >= idx:
                raise ReportError(
                    f"{r['id']}: depends_on 引用了后置断言 {dep!r}"
                    f"（全局 gate+credit 顺序：{r['id']} 第 {idx} 条，{dep} 第 {at} 条）"
                    "——执行器按数组顺序执行，被依赖项必须先算出"
                )


def rubric_layers(task: dict) -> tuple[list[dict], list[dict]]:
    """取出 (gate, credit) 两个断言数组，并校验分层本身成立。

    分层校验放在这里而不是测试里：任何调用方（执行器、脚本、未来的 API）都过这道门，
    不会出现"测试过了但线上用了旧结构"。
    """
    if "rubric" in task:
        raise ReportError("任务仍用单数组 rubric：已改为 rubric_gate + rubric_credit（见 review-response-v1.md §6）")

    gate = list(task.get("rubric_gate") or [])
    credit = list(task.get("rubric_credit") or [])
    if not gate:
        raise ReportError("rubric_gate 为空：没有门禁的提交无从判断任务是否完成")
    if not credit:
        raise ReportError("rubric_credit 为空：没有积分断言的分数恒为 0，评分无意义")

    _check_rubric_item_shapes(task)
    gate_ids = [r["id"] for r in gate]
    credit_ids = [r["id"] for r in credit]
    dup = set(gate_ids) & set(credit_ids)
    if dup:
        raise ReportError(f"断言同时出现在 gate 与 credit：{sorted(dup)}（一条断言只能属于一层）")
    for layer, ids in ((GATE, gate_ids), (CREDIT, credit_ids)):
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise ReportError(f"{layer} 层断言 id 重复：{repeated}（同一层内 id 必须唯一）")

    known = set(gate_ids) | set(credit_ids)
    for r in gate + credit:
        if "depends_on" not in r:
            raise ReportError(f"{r['id']}: 缺 depends_on（无依赖也要写 []）")
        deps = r["depends_on"]
        if not isinstance(deps, list) or any(not isinstance(d, str) or not d for d in deps):
            raise ReportError(f"{r['id']}: depends_on 必须是非空字符串数组（当前 {deps!r}）")
        for dep in deps:
            if dep == r["id"]:
                raise ReportError(f"{r['id']}: depends_on 自依赖")
            if dep not in known:
                raise ReportError(f"{r['id']}: depends_on 引用不存在的断言 {dep!r}")
        if deps and r["type"] != "json_field_equals":
            raise ReportError(
                f"{r['id']}: type={r['type']!r} 不支持 depends_on"
                "（当前只有 json_field_equals 执行依赖折叠）"
            )
    _check_dependency_order(gate, credit)
    for r in gate:
        if r.get("weight", 0) != 0:
            raise ReportError(f"{r['id']} 是门禁，不允许带权重 {r.get('weight')!r}——门禁不计分")
        if r.get("on_fail") != "disqualify":
            raise ReportError(f"{r['id']} 是门禁但 on_fail 不是 disqualify：门禁失败必须给出明确后果")
    for r in credit:
        if not isinstance(r.get("weight"), (int, float)) or r["weight"] <= 0:
            raise ReportError(f"{r['id']} 在 credit 层，权重必须为正数（当前 {r.get('weight')!r}）")
    _check_scoring_policy(task)
    return gate, credit


def _check_result_consistency(rid: str, spec: dict, result: dict, vocab: set[str]) -> None:
    """单条断言的语义校验：状态与错误模式必须自洽，模式必须在 YAML 声明 + 词表登记范围内。"""
    state = result["state"]
    if state not in STATES:
        raise ReportError(f"{rid}: 非法状态 {state!r}")

    patterns = list(result.get("error_patterns") or [])
    declared = set(spec.get("error_patterns") or [])

    if state == "fail":
        if not patterns:
            raise ReportError(f"{rid}: state=fail 但没有给出错误模式（诊断就断在这里）")
        unknown = set(patterns) - declared
        if unknown:
            raise ReportError(f"{rid}: 错误模式 {sorted(unknown)} 未在本断言 error_patterns 中声明")
        unregistered = set(patterns) - vocab
        if unregistered:
            raise ReportError(f"{rid}: 错误模式 {sorted(unregistered)} 未登记进 error-patterns 词表")
    elif patterns:
        raise ReportError(f"{rid}: state={state} 不允许产出错误模式（只有 fail 才有证据）")

    if state in ("blocked", "unscored") and not result.get("reason_code"):
        raise ReportError(f"{rid}: state={state} 必须给 reason_code，否则下游只能猜")


def _assertion_item(
    rid: str,
    spec: dict,
    layer: str,
    result: dict,
    trace_ok: bool,
    trace_audit: dict,
    results: dict,
    vocab: set[str],
) -> dict:
    """单条断言 -> 报告条目。trace 强制改写与依赖折叠都发生在这里。"""
    state = result["state"]
    error_patterns = list(result.get("error_patterns") or [])
    reason_code = result.get("reason_code")

    # 强制改写：trace 不可用时，轨迹类断言一律 unscored——不管调用方填了什么
    if not trace_ok and str(spec["type"]).startswith(TRACE_TYPE_PREFIX):
        state = "unscored"
        error_patterns = []
        reason_code = "trace_not_reported" if not trace_audit["reported"] else "trace_invalid"

    # 依赖折叠：前置断言未通过时，本断言只能是 blocked 或 pass
    if state == "blocked":
        deps = spec.get("depends_on") or []
        unsatisfied = [d for d in deps if results[d]["state"] != "pass"]
        if not unsatisfied:
            raise ReportError(f"{rid}: 标了 blocked，但前置 {deps} 全部通过——blocked 无依据")

    item = {
        "id": rid,
        "layer": layer,
        "type": spec["type"],
        "weight": spec.get("weight", 0) if layer == CREDIT else 0,
        "state": state,
        "skill_id": spec.get("skill_id"),
        "error_patterns": error_patterns,
        "evidence": result.get("evidence") or {"observed": None, "expected": None},
    }
    if reason_code:
        item["reason_code"] = reason_code
    if result.get("note"):
        item["note"] = result["note"]
    _check_result_consistency(rid, spec, item, vocab)
    return item


def _task_version_mismatch(task: dict, raw: dict) -> str | None:
    """TD-038：提交针对的任务版本与当前任务不一致时，返回作废原因码；一致或缺省返回 None。

    缺省不比对（向后兼容）：老客户端不带 task_version 时行为与改动前完全一致，
    五夹具因此保持原分数。这个"缺省即放过"是已知的宽松口，登记为 TD-038 的残留，
    M1 接 API 时改为必填后再收紧（见 review-response-v1.md §60）。
    """
    expected = task.get("task_version")
    submission = raw.get("submission") or {}
    got = submission.get("task_version")
    if not expected or not got:
        return None
    return None if str(got) == str(expected) else "task_version_mismatch"


def _process_fact(task: dict, raw: dict, trace_ok: bool) -> dict:
    """P2 / Q5-D1：这份提交「要不要过程」以及「有没有交」。

    `submitted` 的判据是「有可用的过程证据」：trace 通过结构校验，或提交里写了一段
    非空的 `process_note`（人拍板口径：「写了『不会/没思路』也算交了步骤」）。
    **不把「步骤」等同于「工具调用」** —— 一道题可能只要求写下思路，不要求调工具。

    题目类型由任务 YAML 的 `submission_requirements.requires_process` 决定，
    代码里不写第二份分类表；缺省 false（= 只交答案即可），保持既有任务行为不变。
    """
    cfg = task.get("submission_requirements") or {}
    required = bool(cfg.get("requires_process"))
    submission = raw.get("submission") or {}
    note = submission.get("process_note")
    declared = isinstance(note, str) and bool(note.strip())
    submitted = bool(trace_ok or declared)
    return {"required": required, "submitted": submitted,
            "missing": bool(required and not submitted)}


def _report_attribution(policy: dict, by_state: dict, suppressed: bool) -> dict:
    """整份报告的归因开关（派生值）。

    存在两个理由：① 作废报告必须"不计能力短板"，而这句如果只写在 notes 文案里就
    没有执行者（用户要求：作废时不能只靠文案）；② 下游只读一个布尔，不必自己
    把 attribution_policy 与 by_state 再算一遍。

    它是 attribution_policy 的**派生汇总**，不是第二真相源：任何时刻都可由
    policy + by_state + suppressed 重算出来（测试锁住这一点）。

    `suppressed` = 本次报告不作为能力证据（作废 / 缺过程 / 门禁不合格之外的抑制条件）。
    """
    if suppressed:
        return {"counts_toward_mastery": False, "emit_error_patterns": False}
    return {
        "counts_toward_mastery": any(
            policy[s]["counts_toward_mastery"] and by_state[s] > 0 for s in STATES
        ),
        "emit_error_patterns": any(
            policy[s]["emit_error_patterns"] and by_state[s] > 0 for s in STATES
        ),
    }


def build_report(task: dict, raw: dict, vocab: set[str] | None = None,
                 *, unreachable_reason: str | None = None,
                 pattern_skills: dict[str, tuple[str, ...]] | None = None) -> dict:
    """把「逐条断言结果 + trace 校验结果」组装成符合 schema 的诊断报告。

    raw 结构见 docs/diagnosis-report-schema.md 第 4 节（fixtures/ 下有真实例子）。

    `unreachable_reason`（M1 / B-02）：平台没取到任何证据时（例如提交声明的 Agent
    端点连不上），传一个原因码进来。它**只改文案与归因说明，不改判定语义**：
    断言状态仍由各自 reason_code 表达（全部 `unscored`），分数仍为 0，
    `process_fact` 仍如实记「要过程、这次没收到」——它记的是事实，不负责解释原因。
    存在的唯一目的：别让报告写出「请补交步骤」而把平台采集失败算到学习者头上
    （决策 7「数据缺失 ≠ 能力不足」的直接推论）。
    """
    vocab = vocab if vocab is not None else load_vocabulary()
    pattern_skills = pattern_skills if pattern_skills is not None else load_pattern_skills()
    gate_specs, credit_specs = rubric_layers(task)
    specs = [("gate", s) for s in gate_specs] + [("credit", s) for s in credit_specs]
    by_id_spec = {s["id"]: s for _, s in specs}

    raw_results = raw.get("assertion_results") or []
    results: dict[str, dict] = {}
    for item in raw_results:
        rid = item.get("id")
        if rid in results:
            raise ReportError(f"断言结果重复：{rid}")
        results[rid] = item

    missing, extra = set(by_id_spec) - set(results), set(results) - set(by_id_spec)
    if missing or extra:
        raise ReportError(f"断言结果与 rubric 不一致：缺 {sorted(missing)}，多 {sorted(extra)}")

    trace_audit = dict(raw["trace_audit"])
    trace_ok = bool(trace_audit["reported"]) and bool(trace_audit["schema_valid"])
    notes: list[str] = []
    if unreachable_reason is not None:
        # 放在最前：读者先知道「这次根本没评上」，再读下面的逐条判定才不会误解
        notes.append(
            f"本次未评测：平台未能取到任何证据（{unreachable_reason}）。"
            "全部断言记 unscored —— 0 分、不计入掌握度、不产出错误模式，"
            "**不视为能力不足**。请确认 Agent 端点可达后重新提交"
        )

    assertions = [
        _assertion_item(spec["id"], spec, layer, results[spec["id"]], trace_ok, trace_audit, results, vocab)
        for layer, spec in specs
    ]

    # ── 第一段：门禁 ──────────────────────────────────────────────────────
    gate_assertions = [a for a in assertions if a["layer"] == GATE]
    disqualifying = [a["id"] for a in gate_assertions if a["state"] == "fail"]
    gate_unscored = [a["id"] for a in gate_assertions if a["state"] == "unscored"]
    disqualified = bool(disqualifying)
    # unreachable 时整份都没评测，这条"门禁无法判定"的细节会盖住真正原因，故跳过
    if gate_unscored and unreachable_reason is None:
        # TD-021 / F6②：原文「分数不会因此变高」与实测 ratio=0.5 相反，按 S3-3-6 订正。
        notes.append(
            "门禁 " + ", ".join(gate_unscored) + " 无法判定（数据缺失）：按 on_unscored=do_not_disqualify 不拦，"
            "本次不判不合格；门禁本身不计分，依赖它的 credit 断言记 blocked 也不计分，"
            "但其余 credit 断言仍照常计分——可能出现 0 < ratio < 1，不写成「分数不会变高」"
        )

    # ── 第二段：积分 ──────────────────────────────────────────────────────
    credit_total = sum(s["weight"] for layer, s in specs if layer == CREDIT)
    declared_total = ((task.get("scoring") or {}).get("score") or {}).get("credit", {}).get("total_weight")
    if declared_total is not None and declared_total != credit_total:
        raise ReportError(f"YAML 声明的 credit.total_weight={declared_total}，但 credit 断言权重之和={credit_total}")

    counterfactual_credit = sum(a["weight"] for a in assertions if a["layer"] == CREDIT and a["state"] == "pass")

    # ── P2 / Q5-D1（round-70）：按题目类型决定「缺过程」怎么算 ────────────
    # 人拍板口径（§53.1）：需要步骤的题缺过程 ⇒ 本次不打能力分、不记缺点、记一条中性
    # 流程事实、提示补交；不需要步骤的题只交答案即可，照常计分。
    # 关键：**不改写断言状态**——「缺过程」是本次不算数，不是「无法判定」。
    # 断言照常判定（诊断不丢），只压制分数与归因；否则会把"做对了却没过过程门槛"
    # 的证据一起抹掉，与决策 5「门禁只管分数，不管观测」冲突。
    process_fact = _process_fact(task, raw, trace_ok)
    missing_process = bool(process_fact["missing"])

    # TD-038（round-70）：任务版本不一致 ⇒ 本份「不算数」，但不是「不会」。
    # 处置按人拍板的第三种：报告照出、分数不算、不计能力短板、写清请重交。
    # 与 disqualified 分开记：disqualified 是学生没交东西/没跑完，voided 是这次不算数。
    void_reason = _task_version_mismatch(task, raw)
    voided = void_reason is not None
    suppressed = disqualified or voided or missing_process
    weighted_sum = 0 if suppressed else counterfactual_credit
    ratio = 0.0 if suppressed else round(counterfactual_credit / credit_total, 6)
    by_state = {s: sum(1 for a in assertions if a["state"] == s) for s in STATES}
    if disqualified:
        notes.append(
            "门禁未通过（" + ", ".join(disqualifying) + "）：本次提交无资格评分，ratio 记 0。"
            f"credit 层断言仍照常判定（反事实得分 {counterfactual_credit}/{credit_total}："
            "这是「若过门禁本可得」的权重和，不是本次分数，不得当作分数汇总）"
        )
    if voided:
        expected_ver = task.get("task_version")
        got_ver = (raw.get("submission") or {}).get("task_version")
        notes.append(
            f"任务版本不一致（提交 {got_ver} / 当前 {expected_ver}）：本次提交**不算数**，"
            "分数记 0 且不计入掌握度，但**不代表能力不足**。请按当前版本重新提交；"
            "以下断言判定基于提交版本，仅供参考"
        )
    if missing_process:
        if unreachable_reason is not None:
            # 事实一样（这次确实没过程），责任归属不同：平台没采集 ≠ 学习者没交。
            # 两者的处置都是"本次不计分"，但文案若混为一谈，学习者会以为被追责。
            notes.append(
                "本任务要求提交过程/思路：本次确实没有收到过程证据，但原因是平台未能采集"
                f"（{unreachable_reason}），**不是学习者未提交**，因此不按「流程不完整」追责"
            )
        else:
            notes.append(
                "本任务要求提交过程/思路：本次未收到过程（未上报 trace，也未写思路说明）。"
                "按**流程不完整**处理——本次不计能力分、不计入掌握度、不产出错误模式，"
                f"**不视为能力不足**（反事实得分 {counterfactual_credit}/{credit_total} 仅供参考）。"
                "请补交步骤，或写一句「没思路」后重交"
            )

    attr_src = (task.get("scoring") or {}).get("attribution") or {}
    attribution_policy = {
        s: {
            "counts_toward_mastery": bool(attr_src.get(s, {}).get("counts_toward_mastery")),
            "emit_error_patterns": bool(attr_src.get(s, {}).get("emit_error_patterns")),
        }
        for s in STATES
    }

    # ── 技能缺口聚合（B-06 / ROADMAP R-2）────────────────────────────────
    # 归因优先级：**断言自己声明的 skill_id 永远优先，词表只作兜底**。
    # 这是 Q10=A 的边界（"不改 build_report() 按 spec.skill_id 的归因"，见
    # docs/verify-brief-s3-3-1.md §Q10）——有 skill_id 时词表完全不参与，一对多 / 权重分摊
    # 都不发生，所以既有的逐例归因一位不变；TD-001（跨技能归因）仍未销项。
    # 兜底只在一种情况下生效：任务作者漏写 skill_id。此前这种失败要么静默消失（P5 教训），
    # 要么只能进 unattributed_patterns；现在能经 mapped_skill_ids 找回技能归属。
    gaps: dict[str, dict] = {}
    unattributed: list[dict] = []
    for a in assertions:
        if a["state"] != "fail":
            continue
        skills, by = _attribute(a, pattern_skills)
        if not skills:
            # 有错误模式却没有技能归属的失败（R1/R5 属此类）——不进 skill_gaps，但绝不静默丢弃。
            # P5 的教训：无文件时 skill_gaps 曾是空数组，"没交产物"这件事在报告里完全不留痕。
            unattributed.append(
                {"assertion_id": a["id"], "layer": a["layer"], "patterns": a["error_patterns"]}
            )
            continue
        # 兜底路径可能出现一对多：按技能数等分，保证 Σweight_lost ≤ credit_total（下面有硬校验）。
        share = _split_weight(a["weight"], len(skills))
        for skill_id in skills:
            gap = gaps.setdefault(
                skill_id,
                {
                    "skill_id": skill_id,
                    "failed_assertions": [],
                    "patterns": [],
                    "weight_lost": 0.0,
                },
            )
            gap["failed_assertions"].append(a["id"])
            gap["weight_lost"] = _clean_number(gap["weight_lost"] + share)
            gap.setdefault("attributed_by", set()).add(by)
            for code in a["error_patterns"]:
                if code not in gap["patterns"]:
                    gap["patterns"].append(code)

    for gap in gaps.values():
        gap["attributed_by"] = "+".join(sorted(gap["attributed_by"]))
    lost_total = _clean_number(sum(g["weight_lost"] for g in gaps.values()))
    if lost_total > credit_total + 1e-9:
        # 归因分摊若写错（例如每个技能都记满权重），这里会立刻炸，而不是等状态层算掌握度时才发现
        raise ReportError(
            f"技能缺口 weight_lost 之和 {lost_total} 超过 credit 总权重 {credit_total}，归因必有多记"
        )

    if not trace_audit["reported"] and unreachable_reason is None:
        notes.append("未上报 trace：轨迹类断言记 unscored（分数贡献 0，但不计入掌握度），不判 0")
    elif not trace_audit["schema_valid"]:
        notes.append(
            "trace 未通过前置校验（" + ", ".join(trace_audit["invalid_reasons"]) + "）：轨迹类断言记 unscored"
        )

    score_obj = {
        "weighted_sum": weighted_sum,
        "counterfactual_credit": counterfactual_credit,
        "total_weight": credit_total,
        "ratio": ratio,
        "denominator_policy": DENOMINATOR_POLICY,
        "disqualified": disqualified,
        "disqualifying_assertions": disqualifying,
        "by_state": by_state,
        "voided": voided,
    }
    if voided:
        score_obj["void_reason"] = void_reason

    report = {
        "report_version": REPORT_VERSION,
        "submission": raw["submission"],
        "trace_audit": trace_audit,
        "score": score_obj,
        "attribution_policy": attribution_policy,
        "report_attribution": _report_attribution(
            attribution_policy, by_state, voided or missing_process
        ),
        "process_fact": process_fact,
        "assertions": assertions,
        "skill_gaps": sorted(gaps.values(), key=lambda g: (-g["weight_lost"], g["skill_id"])),
        "unattributed_patterns": unattributed,
    }
    if notes:
        report["notes"] = notes
    return report


def _run_all_fixtures(task_path: str) -> int:
    """--fixture all：跑 fixtures/raw-*.json 全部夹具，每个都过 schema，只打印摘要。"""
    ok = True
    for path in sorted((ROOT / "fixtures").glob("raw-*.json")):
        try:
            report = build_report(load_task(task_path), json.loads(path.read_text(encoding="utf-8")))
            validate_report(report)
        except ReportError as exc:
            print(f"FAIL {path.stem:<16} {exc}", file=sys.stderr)
            ok = False
            continue
        score = report["score"]
        states = " ".join(f"{k}={v}" for k, v in score["by_state"].items())
        flag = "DQ" if score["disqualified"] else "  "
        print(
            f"OK {flag} {path.stem:<16} ratio={score['ratio']:.4f} "
            f"({score['weighted_sum']}/{score['total_weight']}) counterfactual={score['counterfactual_credit']} {states}"
        )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把逐条断言结果组装成诊断报告并过 schema")
    ap.add_argument("--task", default=str(ROOT / "tasks" / "dbg-001-order-report.yaml"))
    ap.add_argument("--fixture", required=True, help="raw 结果 JSON；写 all 表示跑 fixtures/ 下全部 raw-*.json")
    ap.add_argument("--out", help="写入文件；缺省打印到 stdout")
    args = ap.parse_args(argv)

    if args.fixture == "all":
        return _run_all_fixtures(args.task)

    try:
        report = build_report(load_task(args.task), json.loads(Path(args.fixture).read_text(encoding="utf-8")))
        validate_report(report)
    except ReportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"OK: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
