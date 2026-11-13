"""决策演示台（round-72）：11 条已定决策，问哪条就当场跑哪条。

用法
----
    python scripts/demo_decision.py            # 总表：11 条各自属于哪一档
    python scripts/demo_decision.py 4          # 只演示决策 4
    python scripts/demo_decision.py 4 9 10 11  # 多条
    python scripts/demo_decision.py --all      # 全跑（退出码 0 = 无 FAIL）

为什么要有这个脚本
------------------
评审包第 3 节有 11 条「已定决策」。读者（评委）能做的只有一件事：信，或者不信。
"这条已经实现了"是一句自评，不是证据。所以这里把每条决策翻成一个**现场可执行的
动作**，输出的是命令跑出来的东西，不是我的复述。

三条自我约束
------------
1. **不复述决策正文**。标题一律从 `docs/review-packet.md` 第 3 节现读——决策改了，
   演示台自动跟随，不会变成第二个真相源。
2. **期望值从声明里读，不写死在脚本里**。例如"分母恒为 4"里的 4 取自任务 YAML 的
   `scoring.credit.total_weight`；YAML 改了而实现没跟上，对应演示必须变红。
3. **做不到就说不做到**。见下面四档；`PARTIAL` / `NOT_EXECUTABLE` 不算通过，
   单独计数，不许混进"全过"。

四档判定
--------
    PASS              该决策中所有"可在本机判定"的主张，实测全部成立
    PARTIAL           只有一部分主张可在本机判定，且这部分成立；其余依赖外部材料或未实现模块
    NOT_EXECUTABLE    当前仓库没有可执行载体（是阶段未到，不是失败）
    FAIL              可判定部分有任一不成立

只读：不改 fixture、不改任务 YAML、不写任何文件；全部 deepcopy 后现算。
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import reconciliation as rc  # noqa: E402
from arena import store  # noqa: E402
from arena.assertion_evaluator import SUPPORTED_TYPES  # noqa: E402

PACKET = ROOT / "docs" / "review-packet.md"
FIXTURES = ROOT / "fixtures"
TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"

PASS, FAIL, PARTIAL, NOEXEC = "PASS", "FAIL", "PARTIAL", "NOT_EXECUTABLE"
NAMES = ("raw-empty", "raw-no-file", "raw-fake-retry", "raw-no-trace", "raw-perfect")

TASK = dr.load_task(TASK_PATH)
VOCAB = dr.load_vocabulary()
SCHEMA = dr.load_schema()

# 算法题域的特征词：用于决策 1 的可判定部分（"当前任务库不含算法域"）
ALGO_HINTS = ("算法", "竞赛", "ACM", "ICPC", "LeetCode", "algorithm", "leetcode")


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------


def say(text: str = "") -> None:
    print(text)


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def build(raw_obj: dict, task: dict | None = None) -> dict:
    """走真实的 build_report 路径；deepcopy 避免演示之间互相污染。"""
    return dr.build_report(copy.deepcopy(task if task is not None else TASK),
                           copy.deepcopy(raw_obj), VOCAB)


def _strip_note(text: str) -> str:
    """压成一行，并去掉 markdown 标记（终端里 `**` 与反引号只是噪音）。

    只做显示层清理，不改原文档；编号后的正文语义与评审包完全一致。
    """
    joined = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return joined.replace("**", "").replace("`", "")


def decision_text(n: int) -> str:
    """从评审包第 3 节现读第 n 条决策，避免脚本内复制正文。"""
    text = PACKET.read_text(encoding="utf-8")
    try:
        block = text[text.index("## 3. 已定决策"):text.index("## 4. 仓库实况")]
    except ValueError:
        return "(评审包结构已变，找不到第 3 节)"
    m = re.search(r"^%d\. (.+?)(?=^\d+\. |\Z)" % n, block, re.M | re.S)
    return _strip_note(m.group(1)) if m else "(该编号不存在)"


def _all_assertions(report: dict) -> list[dict]:
    return report["assertions"]


def _credit_total() -> float:
    """分母从任务声明读，不写死；读不到就炸（不许默认 0 让判据静默变红）。"""
    credit = (((TASK.get("scoring") or {}).get("score") or {}).get("credit") or {})
    if "total_weight" not in credit:
        raise RuntimeError(
            "任务 YAML 里读不到 scoring.score.credit.total_weight —— "
            "分母无法从声明取得，本演示的判据失去锚点（先修 YAML 路径或本函数）。"
        )
    return float(credit["total_weight"])


def _verdict(ok_list: list[tuple[str, bool, str]]) -> tuple[bool, list[str]]:
    """把 (判据名, 是否成立, 实测值) 三元的列表压成 (是否全成立, 明细行)。"""
    lines = []
    for name, ok, detail in ok_list:
        lines.append(f"  [{'ok' if ok else 'XX'}] {name}：{detail}")
    return all(ok for _, ok, _ in ok_list), lines


# ---------------------------------------------------------------------------
# 决策 1：首发域 = Agent 开发学习
# ---------------------------------------------------------------------------


def demo_1() -> tuple[str, list[str]]:
    lines = ["可判定部分：当前任务库里有没有算法题域的任务。"]
    paths = sorted((ROOT / "tasks").glob("*.yaml"))
    checks = []
    lines.append(f"  任务库：{len(paths)} 个 YAML")
    for p in paths:
        dom = str(dr.load_task(p).get("domain", ""))
        hit = any(h.lower() in dom.lower() for h in ALGO_HINTS)
        lines.append(f"    - {p.name}: domain = {dom}{'   <== 命中算法域词' if hit else ''}")
        checks.append((f"{p.name} 非算法题域", not hit, dom or "(缺失 domain)"))
    ok_l, detail = _verdict(checks)
    lines += detail
    if not paths or not ok_l:
        return FAIL, lines + ["反证条件：任务库为空，或出现算法题域任务 ⇒ 本决策当前未被执行。"]
    lines.append("  可判定部分成立：现有任务全部落在 Agent 开发域。")
    lines.append("  不可判定部分：「算法题域推迟到决赛」是路线承诺，没有代码可验。")
    lines.append("  反证条件：tasks/ 下新增 domain 含算法/竞赛字样的 YAML ⇒ 本决策被推翻。")
    return PARTIAL, lines


# ---------------------------------------------------------------------------
# 决策 2：自研断言层（A2E 已实测证伪）
# ---------------------------------------------------------------------------


def demo_2() -> tuple[str, list[str]]:
    lines = ["可判定部分甲：自研断言层是否真的按「任务级布尔断言」判定，而不是聚合分。"]
    checks = []
    lines.append(f"  assertion_evaluator.SUPPORTED_TYPES = {list(SUPPORTED_TYPES)}")
    checks.append(("断言类型数 >= 4", len(SUPPORTED_TYPES) >= 4, str(len(SUPPORTED_TYPES))))

    states = set()
    for name in NAMES:
        for a in _all_assertions(build(raw(name))):
            states.add(a["state"])
    lines.append(f"  五份夹具实测出现的断言状态：{sorted(states)}")
    checks.append(("状态只落在四态内（无分数型输出）",
                   states <= {"pass", "fail", "blocked", "unscored"}, str(sorted(states))))

    lines.append("可判定部分乙：证伪材料的落盘证据是否在仓内。")
    spike = ROOT / "spike"
    need = ("README.md", "a2e_probe.py", "a2e_probe_result.json", "test_a2e_semantics.py")
    missing = [f for f in need if not (spike / f).exists()]
    lines.append(f"  spike/ 证据文件：{'齐备' if not missing else '缺 ' + str(missing)}")
    checks.append(("证伪材料齐备", not missing, str(len(need) - len(missing)) + "/4"))

    probe_path = spike / "a2e_probe_result.json"
    readback = None
    if probe_path.exists():
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        readback = probe.get("span_count_readback")
        lines.append(f"  a2e_probe_result.json: span_count_readback = {readback}，"
                     f"stored_attribute_keys = {probe.get('stored_attribute_keys')}")
        checks.append(("A2E 侧回读为空（无法据其取属性自研断言）", readback == 0, str(readback)))
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：上述任一判据不成立 ⇒ 决策 2 的依据在当前仓库站不住。"]
    lines.append("  「26 个指标全是聚合分或 LLM judge」这一句的依据是 spike/README.md 的人工整理表，")
    lines.append("  现场无法复跑 A2E（需 1–3GB Docker 镜像）⇒ 该子主张只能读材料，故整体记 PARTIAL。")
    lines.append("  反证条件：若 SUPPORTED_TYPES 里出现聚合分类型，或 spike 证据文件缺失 ⇒ FAIL。")
    return PARTIAL, lines


# ---------------------------------------------------------------------------
# 决策 3：复习项 = 可执行任务，不是抽认卡
# ---------------------------------------------------------------------------


def demo_3() -> tuple[str, list[str]]:
    lines = []
    mods = sorted(p.name for p in (ROOT / "src" / "arena").glob("*.py"))
    lines.append(f"  src/arena 现有模块：{mods}")
    keys = sorted(build(raw("raw-perfect")).keys())
    lines.append(f"  诊断报告顶层字段：{keys}")
    leak = [k for k in keys if re.search(r"review|mastery|schedul|card", k, re.I)]
    lines.append(f"  含复习/掌握度/排程语义的字段：{leak or '无'}")
    if leak:
        return FAIL, lines + [
            "反证条件已触发：诊断报告里出现复习/掌握度字段 ⇒ 决策 3 的边界（诊断层不建这些模型）被破坏。"]
    # 判据升级（round-75）。旧判据是「源码里出现 fsrs/spaced 字样 ⇒ 复习层已出现 ⇒ 本演示过期」。
    # 仓内建了 review_items 表之后，`fsrs_rating` 这种**待填的列名**就把它顶红了——
    # 列名不是调度逻辑，旧判据是用代理指标冒充实质。
    # 新判据直接判决策 3 的实质：**复习项是"可执行任务"还是"正反面文本卡片"**。
    # 它仍可被证伪：把 kind 换成 flashcard、或删掉 task_id 引用，判定立刻变 FAIL（变异组见 §75）。
    ddl = store.SCHEMA_SQL
    if "CREATE TABLE IF NOT EXISTS review_items" not in ddl:
        lines.append("  review_items 表：不存在")
        lines.append("  结论：复习层尚未实现，「复习项 = 可执行任务」在当前仓库**没有载体可演示**。")
        lines.append("  这不是失败，是阶段：设计只写在计划书 §4 与 docs/data-model.md，本脚本不代替它。")
        return NOEXEC, lines

    block = re.search(r"CREATE TABLE IF NOT EXISTS review_items \((.*?)\n\);", ddl, re.S)
    body = block.group(1) if block else ""
    kinds = set(re.findall(r"'([a-z_]+)'", body))
    want = {"variant", "fault_injection", "timed", "replay"}
    card_form = re.search(r"flashcard|card_front|card_back|front\s+TEXT|back\s+TEXT", body, re.I)
    refs_task = "REFERENCES tasks(task_id)" in body
    refs_sub = "REFERENCES submissions(submission_id)" in body
    lines.append(f"  review_items.kind 取值：{sorted(kinds)}")
    lines.append(f"  引用任务 / 历史提交：{refs_task} / {refs_sub}")
    lines.append(f"  卡片正反面文本类字段：{'有' if card_form else '无'}")

    ok_l, detail = _verdict([
        ("kind 覆盖四种可执行形态", want <= kinds, str(sorted(kinds))),
        ("没有卡片正反面文本字段", not card_form, "有" if card_form else "无"),
        ("复习项引用任务与历史提交", refs_task and refs_sub, f"task={refs_task}, sub={refs_sub}"),
    ])
    lines += detail
    if not ok_l:
        return FAIL, lines + [
            "反证条件已触发：复习项形态偏离「可执行任务」⇒ 决策 3 未被执行（或表结构写歪了）。"]

    used = []
    for p in sorted((ROOT / "src" / "arena").glob("*.py")):
        if re.search(r"INSERT INTO review_items|SELECT[^;]*FROM review_items",
                     p.read_text(encoding="utf-8"), re.I):
            used.append(p.name)
    lines.append(f"  实际读写 review_items 的模块：{used or '无'}")
    lines.append("  结论：**表结构**与决策 3 的形态一致（四种形态 + 引用任务/历史提交，无卡片正反面），")
    lines.append("        但没有任何代码往里写或读——复习项生成（M4）与调度都没实现，所以只算 PARTIAL。")
    lines.append("  反证条件：把 kind 改成 flashcard、或删掉 task_id 引用 ⇒ 立刻 FAIL（已用变异锁过）。")
    return PARTIAL, lines


# ---------------------------------------------------------------------------
# 决策 4：两段式 gate / credit，分母恒定
# ---------------------------------------------------------------------------


def demo_4() -> tuple[str, list[str]]:
    gate, credit = dr.rubric_layers(copy.deepcopy(TASK))
    total = _credit_total()
    lines = [f"  gate 条目（id, weight）：{[(r['id'], r.get('weight', 0)) for r in gate]}",
             f"  credit 条目（id, weight）：{[(r['id'], r.get('weight', 0)) for r in credit]}",
             f"  任务声明的 credit 分母 total_weight = {total:g}"]

    checks = []
    gate_weights = [float(r.get("weight", 0)) for r in gate]
    checks.append(("gate 不带权重", all(w == 0 for w in gate_weights), str(gate_weights)))
    credit_sum = sum(float(r.get("weight", 0)) for r in credit)
    checks.append(("credit 实际权重和 == 声明分母", credit_sum == total, f"{credit_sum:g} vs {total:g}"))
    checks.append(("gate 全部 on_fail=disqualify",
                   all(r.get("on_fail") == "disqualify" for r in gate),
                   str([r.get("on_fail") for r in gate])))

    # 分母在五份夹具里必须恒定，且 disqualified 必须等价于「gate 里有 fail」
    for name in NAMES:
        rep = build(raw(name))
        sc = rep["score"]
        gate_fail = [a["id"] for a in rep["assertions"] if a["layer"] == "gate" and a["state"] == "fail"]
        checks.append((f"{name} 分母恒为 {total:g}", float(sc["total_weight"]) == total, str(sc["total_weight"])))
        checks.append((f"{name} disqualified == gate 有 fail",
                       bool(sc["disqualified"]) == bool(gate_fail),
                       f"{sc['disqualified']} / gate_fail={gate_fail}"))
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：任一判据不成立 ⇒ 两段式不成立。"]
    lines.append("  反证条件：给 gate 任一条目加权重、或某夹具 total_weight 偏离声明值 ⇒ FAIL。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 5：门禁只管分数，不管观测
# ---------------------------------------------------------------------------


def demo_5() -> tuple[str, list[str]]:
    rep = build(raw("raw-no-file"))
    sc = rep["score"]
    credit_states = [(a["id"], a["state"]) for a in rep["assertions"] if a["layer"] == "credit"]
    lines = [f"  raw-no-file（过程全对但没写产物）：disqualified={sc['disqualified']}，ratio={sc['ratio']}",
             f"  credit 层逐条状态：{credit_states}",
             f"  counterfactual_credit = {sc['counterfactual_credit']}（分母 {sc['total_weight']:g}）"]
    checks = [
        ("门禁确实拦下（disqualified=True）", sc["disqualified"] is True, str(sc["disqualified"])),
        ("分数为 0", sc["ratio"] == 0, str(sc["ratio"])),
        ("反事实得分不为 0（观测没被吞）", sc["counterfactual_credit"] > 0, str(sc["counterfactual_credit"])),
        ("credit 层仍有 pass（判定照跑）",
         any(s == "pass" for _, s in credit_states), str(credit_states)),
        ("反事实得分 == credit 层 pass 权重和",
         sc["counterfactual_credit"] == sum(a["weight"] for a in rep["assertions"]
                                            if a["layer"] == "credit" and a["state"] == "pass"),
         str(sc["counterfactual_credit"])),
        ("notes 里点明「无资格评分」", any("无资格评分" in n for n in rep.get("notes", [])),
         str(rep.get("notes"))),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：反事实得分为 0，或 credit 断言被改成全 blocked ⇒ 观测被吞。"]
    lines.append("  反证条件：把门禁失败写成「credit 全 blocked」的实现在这里必然红 —— 那正是本决策要防的。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 6：不新增第 4 技能，「没交产物」走 gate 通道
# ---------------------------------------------------------------------------


def demo_6() -> tuple[str, list[str]]:
    tid = TASK.get("primary_skill_id")
    sids = set(TASK.get("secondary_skill_ids") or [])
    if tid:
        sids.add(tid)
    lines = [f"  任务声明的技能集合（{len(sids)} 个）：{sorted(sids)}"]

    gate, credit = dr.rubric_layers(copy.deepcopy(TASK))
    gate_skills = [(r["id"], r.get("skill_id")) for r in gate]
    lines.append(f"  gate 条目的 skill_id：{gate_skills}")

    rep = build(raw("raw-no-file"))
    gaps = [g["skill_id"] for g in rep["skill_gaps"]]
    unattributed = [p.get("pattern") or p for p in rep["unattributed_patterns"]]
    lines.append(f"  raw-no-file 的 skill_gaps：{gaps}")
    lines.append(f"  raw-no-file 的 unattributed_patterns：{unattributed}")

    checks = [
        ("技能数为 3（没冒第 4 个）", len(sids) == 3, str(len(sids))),
        ("gate 条目不带 skill_id", all(s is None for _, s in gate_skills), str(gate_skills)),
        ("无产物错误没进 skill_gaps", "no_output_file" not in gaps, str(gaps)),
        ("无产物错误进了 unattributed_patterns", "no_output_file" in str(unattributed), str(unattributed)),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：技能数变 4，或 no_output_file 出现在 skill_gaps ⇒ 决策 6 未被执行。"]
    lines.append("  反证条件：新增第 4 个技能承载「没交产物」⇒ FAIL。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 7：数据缺失 != 能力不足
# ---------------------------------------------------------------------------


def demo_7() -> tuple[str, list[str]]:
    lines = ["扫描全部夹具 × 全部断言：凡 blocked / unscored，都不许计入掌握度、不许产出错误模式。"]
    checks = []
    seen = {}
    for name in NAMES:
        rep = build(raw(name))
        policy = rep["attribution_policy"]
        for a in _all_assertions(rep):
            st = a["state"]
            if st not in ("blocked", "unscored"):
                continue
            seen.setdefault(st, []).append(f"{name}/{a['id']}")
            p = policy[st]
            checks.append((f"{name}/{a['id']} 为 {st} 时不计掌握度",
                           p["counts_toward_mastery"] is False, str(p["counts_toward_mastery"])))
            checks.append((f"{name}/{a['id']} 为 {st} 时不产错误模式",
                           p["emit_error_patterns"] is False, str(p["emit_error_patterns"])))
            checks.append((f"{name}/{a['id']} 的 error_patterns 为空",
                           not a.get("error_patterns"), str(a.get("error_patterns"))))
    for st, items in sorted(seen.items()):
        lines.append(f"  实测 {st} 共 {len(items)} 条：{items}")
    if not seen:
        return FAIL, lines + ["反证条件已触发：一份 blocked/unscored 都没扫到，说明演示没覆盖到这条决策。"]

    # report_attribution 是派生值，不是第二真相源：用同一函数复算必须一致
    derived_ok = []
    for name in NAMES:
        rep = build(raw(name))
        expect = dr._report_attribution(rep["attribution_policy"], rep["score"]["by_state"],
                                        rep["score"]["voided"] or rep["process_fact"]["missing"])
        derived_ok.append(rep["report_attribution"] == expect)
    checks.append(("report_attribution 可由 policy+by_state 复算（非第二真相源）",
                   all(derived_ok), str(derived_ok)))

    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：任一 blocked/unscored 状态带上能力归因 ⇒ 决策 7 被破坏。"]
    lines.append(f"  判据条数：{len(checks)}（覆盖 {len(seen)} 种状态、"
                 f"{sum(len(v) for v in seen.values())} 条断言）")
    lines.append("  反证条件：把 attribution_policy 里 blocked 或 unscored 的任一项改成 true ⇒ 本演示必红。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 8：掌握度与调度分开，不把 stability 当衰减系数
# ---------------------------------------------------------------------------


def demo_8() -> tuple[str, list[str]]:
    lines = ["可判定部分：诊断层有没有越界建掌握度/调度模型，以及代码里有没有出现「α = f(S)」。"]
    keys = sorted(build(raw("raw-perfect")).keys())
    leak = [k for k in keys if re.search(r"mastery|decay|stability|fsrs|schedul", k, re.I)]
    lines.append(f"  报告顶层字段：{keys}")
    lines.append(f"  含掌握度/衰减/调度语义的字段：{leak or '无'}")

    # 判据升级（round-75）。旧判据是「源码里出现 stability/fsrs 字样」即判不成立。
    # 建表后 `fsrs_rating INTEGER`、`fsrs_card TEXT` 这类**存卡片状态的列名**会被算成
    # "把 S 当衰减系数"，那是误报——列名不是算式。新判据只认真的算了：
    #   ① 赋给 alpha / decay / lambda_ 之类的衰减系数；② 直接参与乘除。
    # 它比旧判据更严（旧判据抓不到 `alpha = f(card.stability)` 之外的形态，也会误伤列名），
    # 且仍可证伪：写入 `alpha = card.stability * 0.1` 立刻 FAIL（变异组见 §75）。
    wide_hits, expr_hits = [], []
    for p in sorted((ROOT / "src" / "arena").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"stability|fsrs", line, re.I) and "schedul" not in line.lower():
                wide_hits.append(f"{p.name}:{i}: {line.strip()[:70]}")
            code = line.split("#", 1)[0]
            if re.search(r"(alpha|decay|lambda_)\s*=\s*[^#]*(stability|\.s\b)", code, re.I) \
                    or re.search(r"(stability|\.s\b)\s*[*\/]\s*[\d(]", code, re.I):
                expr_hits.append(f"{p.name}:{i}: {line.strip()[:70]}")
    lines.append(f"  src/ 中出现 stability/fsrs 字样的行（宽口径，仅展示）：{len(wide_hits)} 行")
    lines.append(f"  其中构成「把 S 当衰减系数」算式的行：{expr_hits or '无'}")

    doc = (ROOT / "docs" / "data-model.md").read_text(encoding="utf-8")
    old_claim = "掌握度 = 本表 R 值" in doc
    lines.append(f"  data-model.md 仍写「掌握度 = 本表 R 值」：{old_claim}")

    checks = [
        ("诊断报告不含掌握度/调度字段", not leak, str(leak)),
        ("代码里没有把 stability 当衰减系数的算式", not expr_hits, str(len(expr_hits))),
        ("旧口径「掌握度 = R 值」已从 data-model 撤下", not old_claim, str(old_claim)),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：上表任一为真 ⇒ 决策 8 未被执行。"]

    # 任务 YAML 里也不该出现掌握度参数
    lines.append("  不可判定部分：掌握度公式本身（EWMA + 复习间隔衰减）尚未实现，无载体可跑。")
    lines.append("  可判定部分只证明了两件事：诊断层不越界；代码里不存在那个不成立的等号。")
    lines.append("  反证条件：若日后实现里出现 `alpha = f(S)` 且无值域约束 ⇒ 决策 8 的反证条件被触发。")
    return PARTIAL, lines


# ---------------------------------------------------------------------------
# 决策 9：缺过程按题型分流（双向演示）
# ---------------------------------------------------------------------------


def demo_9() -> tuple[str, list[str]]:
    r = raw("raw-no-trace")
    a = build(r)  # 任务 YAML: requires_process = true
    t2 = copy.deepcopy(TASK)
    t2.setdefault("submission_requirements", {})["requires_process"] = False
    b = build(r, task=t2)  # 同一份提交，只把开关关掉

    lines = ["双向：同一份「答案对但没报过程」的提交，只改题目开关，看分数怎么变。",
             f"  A（requires_process=True）：ratio={a['score']['ratio']}，"
             f"process_fact={a['process_fact']}，counterfactual={a['score']['counterfactual_credit']}",
             f"  B（requires_process=False）：ratio={b['score']['ratio']}，"
             f"process_fact={b['process_fact']}，counterfactual={b['score']['counterfactual_credit']}"]

    a_states = {x["id"]: x["state"] for x in a["assertions"]}
    b_states = {x["id"]: x["state"] for x in b["assertions"]}
    same = a_states == b_states
    lines.append(f"  两种情形下逐条断言状态是否完全相同：{same}")

    checks = [
        ("A 情形分数为 0", a["score"]["ratio"] == 0, str(a["score"]["ratio"])),
        ("A 情形留下流程事实 required=True/submitted=False",
         a["process_fact"]["required"] is True and a["process_fact"]["submitted"] is False,
         str(a["process_fact"])),
        ("A 情形不计掌握度（report_attribution 全 false）",
         a["report_attribution"]["counts_toward_mastery"] is False
         and a["report_attribution"]["emit_error_patterns"] is False,
         str(a["report_attribution"])),
        ("A 情形仍保留反事实得分（诊断不丢）",
         a["score"]["counterfactual_credit"] > 0, str(a["score"]["counterfactual_credit"])),
        ("关掉开关后恢复计分（B 情形 > 0）", b["score"]["ratio"] > 0, str(b["score"]["ratio"])),
        ("B 情形 required=False", b["process_fact"]["required"] is False, str(b["process_fact"])),
        ("两种情形断言判定逐条相同（缺过程不改写判定）", same,
         f"A={a_states} B={b_states}"),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：A 的断言状态被改写、或 B 仍为 0 ⇒ 分流没生效/诊断被吞。"]
    lines.append("  反证条件：若把 requires_process 关掉后分数仍是 0，说明开关是空转；"
                 "若 A 的 credit 断言全变 unscored，说明用评分逻辑吞了诊断。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 10：保对账（答案数值必须能由过程记录推出）
# ---------------------------------------------------------------------------


def demo_10() -> tuple[str, list[str]]:
    tr = raw("raw-perfect")["trace"]
    pool = rc.trace_numbers(tr)
    lines = [f"  取自 raw-perfect 的真实过程记录，可见数值 {len(pool)} 个，前五个：{pool[:5]}"]
    real = pool[0] if pool else 1.0

    r_ok = rc.reconcile({"value": real}, tr)
    r_bad = rc.reconcile({"value": real + 987654.0}, tr)
    fake = {"tool_calls": [{"tool": "fetch_orders", "params": {"offset": 424242}}]}
    r_params = rc.reconcile({"value": 424242}, fake)
    r_empty = rc.reconcile({"value": real}, {"tool_calls": []})

    lines += [f"  ① 值来自过程记录 → {r_ok['status']}（{rc.summarize(r_ok)}）",
              f"  ② 值过程里没有   → {r_bad['status']}（checked[0].found={r_bad['checked'][0]['found']}）",
              f"  ③ 只把值塞进 params → {r_params['status']}（trace 可见数值 {r_params['trace_number_count']} 个）",
              f"  ④ 过程记录为空     → {r_empty['status']}（{rc.summarize(r_empty)}）"]

    checks = [
        ("真出处的值判 verified", r_ok["status"] == "verified", r_ok["status"]),
        ("编造的值判 mismatch", r_bad["status"] == "mismatch", r_bad["status"]),
        ("只有 params 有数字时判 unverifiable（params 不是出处）",
         r_params["status"] == "unverifiable", r_params["status"]),
        ("空过程记录判 unverifiable 而非 mismatch（数据不足 != 答错）",
         r_empty["status"] == "unverifiable", r_empty["status"]),
        ("unverifiable 不算证据", r_empty["counts_as_evidence"] is False,
         str(r_empty["counts_as_evidence"])),
        ("params 不在出处字段表里", "params" not in rc.SOURCE_FIELDS, str(rc.SOURCE_FIELDS)),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：③ 若判 verified ⇒ 把答案塞进参数就能自证，对账失效。"]
    lines.append("  已知代价（如实写）：对账要求过程记录里有被用到的数值；"
                 "记录缺字段时只能判 unverifiable，不能判答错 —— 这正是它还没接进 rubric 的原因（TD-039）。")
    lines.append("  反证条件：reconcile 把 params 里的数值计入出处池 ⇒ ③ 会变 verified，本档位立即失效。")
    return PASS, lines


# ---------------------------------------------------------------------------
# 决策 11：任务版本不一致 ⇒ 作废（出报告、不算分、不记短板）
# ---------------------------------------------------------------------------


def demo_11() -> tuple[str, list[str]]:
    base = raw("raw-perfect")

    def with_version(v: str | None) -> dict:
        r = copy.deepcopy(base)
        if v is None:
            r["submission"].pop("task_version", None)
        else:
            r["submission"]["task_version"] = v
        return r

    declared = TASK.get("task_version")
    bad = build(with_version("1999-01-01.1"))
    good = build(with_version(declared))
    absent = build(with_version(None))

    lines = [f"  任务 YAML 声明的 task_version = {declared}",
             f"  提交带错版本：voided={bad['score']['voided']}，"
             f"void_reason={bad['score'].get('void_reason')}，ratio={bad['score']['ratio']}",
             f"    断言状态：{[(a['id'], a['state']) for a in bad['assertions']]}",
             f"    反事实得分仍为 {bad['score']['counterfactual_credit']}，"
             f"report_attribution={bad['report_attribution']}",
             f"  提交带对版本：voided={good['score']['voided']}，ratio={good['score']['ratio']}",
             f"  提交不带版本：voided={absent['score']['voided']}，ratio={absent['score']['ratio']}",
             f"  作废报告仍满足 schema：", ]

    schema_ok = True
    try:
        dr.validate_report(bad, SCHEMA)
    except Exception as exc:  # noqa: BLE001 - 演示台要报出任何校验失败
        schema_ok = False
        lines.append(f"    校验失败：{exc}")
    if schema_ok:
        lines.append("    通过（作废不是「报告非法」）")

    checks = [
        ("错版本 ⇒ voided=True", bad["score"]["voided"] is True, str(bad["score"]["voided"])),
        ("void_reason 正确", bad["score"].get("void_reason") == "task_version_mismatch",
         str(bad["score"].get("void_reason"))),
        ("错版本 ⇒ 分数为 0", bad["score"]["ratio"] == 0 and bad["score"]["weighted_sum"] == 0,
         f"{bad['score']['ratio']}/{bad['score']['weighted_sum']}"),
        ("错版本 ⇒ 不计掌握度、不产错误模式",
         bad["report_attribution"] == {"counts_toward_mastery": False, "emit_error_patterns": False},
         str(bad["report_attribution"])),
        ("错版本 ⇒ 断言判定全部保留（不是吞掉这单）",
         all(a["state"] == "pass" for a in bad["assertions"]),
         str([(a["id"], a["state"]) for a in bad["assertions"]])),
        ("错版本 ⇒ notes 里有「重新提交」", any("重新提交" in n for n in bad.get("notes", [])),
         str(len(bad.get("notes", [])))),
        ("错版本 ⇒ 作废报告仍合法", schema_ok, str(schema_ok)),
        ("对版本 ⇒ 正常计分", good["score"]["voided"] is False and good["score"]["ratio"] == 1.0,
         f"{good['score']['voided']}/{good['score']['ratio']}"),
        ("不带版本 ⇒ 不比对（已知宽松口，向后兼容）", absent["score"]["voided"] is False,
         f"{absent['score']['voided']}/{absent['score']['ratio']}"),
        ("voided 与 disqualified 不混用", "disqualified" in bad["score"]
         and bad["score"]["disqualified"] is False, str(bad["score"].get("disqualified"))),
    ]
    ok_l, detail = _verdict(checks)
    lines += detail
    if not ok_l:
        return FAIL, lines + ["反证条件：任一条不成立 ⇒ 决策 11 未被执行。"]

    # 与决策 7 的边界：作废时「不记短板」必须有执行者，不能只写在文案里
    lines.append("  与决策 7 的关系：作废走的是「这次不算数」，不是「没交产物」——"
                 "两者用不同字段记（voided vs disqualifying_assertions），不合并。")
    lines.append("  已知宽松口：不带 task_version 就不比对，旧客户端可绕过；M1 接 API 时改必填。")
    lines.append("  反证条件：若 voided 与 disqualified 在同一份报告里同时为 true 且 "
                 "disqualifying_assertions 为空 ⇒ 两个状态被混用。")
    return PASS, lines


DEMOS = {
    1: demo_1, 2: demo_2, 3: demo_3, 4: demo_4, 5: demo_5, 6: demo_6,
    7: demo_7, 8: demo_8, 9: demo_9, 10: demo_10, 11: demo_11,
}

# 总表用的粗分类（真正判定仍由各 demo 现算；这张表只决定默认看点）
GROUPS = {
    "强可判定（跑代码就得结论）": (4, 5, 6, 7, 9, 10, 11),
    "部分可判定（一半靠材料/未实现）": (1, 2, 8),
    "当前无可执行载体（阶段未到）": (3,),
}


def listing() -> int:
    say("决策演示台 —— 11 条已定决策，按可演示性分三档")
    say("=" * 78)
    for group, ids in GROUPS.items():
        say(f"\n【{group}】{len(ids)} 条")
        for n in ids:
            head = decision_text(n)
            say(f"  {n:>2}. {head[:60]}{'…' if len(head) > 60 else ''}")
    say("\n用法：python scripts/demo_decision.py <编号...>   或   --all")
    say("说明：PARTIAL / NOT_EXECUTABLE 都不算通过，单列计数，不混进「全过」。")
    return 0


def run(ids: list[int]) -> int:
    rows = []
    say(f"决策演示台 —— 本次演示 {len(ids)} 条")
    for n in ids:
        say("")
        say("=" * 78)
        say(f"决策 {n}：{decision_text(n)}")
        say("-" * 78)
        try:
            verdict, lines = DEMOS[n]()
        except Exception as exc:  # noqa: BLE001 - 演示台要报出崩溃，而不是假装通过
            verdict, lines = FAIL, [f"  演示执行崩溃：{type(exc).__name__}: {exc}"]
        for line in lines:
            say(line)
        say(f"  >> {n}: {verdict}")
        rows.append((n, verdict))

    say("")
    say("=" * 78)
    say("汇总")
    for n, v in rows:
        say(f"  {n:>2}  {v}")
    counts = {k: sum(1 for _, v in rows if v == k) for k in (PASS, PARTIAL, NOEXEC, FAIL)}
    say(f"\n  PASS {counts[PASS]} / PARTIAL {counts[PARTIAL]} / "
        f"NOT_EXECUTABLE {counts[NOEXEC]} / FAIL {counts[FAIL]}")
    if counts[FAIL]:
        say("\n  有 FAIL：对应决策当前未被执行，或演示本身过期。逐条看上面的 [XX] 行。")
        return 1
    if counts[PARTIAL] or counts[NOEXEC]:
        say("\n  无 FAIL。注意 PASS 只覆盖「本机可判定的那部分主张」，"
            "PARTIAL / NOT_EXECUTABLE 的剩余部分不由本脚本担保。")
    return 0


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("-")}
    args = [a for a in argv if not a.startswith("-")]
    if "--all" in flags:
        return run(sorted(DEMOS))
    if not args:
        return listing()
    try:
        ids = sorted({int(a) for a in args})
    except ValueError:
        say(f"编号必须是整数：{args}")
        return 2
    unknown = [n for n in ids if n not in DEMOS]
    if unknown:
        say(f"没有这些决策编号：{unknown}（现有 {sorted(DEMOS)}）")
        return 2
    return run(ids)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
