"""试用包（B-47）的约束测试：发出去的那一份里不能有答案，也不能跑不起来。

三条硬约束：

1. **不发答案** —— `rubric_gate` / `rubric_credit` / `scoring` / `derivation` /
   `test_inputs.expected` / `trace_requirements` 一律裁掉。整份发出去，好奇的同学
   翻一眼就知道该写什么、第几次调用会失败，这轮收到的就不是"他做题的过程"了。
2. **裁剪不改行为** —— `tools` 与 `fault_injection` 一个字不动，否则 mock 回放的数据
   和他看到的题面不一致，报告会指控他"取错了数"。
3. **解压就能跑** —— 谁也不想让同学先 `pip install -e .` 再读一段 README。
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import make_trial_pack as pack  # noqa: E402
from arena import lab  # noqa: E402
from arena import lab  # noqa: E402
import yaml  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pack") / "trial.zip"
    return pack.build(out)


def test_pack_ships_no_judging_fields(built):
    with zipfile.ZipFile(built) as zf:
        raw = zf.read(f"{pack.PACK_DIR}/tasks/{pack.TRIAL_TASK}.yaml").decode("utf-8")
    task = yaml.safe_load(raw)
    for key in pack.DROP_TOP:
        assert key not in task, f"{key} 是判分口径，不能发给同学"
    assert "expected" not in (task.get("test_inputs") or {}), "期望值就是答案"


def test_pack_keeps_the_parts_the_mock_needs(built):
    """裁过头的判据：mock 服务只认三块，少一块就跑不出题。"""
    with zipfile.ZipFile(built) as zf:
        task = yaml.safe_load(
            zf.read(f"{pack.PACK_DIR}/tasks/{pack.TRIAL_TASK}.yaml").decode("utf-8")
        )
    assert task["tools"], "工具清单没了，学习者没有可调的东西"
    assert task["fault_injection"], "故障注入没了，这题就没有『失败重试』可判"
    assert (task.get("test_inputs") or {}).get("orders_fixture"), "数据夹具没了，取不到数"

    source = yaml.safe_load((ROOT / "tasks" / f"{pack.TRIAL_TASK}.yaml").read_text(encoding="utf-8"))
    for key in ("tools", "fault_injection"):
        assert task[key] == source[key], f"{key} 被裁剪改动了——回放的数据会和他看到的不一致"


def test_pack_readme_stops_before_the_maintainer_part(built):
    with zipfile.ZipFile(built) as zf:
        readme = zf.read(f"{pack.PACK_DIR}/README.md").decode("utf-8")
    assert "双击 `start.bat`" in readme, "同学第一眼要知道怎么开始"
    assert "维护者侧" not in readme
    assert "curl -X POST" not in readme, "维护者命令不该出现在同学那一页"


def test_pack_has_no_caches_or_tests(built):
    with zipfile.ZipFile(built) as zf:
        names = zf.namelist()
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if "/tests/" in n]
    assert f"{pack.PACK_DIR}/scripts/lab.py" in names
    assert f"{pack.PACK_DIR}/start.bat" in names
    assert f"{pack.PACK_DIR}/start.sh" in names


def test_pack_is_reproducible(built, tmp_path):
    """同样的内容打两次，sha 要一样——否则「这份包对应哪个提交」说不清。"""
    again = pack.build(tmp_path / "again.zip")
    assert again.read_bytes() == built.read_bytes()


def test_secret_scan_catches_a_real_key_but_not_the_word_task():
    ok = [("a.py", b'x = "task-id"; y = os.environ["API_KEY"]')]
    assert pack._forbidden_hits(ok) == [], "task- / API_KEY 是正常代码，不该拦"

    # 假 key 用拼的：写成一个完整字面量，连本仓的推送闸门都会拦（真踩过）
    fake = "sk-" + "a" * 20
    bad = [("b.py", f'KEY = "{fake}"'.encode())]
    hits = pack._forbidden_hits(bad)
    assert hits and "API key" in hits[0]

    path = [("c.py", ('p = "C:' + "\\\\Users\\\\" + 'someone\\\\Desktop"').encode())]
    assert pack._forbidden_hits(path), "本机绝对路径要拦"


def test_pack_runs_after_unzip(built):
    """真的解压、真的起服务、真的转提交。

    这是整件事唯一的验收点：发给同学的包，他双击之后必须能跑完整条链。
    """
    message = pack.verify(built)
    assert message.startswith("OK"), message


def _run_try(root: Path, base: str, mode: str | None, work: Path) -> None:
    args = [sys.executable, str(root / "try.py"), base]
    if mode:
        args += ["--mode", mode]
    done = subprocess.run(args, capture_output=True, text=True, timeout=120, cwd=str(work))
    assert done.returncode == 0, f"try.py 退出 {done.returncode}：{done.stderr[-400:]}"


def test_try_script_stands_in_for_an_agent(built, tmp_path):
    """没有 Agent 的人也要能拿到一份**有内容的**报告。

    判据不是「脚本跑通了」，是两头都对：故意写错的那次必须被判出来（且不只一条），
    改对之后必须满分。少了任何一头，这个脚本就只是装饰。
    """
    import threading

    from arena import lab, store, submission_api as api

    with zipfile.ZipFile(built) as zf:
        zf.extractall(tmp_path)
    root = tmp_path / pack.PACK_DIR
    task = yaml.safe_load((ROOT / "tasks" / f"{pack.TRIAL_TASK}.yaml").read_text(encoding="utf-8"))
    registry = api.TaskRegistry(ROOT / "tasks")

    def one_round(mode: str | None) -> dict:
        work = tmp_path / f"round-{mode or 'naive'}"
        work.mkdir()
        log_path = work / "calls.jsonl"
        call_log = lab.CallLog(log_path)
        server, _ = lab.serve(task, port=0, on_call=call_log)
        base = "http://%s:%d" % server.server_address[:2]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            _run_try(root, base, mode, work)
        finally:
            server.shutdown()
            server.server_close()
            call_log.close()
        calls, artifacts = lab.read_call_log(log_path)
        payload = lab.build_submission(calls, artifacts, task, user_id="u-try")
        db = work / "try.db"

        def conn_factory():
            return store.connect(db)

        api.bootstrap(db, registry)
        return api.submit(payload, registry=registry, conn_factory=conn_factory)

    naive = one_round(None)
    goods = one_round("ok")

    assert naive["score"]["ratio"] < 1.0, "故意写错的脚本竟然满分——那报告里就没东西可看了"
    failed = [a["id"] for a in naive["assertions"] if a["state"] == "fail"]
    assert len(failed) >= 2, f"只判出一条问题（{failed}），演示效果太弱"
    assert goods["score"]["ratio"] == 1.0, [
        (a["id"], a["state"]) for a in goods["assertions"]
    ]
