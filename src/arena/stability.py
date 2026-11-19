"""模型稳定性基线——扰动测试的前置（C 口径，2026-11-19 人拍）。

## 为什么要它

外部复核点破一件事：现在做"改数据重跑、看答案变不变"的扰动测试，会分不清
**数据变了**和**模型抖了**——两者都表现为"答案变了"。所以扰动之前先要有稳定性基线：
**同一输入原样重跑 N 次**，看决策序列稳不稳。基线不稳，扰动结果不可解释。

## 比什么：决策序列，不是分数、不是文本

| 比的对象 | 为什么不用 |
|---|---|
| 分数 / ratio | 两条不同的决策路径能撞出同一个分数；相等不能证明稳定 |
| 模型原始文本 | 措辞变了但决策没变，会误报成"不稳" |
| **有序 `(tool, params)` 决策序列** | 这正是下游重放与判定的输入；序列一致 ⇒ 这场评测可复现 |

指纹只取 `name` 与 `params`（`params` 按 key 排序后序列化），**不取** `status` / 返回体 /
`call_index` 之外的东西——返回体随 mock 故障位变化，不是模型的输出。

## 当前能证明什么、不能证明什么（必须写死，不许混）

- `ScriptedModel` 是确定性剧本：N 次重跑必然 N/N 一致。这**只**证明"链路本身不抖"
  （提示→解析→记录→指纹这一路没有隐藏随机性），**不**证明任何真模型稳定。
- 真模型稳定性**尚未测**：需要固定版本端点 + 配额（B-03b ② 的卡点）。在测到之前，
  本模块只提供判据与探针，不产出"模型稳定"这种结论。谁拿剧本模型的 100% 一致去说
  "模型稳定"，就是把脚手架当成绩。

指纹里带 `model` 标签，是为了让真模型基线能按版本分开存——换版本后旧基线不作数。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


def decision_fingerprint(tool_calls: Iterable[dict], *, model: str = "") -> str:
    """把有序工具调用压成指纹：只含 `(name, sorted params)` 与模型标签。

    缺 `name` / `params` 不是"跳过"，而是拒绝——静默跳过会让两条不同的调用序列
    压成同一个指纹，正好抹掉要测的分叉。
    """
    seq = []
    for i, call in enumerate(tool_calls):
        if not isinstance(call, dict) or "name" not in call or "params" not in call:
            raise ValueError(f"第 {i} 条调用缺 name/params，无法算指纹：{call!r}")
        params = call["params"]
        if not isinstance(params, dict):
            raise ValueError(f"第 {i} 条调用的 params 不是对象：{params!r}")
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        seq.append([call["name"], canonical])
    payload = json.dumps({"model": model, "calls": seq}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StabilityVerdict:
    runs: int
    distinct_fingerprints: int
    stable: bool
    fingerprints: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "runs": self.runs,
            "distinct_fingerprints": self.distinct_fingerprints,
            "stable": self.stable,
            "fingerprints": list(self.fingerprints),
        }


def judge_stability(fingerprints: Iterable[str]) -> StabilityVerdict:
    """N 次重跑的指纹全相同才算稳定；空输入拒绝（0 次重跑不是稳定）。"""
    fps = tuple(fingerprints)
    if not fps:
        raise ValueError("没有重跑指纹，无法判定稳定性")
    distinct = len(set(fps))
    return StabilityVerdict(
        runs=len(fps),
        distinct_fingerprints=distinct,
        stable=(distinct == 1),
        fingerprints=fps,
    )
