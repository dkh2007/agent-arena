"""重放的前提：**返回体能不能只凭「任务数据 + 有序参数序列」重建**。

## 为什么这一层值得单独一个文件

B-20a 在讨论「答案是不是从数据里算出来的」。四条路线里，三条（乙 / 丙 / 丁）都建立在
同一个前提上：平台手里有请求参数，数据在任务 YAML 里，mock 是确定性的，所以
**事后能把「当时返回了什么」重新算出来**——于是不必把返回体全量存下来。

这个前提此前没有一条测试。它的失效方式全是安静的：

| 失效 | 后果 |
|---|---|
| 有人让 mock 依赖时间 / 随机数 | 重建开始偶发不一致，而"不存 body"的决定不会报错 |
| 有人把 fixture 挪出 `tasks/*.yaml`（挪进外部文件或数据库） | `task_git_hash` 不再覆盖数据，数据版本悄悄失锚 |
| 有人以为「冻住单次请求的参数」就够 | 故障额度按 `(tool, match)` 计数，同一参数第 1 次与第 2 次结果不同 |

## 三条判据

1. **可重建**：真跑一遍（记录实际转发的 body），再用新服务按同样顺序重放 ⇒ 逐条逐字节一致；
2. **数据敏感**：只改任务数据里的一条金额 ⇒ 返回体必须变（否则"冻结数据版本"毫无意义）；
3. **位置敏感**：同一组参数连调两次，结果可以不同 ⇒ 重建的对象是**有序序列**，
   不是"单次请求的指纹"。

第 3 条是这三条里唯一会被人踩的：它让「存返回体」与「存参数」之间的取舍有了准确表述——
存的是**序列**，而不是某一次调用。
"""

from __future__ import annotations

import ast
import copy
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import mock_tools as mt  # noqa: E402
from arena import reference_agent as ra  # noqa: E402

TASK = ROOT / "tasks" / "dbg-001-order-report.yaml"


def _run_and_record(task: dict) -> list[dict]:
    """真跑一遍参考 Agent，把**实际转发出去的** (参数, 状态, 返回体) 逐条记下。

    用 `on_call` 钩子而不是读 trace：trace 只记形状（`{"orders": 10, "total": 20}`），
    而这里要的是平台真正转发给 Agent 的那份内容——两者是不同的东西（见 `agent_trace.summary`）。
    """
    seen: list[dict] = []
    srv, svc = mt.make_server(task, port=0, on_call=lambda n, p, s, b=None: seen.append(
        {"name": n, "params": dict(p), "status": s, "body": b}
    ))
    host, port = srv.server_address[:2]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        svc.reset()
        ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}")).run()
    finally:
        srv.shutdown()
        srv.server_close()
    return seen


def _replay(task: dict, calls: list[dict]) -> list[tuple[int, dict]]:
    """新起一个干净服务，按**原顺序**重放，返回每条 (status, body)。

    刻意不走 HTTP：`MockToolService.call()` 就是 handler 里调的那一个函数，而
    `HTTPServer.shutdown()` 在没起过 `serve_forever` 时会**永久阻塞**（等一个永远不会被
    set 的事件）——那会让整个测试进程挂住，而不是报错。重放不需要端口，就不开端口。
    """
    svc = mt.MockToolService(task)
    svc.reset()
    return [svc.call(c["name"], c["params"]) for c in calls]


def test_every_response_is_rebuildable_from_data_and_params():
    """判据 1：不存 body，也能把这轮的每一条返回体重新算出来。"""
    task = dr.load_task(str(TASK))
    calls = _run_and_record(task)
    assert len(calls) >= 4, f"这一轮只有 {len(calls)} 次调用，样本太薄（应含正常页/失败/重试/写报告）"

    rebuilt = _replay(task, calls)
    for i, (call, (status, body)) in enumerate(zip(calls, rebuilt), start=1):
        assert (status, body) == (call["status"], call["body"]), (
            f"第 {i} 条重建不一致：{call['name']}({call['params']})\n"
            f"  当时：{call['status']} {call['body']}\n"
            f"  重建：{status} {body}"
        )


def test_changing_the_data_changes_the_response():
    """判据 2：数据变了返回体就得变——否则「冻结数据版本」是句空话。

    只改第一行金额：它落在第 1 页里，所以第 1 条调用必须变；写报告那条与数据无关，不该变。
    两半都要断言：只断言"变了"会放过"全都变了"（那意味着重建其实与参数无关）。
    """
    task = dr.load_task(str(TASK))
    calls = _run_and_record(task)

    perturbed = copy.deepcopy(task)
    perturbed["test_inputs"]["orders_fixture"][0]["amount"] = 999.99
    rebuilt = _replay(perturbed, calls)

    changed = [i for i, (c, r) in enumerate(zip(calls, rebuilt), start=1)
               if (r[0], r[1]) != (c["status"], c["body"])]
    assert changed, "改了数据返回体却没变：重建根本不依赖数据，那对账无处可对"
    assert 1 in changed, f"改了第 1 行金额，第 1 页却没变（变化的是 {changed}）"


def test_the_same_params_can_answer_differently_by_position():
    """判据 3：同一组参数连调两次，结果可以不同 ⇒ 冻结"单次请求"是不够的。

    `fault_injection.match: {offset: 10}` + `failures[0].attempt: 1` 的意思是
    「该 (tool, match) 的**第 1 次**调用失败」。所以：

    - 同一组参数，第 1 次 500、第 2 次 200 —— 返回体不是参数的函数；
    - 换个不匹配的 offset 先调，**不消耗** offset=10 的额度 —— 计数键是 (tool, match)，
      不是全局调用序号。

    两条合起来说明：重建的输入必须是**有序序列**（平台账本里正好有 `call_index + params`），
    而不是"每次请求各自一个指纹"。这也是"存 body"与"存参数"之间取舍的准确表述。
    """
    task = dr.load_task(str(TASK))
    page2 = {"offset": 10, "limit": 10}
    page1 = {"offset": 0, "limit": 10}

    svc = mt.MockToolService(task)
    svc.reset()
    first, second = svc.call("fetch_orders", page2)[0], svc.call("fetch_orders", page2)[0]
    assert (first, second) == (500, 200), f"同一参数的两次结果应为 500→200，实际 {first}→{second}"

    svc = mt.MockToolService(task)
    svc.reset()
    svc.call("fetch_orders", page1)          # 不匹配 match，不该消耗额度
    still_first = svc.call("fetch_orders", page2)[0]
    assert still_first == 500, (
        f"先用 offset=0 调了一次之后，offset=10 的首次调用应该**仍然**失败（额度按 (tool, match) 计），"
        f"实际 {still_first}——若为 200，说明额度的计数键变了，重建的输入描述也要跟着改"
    )


def test_mock_touches_no_filesystem():
    """判据的前置：mock 的工具执行**不产生真实副作用**。

    这条撑着「重放不需要沙箱隔离」这个成本判断。如果哪天有人给 mock 加一个真写盘的工具，
    重放就会改掉真实文件——那种情况下这个测试是**该**红的，不是误报。
    """
    src = (ROOT / "src" / "arena" / "mock_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "open":
            offenders.append(f"open() @ line {node.lineno}")
        elif isinstance(fn, ast.Attribute) and fn.attr in {
            "write_text", "write_bytes", "mkdir", "makedirs", "unlink", "remove", "rmdir",
        }:
            offenders.append(f"{fn.attr}() @ line {node.lineno}")
    assert not offenders, (
        "mock 出现了文件系统调用：" + "、".join(offenders) +
        "——重放将不再无副作用，沙箱隔离这条成本要重新算"
    )
