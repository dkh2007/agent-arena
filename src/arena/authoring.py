"""加题的生成器与单题自检（B-30 扩题前置，2026-11-22）。

## 这个模块解决的是"加题很贵"这件事

加一道题的完整代价原来是：

| 步 | 动作 | 耗时 | 出错方式 |
|---|---|---|---|
| 1 | 手抄一份母本骨架，改成新情景 | 20 分钟 | 抄漏 `trace_requirements`，继承断掉而全绿 |
| 2 | 手算 `valid_count` / `total_amount` | 5 分钟 | 算错一次，参考 Agent 满分这条测试才报 |
| 3 | 改 3 处写死常量（20 / 16 / lab 三处） | 5 分钟 | 漏一处 → 全量 4 红，还得回头看是不是自己改坏了 |
| 4 | 跑全量才知道哪里错 | 12 分钟 | 报错在 900 行之外，得自己猜是哪一处 |

第 3 步已在 2026-11-22 换成「台账写一句 why」+ 推导计数；本模块处理 1、2、4：
**生成时就把数据与期望值一起算好**，**单题自检 0.6 秒给出"还差什么"**（全库 24 题 1.75 秒）。

## 两条设计纪律

① **不引入第二真相源**。家族表读 `docs/task-families.yaml`（测试与文档也读它），
   期望值用 `arena.derivation` 现算（不是本模块另写一份求和）。本模块只做"编排 + 报错措辞"。
② **自检可以比 pytest 严，不能比它松**。每条判据后面标了对应的测试；比测试松就是骗人
   （自检绿了、pytest 红），比测试严只是多拦一步（自检红、pytest 也可能绿），代价是误报，
   所以只有"确认无害"的才写成警告级 `!` 而不是错误级 `X`。

## 用法

    python scripts/new_task.py --list                    # 家族 × 难度梯度，看缺哪一档
    python scripts/new_task.py --check tasks/xxx.yaml     # 单题自检（实测 0.6 秒）
    python scripts/new_task.py --new --family retry-drill \
        --id ret-007-late-fault --title "……" --rows 28 --page 10 --fault-at 26

`--new` 产出的题**应当直接通过自检与 pytest**；人只需要改 title 与 scenario 的措辞、
把 fixture 的金额换成业务上说得通的数。做不到这一点就是本模块的 bug，不是"加题本来就麻烦"。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from . import derivation as dv
from .diagnosis_report import ReportError, load_task

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tasks"
FAMILIES_PATH = ROOT / "docs" / "task-families.yaml"
LOG_PATH = ROOT / "docs" / "task-authoring-log.yaml"
TAXONOMY_PATH = ROOT / "docs" / "skill-taxonomy-v0.yaml"

DEFAULT_TEMPLATE = "tpl-tool-debug"
# 第三套骨架（2026-11-23）。自检要认识它，否则加一道交接题会报一堆"日志 0 条 ERROR"
# 这种按定位骨架写的假错——那比没有自检更糟：人会学会忽略输出。
ASK_TEMPLATE = "tpl-ask-first"

# 难度 → "要处理多少东西"的下限。与 tests/test_task_variants.py::test_difficulty_is_not_decorative
# 同源：汇报骨架看页数，定位骨架看 ERROR 条数。改这里必须同步改那条测试，否则自检与 pytest 会打架。
MIN_PAGES = {1: 1, 2: 3, 3: 4}
MIN_ERRORS = {1: 2, 2: 3, 3: 4}

# 变体不许重申的骨架块（tests/test_task_variants.py::test_every_variant_declares_its_parent_...）
SKELETON_BLOCKS = ("rubric_gate:", "scoring:", "trace_requirements:", "submission_requirements:")

BANNED_WHY = ("同上", "见上", "略", "待补", "TODO", "新题", "复制粘贴")
MIN_WHY_CHARS = 12

X, WARN, OK = "X", "!", "✓"


@dataclass
class Finding:
    level: str
    message: str

    def render(self) -> str:
        return f"  [{self.level}] {self.message}"


# ── 读表 ────────────────────────────────────────────────────────────────────

def load_families() -> list[dict]:
    return yaml.safe_load(FAMILIES_PATH.read_text(encoding="utf-8"))["families"]


def load_log() -> list[dict]:
    data = yaml.safe_load(LOG_PATH.read_text(encoding="utf-8")) or {}
    return data.get("tasks") or []


def family_of(families: Sequence[dict], family_id: str) -> dict:
    for f in families:
        if f["id"] == family_id:
            return f
    known = ", ".join(f["id"] for f in families)
    raise KeyError(f"家族 {family_id!r} 未登记（已登记：{known}）")


def siblings(family_id: str, families: Sequence[dict] | None = None) -> list[str]:
    """同家族的题 id（按文件名排序）。加题前先看这里，避免和已有题撞考点。"""
    families = families or load_families()
    fam = family_of(families, family_id)
    out = []
    for path in sorted(TASKS_DIR.glob("*.yaml")):
        task = yaml.safe_load(path.read_text(encoding="utf-8"))
        if (task.get("variant_meta") or {}).get("family_id") == family_id:
            out.append(path.stem)
    return out


# ── 单题自检 ────────────────────────────────────────────────────────────────

def _scale(task: dict) -> str:
    """一句话概括"这题要处理多少东西"——自检报告与 --list 共用。

    三套骨架各有各的"量"：汇报/交接看页数，定位看 ERROR 条数。
    原来只分「默认骨架 / 其它」，于是交接题被当成定位题去数日志，
    在 `--list` 里显示成 `0 行日志 / 0 条 ERROR`——**看着像题坏了**，
    后来加题的人会以为是自己的 YAML 有问题。三套骨架就得三个分支。
    """
    tpl = task.get("variant_template_id") or DEFAULT_TEMPLATE
    if tpl == ASK_TEMPLATE:
        rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
        page = _page_max(task)
        pages = -(-rows // page) if page else 0
        # 交接族刻意不注入故障（只考"先问再动手"），所以这里没有故障位可报。
        return f"{rows} 行 / 页 {page} / {pages} 页 / 无故障（只考先问再动手）"
    if tpl != DEFAULT_TEMPLATE:
        log = (task.get("test_inputs") or {}).get("files", {}).get(
            (task.get("locate") or {}).get("log_path", ""), "")
        errors = sum(1 for ln in log.splitlines() if " ERROR " in ln)
        return f"{len(log.splitlines())} 行日志 / {errors} 条 ERROR"
    rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
    page = _page_max(task)
    fi = task.get("fault_injection") or {}
    at = (fi.get("match") or {}).get("offset_covers")
    pages = -(-rows // page) if page else 0
    tail = f" / 故障第 {at} 条" if at else " / 无故障"
    return f"{rows} 行 / 页 {page} / {pages} 页{tail}"


def _static_text(task: dict) -> str:
    """`mock.source=static` 那个工具的固定答复；没有就返回空串。

    交接骨架把"口径"放在这里。自检要读它，才能验"答复里的页大小与分页声明一致"——
    两者不一致时，学习者问了也做不对，而失败会被记成"能力不足"。
    """
    for tool in task.get("tools") or []:
        mock = tool.get("mock") or {}
        if mock.get("source") == "static":
            return str(mock.get("text") or "")
    return ""


def _page_max(task: dict) -> int | None:
    """页大小上限：优先 `mock.pagination.page_size_max`，回退 `limit.maximum`。

    为什么优先 mock：交接骨架（tpl-ask-first）刻意**不**在工具参数上写 `maximum`——
    上限属于"要问的口径"，写进 schema 等于把答案白送，`ask_policy` 就没意义了。
    所以那套骨架的上限只存在于 mock 的分页声明里。
    """
    for tool in task.get("tools") or []:
        pag = (tool.get("mock") or {}).get("pagination") or {}
        if pag.get("page_size_max"):
            return int(pag["page_size_max"])
    for tool in task.get("tools") or []:
        if tool.get("name") == "fetch_orders":
            for p in tool.get("parameters") or []:
                if p.get("name") == "limit":
                    return p.get("maximum")
    return None


def _invalid_rows(task: dict) -> list[int]:
    """按 derivation 的规则列出无效行的 1 基下标——用来算"无效行散在哪几页"。"""
    rows = (task.get("test_inputs") or {}).get("orders_fixture") or []
    rule = task.get("derivation")
    if not rule or not rows:
        return []
    conds = rule.get("valid_when") or []
    out = []
    for i, row in enumerate(rows, start=1):
        if not dv._passes(i, row, conds):
            out.append(i)
    return out


def check_task(path: str | Path, *, families: Sequence[dict] | None = None,
               log: Sequence[dict] | None = None) -> list[Finding]:
    """单题自检。返回 findings；有任何 level == X 即视为不通过。"""
    path = Path(path)
    families = families if families is not None else load_families()
    log = log if log is not None else load_log()
    why_by_id = {row["id"]: (row.get("why") or "") for row in log}
    out: list[Finding] = []

    # ① 能解析（含继承成环、id 与母本撞车）
    try:
        task = load_task(path)
    except ReportError as exc:
        return [Finding(X, f"YAML 读不出来：{exc}")]

    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)

    # ② 文件名即 task_id：注册表按 task_id 索引，两者不一致会让人找不到文件
    if task.get("task_id") != path.stem:
        out.append(Finding(X, f"task_id={task.get('task_id')!r} 与文件名 {path.stem!r} 不一致"))

    # ③ 家族 / 主技能 / 骨架 / 母本 四者一致
    meta = task.get("variant_meta") or {}
    family_id = meta.get("family_id")
    try:
        fam = family_of(families, family_id or "")
    except KeyError as exc:
        out.append(Finding(X, str(exc)))
        fam = None
    if fam:
        if task.get("primary_skill_id") != fam["primary_skill"]:
            out.append(Finding(
                X, f"primary_skill_id={task.get('primary_skill_id')!r}，"
                   f"而家族 {family_id} 声明的是 {fam['primary_skill']!r}"))
        tpl = task.get("variant_template_id") or DEFAULT_TEMPLATE
        if tpl != fam["template"]:
            out.append(Finding(X, f"variant_template_id={tpl!r}，家族声明的是 {fam['template']!r}"))
        if raw.get("base_task") and raw["base_task"] != fam["mother"]:
            out.append(Finding(
                X, f"base_task={raw['base_task']!r}，该骨架的母本是 {fam['mother']!r}"))
        if not path.stem.startswith(fam["prefix"]):
            out.append(Finding(
                WARN, f"id 前缀不是 {fam['prefix']}——台账按前缀归段，混了会排到别的家族下面"))
    if meta.get("kind") != "variant" and raw.get("base_task"):
        out.append(Finding(X, "variant_meta.kind 应为 'variant'"))
    if meta.get("difficulty") not in (1, 2, 3):
        out.append(Finding(X, f"difficulty={meta.get('difficulty')!r} 不在 1/2/3"))

    # ④ 台账登记 + why 有实义
    why = why_by_id.get(path.stem)
    if why is None:
        out.append(Finding(
            X, "台账（docs/task-authoring-log.yaml）没有这一行——加题必须写一句"
               "「这题变的是什么」，否则与原来改数字没区别"))
    else:
        if len(why.strip()) < MIN_WHY_CHARS:
            out.append(Finding(X, f"why 只有 {len(why.strip())} 字，写不出差异说明就是没想清"))
        for bad in BANNED_WHY:
            if bad in why:
                out.append(Finding(X, f"why 里出现了占位式说法「{bad}」"))

    # ⑤ 不许重申骨架块（母本自己当然有）
    if raw.get("base_task"):
        for block in SKELETON_BLOCKS:
            if re.search(rf"^{re.escape(block)}", raw_text, re.MULTILINE):
                out.append(Finding(
                    X, f"抄了骨架里的 {block}——应改为继承，否则「改母本 ⇒ 全族跟着变」就断了"))

    # ⑥ 难度不是装饰：要处理的东西够不够多
    tpl = task.get("variant_template_id") or DEFAULT_TEMPLATE
    diff = meta.get("difficulty")
    if diff in (1, 2, 3):
        if tpl in (DEFAULT_TEMPLATE, ASK_TEMPLATE):
            rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
            page = _page_max(task)
            if not page:
                out.append(Finding(X, "tools 里没有 fetch_orders.limit.maximum，页数算不出来"))
            else:
                pages = -(-rows // page)
                need = MIN_PAGES[diff]
                lvl = X if pages < need else OK
                out.append(Finding(
                    lvl, f"{rows} 行 / 页 {page} = {pages} 页，难度 {diff} 至少要 {need} 页"))
        else:
            log_path = (task.get("locate") or {}).get("log_path")
            log_text = (task.get("test_inputs") or {}).get("files", {}).get(log_path, "")
            errors = sum(1 for ln in log_text.splitlines() if " ERROR " in ln)
            need = MIN_ERRORS[diff]
            lvl = X if errors < need else OK
            out.append(Finding(lvl, f"日志 {errors} 条 ERROR，难度 {diff} 至少要 {need} 条"))

    # ⑦ 题面里的数字与配置一致（学习者照着题面做，题面错了就会"照做也违规"）
    scenario = task.get("scenario") or ""
    if tpl == DEFAULT_TEMPLATE:
        limit = _page_max(task)
        rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
        for needle, hint in (
            (f"1 到 {limit}", f"题面没写清 limit 上限（应为「1 到 {limit}」）"),
            ("invalid_limit", "题面没提越界会报 invalid_limit"),
            (f"共 {rows} 条", f"题面里的总条数与夹具不一致（应为「共 {rows} 条」）"),
        ):
            out.append(Finding(OK if needle in scenario else X, hint if needle not in scenario
                               else f"题面含「{needle}」"))
    elif tpl == ASK_TEMPLATE:
        # 交接骨架与汇报骨架**刚好相反**：口径是那个要问的东西，题面写出来就作弊了。
        # 两条一起构成口径：什么时候该告诉学习者，什么时候不该。
        limit = _page_max(task)
        rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
        out.append(Finding(
            X if limit and f"{limit} 条" in scenario else OK,
            "题面把每页条数写出来了——ask_policy 就成了摆设" if limit and f"{limit} 条" in scenario
            else "题面没有泄漏页大小"))
        out.append(Finding(
            X if "invalid_limit" in scenario else OK,
            "题面提了 invalid_limit，等于提示页大小有限制" if "invalid_limit" in scenario
            else "题面没有泄漏越界错误码"))
        # 正向：口径的来源必须写清，否则学习者会以为是自己漏读了文档（那就变成猜谜）
        src_ok = any(w in scenario for w in ("值班同事", "口述", "当面", "只有人知道"))
        out.append(Finding(OK if src_ok else X,
                           "题面写明了口径来自人" if src_ok
                           else "题面没说清口径来自人——不写就等于让学习者去猜文档在哪"))
        # 答复里的页大小必须与分页声明一致：不一致时学习者问了也做不对，
        # 而失败会记成"能力不足"，其实是题坏了。
        policy = _static_text(task)
        m = re.search(r"一页最多\s*(\d+)\s*条", policy)
        if not policy:
            out.append(Finding(X, "没有 mock.source=static 的工具——交接骨架靠它承载口径"))
        elif not m:
            out.append(Finding(WARN, "答复里没有「一页最多 N 条」，参考 Agent 读不出页大小"))
        elif limit and int(m.group(1)) != limit:
            out.append(Finding(
                X, f"答复说一页 {m.group(1)} 条，而 mock.pagination 是 {limit} 条——问了也做不对"))
        else:
            out.append(Finding(OK, f"答复里的页大小与分页声明一致（{limit} 条）"))
    else:
        locate = task.get("locate") or {}
        for key in ("log_path", "source_path", "diagnosis_path"):
            val = locate.get(key)
            out.append(Finding(OK if val and val in scenario else X,
                               f"题面没写 {key}={val}" if not (val and val in scenario)
                               else f"题面含 {key}"))
        for label in ("type_mismatch", "off_by_one", "null_deref", "other"):
            out.append(Finding(OK if label in scenario else X,
                               f"题面没公布根因取值 {label}" if label not in scenario
                               else f"题面含 {label}"))
        has_last = "最后一条 ERROR" in scenario
        out.append(Finding(OK if has_last else X,
                           "题面含「最后一条 ERROR」" if has_last
                           else "题面没说明看哪条 ERROR（应为「最后一条 ERROR」）"))

    # ⑧ 期望值必须现算得出来，且两处相等
    expected = (task.get("test_inputs") or {}).get("expected") or {}
    credit = task.get("rubric_credit") or []
    r2 = next((r for r in credit if r.get("id") in ("R2_report_correct", "R2_diagnosis_correct")), None)
    if "valid_count" in expected:
        if "derivation" not in task:
            out.append(Finding(
                X, "答案键含 valid_count 但没声明 derivation——期望值只能靠手抄，"
                   "而手抄是本仓写错过两次的那件事"))
        else:
            try:
                got = dv.derive_task(task)
            except Exception as exc:  # noqa: BLE001 - 规则本身写错时要看得见原因
                out.append(Finding(X, f"derivation 规则跑不动：{exc}"))
                got = {}
            if got:
                bad = [k for k, v in expected.items()
                       if k in got and abs(got[k] - v) > 0.005]
                if bad:
                    detail = "；".join(f"{k}: 写的 {expected[k]} ≠ 现算 {got[k]}" for k in bad)
                    out.append(Finding(X, f"期望值与数据不符（{detail}）——把 expected 改成现算值"))
                elif set(got) != set(expected):
                    out.append(Finding(
                        X, f"现算输出字段 {sorted(got)} 与答案键 {sorted(expected)} 不一致"))
                else:
                    out.append(Finding(OK, f"期望值与数据一致：{got}"))
    if r2 is not None and "expected" in r2:
        if r2["expected"] != expected:
            out.append(Finding(
                X, f"rubric_credit[{r2['id']}].expected 与 test_inputs.expected 不一致——"
                   f"两处写的是同一件事"))
        else:
            out.append(Finding(OK, "R2 期望与答案键一致"))

    # ⑨ 故障位三处相等（match / R3.calls[0] / failures[0]）
    fi = task.get("fault_injection") or {}
    match = fi.get("match") or {}
    failures = fi.get("failures") or []
    r3 = next((r for r in credit if r.get("id") == "R3_retry_evidence"), None)
    if match and r3:
        calls = (r3.get("expected") or {}).get("calls") or []
        if len(calls) != 2:
            out.append(Finding(X, f"R3.calls 应为「失败 → 成功」两步，实际 {len(calls)} 步"))
        else:
            # 判据与 tests/test_task_variants.py::test_fault_declaration_matches_retry_expectation
            # 同一口径：**注入 ⊇ 判定**，不是逐字符相等。判定比注入窄是安全的（只认这一个产物路径），
            # 比注入宽才致命（一次不触发故障的调用被当成"应该失败"，R3 对所有人永远 fail）。
            got = calls[0].get("params_match") or {}
            wide = sorted(k for k, v in match.items() if got.get(k) != v)
            if wide:
                out.append(Finding(
                    X, f"R3 的匹配条件比注入更宽：{wide} 在 match 里是 "
                       f"{ {k: match[k] for k in wide} }，R3 写成 {got!r}"))
            else:
                out.append(Finding(OK, f"故障条件相容：注入 {match} ⊇ 判定 {got}"))
            first_err = (failures[0] or {}).get("http_status") if failures else None
            if calls[0].get("http_status") != first_err:
                out.append(Finding(
                    X, f"故障码两处不一致：failures[0].http_status={first_err} / "
                       f"R3.calls[0].http_status={calls[0].get('http_status')}"))
            if calls[0].get("status") != "error" or calls[1].get("status") != "success":
                out.append(Finding(X, "R3 两步的状态应为 error → success"))
    elif match and not r3:
        out.append(Finding(X, "声明了 fault_injection 但 rubric_credit 里没有 R3_retry_evidence"))
    elif r3:
        # 空 match（`match: null` / `{}`）：**故意不给过滤条件**，任何一次对该工具的调用都命中。
        # 2026-11-28 加这道分支，因为上面两条都在 `match and ...` 里，空 match 时会静默跳过
        # ——而"静默跳过"正是故障空转最好的藏身处。空 match 时能查、也必须查的是两件事：
        #   ① 目标工具真的存在（`target_tool` 写错一个字母，故障永不触发，页面一片正常）
        #   ② R3 的第一步真的指这个工具（指错了 ⇒ 判定与注入打在两个工具上，两边都"合理"）
        target = fi.get("target_tool")
        tool_names = [t.get("name") for t in task.get("tools") or []]
        if not target:
            out.append(Finding(X, "空 match 时必须在 fault_injection.target_tool 里指明打哪个工具"))
        elif target not in tool_names:
            out.append(Finding(
                X, f"fault_injection.target_tool={target!r} 不在 tools {tool_names} 里——故障永不触发"))
        calls = (r3.get("expected") or {}).get("calls") or []
        if len(calls) != 2:
            out.append(Finding(X, f"R3.calls 应为「失败 → 成功」两步，实际 {len(calls)} 步"))
        elif target and calls[0].get("name") != target:
            out.append(Finding(
                X, f"R3.calls[0].name={calls[0].get('name')!r} 与 target_tool={target!r} 不一致"
                   "——注入与判定打在两个工具上，各自都'合理'，合起来是错的"))
        else:
            first_err = (failures[0] or {}).get("http_status") if failures else None
            if calls[0].get("http_status") != first_err:
                out.append(Finding(
                    X, f"故障码两处不一致：failures[0].http_status={first_err} / "
                       f"R3.calls[0].http_status={calls[0].get('http_status')}"))
            elif calls[0].get("status") != "error" or calls[1].get("status") != "success":
                out.append(Finding(X, "R3 两步的状态应为 error → success"))
            else:
                out.append(Finding(OK, f"空 match：任何一次 {target} 调用都命中，R3 也指 {target}"))

    # ⑩ 故障位落在数据范围内，且**不能是最后一条**
    #    判据来自 tests/test_task_variants.py::test_page_ceiling_and_report_expectations_are_self_consistent
    #    的 `0 < offset < len(rows)`。理由：故障若落在最后一条，"重试之后还要接着拉"这个考点就没了
    #    ——重试成功即结束，与"重试即完成"的简单情形分不开。2026-11-22 生成 prm-005 时就踩了这条
    #    （生成器当时给了第 20 条 = 最后一条，全量才报出来）。
    if "offset_covers" in match:
        rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
        at = match["offset_covers"]
        if at == rows:
            msg = f"故障位 offset_covers={at} 是最后一条，重试后没有剩余数据要拉——考点塌了"
        elif not 1 <= at < rows:
            msg = f"故障位 offset_covers={at} 不在 1..{rows - 1} 内，永远不会触发"
        else:
            msg = f"故障位 offset_covers={at}（第 {at} 条，数据共 {rows} 条）"
        out.append(Finding(OK if 1 <= at < rows else X, msg))

    # ⑩c `offset_covers` 只能用在分页工具上
    #     2026-11-28 加：ret-008 把故障挪到 write_report 时，最自然的错法是直接抄母本的
    #     `match: {offset_covers: 10}` 只改 target_tool。那时 `page_covers(params, 10)` 会去读
    #     write_report 的 `offset`——它没有这个参数 ⇒ 判"不覆盖" ⇒ 故障永不触发。
    #     而它不会报错、不会崩，只是这道题悄悄退化成"没有故障"（学习者照常满分）。
    #     这是本模块所有检查里唯一能静态抓住那种退化的地方。
    if "offset_covers" in match:
        target = fi.get("target_tool")
        paged = [t.get("name") for t in task.get("tools") or []
                 if (t.get("mock") or {}).get("pagination")]
        if target not in paged:
            out.append(Finding(
                X, f"offset_covers 用在了 {target!r} 上，但它不是分页工具（分页的只有 {paged}）"
                   "——它的入参里没有 offset，故障永不触发"))

    # ⑩b 没显式写 fault_injection 时，提醒"故障是继承来的，不是没有"
    #    这条防的是 2026-11-22 那次误记：文件里没有这个块，台账却写"不注入故障"。
    if raw.get("base_task") and "fault_injection" not in raw and match:
        out.append(Finding(
            WARN, f"本题没写 fault_injection，故障是从母本继承的（第 {match.get('offset_covers')} 条）"
                  f"——不是「没有故障」。台账 why 要按有故障写"))

    # ⑪ 无效行有没有散在各页（警告级：难度靠"每页都要剔除"撑起来时才需要）
    invalid = _invalid_rows(task)
    page = _page_max(task)
    rows = len((task.get("test_inputs") or {}).get("orders_fixture") or [])
    if invalid and page and rows:
        pages = -(-rows // page)
        hit = {(-(-i // page)) for i in invalid}
        missing = sorted(set(range(1, pages + 1)) - hit)
        if missing:
            out.append(Finding(
                WARN, f"第 {missing} 页没有任何无效行——这几页的剔除逻辑没被考到"
                      f"（无效行共 {invalid}）"))
        else:
            out.append(Finding(OK, f"每页都有无效行（共 {len(invalid)} 条）"))

    return out


def summarize(findings: Iterable[Finding]) -> tuple[int, int]:
    xs = sum(1 for f in findings if f.level == X)
    ws = sum(1 for f in findings if f.level == WARN)
    return xs, ws


# ── 生成骨架 ────────────────────────────────────────────────────────────────

_HEADER = """\
# 变体任务（B-05）：本文件**只写与母本的差异**，rubric 骨架 / 工具契约 / 执行边界
# 一律从 `base_task` 深合并继承（规则见 src/arena/diagnosis_report.py 的 _merge_dict 注释）。
# 想改骨架请改母本；改这里只影响本变体。改动后跑 `pytest tests/test_task_variants.py`。
#
# 本文件由 `python scripts/new_task.py --new` 生成（2026-11-22，B-30）。
# 生成时已按 derivation 现算好 expected，所以**默认自检与 pytest 都绿**；
# 需要人改的只有 title 与 scenario 的措辞（那是教学设计，脚本不该替你想）。
"""


def _amounts(rows: int, *, invalid_every: int) -> tuple[list[dict], list[str]]:
    """造 rows 行数据：每 invalid_every 行插一条无效（缺字段 / null / 负数轮流）。

    返回 (fixture, 每行注释)。金额递增且两位小数，方便人一眼看出算错没有。
    """
    fixture: list[dict] = []
    notes: list[str] = []
    kinds = ("missing", "null", "negative")
    valid_idx = 0
    for i in range(1, rows + 1):
        oid = f"nw-{i:03d}"
        if invalid_every and i % invalid_every == 0:
            kind = kinds[(i // invalid_every - 1) % 3]
            if kind == "missing":
                fixture.append({"order_id": oid})
                notes.append(f"# 缺 amount 字段（第 {i} 条）")
            elif kind == "null":
                fixture.append({"order_id": oid, "amount": None})
                notes.append(f"# 金额为 null（第 {i} 条）")
            else:
                fixture.append({"order_id": oid, "amount": -round(100 + 12.5 * valid_idx, 2)})
                notes.append(f"# 负数（第 {i} 条）")
        else:
            valid_idx += 1
            fixture.append({"order_id": oid, "amount": round(100 + 12.5 * valid_idx, 2)})
            notes.append("")
    return fixture, notes


def _render_fixture(fixture: list[dict], notes: list[str], *, indent: str = "    ") -> str:
    lines = []
    for row, note in zip(fixture, notes):
        body = ", ".join(
            f"{k}: {('null' if v is None else (v if isinstance(v, str) else v))}"
            for k, v in row.items()
        )
        pad = " " * max(2, 46 - len(f"{{{body}}}") - len(indent))
        lines.append(f"{indent}- {{{body}}}{pad}{note}".rstrip())
    return "\n".join(lines)


def scaffold(*, family_id: str, task_id: str, title: str, rows: int, page: int,
             fault_at: int | None, invalid_every: int = 5,
             version: str = "2026-11-22.1") -> tuple[str, str]:
    """返回 (YAML 文本, 台账里的 why 占位)。期望值在这里就算好，不留给手抄。

    `fault_at=None` 时**不会**生成"无故障"的题，而是自动取一个合理位置并把它写出来。
    原因见下面 `_default_fault_at` 的注释：汇报骨架的 R3 断言天然依赖一次失败，
    "没写 fault_injection" 在继承语义下等于"照抄母本的第 10 条"，而不是"没有故障"。
    """
    families = load_families()
    fam = family_of(families, family_id)
    if not task_id.startswith(fam["prefix"]):
        raise ValueError(f"task_id 应以 {fam['prefix']} 开头（家族 {family_id} 的 id 前缀）")
    if (TASKS_DIR / f"{task_id}.yaml").exists():
        raise ValueError(f"tasks/{task_id}.yaml 已存在")

    if fam["template"] == ASK_TEMPLATE:
        # 交接族没有故障位（只考"先问再动手"），`fault_at` 在这里没有意义，直接忽略。
        return _scaffold_ask(fam=fam, task_id=task_id, title=title,
                             rows=rows, page=page, version=version)
    if fam["template"] != DEFAULT_TEMPLATE:
        return _scaffold_locate(fam=fam, task_id=task_id, title=title,
                                version=version)

    if fault_at is None:
        fault_at = _default_fault_at(rows, page)
    # 上界是 rows 而不是 rows-1：故障落在**最后一条**会让"重试后还有数据要拉"这个考点消失，
    # 而 `tests/test_task_variants.py` 正是这样断言的（`0 < offset < len(rows)`）。
    if not 1 <= fault_at < rows:
        raise ValueError(f"fault_at={fault_at} 必须在 1..{rows - 1} 之间"
                         f"（最后一条做故障就没有「重试后接着拉」这一步了）")

    fixture, notes = _amounts(rows, invalid_every=invalid_every)
    rule = {
        "valid_when": [
            {"field": "amount", "rule": "not_null"},
            {"field": "amount", "rule": "gte", "value": 0},
        ],
        "outputs": {"valid_count": {"op": "count"},
                    "total_amount": {"op": "sum", "field": "amount"}},
        "distinct_by": "order_id",
        "rows": "test_inputs.orders_fixture",
    }
    # 现算：与判分侧完全同一条规则（dv.derive），不在这里另写一份求和
    got = dv.derive(fixture, dv.load_rule({"derivation": rule}))
    valid = int(got["valid_count"])
    total = round(float(got["total_amount"]), 2)
    pages = -(-rows // page)

    blocks = [
        _HEADER,
        f"base_task: {fam['mother']}",
        f"task_id: {task_id}",
        f'task_version: "{version}"',
        f"title: {title}",
        f"primary_skill_id: {fam['primary_skill']}",
        "secondary_skill_ids: ['multi_step_planning', 'tool_param_validation']",
        f"variant_template_id: {fam['template']}",
        "variant_meta:",
        f"  family_id: {family_id}",
        f"  difficulty: {_suggest_difficulty(pages)}          "
        f"# {rows} 行 / 每页 ≤{page} ⇒ {pages} 页；改页数记得同步这一行",
        "  kind: variant",
        "",
        "scenario: |",
        "  你是一个数据助理 Agent，请完成以下工作：",
        f"  1. 使用工具 fetch_orders 分页拉取全部记录（limit 取值必须在 1 到 {page} 之间，"
        f"超过 {page} 服务端会返回错误 invalid_limit；共 {rows} 条记录）",
        "  2. 剔除无效记录：amount 字段缺失、为 null 或为负数",
        "  3. 统计有效记录数量与总金额",
        "  4. 调用 write_report 将结果写入 /workspace/report.json，",
        '     内容为 JSON：{"valid_count": <有效记录数>, "total_amount": <总金额, 浮点>}',
        "",
    ]

    if fault_at is not None:
        if not 1 <= fault_at <= rows:
            raise ValueError(f"fault_at={fault_at} 超出 1..{rows}")
        code, err = 500, "internal_error"
        blocks += [
            "fault_injection:",
            "  match:",
            f"    offset_covers: {fault_at}",
            "  failures:",
            "    - attempt: 1",
            f"      http_status: {code}",
            f"      error: {err}",
            "",
        ]

    blocks += [
        "tools:",
        "  - name: fetch_orders",
        "    parameters:",
        "      - name: offset",
        "        type: integer",
        "        required: true",
        "        minimum: 0",
        "      - name: limit",
        "        type: integer",
        "        required: true",
        "        minimum: 1",
        f"        maximum: {page}",
        "    mock:",
        "      pagination:",
        f"        page_size_max: {page}",
        "",
        "test_inputs:",
        f"  # {rows} 条记录，{pages} 页；被剔除的行已逐行标注原因。",
        "  orders_fixture:",
        _render_fixture(fixture, notes),
        "  expected:",
        f"    valid_count: {valid}",
        f"    total_amount: {total}",
        "",
        "rubric_credit:",
        "  - id: R2_report_correct",
        f"    expected: {{valid_count: {valid}, total_amount: {total}}}",
    ]
    if fault_at is not None:
        blocks += [
            "  - id: R3_retry_evidence",
            "    expected:",
            "      calls:",
            f"        - {{name: fetch_orders, params_match: {{offset_covers: {fault_at}}}, "
            f"status: error, http_status: 500}}",
            f"        - {{name: fetch_orders, params_match: {{offset_covers: {fault_at}}}, "
            f"status: success}}",
        ]
    blocks.append("")
    why = (f"（待填）{rows} 行 / 每页 ≤{page} / {pages} 页"
           + (f"，故障在第 {fault_at} 条" if fault_at else "，不注入故障"))
    return "\n".join(blocks), why


def _suggest_difficulty(pages: int) -> int:
    if pages <= 1:
        return 1
    if pages <= 3:
        return 2
    return 3


def _default_fault_at(rows: int, page: int) -> int:
    """默认故障位：最后一页的第 2 条（只剩一条时就取最后一条）。

    为什么必须有默认值而不是"不写就没有"：`fault_injection` 是**继承**字段。
    变体文件里不写它，不等于"这道题没有故障"，而是"照抄母本的第 10 条"。
    这条语义差异在 2026-11-22 坑过一次——台账里按"没写=无故障"写了两道题的 why，
    实际它们都带着第 10 条 500，只是没显式写出来。所以生成器一律把位置写明。

    取"最后一页第 2 条"是因为它同时压住两件事：重试后还得再拉一页（不是拉完就结束），
    且该页通常还有剩下的行要剔除（不会因为故障在页尾而恰好无事可做）。
    """
    pages = max(1, -(-rows // page))
    tail_start = (pages - 1) * page + 1
    candidate = tail_start + 1
    # 上限 rows-1：最后一条做故障会让"重试后还要接着拉"消失（同 check 的 ⑩）。
    return max(1, min(candidate, rows - 1))


def _yaml_block(lines: Sequence[str], indent: str = "      ") -> str:
    """把若干行文字排成 YAML 的 `|` 块体：每行统一缩进 6 空格（键在 4 空格处）。

    踩过一次：手写模板时给块内容留了 2 空格缩进，少于键的缩进 ⇒ YAML 认为块已结束，
    整段日志被当成顶层标量，报 `could not find expected ':'`。生成器不能再犯。
    """
    return "\n".join((indent + ln) if ln.strip() else "" for ln in lines)


def _scaffold_locate(*, fam: dict, task_id: str, title: str, version: str) -> tuple[str, str]:
    """定位族：日志 + 源码 + 结论三项。数据留占位，自检会指出缺哪几样。"""
    log_path = "/workspace/logs/app.log"
    src_path = "/workspace/service/handler.py"
    out_path = "/workspace/diagnosis.json"
    # 模板默认给 type_mismatch 而不是 IndexError：参考 Agent 的定位器只认
    # 「不支持的运算符」与「缺失的属性名」两类特征（见 reference_agent._diagnose），
    # IndexError 的 `list index out of range` 里没有任何可定位特征，生成的题会解不出来。
    # 干扰行刻意也用比较符（`>`），让"只找第一个比较符"的实现落到错行。
    log_body = _yaml_block([
        "2026-11-22 09:00:01,120 INFO  request_id=r-1001 endpoint=/x status=200",
        "2026-11-22 09:00:02,540 ERROR request_id=r-1002 TypeError in load_amount: "
        "'>' not supported between instances of 'str' and 'int'",
        "2026-11-22 09:00:03,010 WARN  request_id=r-1002 retry budget 1/3 used",
        "2026-11-22 09:00:03,900 ERROR request_id=r-1003 TypeError in check_total: "
        "'<' not supported between instances of 'str' and 'int'",
        "2026-11-22 09:00:04,220 INFO  request_id=r-1003 status=500",
    ])
    src_body = _yaml_block([
        'LIMIT = 100',
        '',
        '',
        'def check_total(rows, limit):',
        '    if rows < limit:',      # ← 第 5 行：真因
        '        return "ok"',
        '    return "over"',
        '',
        '',
        'def first_item(items):',
        '    return items[1]',
    ])
    text = "\n".join([
        _HEADER,
        f"base_task: {fam['mother']}",
        f"task_id: {task_id}",
        f'task_version: "{version}"',
        f"title: {title}",
        f"primary_skill_id: {fam['primary_skill']}",
        "scenario: |",
        "  你是这个服务的值班工程师，请定位刚才那次 500 的根因：",
        f"  1. 调用 read_file 读取服务日志 {log_path}",
        f"  2. 调用 read_file 读取源码 {src_path}",
        "  3. 日志把每次异常压成一行：`时间 级别 request_id=.. <异常类> in <函数名>: <消息>`。",
        "     **最后一条 ERROR** 对应本次失败；更早的 ERROR 是别的已知问题，不是这次的根因",
        "  4. 根因取值必须是以下之一（按异常类判定，不要自创标签）：",
        "     - 异常类是 TypeError                      → type_mismatch",
        "     - 异常类是 IndexError                     → off_by_one",
        "     - 异常类是 AttributeError 且消息含 NoneType → null_deref",
        "     - 其余                                    → other",
        "  5. 出错行号按下面的规则从**源码**里定位（日志只给函数名，不含行号）：",
        "     - 消息形如 `'X' not supported ...` ⇒ 在报错的函数体内找第一处含运算符 X 的行",
        "     - 消息形如 `has no attribute 'Y'`  ⇒ 在报错的函数体内找第一处含 .Y 的行",
        f"  6. 调用 write_report 把结论写入 {out_path}，内容为 JSON：",
        '     {"root_cause": "<上面取值之一>", "offending_line": <1 基行号, 整数>}',
        "",
        "variant_meta:",
        f"  family_id: {fam['id']}",
        "  difficulty: 1          # 日志里 ERROR 条数决定：≥2 才能记 1，≥3 记 2，≥4 记 3",
        "",
        "fault_injection:",
        "  enabled: true",
        "  target_tool: read_file",
        "  retry_scope: agent",
        "  reset_policy: per_submission",
        "  match:",
        f"    path: {src_path}",
        "  failures:",
        "    - attempt: 1",
        "      http_status: 503",
        "      error: service_unavailable",
        "  then: success",
        "",
        "locate:",
        f"  log_path: {log_path}",
        f"  source_path: {src_path}",
        f"  diagnosis_path: {out_path}",
        "",
        "test_inputs:",
        "  files:",
        f"    {log_path}: |",
        log_body,
        f"    {src_path}: |",
        src_body,
        "  expected:",
        "    root_cause: type_mismatch",
        "    offending_line: 5",
        "",
        "rubric_credit:",
        "  - id: R2_diagnosis_correct",
        "    expected: {root_cause: type_mismatch, offending_line: 5}",
        "  - id: R3_retry_evidence",
        "    expected:",
        "      ordered: true",
        "      allow_interleaved: true",
        "      calls:",
        f"        - {{name: read_file, params_match: {{path: {src_path}}}, status: error, "
        "http_status: 503}",
        f"        - {{name: read_file, params_match: {{path: {src_path}}}, status: success}}",
        "      note: '故障在读源码这一侧；params_match 只锁 path'",
        "",
    ])
    return text, "（待填）定位题：说清这题的根因类别与干扰线索与同族其它题有何不同"


def _ask_amounts(rows: int, *, page: int) -> tuple[list[dict], list[str]]:
    """造交接题数据：**每页末一条**是无效行，四种原因轮换（缺 / null / 负数 / cancelled）。

    为什么按页放而不是按「每 N 条」放：交接骨架有一条自检「每页都要有无效行」——
    最后一页若全有效，"每页都按规则看一遍"这个考点就漏掉了。按页放是唯一能保证它的做法。
    （汇报族按 `invalid_every` 放，是因为它的难度靠"无效行多不多"撑；两族的考点不同。）

    第 1 页末放 `status=cancelled`——那是**唯一只在人嘴里**的规则。不问题的行者会把
    本该剔除的行算进去，R2 因此变错；这正是 no_ask 形态能区分开的前提。
    所以 cancelled 行必须带**正常金额**：金额也缺失的话，算错也算不出差。
    """
    if rows < 1 or page < 1:
        raise ValueError(f"交接题需要 rows>=1 且 page>=1（收到 rows={rows}, page={page}）")
    pages = -(-rows // page)
    kinds = ("cancelled", "null", "negative", "missing")
    mark = {min((p + 1) * page, rows): kinds[p % len(kinds)] for p in range(pages)}

    fixture: list[dict] = []
    notes: list[str] = []
    seq = 0
    for i in range(1, rows + 1):
        oid = f"hv-{i:03d}"
        kind = mark.get(i)
        if kind is None or kind == "cancelled":
            seq += 1
            fixture.append({
                "order_id": oid,
                "amount": round(100 + 12.5 * seq, 2),
                "status": "cancelled" if kind else "paid",
            })
            notes.append(f"# status=cancelled → 无效（口径只在人嘴里，第 {i} 条）" if kind else "")
        elif kind == "null":
            fixture.append({"order_id": oid, "amount": None, "status": "paid"})
            notes.append(f"# 金额为 null（第 {i} 条）")
        elif kind == "negative":
            fixture.append({"order_id": oid, "amount": -round(100 + 12.5 * i, 2),
                            "status": "paid"})
            notes.append(f"# 负数（第 {i} 条）")
        else:                                  # missing
            fixture.append({"order_id": oid, "status": "paid"})
            notes.append(f"# 缺 amount 字段（第 {i} 条）")
    return fixture, notes


def _scaffold_ask(*, fam: dict, task_id: str, title: str, rows: int, page: int,
                  version: str) -> tuple[str, str]:
    """交接族：只写差异（题面 / 两个工具的口径 / 数据 / 期望值），骨架全部继承母本。

    生成器必须认识这套骨架，因为有两处**没有通用模板能替人记住**：
      ① `fetch_orders.limit` 刻意不写 `maximum`——上限是要问出来的口径，写进 schema 等于白送；
      ② `ask_policy` 的固定答复里必须复述**同一个**页大小。
    写错任一处就是「问了也做不对」，而失败会被记成学习者"能力不足"。
    """
    fixture, notes = _ask_amounts(rows, page=page)
    rule = {
        "valid_when": [
            {"field": "amount", "rule": "not_null"},
            {"field": "amount", "rule": "gte", "value": 0},
            {"field": "status", "rule": "ne", "value": "cancelled"},
        ],
        "outputs": {"valid_count": {"op": "count"},
                    "total_amount": {"op": "sum", "field": "amount"}},
        "rows": "test_inputs.orders_fixture",
    }
    got = dv.derive(fixture, dv.load_rule({"derivation": rule}))
    valid, total = int(got["valid_count"]), round(float(got["total_amount"]), 2)
    pages = -(-rows // page)
    invalid = sum(1 for n in notes if n)

    text = "\n".join([
        _HEADER,
        f"base_task: {fam['mother']}",
        f"task_id: {task_id}",
        f'task_version: "{version}"',
        f"title: {title}",
        f"primary_skill_id: {fam['primary_skill']}",
        "secondary_skill_ids: ['tool_param_validation', 'multi_step_planning']",
        f"variant_template_id: {ASK_TEMPLATE}",
        "variant_meta:",
        f"  family_id: {fam['id']}",
        f"  difficulty: {_suggest_difficulty(pages)}          "
        f"# {rows} 行 / 每页 ≤{page} ⇒ {pages} 页；改页数记得同步这一行",
        "  kind: variant",
        "",
        "scenario: |",
        "  你是刚接手这块业务的运营助理。这份汇总今天要交，但上周交接只当面说过口径，",
        "  任何文档里都查不到。",
        "",
        f"  1. 用 fetch_orders 分页拉取全部记录（共 {rows} 条），一条不剩地拉完。",
        "  2. 汇总两个数：有效记录条数、有效记录金额合计。",
        "  3. 三条口径——一页最多能拉几条、哪些记录算无效、合计怎么取整——只有值班同事知道。",
        "  4. 调用 write_report 写入 /workspace/report.json，内容为 JSON：",
        '     {"valid_count": <有效记录数, 整数>, "total_amount": <有效记录金额合计, 数字>}',
        "",
        "# 只覆盖 mock.text：ask_policy 的 description/parameters 与 fetch_orders 的分页语义",
        "# （offset_base / range / error_code …）全部继承母本，改一处不会漂另一处。",
        "tools:",
        "  - name: ask_policy",
        "    mock:",
        "      text: |",
        "        上周我们定的是这三条：",
        f"        ① 接口一页最多 {page} 条，多了会报 invalid_limit。",
        "        ② 无效记录：amount 缺失、amount 为 null、amount 为负数，或者 status 是 cancelled。",
        "        ③ 合计四舍五入保留两位小数。",
        "  - name: fetch_orders",
        "    mock:",
        "      pagination:",
        f"        page_size_max: {page}",
        "",
        "test_inputs:",
        f"  # {rows} 条记录，{pages} 页；每页末一条被剔除，原因逐行标注（共 {invalid} 条无效）。",
        "  orders_fixture:",
        _render_fixture(fixture, notes),
        "  expected:",
        f"    valid_count: {valid}",
        f"    total_amount: {total}",
        "",
        "rubric_credit:",
        "  - id: R2_report_correct",
        f"    expected: {{valid_count: {valid}, total_amount: {total}}}",
        "",
    ])
    return text, (f"（待填）交接题：{rows} 行 / 每页 ≤{page} / {pages} 页，"
                  "说清它与同族其它题的口径或数据有何不同")


# ── 台账写入 ────────────────────────────────────────────────────────────────

def register_in_log(task_id: str, why: str) -> None:
    """把一行 why 插到该家族段的末尾（保持"按家族分段"这条约束）。"""
    families = load_families()
    fam = next((f for f in families if task_id.startswith(f["prefix"])), None)
    if fam is None:
        raise KeyError(f"{task_id} 的前缀不属于任何已登记家族，无法归段")
    text = LOG_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()

    # 找到该家族最后一条 `- id: <prefix>...` 的行号；找不到就退回到文件末尾
    idx = None
    for i, line in enumerate(lines):
        if line.startswith(f"  - id: {fam['prefix']}"):
            idx = i
    entry = f"  - id: {task_id}\n    why: {why}"
    if idx is None:
        # 该家族还没有题：找该家族的分段注释行，没有就追加到末尾
        marker = next((i for i, ln in enumerate(lines)
                       if ln.startswith("# ──") and fam["id"] in ln), None)
        if marker is None:
            new = "\n".join(["", entry, ""])
            LOG_PATH.write_text(text.rstrip("\n") + "\n" + new, encoding="utf-8")
            return
        lines.insert(marker + 1, entry)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    # 跳过多行 why 的续行（以更深的缩进开头）
    j = idx
    while j + 1 < len(lines) and (lines[j + 1].startswith("    ") or lines[j + 1].strip() == ""):
        if lines[j + 1].strip() == "":
            break
        j += 1
    lines.insert(j + 1, entry)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _catalog_report() -> str:
    families = load_families()
    out: list[str] = []
    total = 0
    for fam in families:
        ids = siblings(fam["id"], families)
        total += len(ids)
        out.append(f"{fam['id']}  ({fam['primary_skill']}, {fam['template']}, 母本 {fam['mother']})")
        out.append(f"  {fam['about']}")
        buckets: dict[int, int] = {}
        for stem in ids:
            task = load_task(TASKS_DIR / f"{stem}.yaml")
            diff = (task.get("variant_meta") or {}).get("difficulty")
            buckets[diff] = buckets.get(diff, 0) + 1
            out.append(f"  d{diff}  {stem:28s} {_scale(task)}")
        gaps = [d for d in (1, 2, 3) if not buckets.get(d)]
        grad = "  ".join(f"d{d}×{buckets.get(d, 0)}" for d in (1, 2, 3))
        out.append(f"  梯度：{grad}" + (f"   ← 缺 d{gaps} 档" if gaps else ""))
        out.append("")
    out.append(f"合计 {total} 道（台账 {len(load_log())} 行）")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="new_task.py",
        description="加一道题：生成骨架 / 单题自检 / 看难度梯度")
    parser.add_argument("--list", action="store_true", help="列家族 × 难度梯度，看缺哪一档")
    parser.add_argument("--check", nargs="+", metavar="YAML",
                        help="单题自检（实测 0.6 秒，不用跑全量）")
    parser.add_argument("--new", action="store_true", help="生成一道新题的骨架并登记台账")
    parser.add_argument("--family", help="家族 id（--new 用）")
    parser.add_argument("--id", dest="task_id", help="新题 task_id（--new 用）")
    parser.add_argument("--title", help="新题标题（--new 用）")
    parser.add_argument("--rows", type=int, default=24, help="数据条数（默认 24）")
    parser.add_argument("--page", type=int, default=10, help="limit 上限（默认继承 10）")
    parser.add_argument("--fault-at", type=int, default=None,
                        help="故障位（第几条）；不给则自动取「最后一页第 2 条」")
    parser.add_argument("--invalid-every", type=int, default=5,
                        help="每几条插一条无效行（默认 5）")
    parser.add_argument("--why", default=None,
                        help="台账里的 why；不给则写占位，自检会提醒你补")
    parser.add_argument("--write", action="store_true", help="真的写文件（默认只预览）")
    args = parser.parse_args(argv)

    if args.list:
        print(_catalog_report())
        return 0

    if args.check:
        families, log = load_families(), load_log()
        xs = ws = 0
        for name in args.check:
            path = Path(name)
            if not path.exists() and (TASKS_DIR / f"{name}.yaml").exists():
                path = TASKS_DIR / f"{name}.yaml"
            print(f"\n{path.name}")
            findings = check_task(path, families=families, log=log)
            for f in findings:
                print(f.render())
            x, w = summarize(findings)
            xs, ws = xs + x, ws + w
            print(f"  → {'通过' if x == 0 else f'{x} 项要修'}{f'（{w} 条建议）' if w else ''}")
        if xs:
            print(f"\n合计 {xs} 项错误{('、' + str(ws) + ' 条建议') if ws else ''}。"
                  f"改完再跑一次；每项都对应 tests/ 里的一条用例。")
            return 1
        print(f"\n自检通过{'（有 ' + str(ws) + ' 条建议，不影响测试）' if ws else ''}。"
              f"接着跑：PYTHONPATH=src pytest tests/test_task_variants.py -q -k "
              f"'full_marks or own_failure'")
        return 0

    if not args.new:
        parser.print_help()
        return 2

    missing = [k for k in ("family", "task_id", "title") if not getattr(args, k)]
    if missing:
        print(f"--new 缺少参数：{', '.join('--' + m.replace('_', '-') for m in missing)}")
        return 2
    try:
        text, why_placeholder = scaffold(
            family_id=args.family, task_id=args.task_id, title=args.title,
            rows=args.rows, page=args.page, fault_at=args.fault_at,
            invalid_every=args.invalid_every)
    except (KeyError, ValueError) as exc:
        print(f"生成失败：{exc}")
        return 2

    target = TASKS_DIR / f"{args.task_id}.yaml"
    print(f"→ {target.relative_to(ROOT)}")
    print(text if args.write else f"（预览，加 --write 才落盘。前 12 行如下）\n"
          + "\n".join(text.splitlines()[:12]))
    print(f"\n→ 台账将追加：- id: {args.task_id}\n               why: {args.why or why_placeholder}")
    if not args.write:
        return 0

    target.write_text(text, encoding="utf-8")
    register_in_log(args.task_id, args.why or why_placeholder)
    print(f"\n已写入。下一步：")
    print(f"  1. 改 tasks/{args.task_id}.yaml 的 title 与 scenario 措辞（教学设计，脚本不替你想）")
    print(f"  2. 补 docs/task-authoring-log.yaml 里这道题的 why（说清这题变的是什么）")
    print(f"  3. python scripts/new_task.py --check {args.task_id}")
    print(f"  4. PYTHONPATH=src pytest tests/test_task_variants.py -q "
          f"-k 'full_marks or own_failure'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
