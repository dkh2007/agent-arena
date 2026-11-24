"""学习者申诉（v7 / 2026-11-24）：「我认为判错了」这个入口。

**这一层刻意不做的事**（写清边界，避免下轮重猜）：

1. **不自动重评**。`accepted` 只表示"承认这条判分有问题"，新判据是人的判断
   （`reappraisal.reappraise()` 的 `task` 参数由人给）。申诉层替人决定"该怎么改"，
   等于把"谁在什么时候按什么标准改的"这条留痕抹掉。
2. **不碰 `submissions` / `diagnosis`**。申诉是新增的旁证，不是覆盖原判——
   与"诊断记录不覆盖"是同一条铁律。
3. **不假设申诉会成立**。落库即 `pending`，等裁决。所以不能拿"有申诉"当"判错了"用。

它补的是链上缺的那个**入口**：原来争议只能由系统发现（`judge_pipeline.record_arbitration`
在双评不一致时入队），学习者本人没有任何发起渠道。
"""

from __future__ import annotations

import copy
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import store  # noqa: E402
from arena.trace_pipeline import run_fixture  # noqa: E402

TASK_YAML = ROOT / "tasks" / "dbg-001-order-report.yaml"
USER = "u-1"
SUB = "s-1"
AT = "2026-11-24T10:00:00Z"

# `raw-perfect` 的断言 id，写在测试里当预期值（不去现算，否则"报告少了断言"这条抓不到）
EXPECTED_IDS = {"R1_file_exists", "R5_clean_exit", "R2_report_correct",
                "R3_retry_evidence", "R4_no_param_hallucination"}


def _report(name: str = "raw-perfect", *, submission_id: str = SUB, at: str = AT) -> dict:
    report = copy.deepcopy(run_fixture(name))
    report["submission"]["submission_id"] = submission_id
    report["submission"]["evaluated_at"] = at
    return report


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(tmp_path / "appeal.db")
    store.init_db(conn)
    store.register_skills(conn)
    store.register_patterns(conn)
    store.register_task(conn, TASK_YAML)
    # 申诉必须有"被判的那份报告"作外键，所以先落一份
    store.save_submission(conn, _report(), user_id=USER, created_at=AT)
    yield conn
    conn.close()


def _appeal(db, **over) -> dict:
    kw = {"user_id": USER, "reason_text": "R3 判我没重试，但我在第二次请求里重试了"}
    kw.update(over)
    return store.create_appeal(db, SUB, **kw)


# --- ① 落库与读回 -----------------------------------------------------------

def test_appeal_lands_and_reads_back(db):
    row = _appeal(db, target_assertion="R3_retry_evidence")
    assert row["status"] == "pending"
    assert row["submission_id"] == SUB
    assert row["user_id"] == USER
    assert row["target_assertion"] == "R3_retry_evidence"
    assert row["resolved_at"] is None and row["resolution_note"] is None, (
        "刚提的申诉不该带裁决痕迹——表上的 CHECK 也不允许"
    )
    assert store.load_appeal(db, row["appeal_id"]) == row


def test_appeal_ids_do_not_collide_within_the_same_second(db):
    """id 不含时刻：同一秒提的两条申诉必须是两个 id。

    第一版把 `created_at` 掺进派生式，同一秒两条（用户连点、测试循环）会撞主键，
    报出来却是"这条申诉已经在队列里了"——把两个不同理由说成了一条。
    现在按"该提交下第几条"派生，确定、单调、与时间轴无关。
    """
    first = _appeal(db, reason_text="甲")
    store.resolve_appeal(db, first["appeal_id"], status="withdrawn", note="撤回", at=AT)
    second = _appeal(db, reason_text="乙", created_at=first["created_at"])  # 刻意同一秒
    assert second["appeal_id"] != first["appeal_id"], (
        "同一秒的两条申诉派生了同一个 id——派生式里还掺着时刻"
    )


# --- ② 一人一条待处理 -------------------------------------------------------

def test_second_pending_appeal_is_refused(db):
    _appeal(db, reason_text="第一次")
    with pytest.raises(store.StoreError, match="已经在队列里"):
        _appeal(db, reason_text="第二次")


def test_can_appeal_again_after_resolution(db):
    """裁决完可以再提——限制的是"同时"只有一条，不是"一辈子只有一条"。"""
    first = _appeal(db, reason_text="第一次")
    store.resolve_appeal(db, first["appeal_id"], status="rejected", note="查过", at=AT)
    second = _appeal(db, reason_text="第二次", created_at="2026-11-24T11:00:00Z")
    assert second["appeal_id"] != first["appeal_id"]
    assert len(store.load_appeals(db, SUB)) == 2, "历史申诉要留痕，不能被后一条覆盖"


# --- ③ 裁决：必须写理由、不许改判 -------------------------------------------

def test_resolve_requires_a_note(db):
    row = _appeal(db)
    for status in ("accepted", "rejected", "withdrawn"):
        with pytest.raises(store.StoreError, match="必须写 note"):
            store.resolve_appeal(db, row["appeal_id"], status=status, note="   ")
    assert store.load_appeal(db, row["appeal_id"])["status"] == "pending"


def test_pending_is_not_a_verdict(db):
    row = _appeal(db)
    with pytest.raises(store.StoreError, match="不是裁决结果"):
        store.resolve_appeal(db, row["appeal_id"], status="pending", note="再等等")
    with pytest.raises(store.StoreError, match="合法|裁决状态"):
        store.resolve_appeal(db, row["appeal_id"], status="whatever", note="乱填")


