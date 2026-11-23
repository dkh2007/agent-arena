"""加题台账（docs/task-authoring-log.yaml）的约束测试。

## 为什么要有这份测试

以前加一道题要手改三处 Python 常量（`len(all_tasks) == 20`、`checked == 16`、
`test_lab.py` 的三处 `== 20`）。那三个数字是**故意**写死的——逼人停下来确认
「这题真的独立、不是复制粘贴」。但"停下来想"靠改一个数字是逼不出来的：
把 20 改成 21 不需要任何判断，而且这个数字本身没有信息量，机器能自己数。

所以闸门换了个位置：**加题必须在一份台账里写一句 why**，说清"这题变的是什么"。
数量不再由人声明（由台账行数决定），判断仍由人做（写在 why 里，留痕可 review）。

这份文件锁三件事：台账与磁盘**双向**一致、why 有实义、id 不重复。
"why 写得对不对"机器锁不住（属于 `docs/authoring-tasks.md` §5 的诚实清单第 1 条），
只能人审——但"有没有写"和"是不是占位符"能锁。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"
LOG_PATH = ROOT / "docs" / "task-authoring-log.yaml"
FAMILIES_PATH = ROOT / "docs" / "task-families.yaml"
TAXONOMY_PATH = ROOT / "docs" / "skill-taxonomy-v0.yaml"

# 占位式 why：写了等于没写。判据不是"字数够"，而是"这句话对任何一道题都成立"。
BANNED_WHY = ("同上", "见上", "略", "待补", "TODO", "新题", "换了个业务名", "复制粘贴")

MIN_WHY_CHARS = 12


def _log() -> list[dict]:
    data = yaml.safe_load(LOG_PATH.read_text(encoding="utf-8"))
    return data["tasks"]


def _task_ids_on_disk() -> set[str]:
    return {p.stem for p in TASKS_DIR.glob("*.yaml")}


def test_every_task_on_disk_is_registered():
    """磁盘上每道题都要在台账里有一行——加了题不登记，就是原来「改数字」那一步。"""
    registered = {row["id"] for row in _log()}
    missing = sorted(_task_ids_on_disk() - registered)
    assert not missing, (
        f"这些题在 tasks/ 里但台账没登记：{missing}\n"
        f"补一行 why（说清这题变的是什么）到 docs/task-authoring-log.yaml，"
        f"或用 `python scripts/new_task.py --check <文件>` 看还差什么"
    )


def test_no_orphan_entries():
    """台账里不许有磁盘上不存在的 id——否则删了题测试照样绿，数量失真。"""
    orphans = sorted({row["id"] for row in _log()} - _task_ids_on_disk())
    assert not orphans, f"台账里有磁盘上不存在的任务：{orphans}（题被删了就把这行也删掉）"


def test_task_count_is_derived_not_declared():
    """数量由台账行数决定，所以这条只在台账自身重复时才红。

    保留它是因为原来那个写死的 20 顺带防住了另一件事：**同一道题被登记两次**
    （并行加题时两行都写进合并结果）。这条把那个守卫接过来。
    """
    ids = [row["id"] for row in _log()]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"台账里重复登记：{dup}"


@pytest.mark.parametrize("row", _log(), ids=[r["id"] for r in _log()])
def test_why_is_a_real_sentence(row):
    why = (row.get("why") or "").strip()
    assert why, f"{row['id']}: why 是空的——加题必须写清这题变的是什么"
    assert len(why) >= MIN_WHY_CHARS, (
        f"{row['id']}: why 只有 {len(why)} 字，写不出差异说明就是没想清"
    )
    for bad in BANNED_WHY:
        assert bad not in why, f"{row['id']}: why 里出现了占位式说法「{bad}」"


def test_log_order_groups_families_together():
    """台账按家族分组，加题时一眼看出"这个家族现在几道、梯度够不够"。"""
    families = [row["id"].split("-")[0] for row in _log()]
    seen: list[str] = []
    for fam in families:
        if not seen or seen[-1] != fam:
            assert fam not in seen, (
                f"家族 {fam} 的条目被其他家族隔开了——台账要按家族分段，"
                f"否则看不出某个家族的难度梯度（当前顺序：{families}）"
            )
            seen.append(fam)


def test_log_covers_every_template():
    """登记表里的每一套骨架都必须有题在跑。

    原来这里写死 `== {"tpl-tool-debug", "tpl-bug-locate"}`——加第三套骨架时它只逼人
    改一行常量，改完就绿，没有任何判断。现在从 `docs/task-families.yaml` 推导：
    表里登记了几套骨架，任务库就必须真的用到这几套，且不多不少。
    （"表里能登记、现实却没人用"才是真正的失败模式：新骨架投产了却一题没有。）
    """
    families = yaml.safe_load(
        (ROOT / "docs" / "task-families.yaml").read_text(encoding="utf-8")
    )["families"]
    declared = {f["template"] for f in families}
    used = set()
    for path in TASKS_DIR.glob("*.yaml"):
        task = yaml.safe_load(path.read_text(encoding="utf-8"))
        used.add(task.get("variant_template_id") or "tpl-tool-debug")
    assert used == declared, (
        f"任务库用到 {sorted(used)}，登记表声明 {sorted(declared)}——两边必须一致"
    )
    assert len(declared) >= 2, "至少要有两套骨架，否则'换题型'这件事没有被验证过"


# ---------------------------------------------------------------------------
# 家族登记表（docs/task-families.yaml）
#
# 这张表原来散在测试代码、加题文档、加题脚本三处。现在只此一份，
# 所以它自己必须是对的——写错一个 mother 会让新题静默继承错的骨架。
# ---------------------------------------------------------------------------

def _families() -> list[dict]:
    return yaml.safe_load(FAMILIES_PATH.read_text(encoding="utf-8"))["families"]


def test_family_ids_are_unique():
    ids = [f["id"] for f in _families()]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, f"家族 id 重复：{dup}"


@pytest.mark.parametrize("family", _families(), ids=[f["id"] for f in _families()])
def test_family_primary_skill_exists_in_taxonomy(family):
    tax = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    known = {s["skill_id"] for s in tax["skills"]}
    assert family["primary_skill"] in known, (
        f"家族 {family['id']} 的主技能 {family['primary_skill']} 不在词表里：{sorted(known)}"
    )


@pytest.mark.parametrize("family", _families(), ids=[f["id"] for f in _families()])
def test_family_mother_exists_and_matches_template(family):
    mother = TASKS_DIR / f"{family['mother']}.yaml"
    assert mother.exists(), f"家族 {family['id']} 的母本 {mother.name} 不存在"
    actual = yaml.safe_load(mother.read_text(encoding="utf-8")).get("variant_template_id") \
        or "tpl-tool-debug"
    assert actual == family["template"], (
        f"家族 {family['id']} 声明骨架 {family['template']}，但母本 {mother.name} "
        f"实际是 {actual}——跨骨架继承会拿到一套与题面无关的断言"
    )


def test_one_mother_per_template():
    """同一骨架的所有家族必须指向同一个母本，否则"改母本全族跟着变"就不成立。"""
    by_tpl: dict[str, set[str]] = {}
    for f in _families():
        by_tpl.setdefault(f["template"], set()).add(f["mother"])
    bad = {t: sorted(m) for t, m in by_tpl.items() if len(m) > 1}
    assert not bad, f"一个骨架挂了多个母本：{bad}"
