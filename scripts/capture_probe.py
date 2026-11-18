"""平台侧采集的验收（B-03b ①）：跑法 `python scripts/capture_probe.py`。

四段，每段证一件事：

  1. 参考 Agent 指向**平台代理**跑一遍 ⇒ 平台账本（平台自己编号）与它自报的 trace 一致。
     两份东西独立产生后对上，这才叫"平台真的在观测"，不是断言说它对。
  2. 删掉自报里的失败调用 ⇒ 对账 `mismatch`，点名"第 2 条平台有、自报缺"。
  3. 把失败那条与后一条的**编号对调** ⇒ 仍是 `mismatch`，且指出的是**状态不符**而不是
     "参数不符"——说明对账按编号对齐，没退化成按数组次序比。
  4. 三种篡改送进评测器，看它能不能自己发现：

       形态                       评测器                         平台对账
       删掉失败调用               R3 unscored / schema_valid 假    mismatch
       失败改成功                 R3 fail                         mismatch
       **伪造一条"失败+重试"**    R3 fail / **schema_valid 真**   **mismatch** ← 只有对账能抓

     第四行是本模块存在的理由：结构层与断言层都没有红灯（`schema_valid=True`，
     `call_index` 连续、字段齐全），一份凭空编出来的重试在评测器眼里完全合法。
     能说"这次调用平台没收到过"的，只有平台自己的账本。

末行 `CAPTURE_OK=...` 给自动检查看。脚本自己起服务、自己跑，不写仓库里任何数据。
"""

from __future__ import annotations

import copy
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import diagnosis_report as dr  # noqa: E402
from arena import platform_capture as pc  # noqa: E402
from arena import reference_agent as ra  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"


def _evaluate(task: dict, run, calls: list[dict]) -> dict:
    sub = {
        "submission_id": "probe-capture",
        "task_id": task["task_id"],
        "task_git_hash": "deadbee",
        "task_version": task["task_version"],
        "agent_id": ra.AGENT_ID,
        "evaluated_at": "2026-11-18T00:00:00Z",
    }
    raw = tp.assemble_raw(
        sub, task,
        trace={"tool_calls": calls, "final_status": "completed"},
        artifacts=run.artifacts,
    )
    return dr.build_report(task, tp.recompute_all(raw, task=task))


def _r3(report: dict) -> tuple[str, str | None]:
    item = next(a for a in report["assertions"] if a["id"].startswith("R3"))
    return item["state"], item.get("reason_code")


def main() -> int:
    task = dr.load_task(TASK_PATH)
    recorder = pc.CaptureRecorder()
    srv, svc = pc.capture_proxy(task, recorder, port=0)
    host, port = srv.server_address[:2]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    ok = False
    try:
        svc.reset()
        run = ra.ReferenceAgent(task, ra.ToolClient(f"http://{host}:{port}")).run()
        reported = run.trace["tool_calls"]
        platform = recorder.trace_items()

        print("[1] 平台账本（编号由平台给）：")
        for c in platform:
            print(f"    #{c['call_index']} {c['name']} {c['status']} "
                  f"http_status={c.get('http_status')} params={c['params']}")
        print(f"[1] 自报 {len(reported)} 条 / 平台 {len(platform)} 条")

        first = pc.compare_traces(reported, platform)
        print(f"[1] 对账：{first.verdict()}（比了 {first.compared} 条）")
        for line in first.mismatches:
            print(f"    - {line}")

        trimmed = [c for c in reported if c.get("status") != "error"]
        second = pc.compare_traces(trimmed, platform)
        print(f"[2] 删掉失败调用后对账：{second.verdict()}")
        for line in second.mismatches:
            print(f"    - {line}")

        swapped = [dict(c) for c in reported]
        errs = [i for i, c in enumerate(swapped) if c.get("status") == "error"]
        if errs:
            i = errs[0]
            j = min(i + 1, len(swapped) - 1)
            swapped[i]["call_index"], swapped[j]["call_index"] = (
                swapped[j]["call_index"], swapped[i]["call_index"],
            )
        third = pc.compare_traces(swapped, platform)
        print(f"[3] 对调失败调用与后一条的编号后对账：{third.verdict()}")
        for line in third.mismatches:
            print(f"    - {line}")
        states_only = all("status 不符" in m or "http_status" in m for m in third.mismatches)
        print(f"[3] 指名的是状态类差异（不是参数）：{states_only}")

        # [4] 四种形态一起看：结构层 / 断言层 / 平台对账各自发现了什么
        flipped = copy.deepcopy(reported)
        for c in flipped:
            if c.get("status") == "error":
                c["status"] = "success"
                c.pop("http_status", None)
                c.pop("error", None)

        forged = copy.deepcopy(reported)
        forged.insert(2, {"call_index": 0, "name": "fetch_orders",
                          "params": {"offset": 10, "limit": 10},
                          "status": "error", "http_status": 500,
                          "error": "internal_error"})
        for i, c in enumerate(forged, start=1):
            c["call_index"] = i

        forms = [
            ("原样（未篡改）", reported),
            ("删掉失败调用", trimmed),
            ("失败改成功", flipped),
            ("伪造一条「失败+重试」", forged),
        ]
        rows = []
        print("[4] 篡改形态 → 结构层 / 断言层 / 平台对账：")
        for label, calls in forms:
            report = _evaluate(task, run, calls)
            state, reason = _r3(report)
            cmp_result = pc.compare_traces(calls, platform)
            rows.append((label, report["trace_audit"]["schema_valid"], state, cmp_result.verdict()))
            print(f"    {label:22s} schema_valid={str(report['trace_audit']['schema_valid']):5s} "
                  f"R3={state}{'/' + reason if reason else '':16s} 对账={cmp_result.verdict()}")

        fab = next(r for r in rows if r[0].startswith("伪造"))
        print("[4] 关键一行：伪造的重试在评测器里完全合法（schema_valid=True、断言层没有"
              "「平台没见过这次调用」这条），只有对账说得出来。")

        ok = (
            first.ok
            and not second.ok
            and not third.ok
            and states_only
            # 伪造形态：结构层没红灯（这才是对账不可替代的地方），但编号/条数被点名
            and fab[1] is True
            and fab[3] == "mismatch"
        )
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    print(f"CAPTURE_OK={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