def test_a_resolved_appeal_cannot_be_rejudged(db):
    """裁决不可覆盖。改结论要另存说明——与 `submissions` 不覆盖同一个理由。"""
    row = _appeal(db)
    store.resolve_appeal(db, row["appeal_id"], status="accepted", note="确实漏判", at=AT)
    with pytest.raises(store.StoreError, match="已经裁决过"):
        store.resolve_appeal(db, row["appeal_id"], status="rejected", note="改主意了", at=AT)
    assert store.load_appeal(db, row["appeal_id"])["status"] == "accepted"


def test_withdraw_records_who_and_when(db):
    row = _appeal(db)
    got = store.resolve_appeal(db, row["appeal_id"], status="withdrawn",
                               note="用户撤回", at="2026-11-24T12:00:00Z")
    assert got["status"] == "withdrawn"
    assert got["resolved_at"] == "2026-11-24T12:00:00Z"
    assert got["resolution_note"] == "用户撤回"


def test_resolving_unknown_appeal_is_an_error(db):
    with pytest.raises(store.StoreError, match="没有这条申诉"):
        store.resolve_appeal(db, "ap-nope", status="accepted", note="x")


# --- ④ 只能替自己申诉 -------------------------------------------------------

def test_cannot_appeal_on_behalf_of_someone_else(db):
    with pytest.raises(store.StoreError, match="只能对自己的判分提申诉"):
        _appeal(db, user_id="someone-else")


def test_unknown_submission_is_refused(db):
    with pytest.raises(store.StoreError, match="还没落库"):
        store.create_appeal(db, "s-nope", user_id=USER, reason_text="x")


def test_reason_text_is_required(db):
    for bad in ("", "   ", None):
        with pytest.raises(store.StoreError, match="必须写理由"):
            _appeal(db, reason_text=bad)
    with pytest.raises(store.StoreError, match="过长"):
        _appeal(db, reason_text="字" * 2001)


# --- ⑤ 指向哪一条断言 -------------------------------------------------------

def test_target_assertion_must_exist_in_the_report(db):
    """申诉一条不存在的断言，裁决的人会去找一条假证据。

    前端应从报告 `assertions[].id` 取；这条挡住"手填一个 R9"。
    """
    with pytest.raises(store.StoreError, match="不在这份报告里"):
        _appeal(db, target_assertion="R9_does_not_exist")
    # 存在的那几条都能过
    for assertion_id in EXPECTED_IDS:
        first = store.list_appeals(db, status="pending")
        for row in first:  # 清场，避免撞"一人一条 pending"
            store.resolve_appeal(db, row["appeal_id"], status="withdrawn", note="测下一个")
        got = _appeal(db, target_assertion=assertion_id)
        assert got["target_assertion"] == assertion_id


def test_empty_target_means_the_whole_report(db):
    """不指定断言 = 对整份报告有异议，不是"指向空字符串"。"""
    assert _appeal(db, target_assertion=None)["target_assertion"] is None
    for row in store.list_appeals(db, status="pending"):
        store.resolve_appeal(db, row["appeal_id"], status="withdrawn", note="测下一个")
    assert _appeal(db, target_assertion="  ")["target_assertion"] is None


# --- ⑥ 列队列 ---------------------------------------------------------------

def test_list_defaults_to_pending_only(db):
    first = _appeal(db, reason_text="甲")
    store.resolve_appeal(db, first["appeal_id"], status="rejected", note="查过", at=AT)
    second = _appeal(db, reason_text="乙", created_at="2026-11-24T11:00:00Z")

    pending = store.list_appeals(db)
    assert [r["appeal_id"] for r in pending] == [second["appeal_id"]], (
        "默认只该给 pending——顺手把历史都拉出来，裁决的人会重复看已处理过的"
    )
    assert len(store.list_appeals(db, status=None)) == 2


def test_list_rejects_unknown_status(db):
    with pytest.raises(store.StoreError, match="未知的申诉状态"):
        store.list_appeals(db, status="someday")
    with pytest.raises(store.StoreError, match="正整数"):
        store.list_appeals(db, limit=0)


# --- ⑦ 「该重评的清单」------------------------------------------------------

def test_accepted_appeals_are_listed_for_reappraisal(db):
    """接受 ≠ 已重评。清单要的是"接受了但还没重评"的那些。

    判据用 `reappraisals` 里有没有记录，**不看时间戳**：重评可能晚于裁决好几天，
    用时间比会漏掉"先接受、后批量重算"的那批。
    """
    accepted = _appeal(db, reason_text="该重评的")
    store.resolve_appeal(db, accepted["appeal_id"], status="accepted", note="确实漏判", at=AT)

    rejected = _appeal(db, reason_text="不该重评的", created_at="2026-11-24T11:00:00Z")
    store.resolve_appeal(db, rejected["appeal_id"], status="rejected", note="查过", at=AT)
    still_pending = _appeal(db, reason_text="还没裁的", created_at="2026-11-24T12:00:00Z")

    listed = [r["appeal_id"] for r in store.list_accepted_appeals(db)]
    assert listed == [accepted["appeal_id"]], (
        f"清单应只有'已接受'那条（实际 {listed}）；"
        f"rejected={rejected['appeal_id']} / pending={still_pending['appeal_id']} 都不该进"
    )


# --- ⑧ 申诉不动原判 ---------------------------------------------------------

def test_appealing_does_not_touch_the_original_verdict(db):
    """申诉是旁证，不是覆盖。原报告的分数与诊断必须一字不动。"""
    before = store.load_submission(db, SUB)
    _appeal(db, reason_text="我觉得判错了")
    after = store.load_submission(db, SUB)
    for key in ("diagnosis", "assert_score", "trace", "artifacts"):
        assert after[key] == before[key], f"申诉改了 {key}——那是重评该做的事，不是申诉"
    assert db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1, (
        "申诉不该新增 submission 行：一份作答永远只算一个 step"
    )
