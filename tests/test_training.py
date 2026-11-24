"""训练单口径（B：同场景逐步加难）的契约测试。

守的是三件事：
1. 只从任务 YAML 已有的 `variant_meta` 读族与难度，不另造标签；
2. 缺字段返回 None（不默认成第 1 档），族尾返回 `top_of_family`（不编造「再加难」）；
3. "练什么" 的文字来自任务自述的编号要求，跨行要接起来，子项要读得通。

第 3 条容易被当成排版细节而忽略：真实使用里学生看到的就是这些句子，
半句「…report.json，」比没显示更糟——他会以为要求本身缺了。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena.training import (  # noqa: E402
    CRITERION,
    TrainingError,
    asks_of,
    drill_brief,
    family_position,
    levels_in_family,
    load_catalog,
    next_in_family,
)

TASKS = ROOT / "tasks"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(TASKS)


@pytest.fixture(scope="module")
def by_id(catalog):
    return {t["task_id"]: t for t in catalog}


# ---------------------------------------------------------------- 读族与难度


def test_family_position_reads_variant_meta(by_id):
    assert family_position(by_id["dbg-001-order-report"]) == ("order-report", 1)
    assert family_position(by_id["prm-003-page-ceiling-4"]) == ("param-surface", 3)


def test_family_position_returns_none_when_metadata_missing():
    """缺字段不许当成第 1 档：那会让推荐链凭空多出一档不存在的题。"""
    assert family_position({"task_id": "x"}) is None
    assert family_position({"variant_meta": {"family_id": "f"}}) is None
    assert family_position({"variant_meta": {"family_id": "", "difficulty": 1}}) is None


@pytest.mark.parametrize("bad", [True, "1", 1.0, 0, -1])
def test_family_position_rejects_non_positive_or_non_int_difficulty(bad):
    assert family_position({"variant_meta": {"family_id": "f", "difficulty": bad}}) is None


def test_levels_in_family_lists_each_family(catalog):
    # 2026-11-22 扩题（13 → 20）：四个家族都补齐到 1/2/3。
    # locate-drill 此前只有 [1, 2]（缺难度 3），fix-004 补上后变 [1, 2, 3]。
    assert levels_in_family("order-report", catalog) == [1, 2, 3]
    assert levels_in_family("locate-drill", catalog) == [1, 2, 3]
    assert levels_in_family("param-surface", catalog) == [1, 2, 3]
    assert levels_in_family("retry-drill", catalog) == [1, 2, 3]
    assert levels_in_family("no-such-family", catalog) == []


# ---------------------------------------------------------------- 下一档


@pytest.mark.parametrize(
    "task_id,expected",
    [
        ("dbg-001-order-report", "dbg-003-purchase-report"),
        ("dbg-002-invoice-report", "dbg-003-purchase-report"),
        ("dbg-003-purchase-report", "dbg-004-sensor-batch"),
        ("fix-001-token-expiry", "fix-002-cart-none-deref"),
        ("prm-001-page-ceiling-5", "prm-002-ceiling-5-long"),
        ("prm-002-ceiling-5-long", "prm-003-page-ceiling-4"),
        ("ret-001-refund-503", "ret-002-second-page-fault"),
        ("ret-002-second-page-fault", "ret-003-fourth-page-fault"),
    ],
)
def test_next_in_family_walks_the_real_chains(by_id, task_id, expected):
    nxt = next_in_family(by_id[task_id], list(by_id.values()))
    assert nxt is not None and nxt["task_id"] == expected


@pytest.mark.parametrize(
    "task_id",
    [
        "dbg-004-sensor-batch",
        # 2026-11-22：locate-drill 的族尾从 fix-002（难度 2）变成 fix-004（难度 3），
        # fix-002 现在有下一档 fix-004，不再满足"没有下一档"。
        "fix-004-cache-lost-update",
        "ret-003-fourth-page-fault",
    ],
)
def test_top_of_family_has_no_next(by_id, task_id):
    assert next_in_family(by_id[task_id], list(by_id.values())) is None


def test_next_never_returns_the_task_itself(by_id):
    for task in by_id.values():
        nxt = next_in_family(task, list(by_id.values()))
        if nxt is not None:
            assert nxt["task_id"] != task["task_id"]


def test_same_level_tie_breaks_by_task_id(by_id):
    """同族同档有两道题时必须确定性选一道，否则同一个学生刷新两次得到不同推荐。"""
    clone = dict(by_id["dbg-003-purchase-report"])
    clone["task_id"] = "dbg-000-clone"  # 与 dbg-003 同为 order-report/2
    nxt = next_in_family(by_id["dbg-001-order-report"], [*by_id.values(), clone])
    assert nxt["task_id"] == "dbg-000-clone"


# ---------------------------------------------------------------- 「练什么」的文字


def test_asks_join_wrapped_lines(by_id):
    """变异：YAML 软换行把一句要求劈成两行——逐行取会得到半句。"""
    asks = asks_of(by_id["dbg-003-purchase-report"])
    write_report = [a for a in asks if "write_report" in a]
    assert write_report, asks
    assert "内容为 JSON" in write_report[0]


def test_asks_flatten_bullet_subitems(by_id):
    """子项写成「- xxx」，拼一行后不许留下「：；」或成串横线。"""
    asks = asks_of(by_id["fix-002-cart-none-deref"])
    joined = "\n".join(asks)
    assert " - " not in joined
    assert "：；" not in joined
    assert "type_mismatch" in joined


def test_asks_empty_when_scenario_has_no_numbered_items():
    assert asks_of({"scenario": "随便写点什么，没有编号。"}) == []
    assert asks_of({"scenario": None}) == []


def test_asks_respects_limit(by_id):
    assert len(asks_of(by_id["dbg-003-purchase-report"], limit=2)) == 2


# ---------------------------------------------------------------- 训练单组装


def test_drill_brief_has_next_carries_task_and_criterion(by_id):
    titles = {"R2_report_correct": "结论是否与判据一致", "R3_retry_evidence": "关键步骤是否走到"}
    brief = drill_brief(by_id["dbg-001-order-report"], list(by_id.values()),
                        focus_assertions=["R2_report_correct", "R3_retry_evidence"],
                        assertion_titles=titles)
    assert brief["status"] == "has_next"
    assert brief["family_id"] == "order-report"
    assert (brief["level"], brief["levels_total"]) == (1, 3)
    assert brief["next"]["task_id"] == "dbg-003-purchase-report"
    assert brief["next"]["asks"], "下一档的要求不许为空——空卡片比没有更糟"
    assert brief["criterion"] == CRITERION
    assert brief["focus_assertions"] == [
        {"id": "R2_report_correct", "title": "结论是否与判据一致"},
        {"id": "R3_retry_evidence", "title": "关键步骤是否走到"},
    ]
    assert brief["task_id"] == "dbg-001-order-report", "训练单必须带 task_id，供页面与报告对账"


def test_focus_assertion_without_title_is_loud(by_id):
    """没有标题就必须抛错，不许回退成 id。

    回退的后果不是红，是**页面上出现 `R2_report_correct`**：看起来像样式问题，
    实际是数据缺口，而且会一直缺下去——没人会为「页面上多了个英文单词」提 bug。
    """
    with pytest.raises(TrainingError, match="中文标题"):
        drill_brief(by_id["dbg-001-order-report"], list(by_id.values()),
                    focus_assertions=["R2_report_correct"],
                    assertion_titles={"R3_retry_evidence": "关键步骤是否走到"})


def test_focus_assertion_rows_keep_input_order(by_id):
    """顺序与输入一致：复习时要按报告里失败的先后回看，不是按字典序。"""
    titles = {"R3_retry_evidence": "关键步骤是否走到", "R2_report_correct": "结论是否与判据一致"}
    brief = drill_brief(by_id["dbg-001-order-report"], list(by_id.values()),
                        focus_assertions=["R3_retry_evidence", "R2_report_correct"],
                        assertion_titles=titles)
    assert [row["id"] for row in brief["focus_assertions"]] == [
        "R3_retry_evidence", "R2_report_correct",
    ]


def test_drill_brief_top_of_family_says_so(by_id):
    brief = drill_brief(by_id["dbg-004-sensor-batch"], list(by_id.values()))
    assert brief["status"] == "top_of_family"
    assert brief["next"] is None


def test_drill_brief_no_family_does_not_invent_a_level(by_id):
    brief = drill_brief({"task_id": "x", "title": "无族任务"}, list(by_id.values()))
    assert brief == {
        "task_id": "x",
        "status": "no_family",
        "family_id": None,
        "level": None,
        "levels_total": 0,
        "next": None,
        "criterion": CRITERION,
        "focus_assertions": [],
    }


def test_mutation_raising_difficulty_removes_the_recommendation(by_id, tmp_path):
    """把下一档的 difficulty 改掉，推荐必须跟着消失——证明它真在读 YAML，不是常量。"""
    # 2026-11-22：同一档可以有多道题（order-report 的难度 2 现在有 dbg-003 与 dbg-005），
    # 只改走一道时 dbg-001 仍有下一档，断言会假红。必须把该档**整档**改走。
    # 用 family_position 动态挑而不是写死 task_id：以后再加同档题也不用回来改这条。
    target = ("order-report", 2)
    catalog = []
    raised = 0
    for task_id, task in by_id.items():
        copy = yaml.safe_load(yaml.safe_dump(task, allow_unicode=True))
        if family_position(copy) == target:
            copy["variant_meta"]["difficulty"] = 9
            raised += 1
        catalog.append(copy)
    assert raised >= 1, f"前置条件不成立：{target} 在任务库里一道都没有"
    brief = drill_brief(by_id["dbg-001-order-report"], catalog)
    assert brief["status"] == "top_of_family", (
        f"{target} 整档（{raised} 道）被改走后，dbg-001 不该还有下一档"
    )


def test_mutation_dropping_family_disables_recommendation(by_id):
    catalog = []
    for task_id, task in by_id.items():
        copy = yaml.safe_load(yaml.safe_dump(task, allow_unicode=True))
        if task_id != "dbg-001-order-report":
            copy["variant_meta"].pop("family_id", None)
        catalog.append(copy)
    brief = drill_brief(by_id["dbg-001-order-report"], catalog)
    assert brief["status"] == "top_of_family"
