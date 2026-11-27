"""「做完了」：学生自己把一条复习项标掉的判据锁（B-08c）。

这一环补的是一个很具体的断点：`/s/<token>` 只**告诉**他今天该复习哪几项，
没有任何地方能让他把项做掉——队列只增不减，到后来整页都是陈年旧账。

四条口径，每条对应一种会悄悄出错的实现：

1. **只改自己的**。`review_id` 会出现在链接里；拿别人链接的人不应该动得了它。
   不可用时**不报错、只做 no-op**：报错等于告诉他"这个 id 存在"。
2. **幂等**。刷新、连点、浏览器预抓都会重放这个 GET，第二次不该炸。
3. **不推进 FSRS 卡、不改掌握度**。点按钮不是证据——证据只能来自交卷，
   否则会出现"页面说他掌握了、成绩单上没有"的两套账。
4. **页面上看不见 review_id**。它只进 `href`，文案里仍只有技能名、形态、逾期天数。
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import hosting  # noqa: E402
from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

TASK_ID = "dbg-001-order-report"
SKILL = "tool_param_validation"
ADMIN = "k-test"
PAST = "2026-11-20T00:00:00Z"


def _item(user_id: str, review_id: str = "r-1", *, due: str = PAST) -> dict:
    return {"review_id": review_id, "user_id": user_id, "skill_id": SKILL,
            "kind": "variant", "task_id": None, "source_submission_id": None,
            "due": due, "status": "pending"}


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "review.db"
    registry = api.TaskRegistry(ROOT / "tasks")
    api.bootstrap(db, registry)
    srv = api.make_server(db, port=0, quiet=True, admin_token=ADMIN)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()

    sessions = hosting.SessionRegistry(ROOT / "tasks", conn_factory=lambda: store.connect(db))

    def call(method: str, path: str):
        conn = http.client.HTTPConnection(host, port, timeout=15)
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        location = resp.getheader("Location")
        conn.close()
        return resp.status, body, location

    def conn():
        return store.connect(db)

    try:
        yield SimpleNamespace(call=call, conn=conn, sessions=sessions, db=db)
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def _seed(env, user_id: str, review_id: str = "r-1") -> str:
    """造一个绑到 `user_id` 的会话 + 一条已到期的 pending 复习项。返回 token。"""
    token = env.sessions.create(TASK_ID, user_id=user_id)["token"]
    c = env.conn()
    try:
        store.ensure_user(c, user_id)
        store.save_review_items(c, [_item(user_id, review_id)])
    finally:
        c.close()
    return token


def _status_of(env, review_id: str) -> str:
    c = env.conn()
    try:
        return c.execute("SELECT status FROM review_items WHERE review_id = ?",
                         (review_id,)).fetchone()["status"]
    finally:
        c.close()


# ── 1. 只改自己的 ────────────────────────────────────────────────────────────


def test_marking_your_own_item_done(env):
    token = _seed(env, "u-a")
    status, _body, location = env.call("GET", f"/s/{token}/review/r-1/done")

    assert status == 303 and location == f"/s/{token}?marked=1"
    assert _status_of(env, "r-1") == "done"


def test_you_cannot_mark_someone_elses_item(env):
    """别人的链接能改我的复习项，等于我的队列可以被别人清空。

    不报错、只 no-op：报错就告诉对方"这个 id 存在、只是不属于你"。
    """
    _seed(env, "u-a", "r-1")
    other = env.sessions.create(TASK_ID, user_id="u-b")["token"]

    status, _body, _loc = env.call("GET", f"/s/{other}/review/r-1/done")

    assert status == 303, "不该用错误码暴露这条 id 的存在"
    assert _status_of(env, "r-1") == "pending", "被别人标掉了"


def test_marking_twice_is_harmless(env):
    """刷新/连点/浏览器预抓都会重放这个 GET。"""
    token = _seed(env, "u-a")
    env.call("GET", f"/s/{token}/review/r-1/done")
    status, _body, _loc = env.call("GET", f"/s/{token}/review/r-1/done")

    assert status == 303
    assert _status_of(env, "r-1") == "done"


def test_unknown_review_id_is_a_noop(env):
    token = _seed(env, "u-a")
    status, _body, _loc = env.call("GET", f"/s/{token}/review/no-such-id/done")

    assert status == 303
    assert _status_of(env, "r-1") == "pending"


# ── 2. 不推进卡：点按钮不是证据 ──────────────────────────────────────────────


def test_marking_does_not_touch_the_mastery_card(env):
    """标 done 只动队列，不动 `user_skill_states`。

    若这里顺手推进卡片，就会出现"页面说他掌握了、成绩单上没有"——两套账。
    代价是这次复习不计入记忆强度，那是保守的低估，不会高估。
    """
    token = _seed(env, "u-a")
    env.call("GET", f"/s/{token}/review/r-1/done")

    c = env.conn()
    try:
        rows = c.execute("SELECT * FROM user_skill_states WHERE user_id = 'u-a'").fetchall()
    finally:
        c.close()
    assert rows == [], "标完成不该写卡：证据只能来自交卷"


# ── 3. 页面：有链接、不露 id、点了有回话 ────────────────────────────────────


def _visible_text(page: str) -> str:
    """去标签与属性后的可见文字（`href` 里的东西不算"给人看的话"）。"""
    return re.sub(r"<[^>]+>", " ", page)


def test_done_link_is_there_but_the_id_is_not_visible(env):
    token = _seed(env, "u-a")
    page = hosting.render_landing(
        env.sessions.get(token), {"title": "标题", "scenario": "题面", "tools": []},
        "http://x", review=hosting.review_of(env.conn(), "u-a"),
    )

    assert "/review/r-1/done" in page, "「做完了」的入口没出现，队列就清不掉"
    visible = _visible_text(page)
    assert "r-1" not in visible, "review_id 漏进了给人看的字里"
    assert "review_id" not in page


def test_landing_confirms_it_was_recorded(env):
    """点完回到本页要有一句确认：最后一条被关掉时整块会消失，不吭声像出了故障。"""
    token = _seed(env, "u-a")
    page = hosting.render_landing(
        env.sessions.get(token), {"title": "标题", "scenario": "题面", "tools": []},
        "http://x", just_marked=True,
    )

    assert "记下了" in page
    assert "不产生成绩" in page, "没说清点按钮不等于有成绩"
