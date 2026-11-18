"""LLM Agent 的验收（B-03b ②）：跑法 `python scripts/llm_agent_probe.py`。

**先说清它证不了什么**：这里用的是**剧本模型**（`ScriptedModel`），决策是写死的。
所以本脚本证明的是**链路**——提示构造 → 协议解析 → 观察回填 → 记录 → 评测 → 对账，
一条都不缺；它**不**证明"某个真模型会自己想到重试"。后者要真跑，且必须钉住模型版本，
那是下一步（配额）的事。把这两件事混在一起说，就是拿脚手架当成绩。

四段：

  1. 好剧本（翻页 → 撞 500 → 重试 → 写报告）⇒ trace 结构合法、R1/R2/R3/R5 全 pass，
     自报与平台账本逐条一致。
  2. 不重试的剧本 ⇒ **R3 fail**。与第 1 段只差一次决策：结论的差别只能来自决策，
     不能来自框架——框架若自动重试，这个差别会消失（`mutate_llm_agent.py` M1 管着）。
  3. 参数幻觉剧本（limit=50 → 服务端拒 → 自己改回 10）⇒ R4 fail，其余不塌。
  4. 提示体检：期望值、故障位一个都不在提示里。

末行 `LLM_PROBE_OK=...` 给自动检查看。脚本自己起服务，不联网、不花钱、不写仓库数据。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import llm_agent as la  # noqa: E402
from arena import platform_capture as pc  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402
from arena.reference_agent import ToolClient  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
REPORT = "/workspace/report.json"
ANSWER = {"valid_count": 17, "total_amount": 1837.99}


def _call(tool: str, **params) -> dict:
    return {"action": "call_tool", "tool": tool, "params": params}


def _finish(answer: dict | None = None) -> dict:
    return {"action": "finish", "answer": ANSWER if answer is None else answer}


def _report() -> dict:
    return _call("write_report", path=REPORT,
                 content=json.dumps(ANSWER, ensure_ascii=False, sort_keys=True))


GOOD = [
    _call("fetch_orders", offset=0, limit=10),
    _call("fetch_orders", offset=10, limit=10),   # 故障注入：第一次 500
    _call("fetch_orders", offset=10, limit=10),   # 模型决定重试
    _report(),
    _finish(),
]

NO_RETRY = [
    _call("fetch_orders", offset=0, limit=10),
    _call("fetch_orders", offset=10, limit=10),
    _finish({}),                                   # 直接放弃，不写报告
]

HALLUCINATED_PARAM = [
    _call("fetch_orders", offset=0, limit=50),     # 超出 limit≤10
    _call("fetch_orders", offset=0, limit=10),
    _call("fetch_orders", offset=10, limit=10),
    _call("fetch_orders", offset=10, limit=10),
    _report(),
    _finish(),
]


def _evaluate(task: dict, run, name: str) -> dict:
    sub = {
        "submission_id": f"probe-llm-{name}",
        "task_id": task["task_id"],
        "task_git_hash": "deadbee",
        "task_version": task["task_version"],
        "agent_id": run.trace["agent_id"],
        "evaluated_at": "2026-11-18T00:00:00Z",
    }
    built = tp.recompute_all(
        tp.assemble_raw(sub, task, trace=run.trace, artifacts=run.artifacts), task=task
    )
    return dr.build_report(task, built)


def main() -> int:
    task = dr.load_task(TASK_PATH)
    rows = []
    checks: list[bool] = []

    for label, script in (("好剧本（会重试）", GOOD),
                          ("不重试", NO_RETRY),
                          ("参数幻觉后自行纠正", HALLUCINATED_PARAM)):
        recorder = pc.CaptureRecorder()
        srv, svc = pc.capture_proxy(task, recorder, port=0)
        host, port = srv.server_address[:2]
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            svc.reset()
            model = la.ScriptedModel(script)
            agent = la.LLMAgent(task, ToolClient(f"http://{host}:{port}"), model.client())
            run = agent.run()
            ledger = recorder.trace_items()
        finally:
            srv.shutdown()
            srv.server_close()
            th.join(timeout=5)

        report = _evaluate(task, run, label)
        state = {a["id"].split("_")[0]: a["state"] for a in report["assertions"]}
        cmp_result = pc.compare_traces(run.trace["tool_calls"], ledger)
        rows.append({
            "label": label,
            "final": run.trace["final_status"],
            "calls": len(run.trace["tool_calls"]),
            "ledger": len(ledger),
            "R1": state["R1"], "R2": state["R2"], "R3": state["R3"], "R4": state["R4"],
            "score": report["score"].get("ratio"),
            "ledger_match": cmp_result.ok,
            "rejected": len(run.meta["rejected_actions"]),
            "model": run.meta["model"],
        })

    header = f"{'剧本':16s} {'final':10s} {'调用':>4s} {'账本':>4s} {'R1':>6s} {'R2':>6s} {'R3':>6s} {'R4':>6s} {'ratio':>6s} {'对账':>6s}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['label']:16s} {r['final']:10s} {r['calls']:>4d} {r['ledger']:>4d} "
              f"{r['R1']:>6s} {r['R2']:>6s} {r['R3']:>6s} {r['R4']:>6s} "
              f"{str(r['score']):>6s} {('一致' if r['ledger_match'] else '不符'):>6s}")

    good, norety, hallu = rows
    checks.append(good["final"] == "completed" and good["R1"] == good["R2"] == good["R3"] == "pass")
    checks.append(good["ledger_match"] and good["calls"] == good["ledger"] == 4)
    checks.append(norety["R3"] == "fail")              # 框架没替它重试
    checks.append(good["score"] > norety["score"])     # 差别只在决策
    checks.append(hallu["R4"] == "fail" and hallu["R3"] == "pass")  # 只打掉该打的那条

    prompt_text = "\n".join(m["content"] for m in la.build_messages(task))
    leaks = [k for k in ("1837.99", "o-001", "fault_injection", "attempt", "internal_error")
             if k in prompt_text]
    checks.append(not leaks)

    print()
    print(f"[提示体检] 期望值/故障位泄漏：{leaks or '无'}")
    print(f"[可复现性] prompt_version={la.PROMPT_VERSION} temperature=0.0 "
          f"model={good['model']}")
    print("[边界] 剧本模型只证明链路通，不证明真模型会这样做；真跑要钉模型版本，尚未进行。")

    ok = all(checks)
    print(f"\nLLM_PROBE_OK={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
