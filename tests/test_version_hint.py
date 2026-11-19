"""B 口径：只加一句"有修改"提示，不改判定、不新增存储。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from arena import version_hint as vh


class _Out:
    def __init__(self, rc, stdout):
        self.returncode = rc
        self.stdout = stdout


def _runner(rc, stdout):
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return _Out(rc, stdout)
    return run, calls


def test_dirty_tasks_returns_exactly_one_hint():
    run, calls = _runner(0, " M tasks/dbg-001-order-report.yaml\n")
    hint = vh.workspace_dirty_hint(Path("/repo/tasks"), run=run)
    assert hint == vh.HINT
    assert "\n" not in hint.strip(), "提示必须是一句话，不是一份报告"
    # 检测范围只限 tasks/（Path 会按平台规范化分隔符，所以比较 Path 对象）
    assert Path(calls[0][0][-1]) == Path("/repo/tasks"), calls[0][0]


def test_clean_returns_none():
    run, _ = _runner(0, "")
    assert vh.workspace_dirty_hint(Path("/repo/tasks"), run=run) is None


def test_untracked_task_counts_as_dirty():
    run, _ = _runner(0, "?? tasks/new-task.yaml\n")
    assert vh.workspace_dirty_hint(Path("/repo/tasks"), run=run) == vh.HINT


def test_changes_outside_tasks_do_not_trigger():
    """命令只查 tasks/ 路径；git 返回空说明别处的改动不被算进来。"""
    run, calls = _runner(0, "")
    assert vh.workspace_dirty_hint(Path("/repo/tasks"), run=run) is None
    assert "tasks" in calls[0][0][-1]


def test_git_failure_is_not_a_failure():
    """查不了版本没有资格把评测判坏。"""
    run, _ = _runner(128, "fatal: not a git repository")
    assert vh.workspace_dirty_hint(Path("/repo/tasks"), run=run) is None

    def boom(*a, **k):
        raise OSError("no git")
    assert vh.workspace_dirty_hint(Path("/repo/tasks"), run=boom) is None


def test_hint_never_touches_the_hash_pattern():
    """提示里不能出现会被误当哈希的东西；哈希仍由 repo_head 独占。"""
    import re
    assert not re.search(r"\b[0-9a-f]{7,40}\b", vh.HINT)
