"""变异执行器（`tools/mutations/_runner.py`）的判据。

这一层的价值全在"判定"两个字上：**只有 `rc == 1` 才算抓住**。判定写错不会报错，
只会让变异脚本打印一行 `DETECTED` 告诉你"测试在管这事"，而实际是那条用例根本没跑
（round-89 M14：测试改名后节点不存在，pytest 退出码 4，旧判定当成了抓住）。

所以这里锁三件事：

1. 退出码 → 判定的映射（纯函数，不跑子进程）；
2. 所有变异脚本都**不再**自己写一份判定（改一个口径要改 19 处，迟早分叉）；
3. 执行器自己的 `--self-test` 是绿的——它包含"节点名写错必须报 INVALID"这一条，
   也就是旧规则假绿的那条路。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MUT_DIR = ROOT / "tools" / "mutations"

# `_runner` 在 import 时 chdir 到仓根（脚本要能被打包机以外的地方调用）。
# 这里先把 cwd 存下来，import 完再放回去——不然这条 import 会悄悄改掉别的测试的
# 相对路径基准，而且是那种"单独跑绿、全量跑红"的错。
_CWD = os.getcwd()
sys.path.insert(0, str(MUT_DIR))
import _runner as runner  # noqa: E402  （必须在 sys.path 之后）
os.chdir(_CWD)


# ── 1. 判定映射 ─────────────────────────────────────────────────────────────

def test_only_exit_code_1_counts_as_detected():
    assert runner.judge(1)[0] == runner.DETECTED


@pytest.mark.parametrize("rc", [2, 3, 4, 5])
def test_other_nonzero_codes_are_invalid_not_detected(rc):
    """2=中断 / 3=内部错 / 4=用法错（节点名写错）/ 5=没收集到用例。

    这四条里 **4 曾经被判成 DETECTED**——测试改名之后就出现假绿。这是本文件存在的理由。
    """
    verdict, detail = runner.judge(rc)
    assert verdict == runner.INVALID
    assert str(rc) in detail, "详情里要写出退出码，不然读的人不知道发生了什么"


def test_green_suite_is_missed_not_invalid():
    """`rc == 0` 是"变异没被测试管到"，不是"脚本写错"——两者处置不同。"""
    assert runner.judge(0)[0] == runner.MISSED


def test_invalid_detail_names_the_meaning_not_just_the_number():
    """退出码 4 的文案要能直接读懂：`用法错误（节点名可能写错）`。"""
    assert "节点名" in runner.judge(4)[1]


# ── 2. 入参归一：4 元组 / 5 元组 / 写错要给话说 ──────────────────────────────

def test_four_tuple_takes_the_default_path():
    items = runner._normalize([("x", "old", "new", "tests/t.py::test_a")], Path("src/a.py"))
    assert items == [("x", Path("src/a.py"), "old", "new", "tests/t.py::test_a")]


def test_five_tuple_carries_its_own_path():
    items = runner._normalize([("x", Path("src/b.py"), "old", "new", "tests/t.py::test_b")], None)
    assert items[0][1] == Path("src/b.py")


def test_four_tuple_without_default_path_is_an_error_not_a_guess():
    """没有默认路径时不许猜一个——猜错的后果是"改了别的文件"或"锚点没命中"。"""
    with pytest.raises(ValueError, match="default_path"):
        runner._normalize([("x", "old", "new", "tests/t.py::test_a")], None)


def test_wrong_arity_is_rejected():
    with pytest.raises(ValueError, match="4 或 5 元组"):
        runner._normalize([("x", "old", "new")], Path("src/a.py"))


# ── 3. 每个脚本都只用这一份判定 ─────────────────────────────────────────────

def _scripts() -> list[Path]:
    return sorted(MUT_DIR.glob("mutate_*.py"))


def test_there_are_scripts_to_check():
    assert len(_scripts()) >= 15, "找不到变异脚本？先确认路径"


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_script_delegates_judging_to_the_runner(path):
    """脚本里只许有"变异长什么样"，不许再有一份判定。

    判据是三个字符串都在：import 了执行器、把 MUTATIONS 交出去、**没有**自己的
    `DETECTED` / `MISSED` 字符串字面量产出逻辑（后者用"判定词汇只出现在说明文字里"
    没法机械判断，所以退一步锁前两条 + 下面那条"只有一处 def judge"）。
    """
    text = path.read_text(encoding="utf-8")
    assert "from _runner import run_mutations" in text, f"{path.name} 没接入统一执行器"
    assert "return run_mutations(" in text, f"{path.name} 的 main() 没交给执行器"


def test_judging_exists_in_exactly_one_place():
    """`def judge(` 只许出现在 `_runner.py`。

    哪天有人复制一份判到某个脚本里，口径就又有两处了——而两处口径的差别不会报错，
    只会在某次测试改名后给出相反的结论。
    """
    owners = [p.name for p in MUT_DIR.glob("*.py") if "def judge(" in p.read_text(encoding="utf-8")]
    assert owners == ["_runner.py"], f"判定实现出现在多处：{owners}"


def test_mastery_still_repeats_each_mutation():
    """掌握度那组是唯一需要连跑两遍的（用例本身会抖），别在统一时把这层丢了。"""
    text = (MUT_DIR / "mutate_mastery.py").read_text(encoding="utf-8")
    assert "repeats=2" in text


# ── 4. 执行器自查：真跑四条，含"节点名写错"那条 ──────────────────────────────

def test_runner_self_test_passes():
    """`--self-test` 里第四条就是旧规则假绿的那条路：节点名写错必须报 INVALID。

    它为什么要真跑：判定器是"用来判别人的"，自己在纸面上永远对。
    代价是四条子进程（约十秒），换来的是"判定器坏了会先在这里红"。
    """
    r = subprocess.run([sys.executable, "_runner.py", "--self-test"],
                       cwd=MUT_DIR, capture_output=True, text=True)
    assert "SELF_TEST_PASS=True" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0
