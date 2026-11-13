"""评审包生成器的约束测试。

这些用例守的不是"代码能跑"，而是**"评审包不会再变成旧版本"**这件事本身：
- 手工模板里的占位符必须全被填掉（否则外部 AI 会看到 {{AUTO_SCORES}} 这种残渣）
- 正文里的测试数必须等于仓库实际（这正是 v1 最大的失效点：写着 14 passed，实际 46）
- 手改生成物、仓库漂移、YAML 自相矛盾，三种情况都必须被抓
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import review_packet  # noqa: E402

PACKET = review_packet.DEFAULT_OUT

# TD-040：`--fast` 生成的包缺实测数据，会被 check() 判 NOT_RELEASABLE（这是刻意的）。
# 本文件多数用例要测的是 TAMPERED / STALE / 锚点冻结，与「可发布性」无关，
# 所以用一个全绿记录把可发布性这一维固定住，免得两件事互相干扰。
GREEN_LINE = "- 单元测试实测：当前工作区本地实测 **688 passed / 688 total**（测试构造）"
FAILED_LINE = "- 单元测试实测：当前工作区本地实测 **1 failed, 687 passed / 688 total**（测试构造）"


def write_green(out: Path, **kwargs) -> None:
    """写一份「记录全绿」的包：除可发布性外，其余行为与 fast 生成一致。

    总数取仓库真实收集数，不写死——写死会让「用例数对不上」变成测试自身的噪音，
    而那条检查（`test_test_count_matches_repo`）本来是专门守这个的。
    """
    n = review_packet._pytest_count()
    line = f"- 单元测试实测：当前工作区本地实测 **{n} passed / {n} total**（测试构造）"
    review_packet.write_packet(out, fast=True, test_result=line, **kwargs)


@pytest.fixture(scope="module")
def packet_text() -> str:
    assert PACKET.is_file(), "评审包不存在，先跑 src/arena/review_packet.py"
    return PACKET.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def meta(packet_text: str) -> str:
    return packet_text.partition("\n")[0]


@pytest.fixture(scope="module")
def body(packet_text: str) -> str:
    return packet_text.partition("\n")[2]


def test_no_placeholder_left(body: str):
    """占位符没填干净，外部 AI 会看到模板残渣——这类错误必须生成阶段就拦。"""
    assert "{{AUTO_" not in body


def test_fingerprint_matches_body(meta: str, body: str):
    """头部指纹必须等于正文实算值，否则"防手改"形同虚设。"""
    m = re.search(r"packet-sha256=([0-9a-f]{16})", meta)
    assert m, f"头部缺 packet-sha256：{meta}"
    assert m.group(1) == hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def test_snapshot_sha_recorded(meta: str):
    assert re.search(r"snapshot-sha=[0-9a-f]{4,40}", meta), "头部缺少快照 sha，无法追溯生成自哪个版本"


def test_test_count_matches_repo(body: str):
    """v1 的失效点回归锁：评审包里的测试数必须来自仓库现况。

    v1 手写"14 passed"、实际 46 —— 传给外部 AI 等于让它在评一个不存在的版本。
    这条断言把"数字漂移"变成红灯。
    """
    n = review_packet._pytest_count()
    assert f"**{n} 个用例被收集**" in body, f"评审包里的测试数与实际（{n}）不一致"
    # 生成器只做 collect-only，不许借 "passed" 字样把"数过"说成"跑过"
    assert " passed**" not in body


def test_score_table_reflects_real_run(body: str):
    """分数表必须是现跑的，不能是手抄的。抽查两条最有辨识度的行。"""
    assert "raw-empty" in body and "0.0000 (0/4)" in body
    assert "raw-perfect" in body and "1.0000 (4/4)" in body


def test_guard_rejects_forbidden():
    """从配置里取词，不在测试文件里写出真实校名——测试文件本身也要守匿名纪律。"""
    forbidden = review_packet.FORBIDDEN[0]
    with pytest.raises(review_packet.PacketError, match="匿名守卫"):
        review_packet._guard(f"这段文字里出现了{forbidden}")


def test_guard_passes_clean_text():
    review_packet._guard("这是一段干净的文字，只提到某高校和某大赛")  # 不抛异常即通过


def test_forbidden_list_is_not_empty():
    """守卫词表为空等于没有守卫，这种"静默失效"要单独锁。"""
    assert review_packet.FORBIDDEN and all(review_packet.FORBIDDEN)


def test_build_rejects_forbidden_in_manual(tmp_path, monkeypatch):
    """守卫必须真的接在生成流程里。

    只测 _guard() 函数是不够的：把 build() 里那行 `_guard(body)` 删掉，
    _guard 的用例照样全绿，而禁用词会直接印进对外文档。
    """
    forbidden = review_packet.FORBIDDEN[0]
    # Q7 起 build() 先过核心事实校验，极简模板也要带一节合法的硬事实，
    # 否则会先抛「找不到第 3 节」，就测不到匿名守卫本身了。
    facts = " ".join(
        token for tokens in review_packet._core_fact_tokens().values() for token in tokens
    )
    manual = tmp_path / "manual.md"
    manual.write_text(
        "# 标题\n"
        + "".join(f"{p}\n" for p in review_packet.PLACEHOLDERS)
        + f"\n## 3. 决策\n{facts}\n这段里出现了{forbidden}\n"
        + "\n## 4. 快照\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(review_packet, "MANUAL", manual)

    with pytest.raises(review_packet.PacketError, match="匿名守卫"):
        review_packet.build(fast=True)


def test_core_facts_accept_current_manual():
    """Q7 基线：现役手工段的硬事实必须全部对得上。"""
    review_packet._check_core_facts(review_packet.MANUAL.read_text(encoding="utf-8"))


def test_core_facts_reject_wrong_scoring_mode(tmp_path, monkeypatch):
    """Q7 验证动作 1：把「两段式 gate / credit」写成「单段加权评分」必须被拦。"""
    bad = tmp_path / "manual.md"
    text = review_packet.MANUAL.read_text(encoding="utf-8")
    assert "两段式 gate / credit" in text
    bad.write_text(text.replace("两段式 gate / credit", "单段加权评分", 1), encoding="utf-8")
    monkeypatch.setattr(review_packet, "MANUAL", bad)

    with pytest.raises(review_packet.PacketError, match="核心事实不匹配"):
        review_packet.build(fast=True)


def test_core_facts_allow_rewording(tmp_path, monkeypatch):
    """Q7 验证动作 2：只改判断句措辞、不动硬事实，校验必须放行。"""
    text = review_packet.MANUAL.read_text(encoding="utf-8")
    assert "不再重议" in text
    reworded = text.replace("不再重议", "不再讨论", 1).replace(
        "不是抽认卡", "而不是记忆卡片", 1
    )
    review_packet._check_core_facts(reworded)
    bad = tmp_path / "manual.md"
    bad.write_text(reworded, encoding="utf-8")
    monkeypatch.setattr(review_packet, "MANUAL", bad)
    body, _, _ = review_packet.build(fast=True)
    assert "不再讨论" in body


def test_core_fact_tokens_derive_from_yaml(tmp_path, monkeypatch):
    """白名单不能是写死的结论：改 YAML 分母，期望标记必须跟着变。"""
    task = yaml.safe_load(review_packet.TASK.read_text(encoding="utf-8"))
    task["rubric_credit"][0]["weight"] = 7
    bad = tmp_path / "task.yaml"
    bad.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(review_packet, "TASK", bad)
    tokens = review_packet._core_fact_tokens()
    assert "分母恒为 9" in tokens["credit 分母"]


def test_yaml_contradiction_raises(tmp_path, monkeypatch):
    """YAML 自相矛盾时不能印到对外文档里，必须生成阶段就炸。"""
    task = yaml.safe_load(review_packet.TASK.read_text(encoding="utf-8"))
    task["scoring"]["score"]["credit"]["total_weight"] = 99
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")

    monkeypatch.setattr(review_packet, "TASK", bad)
    with pytest.raises(review_packet.PacketError, match="自相矛盾"):
        review_packet._task_struct()


def test_check_missing_file_returns_1(tmp_path):
    assert review_packet.check(tmp_path / "nope.md") == 1


def test_check_distinguishes_ok_tampered_stale(tmp_path, monkeypatch):
    """三种状态必须可区分，这是整套方案的意义所在。"""
    out = tmp_path / "packet.md"
    write_green(out)

    # 1) 刚生成 → 一致
    assert review_packet.check(out) == 0

    # 2) 手改正文 → TAMPERED（不依赖重新生成就能抓）
    original = out.read_text(encoding="utf-8")
    out.write_text(original.replace("# 外部评审包", "# 外部评审包（被人改过）", 1), encoding="utf-8")
    assert review_packet.check(out) == 1

    # 3) 复原后仓库漂移 → STALE
    out.write_text(original, encoding="utf-8")
    monkeypatch.setattr(review_packet, "_env", lambda: "依赖已变（模拟）")
    assert review_packet.check(out) == 1


def test_ok_reports_anchor_behind(tmp_path, monkeypatch, capsys):
    """内容绿但锚点陈旧时必须有信息级提示（round-6 ②-1 唯一成立的部分）。

    不阻塞是刻意的：打包提交必然让锚点落后 1 个，若阻塞就等于永久假阳性。
    """
    out = tmp_path / "packet.md"
    write_green(out)
    real_run = review_packet._run

    def three_commits_behind(cmd: list[str]) -> str:
        if cmd[:2] == ["git", "rev-list"]:
            return "3\n"
        return real_run(cmd)

    monkeypatch.setattr(review_packet, "_run", three_commits_behind)
    assert review_packet.check(out) == 0
    printed = capsys.readouterr().out
    assert "OK:" in printed
    assert "落后当前 HEAD 3 个提交" in printed
    assert "不阻塞" in printed


def test_check_detects_score_drift(tmp_path, monkeypatch):
    """可执行反证：round-6 ②-1 说「锚点冻结会让分数表永远是旧版本」——不成立。

    build(ref) 的 ref 只冻结 `_snapshot`；`_scores` / `_task_struct` / `_env` /
    `_pytest_count` 一律取当前工作区。评分口径一变，`--check` 立刻 STALE。
    """
    out = tmp_path / "packet.md"
    write_green(out)
    monkeypatch.setattr(
        review_packet, "_scores", lambda: "| 夹具 | 得分 |\n|---|---|\n| fake | 9.9999 |"
    )
    assert review_packet.check(out) == 1


def test_score_table_matches_report_core(body: str):
    """端到端对拍：表格数字必须等于 build_report() 直接算出的值。

    `test_score_is_monotonic_across_fixtures` 守的是 DTO 核心；这条守的是
    「DTO → --fixture all 打印 → SCORE_RE 解析 → Markdown 表格」这段搬运，
    防止打印格式或正则列序改错后，表格数字语义错位却无人发现（round-6 ①-2 的真盲区）。
    """
    import json

    from arena import diagnosis_report

    task = diagnosis_report.load_task(review_packet.TASK)
    rows = {
        line.split("|")[1].strip().strip("`"): [c.strip() for c in line.split("|")]
        for line in body.splitlines()
        if line.startswith("| `raw-")
    }
    assert rows, "评审包里没有夹具表格行"

    for path in sorted((ROOT / "fixtures").glob("raw-*.json")):
        report = diagnosis_report.build_report(task, json.loads(path.read_text(encoding="utf-8")))
        score = report["score"]
        cells = rows[path.stem]
        assert cells[3].startswith(f"{score['ratio']:.4f}"), f"{path.stem} 的 ratio 列与 DTO 不符"
        assert cells[4] == str(score["counterfactual_credit"]), f"{path.stem} 的反事实得分列与 DTO 不符"


def test_check_survives_own_packaging_commit(tmp_path, monkeypatch):
    """评审包自己进下一个提交后 HEAD 变了，但内容没变，不应误报过期。

    这是"锚点冻结"的回归锁：若 check() 改用 HEAD 重算（而不是文件头记录的
    snapshot-sha），本用例会红。那种实现会让每次打包提交后 --check 都假报
    STALE，用户几轮之后就学会忽略它，整套检测等于作废。
    """
    out = tmp_path / "packet.md"
    write_green(out)

    meta = out.read_text(encoding="utf-8").partition("\n")[0]
    anchor = re.search(r"snapshot-sha=([0-9a-f]+)", meta).group(1)
    new_sha = "deadbee"
    real_run = review_packet._run
    frozen_log = review_packet._git_log(anchor)

    def after_packaging_commit(cmd: list[str]) -> str:
        # 只让 git 的 HEAD 查询"看到"新提交；锚点查询与 python 子进程照常
        if cmd and cmd[0] == "git":
            if cmd[:3] == ["git", "rev-parse", "--short"] and cmd[-1] == "HEAD":
                return new_sha + "\n"
            if cmd[:2] == ["git", "log"] and cmd[-1] == "HEAD":
                return frozen_log + f"\n- `{new_sha}` chore: 打包评审包（2026-11-07）"
        return real_run(cmd)

    monkeypatch.setattr(review_packet, "_run", after_packaging_commit)
    assert review_packet.check(out) == 0


def test_check_rejects_broken_anchor(tmp_path):
    """文件头的锚点被改坏时不能静默按当前 HEAD 蒙混过关。"""
    out = tmp_path / "packet.md"
    write_green(out)
    text = out.read_text(encoding="utf-8")
    meta, _, body = text.partition("\n")
    out.write_text(
        meta.replace(re.search(r"snapshot-sha=[0-9a-f]+", meta).group(0), "snapshot-sha=0000000")
        + "\n" + body,
        encoding="utf-8",
    )
    assert review_packet.check(out) == 1


def test_packet_written_as_lf(tmp_path):
    """生成物统一 LF，避免 Windows 下 CRLF 造成整文件 diff 噪音。"""
    out = tmp_path / "packet.md"
    write_green(out)
    assert b"\r\n" not in out.read_bytes()


def test_manual_must_be_present(monkeypatch, tmp_path):
    monkeypatch.setattr(review_packet, "MANUAL", tmp_path / "gone.md")
    with pytest.raises(review_packet.PacketError, match="缺少手工模板"):
        review_packet.build(fast=True)


# ---------------------------------------------------------------------------
# Q9：实测通过率 + --fast
# ---------------------------------------------------------------------------


def test_fast_mode_never_runs_pytest(monkeypatch):
    """--fast 跳过测试**执行**，但仍允许 collect-only 计数（包里的用例数要靠它）。"""
    real_run = review_packet.subprocess.run

    def guard(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "pytest" in joined and "--collect-only" not in joined:
            raise AssertionError("--fast 模式不该执行测试，只允许 collect-only 计数")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(review_packet.subprocess, "run", guard)
    line = review_packet._pytest_result(fast=True, anchor="abc1234")
    assert line == review_packet.FAST_TEST_LINE
    body, _, _ = review_packet.build(fast=True)
    assert review_packet.FAST_TEST_LINE in body
    assert "total**" not in body


def test_pytest_summary_reports_failure_honestly():
    """Q9 验证动作 2：弄挂一个用例时，结果行必须如实带 failed，不能强行全绿。"""
    summary, total = review_packet._parse_pytest_summary(
        "FAILED tests/test_x.py::test_a - assert 1 == 2\n1 failed, 71 passed in 28.75s"
    )
    assert summary.startswith("1 failed"), summary
    assert "71 passed" in summary
    assert total == 72
    assert "全绿" not in summary


def test_pytest_summary_handles_green_and_errors():
    assert review_packet._parse_pytest_summary("72 passed in 27.10s") == ("72 passed", 72)
    summary, total = review_packet._parse_pytest_summary("2 errors, 70 passed in 3.1s")
    assert summary.startswith("2 errors") and total == 72


def test_pytest_summary_rejects_unparseable():
    """解析不出结果必须拒绝生成，不能悄悄印一行空的「实测」。"""
    with pytest.raises(review_packet.PacketError, match="未能解析"):
        review_packet._parse_pytest_summary("no tests ran in 0.01s")
    with pytest.raises(review_packet.PacketError, match="未能解析"):
        review_packet._parse_pytest_summary("")


def test_default_mode_formats_real_run(monkeypatch):
    """默认模式：把跑命令替身成固定输出，验证整行措辞绑定版本与环境、不宣称普适正确。"""
    class FakeProc:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git":
            return FakeProc("abc1234\n")
        return FakeProc("72 passed in 27.10s")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(review_packet.subprocess, "run", fake_run)
    line = review_packet._pytest_result(fast=False, anchor="abc1234")
    assert "72 passed / 72 total" in line
    assert "本地 HEAD" in line and "生成锚点 `abc1234`" in line
    assert "当前工作区" in line and "不宣称普适正确" in line
    assert "锚点实测" not in line  # 测试跑的是工作区，写成锚点实测就是过度声称


def test_build_inside_pytest_refuses_default_mode(monkeypatch):
    """防递归护栏：测试里写裸 build() 会无限起 pytest，必须立刻报可操作的错。

    这不是假想——本轮改 Q9 时真踩过一次，全量测试被超时掐断。
    这里同时拦住「真去启动 pytest」：护栏一旦被删，变异测试要**立刻变红**，
    而不是再套一层 pytest 把整轮跑挂死（那样是无效变异）。
    """
    real_run = review_packet.subprocess.run

    def guard(cmd, *args, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if "pytest" in joined and "--collect-only" not in joined:
            raise AssertionError("护栏失效：竟在 pytest 进程里又启动了一轮 pytest")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(review_packet.subprocess, "run", guard)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y")
    with pytest.raises(review_packet.PacketError, match="递归启动 pytest"):
        review_packet.build()


def test_check_does_not_rerun_pytest(tmp_path, monkeypatch):
    """--check 重放包里已记录的那行，不重跑测试（两次执行/偶发波动会造成假 STALE）。"""
    out = tmp_path / "packet.md"
    write_green(out)

    def bomb(*args, **kwargs):
        raise AssertionError("--check 不该重新执行 pytest")

    monkeypatch.setattr(review_packet, "_pytest_result", bomb)
    assert review_packet.check(out) == 0


# --- TD-036：生成路径与执行路径共用评分策略对账（review-response-v1.md §41） ---

TD036_POLICY_DRIFTS = [
    ("gate.disqualified_if", ("scoring", "score", "gate", "disqualified_if"), "never"),
    ("gate.disqualify_ratio", ("scoring", "score", "gate", "disqualify_ratio"), 0.3),
    ("gate.on_unscored", ("scoring", "score", "gate", "on_unscored"), "disqualify"),
    ("credit.denominator", ("scoring", "score", "credit", "denominator"), "sum_of_all"),
    ("credit.contribution", ("scoring", "score", "credit", "contribution"), {"pass": "weight"}),
]


def _td036_task_with_drift(tmp_path, path, value):
    task = yaml.safe_load(review_packet.TASK.read_text(encoding="utf-8"))
    node = task
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    bad = tmp_path / "drift.yaml"
    bad.write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    return bad


def test_td036_current_task_passes_policy_check():
    """现役 YAML 与 builder 常量一致：生成器不该被自己的对账挡住。"""
    assert "disqualify_ratio=0" in review_packet._task_struct()


@pytest.mark.parametrize(
    "path,value",
    [(d[1], d[2]) for d in TD036_POLICY_DRIFTS],
    ids=[d[0] for d in TD036_POLICY_DRIFTS],
)
def test_td036_generator_rejects_drifted_policy(tmp_path, monkeypatch, path, value):
    """改任一影响分数的声明 → 生成器拒绝，且错误里点名具体路径。"""
    monkeypatch.setattr(review_packet, "TASK", _td036_task_with_drift(tmp_path, path, value))
    with pytest.raises(review_packet.PacketError, match="评分策略漂移") as exc:
        review_packet._task_struct()
    assert ".".join(path) in str(exc.value)


def test_td036_write_packet_refuses_before_writing(tmp_path, monkeypatch):
    """拒绝必须发生在写盘之前：错值不能落到任何对外文件里。"""
    monkeypatch.setattr(
        review_packet, "TASK",
        _td036_task_with_drift(
            tmp_path, ("scoring", "score", "gate", "disqualify_ratio"), 0.3
        ),
    )
    out = tmp_path / "packet.md"
    with pytest.raises(review_packet.PacketError, match="评分策略漂移"):
        write_green(out)
    assert not out.exists()


def test_td036_generator_follows_builder_constants(monkeypatch):
    """改 builder 常量 → 生成器跟着拒绝：证明没有在生成器里复制一份 0。"""
    from arena import diagnosis_report

    monkeypatch.setattr(diagnosis_report, "GATE_DISQUALIFY_RATIO", 0.3)
    with pytest.raises(review_packet.PacketError, match="disqualify_ratio"):
        review_packet._task_struct()


def test_td036_check_fails_on_policy_drift(tmp_path, monkeypatch, capsys):
    """策略漂移时 --check 返回 1 并说明原因，不把错包判成 OK。

    用自建的**可发布**包，不读 `DEFAULT_OUT`：否则这条用例会依赖默认包当前的
    记录状态（`--fast` 生成的默认包会被判 2），把「测策略漂移」变成「测包状态」，
    还会让生成评审包的过程与它互相卡住。
    """
    out = tmp_path / "p.md"
    write_green(out)
    monkeypatch.setattr(
        review_packet, "TASK",
        _td036_task_with_drift(
            tmp_path, ("scoring", "score", "gate", "on_unscored"), "disqualify"
        ),
    )
    assert review_packet.check(out) == 1
    assert "评分策略漂移" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# TD-040：包的「可发布性」与「记录是否仍成立」
#
# 对比试验（scripts/td040_compare.py）的结论：
#   - 候选 B「锚点落后 N 个提交」被否：最好也只有 2/4 正确，且它对测试红绿
#     没有因果关系，N=1 在正常发布的包上误报、N≥2 放过真正该拦的样本。
#   - 候选 A「生成门」保留：4/4，成本只是解析一行文本。
#   - 另补候选 C「--check --full 重跑复核」：A 拦不住「生成后才变红」。
#
# 下面是 A 与 C 的锁。A 是**预防**（不让带失败的包生成），C 是**检测**（事后复核）。
# ---------------------------------------------------------------------------


def test_test_gate_classifies_three_cases():
    """生成与校验共用同一判定，不许两套口径。"""
    assert review_packet._test_gate(GREEN_LINE) == (True, "")
    ok, why = review_packet._test_gate(FAILED_LINE)
    assert ok is False and "1 条失败" in why, why
    ok, why = review_packet._test_gate(review_packet.FAST_TEST_LINE)
    assert ok is False and "无实测数据" in why, why
    ok, why = review_packet._test_gate("")
    assert ok is False and "无实测数据" in why, why


def test_failed_record_puts_warning_into_body(tmp_path):
    """警告必须写进**正文**：终端日志会滚掉，文件会被发给外部 AI 和评委。"""
    out = tmp_path / "p.md"
    review_packet.write_packet(out, fast=True, test_result=FAILED_LINE)
    text = out.read_text(encoding="utf-8")
    assert "本包不可对外发布" in text
    assert "1 条失败" in text


def test_green_record_has_no_warning(tmp_path):
    """反向锁：全绿时正文不能出现警告，否则每次发布都带一条噪音。"""
    out = tmp_path / "p.md"
    write_green(out)
    assert "本包不可对外发布" not in out.read_text(encoding="utf-8")


def test_check_blocks_failed_record(tmp_path):
    """带失败记录的包 check 必须返回 2（NOT_RELEASABLE），不能是 OK。"""
    out = tmp_path / "p.md"
    review_packet.write_packet(out, fast=True, test_result=FAILED_LINE)
    assert review_packet.check(out) == 2


def test_check_blocks_fast_packet(tmp_path):
    """`--fast` 包没有实测数据，同样不可发布。"""
    out = tmp_path / "p.md"
    review_packet.write_packet(out, fast=True)
    assert review_packet.check(out) == 2


def test_block_precedes_stale(tmp_path, monkeypatch):
    """顺序锁：可发布性判定必须先于陈旧度判定。

    若反了，一个带失败记录的包会被报成 STALE，把人引去「重新生成」——
    而重新生成出来的仍然是带失败的包，问题原地打转。
    """
    out = tmp_path / "p.md"
    review_packet.write_packet(out, fast=True, test_result=FAILED_LINE)
    monkeypatch.setattr(review_packet, "_env", lambda: "依赖已变（模拟）")
    assert review_packet.check(out) == 2, "不可发布必须优先于陈旧"


def test_historical_case_487ff6f_must_be_blocked():
    """真实案例回归锁。

    `487ff6f` 那次生成的包，实测行诚实写着 1 failed，当时 `--check` 判 OK
    （已用 `git worktree` 在原时刻复现）。这是 TD-040 的立案证据，
    本条确保它以后不再被判绿。
    """
    line = ("- 单元测试实测：当前工作区本地实测 **1 failed, 666 passed / 667 total**"
            "（`pytest tests/ -q`；本地 HEAD `1a9e79a`，生成锚点 `1a9e79a`）")
    ok, why = review_packet._test_gate(line)
    assert ok is False and "1 条失败" in why


def _fake_pytest(monkeypatch, *, failed: int = 0, total_delta: int = 0) -> None:
    """伪造 pytest **执行结果**，其余命令（git、--collect-only）透传真实执行。

    - 只拦不带 `--collect-only` 的 pytest 调用：用例收集数是包内容的一部分，
      伪造它会让「内容一致性」先失败，测不到 full 分支。
    - 必须临时摘掉 `PYTEST_CURRENT_TEST`：`_verify_recorded_run` 用它判断
      「我是不是在 pytest 进程里」并据此拒绝重跑（防递归）。测 full 路径时
      我们本来就不打算真跑 pytest，摘掉是安全的；递归防护本身由
      `test_full_refuses_inside_pytest` 单独覆盖。
    - 尾部数字按真实收集数生成，用 `total_delta` 制造「用例数漂移」。
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    real_run = review_packet.subprocess.run
    n = review_packet._pytest_count() + total_delta  # patch 前取真实值
    tail = (f"{failed} failed, {n - failed} passed in 1.0s\n" if failed
            else f"{n} passed in 1.0s\n")

    def fake(cmd, **kwargs):  # noqa: ANN001 - 需与 subprocess.run 同形
        if any("pytest" in str(part) for part in cmd) and "--collect-only" not in cmd:
            return review_packet.subprocess.CompletedProcess(cmd, 1 if failed else 0, tail, "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(review_packet.subprocess, "run", fake)


def test_full_check_detects_drift(tmp_path, monkeypatch, capsys):
    """C 的核心：包说全绿、实际变红，只有重跑看得见。"""
    out = tmp_path / "p.md"
    write_green(out)
    _fake_pytest(monkeypatch, failed=3)
    assert review_packet.check(out, full=True) == 3
    printed = capsys.readouterr().out
    assert "TEST_DRIFT" in printed
    assert "对外材料与实际相反" in printed


def test_full_check_passes_when_record_still_holds(tmp_path, monkeypatch, capsys):
    """反向锁：记录仍成立时必须给 0，否则 --full 就成了永红。"""
    out = tmp_path / "p.md"
    write_green(out)
    _fake_pytest(monkeypatch)
    assert review_packet.check(out, full=True) == 0
    assert "OK_FULL" in capsys.readouterr().out


def test_full_check_flags_count_drift(tmp_path, monkeypatch, capsys):
    """用例数变了也算漂移：包里的数就对不上了。"""
    out = tmp_path / "p.md"
    write_green(out)
    _fake_pytest(monkeypatch, total_delta=5)
    assert review_packet.check(out, full=True) == 3
    assert "TEST_DRIFT" in capsys.readouterr().out


def test_full_refuses_inside_pytest(capsys):
    """在 pytest 进程内重跑 pytest 会递归，必须立刻拒绝而不是挂死。"""
    assert review_packet._verify_recorded_run(GREEN_LINE) == 1
    assert "递归" in capsys.readouterr().out


def test_full_requires_check_flag(capsys):
    """`--full` 单独用是误用：生成时本来就跑测试。"""
    assert review_packet.main(["--full"]) == 1
    assert "--full 只用于 --check" in capsys.readouterr().err
