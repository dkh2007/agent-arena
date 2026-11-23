"""`bootstrap()` 的事务边界：25 条任务注册要么全进，要么一条都不进。

为什么值得单独锁：2026-11-23 为了提速把逐条提交合并成一个事务（B-36）。这类改动
最典型的翻车不是"变慢"，而是**悄悄少写/多写**——库里看着有数据，某几行却和
逐条提交时不同。所以判据不是"能跑"，是下面两条：

1. 产物与逐条提交时**逐行相同**（`iterdump()` 归一化时间戳后比对）；
2. 中途失败**不留半成品库**（这条以前是反过来的：逐条提交会留下前 k 条）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import store  # noqa: E402
from arena import submission_api as api  # noqa: E402

_TS = re.compile(r"\d{4}-\d\d-\d\dT[\d:+]+")


def _dump(db: str) -> list[str]:
    conn = store.connect(db)
    try:
        return [_TS.sub("<TS>", ln) for ln in conn.iterdump()]
    finally:
        conn.close()


def _db(tmp_path: Path, name: str) -> str:
    return str(tmp_path / name)


def test_single_transaction_writes_exactly_what_loose_commits_wrote(tmp_path):
    """合并事务后，库内容必须与"逐条提交"逐行一致（时间戳除外）。"""
    reg = api.TaskRegistry()
    batched = _db(tmp_path, "batched.db")
    loose = _db(tmp_path, "loose.db")

    api.bootstrap(batched, reg)

    conn = store.connect(loose)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        for tid, _task in reg.items():
            store.register_task(conn, reg.path_of(tid))
    finally:
        conn.close()

    assert _dump(batched) == _dump(loose)


def test_halfway_failure_leaves_no_half_registered_library(tmp_path, monkeypatch):
    """第 k 条注册失败 ⇒ 一条任务都不落。半成品库比报错难查得多。"""
    reg = api.TaskRegistry()
    db = _db(tmp_path, "atomic.db")
    real = store.register_task
    seen: list[str] = []

    def explode_at_the_twelfth(conn, yaml_path, git_hash=None):
        seen.append(str(yaml_path))
        if len(seen) == 12:
            raise RuntimeError("模拟第 12 条注册时崩溃")
        return real(conn, yaml_path, git_hash)

    monkeypatch.setattr(store, "register_task", explode_at_the_twelfth)
    with pytest.raises(RuntimeError, match="第 12 条"):
        api.bootstrap(db, reg)
    assert len(seen) == 12, "应当先成功写入 11 条再失败"

    conn = store.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    finally:
        conn.close()


def test_bootstrap_is_idempotent(tmp_path):
    """重复导入不新增行、不报错——服务器每次起都会跑一遍。"""
    reg = api.TaskRegistry()
    db = _db(tmp_path, "twice.db")
    api.bootstrap(db, reg)
    first = _dump(db)
    api.bootstrap(db, reg)
    assert _dump(db) == first


def test_bootstrap_registers_every_task_on_disk(tmp_path):
    """防止"事务包住"顺手把循环写漏：题数必须等于磁盘上的 YAML 数。"""
    reg = api.TaskRegistry()
    db = _db(tmp_path, "count.db")
    api.bootstrap(db, reg)
    conn = store.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()
    assert n == len(list((ROOT / "tasks").glob("*.yaml")))
