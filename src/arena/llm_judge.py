"""B-04 / ROADMAP M1：LLM judge 双评 + 自一致性（软分侧证层）。

**这一层不碰分数。** 这是本模块存在的全部意义所在，写在最前面：

    确定性断言（R1–R5）是对账的地基——报告里每个 `pass/fail` 都能从 trace 逐字复算
    （决策 10：防作弊「保对账」）。judge 一旦参与 `ratio`、或往 `error_patterns` 里
    写东西，报告就变成「模型说了算」，复算不出来，也无法向第三方证明。

所以 judge 在这里只回答一个问题，而且只回答这一个：

    **换一个观察者重看同一份提交，能不能得出同样的结论？**

它产出两个字段（`docs/data-model.md` §5.1 早就留好了位置）：
`judge_scores`（每次评价的原始记录）与 `judge_agreement`（两次评价的一致率）。
`judge_agreement` 低 ⇒ 说明**判据本身的表述有歧义**，该进人工仲裁；
它不是「模型不好」，也不是「考生答错了」。

### 为什么是「分档」而不是「打分」

LLM 报连续分（比如 0.73 / 0.76）几乎必然不一致——那是浮点噪声，不是分歧，双评会
恒定为「不一致」，指标直接失去判别力。所以本模块要求 judge 只输出 1–4 的整数档
（与 `docs/data-model.md` §6 的 FSRS 映射同口径），**分档分歧才是真分歧**。

### 三条「不许静默」的硬约束

1. `rating` 越界（如 5、0、3.5）⇒ `JudgeError`。**不做 clamp**：把「模型乱说」
   悄悄夹成合法值，等于把故障伪装成一个合法的低分。
2. judge 给出的 `pattern_id` 不在词表内 ⇒ `JudgeError`。放过它就等于让模型
   自己发明错误分类，`skill_gaps` 会随之出现词表里没有的技能。
3. 双评少于 2 次 ⇒ `JudgeError`。「一致性」在 n=1 时无定义，返回 1.0 是自欺。

### 一致率怎么算（口径固定，改要人拍）

`judge_agreement` = **两两比较中 `rating` 相同的比例**。n=2 时就是「同 / 不同」两值。
`pattern_jaccard`（建议模式的 Jaccard 均值）单列输出，**不进 `judge_agreement`**：
仲裁要解决的是「这次该记几分」，模式建议差异不改变分数。

整个模块**不设阈值**。阈值要拿真数据标定（B-04 的实验脚本），在此之前任何
「0.8 以上算通过」都是拍脑袋——真到 0.6 就没人敢改，先给数字再定线。

零新依赖：一致性计算用标准库；真调用见同目录 `llm_client.py`（S2 落地）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Iterable, Sequence

RATING_MIN = 1
RATING_MAX = 4
DEFAULT_RUNS = 2


class JudgeError(RuntimeError):
    """judge 不可用或输出不合规——一律抛错，**绝不降级成「0 分」或「一致」**。"""


# ---------------------------------------------------------------------------
# 单次评价
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeVerdict:
    """一次评价的结果。构造即校验：不合规的 verdict 根本造不出来。"""

    rating: int
    patterns: tuple[str, ...] = ()
    rationale: str = ""
    model: str = ""
    latency_ms: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.rating, bool) or not isinstance(self.rating, int):
            raise JudgeError(f"rating 必须是整数档位（1–4），实际 {self.rating!r}")
        if not RATING_MIN <= self.rating <= RATING_MAX:
            raise JudgeError(
                f"rating={self.rating} 越界（应在 {RATING_MIN}–{RATING_MAX}）。"
                "不 clamp：把越界值夹成合法值会让故障看起来像一个正常的低分。"
            )
        if len(set(self.patterns)) != len(self.patterns):
            raise JudgeError(f"patterns 有重复项：{self.patterns!r}")

    def to_dict(self) -> dict[str, Any]:
        """输出用 `suspected_patterns` 而不是 `patterns`——这个命名是防误用的第一道防线。

        `docs/error-patterns-v0.yaml` 里每条模式都有 `detection_rule.assertion_id`，
        意思是「谁检出它」是唯一确定的。judge 给的是**怀疑**，不是检出：它既不进
        `detected_patterns`，也不进 `skill_gaps`，更不改分。换个名字能让接手的人
        在接错之前先愣一下，这比写十条注释有用。
        """
        out: dict[str, Any] = {"rating": self.rating, "suspected_patterns": list(self.patterns)}
        if self.rationale:
            out["rationale"] = self.rationale
        if self.model:
            out["model"] = self.model
        if self.latency_ms is not None:
            out["latency_ms"] = self.latency_ms
        return out


def check_patterns(verdict: JudgeVerdict, vocab: Iterable[str]) -> None:
    """词表外模式一律报错（约束 2）。

    judge 允许说「没问题」（`patterns=()`），但不允许发明 `skill_gaps` 认不出的分类。
    """
    known = set(vocab)
    unknown = [p for p in verdict.patterns if p not in known]
    if unknown:
        raise JudgeError(
            f"judge 给出了词表外的错误模式 {unknown!r}；"
            f"合法值见 docs/error-patterns-v0.yaml（共 {len(known)} 条）。"
            "放过它会让 skill_gaps 出现词表里没有的技能。"
        )


# ---------------------------------------------------------------------------
# 双评
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoubleJudgeResult:
    """n 次评价 + 一致性指标。**只描述事实，不判「够不够一致」**。"""

    verdicts: tuple[JudgeVerdict, ...]
    rating_agreement: float
    pattern_agreement: float

    @property
    def runs(self) -> int:
        return len(self.verdicts)

    @property
    def rating_spread(self) -> int:
        """最高档与最低档之差。0 = 所有人给了同一档。"""
        scores = [v.rating for v in self.verdicts]
        return max(scores) - min(scores) if scores else 0

    @property
    def agreement(self) -> float:
        """对外只暴露一个数，且它就是 `rating_agreement`（口径见模块 docstring）。"""
        return self.rating_agreement

    def needs_arbitration(self, threshold: float) -> bool:
        """低于阈值 ⇒ 进人工仲裁队列。

        阈值**必须由调用方显式传入**：本模块不提供默认值，因为任何默认值都会变成
        事实上的拍板。B-04 的实验（`scripts/judge_agreement_experiment.py`）先出分布，
        再定线。
        """
        if not 0.0 <= threshold <= 1.0:
            raise JudgeError(f"threshold 应在 [0,1]，实际 {threshold!r}")
        return self.rating_agreement < threshold

    def to_report_fields(self) -> dict[str, Any]:
        """落库/进报告用。键名严格对齐 `docs/data-model.md` §5.1，不扩 schema。"""
        return {
            "judge_scores": [v.to_dict() for v in self.verdicts],
            "judge_agreement": self.rating_agreement,
        }


def _pairwise_agreement(verdicts: Sequence[JudgeVerdict]) -> tuple[float, float]:
    pairs = list(combinations(range(len(verdicts)), 2))
    if not pairs:
        raise JudgeError(
            f"只有 {len(verdicts)} 次评价，无法计算一致性。"
            f"「双评」至少需要 {DEFAULT_RUNS} 次——n=1 时返回 1.0 是自欺。"
        )
    same = 0
    jaccards: list[float] = []
    for i, j in pairs:
        a, b = verdicts[i], verdicts[j]
        if a.rating == b.rating:
            same += 1
        sa, sb = set(a.patterns), set(b.patterns)
        union = sa | sb
        jaccards.append(len(sa & sb) / len(union) if union else 1.0)
    return same / len(pairs), sum(jaccards) / len(jaccards)


JudgeFn = Callable[[dict[str, Any]], JudgeVerdict]


def double_judge(
    judge: JudgeFn,
    context: dict[str, Any],
    *,
    runs: int = DEFAULT_RUNS,
    vocab: Iterable[str] | None = None,
) -> DoubleJudgeResult:
    """同一 context 连续评 `runs` 次，算一致性。

    `judge` 抛异常时**原样上抛**（包一层 `JudgeError` 说明是哪一次），
    不把「有一次崩了」记成「不一致」——那会把接口故障算成判据歧义，结论正好反了。
    """
    if runs < DEFAULT_RUNS:
        raise JudgeError(f"runs={runs} 太小，「双评」至少 {DEFAULT_RUNS} 次")
    verdicts: list[JudgeVerdict] = []
    for k in range(runs):
        try:
            verdict = judge(context)
        except JudgeError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 故意收窄成一句可读的话
            raise JudgeError(f"第 {k + 1}/{runs} 次评价失败：{type(exc).__name__}: {exc}") from exc
        if not isinstance(verdict, JudgeVerdict):
            raise JudgeError(f"judge 应返回 JudgeVerdict，实际 {type(verdict).__name__}")
        if vocab is not None:
            check_patterns(verdict, vocab)
        verdicts.append(verdict)
    rating_agreement, pattern_agreement = _pairwise_agreement(verdicts)
    return DoubleJudgeResult(
        verdicts=tuple(verdicts),
        rating_agreement=rating_agreement,
        pattern_agreement=pattern_agreement,
    )


# ---------------------------------------------------------------------------
# 桩：不联网、可复现，供测试与离线演示
# ---------------------------------------------------------------------------


class StubJudge:
    """按预设队列逐次返回的假 judge。

    存在理由：一致性机制必须能在**不花钱、不联网**的前提下被验证。
    真模型评同一份提交结果会漂，用它写断言等于把测试建在沙子上。
    """

    def __init__(self, verdicts: Sequence[JudgeVerdict]) -> None:
        if not verdicts:
            raise JudgeError("StubJudge 至少需要一条预设结果")
        self._queue = list(verdicts)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, context: dict[str, Any]) -> JudgeVerdict:
        self.calls.append(context)
        if not self._queue:
            raise JudgeError("StubJudge 预设结果已用尽（调用次数超出预期）")
        return self._queue.pop(0)


def parse_verdict(payload: str | dict[str, Any], *, model: str = "") -> JudgeVerdict:
    """把模型返回的 JSON 解析成 verdict。格式不合规一律 `JudgeError`。

    只接受一个 JSON 对象（允许被 ```json 围栏包着）。**不做猜测式修复**：
    少字段、类型错、多给一个 `score: 0.8` 都直接报错——否则「模型没按格式答」
    会被悄悄当成「模型认为 1 分」。
    """
    text = payload if isinstance(payload, dict) else _strip_fence(str(payload))
    if isinstance(payload, str):
        try:
            text = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JudgeError(f"judge 返回的不是合法 JSON：{str(payload)[:200]}") from exc
    if not isinstance(text, dict):
        raise JudgeError(f"judge 应返回 JSON 对象，实际 {type(text).__name__}")
    # 只认 `suspected_patterns` 一个名字，和 prompt 要求的输出字段严格一致。
    # 兼容 `patterns` 会让「模型按旧 prompt 答」悄悄通过，两套命名悄悄并存。
    missing = [k for k in ("rating", "suspected_patterns") if k not in text]
    if missing:
        raise JudgeError(f"judge 返回缺字段 {missing}：{str(text)[:200]}")
    patterns = text["suspected_patterns"]
    if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
        raise JudgeError(f"patterns 应是字符串数组，实际 {patterns!r}")
    return JudgeVerdict(
        rating=text["rating"],
        patterns=tuple(patterns),
        rationale=str(text.get("rationale") or "")[:1000],
        model=model or str(text.get("model") or ""),
    )


def _strip_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏。模型爱加，这不算格式错，包一层不算「猜测式修复」。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        body = lines[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        return "\n".join(body).strip()
    return s


# ---------------------------------------------------------------------------
# 喂给真模型的材料（白名单构造，不是整块 dump）
# ---------------------------------------------------------------------------

RESULT_CHARS = 240
PARAMS_CHARS = 160
INSTRUCTIONS_CHARS = 2400
MAX_CALLS = 24

RATING_MEANING: tuple[tuple[int, str], ...] = (
    (4, "完全达成任务目标，答案可直接使用，过程无缺陷"),
    (3, "基本达成，答案有小瑕疵但可用"),
    (2, "部分达成，答案有实质错误或不完整"),
    (1, "未达成，答案不可用，或根本没交产物"),
)

JUDGE_SYSTEM_PROMPT = """你是独立的复核员。给你一份任务说明、一份 Agent 交上来的产物、以及它的工具调用记录，
请你独立判断这次提交做得怎么样，并给一个 1–4 的整数档位。

你**没有**标准答案，也不需要猜标准答案。你要判断的是：按任务说明看，这份产物和这个过程是否合格。

档位含义（必须严格按此，不要自创）：
4 = 完全达成任务目标，答案可直接使用，过程无缺陷
3 = 基本达成，答案有小瑕疵但可用
2 = 部分达成，答案有实质错误或不完整
1 = 未达成，答案不可用，或根本没交产物

同时，如果你在过程里看到明确的缺陷，可以从下面这份**固定清单**里挑出对应的 pattern_id。
清单之外的词一律不要用；没有明确缺陷就给空数组，不要为了凑数硬挑。

可用 pattern_id（格式为 `id：中文名 — 说明`）：
{catalog}

只输出一个 JSON 对象，不要解释、不要 markdown 围栏，字段就这三个：
{{"rating": <1-4 的整数>, "suspected_patterns": [<pattern_id 字符串>], "rationale": "<两句话内，说清为什么给这个档>"}}
"""


def load_pattern_catalog(path: str | Path | None = None) -> list[dict[str, str]]:
    """给 prompt 用的模式清单。

    **只取 `pattern_id` / `label` / `description`，不取 `detection_rule`。**
    给了检测规则，模型会照着规则「模拟」检出一遍，那份一致性就不再是独立判断，
    而是把同一套规则实现了两遍——测出来的东西完全不同（round-79 记）。
    """
    from arena.diagnosis_report import VOCAB_PATH  # 局部导入：避免模块级循环依赖

    source = Path(path) if path is not None else VOCAB_PATH
    raw = yaml_safe_load(source)
    return [
        {
            "pattern_id": str(p["pattern_id"]),
            "label": str(p.get("label") or ""),
            "description": str(p.get("description") or ""),
        }
        for p in raw["patterns"]
    ]


def yaml_safe_load(path: Path) -> dict[str, Any]:
    import yaml  # 局部导入：只有这条路需要 yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "…（截断）"


def build_judge_context(
    task: dict[str, Any],
    raw: dict[str, Any],
    *,
    max_calls: int = MAX_CALLS,
) -> dict[str, Any]:
    """构造 judge 的输入。**白名单逐项取**，绝不整块 dump。

    为什么必须白名单：`raw["assertion_results"]` 里带着每条断言的 `expected`
    （也就是标准答案与判据），`task["test_inputs"]` 里也带着 `expected`。
    整块丢给模型，它只要照着 `expected` 念就能拿到高一致性——那样的「双评一致」
    证明不了任何事，只证明了模型会抄。

    也因此要接受一个现实：**judge 能看到什么，决定了它能证明什么**。当前 fixture 的
    `result_summary` 只是 `"10 orders"` 这种摘要，没有明细数据，模型无法独立复算答案，
    它只能判断「过程规不规范 + 答案合不合理」。这是材料的上限，不是模型的上限。
    """
    trace = raw.get("trace") or {}
    calls = trace.get("tool_calls") or []
    process = []
    for call in list(calls)[:max_calls]:
        item: dict[str, Any] = {
            "i": call.get("call_index"),
            "tool": call.get("name"),
            "params": _clip(call.get("params") or {}, PARAMS_CHARS),
            "status": call.get("status"),
        }
        for key in ("result_summary", "result", "response", "output"):
            if call.get(key) is not None:
                item["result"] = _clip(call[key], RESULT_CHARS)
                break
        if call.get("http_status") is not None:
            item["http_status"] = call.get("http_status")
        if call.get("error") is not None:
            item["error"] = call.get("error")
        process.append(item)

    artifacts = raw.get("artifacts") or {}
    answer = {str(name): _clip(body, 1200) for name, body in list(artifacts.items())[:5]}

    return {
        "task_id": task.get("task_id"),
        "task_title": task.get("title"),
        "task_instructions": _clip(task.get("scenario") or "", INSTRUCTIONS_CHARS),
        "answer": answer,
        "final_status": trace.get("final_status"),
        "call_count": len(calls),
        "process": process,
        "process_truncated": len(calls) > max_calls,
    }


def render_catalog(catalog: Iterable[dict[str, str]]) -> str:
    return "\n".join(f"- {p['pattern_id']}：{p['label']} — {p['description']}" for p in catalog)


class LLMJudge:
    """把 `llm_client` 接成 `double_judge` 能直接用的可调用对象。

    刻意**不做**的事：
      - 不重试「模型输出格式错」——那要走 `LLMError`/`JudgeError` 让上层看见，
        静默重试会掩盖「模型经常不按格式答」这个真问题（它直接影响一致率）。
      - 不缓存同一次结果。两次评价必须各自真打一次接口，否则 `judge_agreement`
        恒为 1.0，指标变成摆设。

    **`max_tokens` 默认 4096，不是 1024**（round-79 真跑实测）：`deepseek-flash` 会先花
    `reasoning_tokens` 再输出正文，读一份长材料时单次思考实测吃过 1024 的整份预算，
    正文返回空。这个参数是**上限不是花销**——给大不额外花钱，给小会直接把任务判死。
    """

    def __init__(
        self,
        client: Any,
        catalog: Iterable[dict[str, str]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> None:
        self.client = client
        self.catalog = list(catalog)
        self._ids = {p["pattern_id"] for p in self.catalog}
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system_prompt = JUDGE_SYSTEM_PROMPT.format(catalog=render_catalog(self.catalog))
        self.prompts: list[str] = []

    def __repr__(self) -> str:
        return f"LLMJudge(catalog={len(self.catalog)} 条, model={getattr(self.client.config, 'model', '?')!r})"

    def build_messages(self, context: dict[str, Any]) -> list[dict[str, str]]:
        user = "以下是本次要复核的提交材料（JSON）：\n" + json.dumps(context, ensure_ascii=False, indent=2)
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]

    def __call__(self, context: dict[str, Any]) -> JudgeVerdict:
        messages = self.build_messages(context)
        self.prompts.append(messages[1]["content"])
        completion = self.client.complete(
            messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            response_format_json=True,
        )
        verdict = parse_verdict(completion.text, model=completion.model or self.client.config.model)
        unknown = [p for p in verdict.patterns if p not in self._ids]
        if unknown:
            raise JudgeError(
                f"模型给出了清单外的 pattern_id {unknown!r}；"
                "不用 check_patterns 是因为这里要能区分「词表本身变了」与「模型瞎编」"
            )
        return JudgeVerdict(
            rating=verdict.rating,
            patterns=verdict.patterns,
            rationale=verdict.rationale,
            model=verdict.model,
            latency_ms=completion.latency_ms,
        )
