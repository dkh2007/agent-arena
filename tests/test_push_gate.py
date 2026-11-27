"""推送闸门（scripts/push_gate.py）的判据测试。

## 为什么这份测试是补的、而且必须补

闸门从 2026-11-14 起就存在，但**一直没有测试**——它的判据从未被单独验证过，
只在每次 push 时"跑一下看是不是 CLEAN"。于是出两类问题都发现得晚：

- **漏**（该拦没拦）：没人验证过某个形状的密钥真的会被拦，只有"我记得我写了"；
- **误**（不该拦却拦）：2026-11-22 一次纯文档提交被拦下 37 处 SECRET，原因是
  裸前缀 `sk-` 命中了文件名 `task-authoring-log.yaml`（"ta|sk-"）。当天查了历史：
  `sk-<16+位>` 命中 **0**，确实是假阳性。

规则收紧后，两个方向都必须有判据钉住——否则下次"为了让闸门过"又会改松。

## 判据清单

| # | 输入 | 期望 | 防的是 |
|---|---|---|---|
| 1 | `task-authoring-log.yaml` | 无标签 | 假阳性回归（"ta**sk-**uthoring"） |
| 2 | `sk-` + 40 位 | SECRET | 真密钥漏拦 |
| 3 | `ghp_` + 36 位 | SECRET | 同上（GitHub PAT） |
| 4 | 私钥文件头（五个连字符 + `BEGIN`） | SECRET | 私钥被复制进仓库 |
| 5 | 本机绝对路径 / 用户名 | ABS_PATH / USERNAME | 匿名纪律（rules/协作约定） |
| 6 | 闸门源码自身 | 零标签 | self_audit：闸门不能拦自己的提交 |
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import push_gate as gate  # noqa: E402

NO_USER = "no-such-user-in-this-repo"


def _tags(text: str, words: tuple[str, ...] = ()) -> list[str]:
    return gate.tag_text(text, words, [], NO_USER)


# ── 1. 假阳性回归 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "docs/task-authoring-log.yaml",
    "docs/task-families.yaml",
    "tests/test_task_authoring.py",
    "risk-assessment（t a s k - 之后不是令牌体）",
    "task-id 与 task_version 都是普通字段",
])
def test_ordinary_task_words_are_not_secrets(text):
    """`task-` 里含 `sk-`：裸前缀匹配会把它当 OpenAI 密钥。2026-11-22 就是这么被拦的。"""
    assert "SECRET" not in _tags(text), f"{text!r} 被误判成密钥"


# ── 2–4. 真令牌必须拦住 ──────────────────────────────────────────────────

def test_openai_shaped_key_is_blocked():
    assert "SECRET" in _tags("OPENAI_API_KEY=" + "s" "k-" + "A1b2" * 12)


def test_github_pat_is_blocked():
    assert "SECRET" in _tags("token: " + "g" "hp_" + "Z9y8" * 9)


def test_private_key_header_is_blocked():
    assert "SECRET" in _tags("-----" "BEGIN RSA PRIVATE KEY-----")


def test_password_assignment_is_blocked():
    assert "SECRET" in _tags("PASS" "WORD=hunter2")


def test_bearer_token_is_blocked():
    """真凭据形状照拦：协议词 + 一段够长的随机串。"""
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    assert "SECRET" in _tags("Authorization: " + "Bea" "rer " + jwt)


def test_bearer_placeholder_in_docs_is_not_a_secret():
    """文档里教人怎么调接口，写的是占位符，不是钥匙。

    2026-11-27 实测：`Authorization: Bea rer <会话编号>` 被裸前缀判成泄露，
    整批提交被 BLOCKED。那与 2026-11-22 的 `sk-` 假阳性同类——前缀本身不构成凭据，
    必须连"够长的随机串"一起才算法。
    """
    assert "SECRET" not in _tags("Authorization: " + "Bea" "rer " + "<会话编号>")
    assert "SECRET" not in _tags("请求头用 " + "Bea" "rer " + "加会话编号")


# ── 5. 匿名纪律的两个面 ─────────────────────────────────────────────────

def test_local_absolute_path_is_flagged():
    paths, _ = gate.needles()
    assert "ABS_PATH" in gate.tag_text(f"见 {paths[0]}docs/x.md", (), paths, NO_USER)


def test_username_is_flagged():
    _, user = gate.needles()
    assert "USERNAME" in gate.tag_text(f"作者 {user}", (), [], user)


# ── 6. 自审：闸门不能拦自己 ─────────────────────────────────────────────

def test_gate_source_does_not_trip_its_own_gate():
    """若这条红了，说明有人往闸门源码里写了连续的前缀字面量——那会让它永远拦自己的提交。"""
    words = gate.forbidden_words()
    paths, user = gate.needles()
    assert gate.self_audit(words, paths, user) == [], (
        "闸门源码命中了它自己的规则；把字面量拆成相邻字符串（'s' 'k-'）"
    )
