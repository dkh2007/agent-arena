"""B-04 双评机制的约束测试（round-79）。

锁的边界：
  ① judge **不碰分数**——产出的键名只有 `judge_scores` / `judge_agreement`，
     一旦有人往里塞 `ratio` 或 `error_patterns`，这里就红；
  ② 越界不 clamp、词表外不放过、n<2 不许算一致率——三条都不许静默降级；
  ③ 「有一次崩了」≠「两次不一致」——接口故障不能被算成判据歧义，那会把结论判反。

真模型的调用不在这里测（它花钱、且结果会漂）。这里全部走 `StubJudge`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import llm_judge as lj  # noqa: E402

VOCAB = {"no_retry", "retry_wrong_params", "wrong_filter", "tool_no_response"}


def _v(rating: int, *patterns: str) -> lj.JudgeVerdict:
    return lj.JudgeVerdict(rating=rating, patterns=tuple(patterns))


# ---------------------------------------------------------------------------
# 契约：judge 不碰分数
# ---------------------------------------------------------------------------


def test_report_fields_never_touch_score_fields():
    """锁 ①：judge 的输出只允许两个键，且都是 data-model 已留好的位置。

    这条是对「judge 不得参与 ratio」的机械保证——有人加 `"ratio"` 或
    `"error_patterns"` 进来，这里立刻红。

    `detected_patterns` 单列一条：词表里每条模式都有唯一的 `detection_rule.assertion_id`，
    judge 报的只能是「怀疑」，一旦写成 `detected_patterns` 就等于绕过检测规则。
    """
    res = lj.double_judge(lj.StubJudge([_v(3, "no_retry"), _v(3, "no_retry")]), {}, vocab=VOCAB)
    fields = res.to_report_fields()
    assert set(fields) == {"judge_scores", "judge_agreement"}
    assert isinstance(fields["judge_scores"], list) and len(fields["judge_scores"]) == 2
    assert 0.0 <= fields["judge_agreement"] <= 1.0
    for score in fields["judge_scores"]:
        assert "detected_patterns" not in score
        assert score["suspected_patterns"] == ["no_retry"], "键名必须带『怀疑』语义"


# ---------------------------------------------------------------------------
# 约束 1：越界不 clamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 5, -1, 99])
def test_rating_out_of_range_is_loud(bad: int):
    """越界必须报错。clamp 会把「模型乱说」伪装成一个合法的低分。"""
    with pytest.raises(lj.JudgeError, match="越界"):
        lj.JudgeVerdict(rating=bad)


@pytest.mark.parametrize("bad", [3.0, 3.7, "3", True, None])
def test_rating_type_must_be_int(bad):
    """`True` 也是坑：它是 int 的子类，会静默变成 1 分。"""
    with pytest.raises(lj.JudgeError, match="整数档位"):
        lj.JudgeVerdict(rating=bad)


def test_duplicate_patterns_rejected():
    with pytest.raises(lj.JudgeError, match="重复"):
        lj.JudgeVerdict(rating=2, patterns=("no_retry", "no_retry"))


# ---------------------------------------------------------------------------
# 约束 2：词表外不放过
# ---------------------------------------------------------------------------


def test_pattern_outside_vocabulary_is_loud():
    """judge 允许说「没问题」，但不许发明词表里没有的分类。"""
    bad = _v(2, "hallucinated_pattern")
    with pytest.raises(lj.JudgeError, match="词表外"):
        lj.double_judge(lj.StubJudge([bad, bad]), {}, vocab=VOCAB)


def test_empty_patterns_is_legal():
    """「两次都认为没问题」是合法结论，不能被当成缺字段。"""
    res = lj.double_judge(lj.StubJudge([_v(4), _v(4)]), {}, vocab=VOCAB)
    assert res.agreement == 1.0
    assert res.pattern_agreement == 1.0


# ---------------------------------------------------------------------------
# 约束 3：n<2 不算一致率
# ---------------------------------------------------------------------------


def test_single_run_cannot_yield_agreement():
    """n=1 时一致性无定义。返回 1.0 是自欺，必须报错。

    match 串必须唯一指向 `double_judge` 自己的前置校验：`_pairwise_agreement`
    在 n=1 时也会抛错，措辞里同样有「至少」二字。round-79 变异 M9 证明，
    匹配得太松时**删掉前置校验测试也不会红**——那这条锁就是假的。
    """
    with pytest.raises(lj.JudgeError, match="runs=1 太小"):
        lj.double_judge(lj.StubJudge([_v(3)]), {}, runs=1)


def test_single_verdict_sequence_raises_at_pairwise():
    with pytest.raises(lj.JudgeError, match="无法计算一致性"):
        lj._pairwise_agreement([_v(3)])


# ---------------------------------------------------------------------------
# 一致率口径
# ---------------------------------------------------------------------------


def test_two_runs_same_rating_agree():
    res = lj.double_judge(lj.StubJudge([_v(3, "no_retry"), _v(3, "no_retry")]), {}, vocab=VOCAB)
    assert res.rating_agreement == 1.0
    assert res.pattern_agreement == 1.0
    assert res.rating_spread == 0


def test_two_runs_different_rating_disagree():
    res = lj.double_judge(lj.StubJudge([_v(2), _v(3)]), {}, vocab=VOCAB)
    assert res.rating_agreement == 0.0
    assert res.rating_spread == 1
    assert res.needs_arbitration(0.5) is True


def test_three_runs_use_pairwise_not_vote():
    """三次评分 3/3/3 → 全一致；3/3/2 → 三对里只有一对一致 = **1/3**。

    这里锁的是「两两一致率」而非「多数派占比」（后者会给 2/3）。两者在 n=2 时相同，
    在 n=3 时分岔，选两两的理由：

      - 仲裁要回答的是「**重评一次会不会变**」，两两比较正是这个问题的直接度量；
      - 多数派占比会掩盖少数派：3/3/2 与 3/2/2 在多数派口径下都是 2/3，
        但前者是「偶发跑偏」、后者是「几乎对半」，风险完全不同。

    代价是 n=3 时对个别跑偏惩罚较重（1/3 看起来很吓人）。这一点是**已知且有意接受**的：
    n=2 是本模块的默认档，n>2 只在实验脚本里用来观察漂移。
    """
    same = lj.double_judge(lj.StubJudge([_v(3), _v(3), _v(3)]), {}, runs=3, vocab=VOCAB)
    assert same.rating_agreement == 1.0
    mixed = lj.double_judge(lj.StubJudge([_v(3), _v(3), _v(2)]), {}, runs=3, vocab=VOCAB)
    assert mixed.rating_agreement == pytest.approx(1 / 3)
    assert mixed.rating_spread == 1


def test_pattern_jaccard_is_separate_from_rating_agreement():
    """档位一致但模式不同 ⇒ 一致率仍 1.0，Jaccard 降到 0。

    这条是口径的可测表达：仲裁看的是「该记几分」，模式差异不改变分数。
    **`agreement` 属性必须单独断言**——它是唯一对外的合成指标，只测 `rating_agreement`
    等于没测它（round-79 变异 M8 就是从这里漏过去的）。
    """
    res = lj.double_judge(
        lj.StubJudge([_v(3, "no_retry"), _v(3, "retry_wrong_params")]), {}, vocab=VOCAB
    )
    assert res.rating_agreement == 1.0
    assert res.pattern_agreement == 0.0
    assert res.agreement == 1.0, "对外指标只跟档位走；混入模式一致率会变成 0.5"


def test_pattern_jaccard_partial_overlap():
    res = lj.double_judge(lj.StubJudge([_v(2, "no_retry", "wrong_filter"), _v(2, "no_retry")]), {}, vocab=VOCAB)
    assert res.pattern_agreement == pytest.approx(0.5)
    assert res.rating_agreement == 1.0


# ---------------------------------------------------------------------------
# 故障 ≠ 不一致
# ---------------------------------------------------------------------------


def test_judge_exception_is_not_disagreement():
    """第二次调用崩掉时，不能记成「两次不一致」。

    把接口故障算成判据歧义，结论正好反了：会去改 prompt，而真正该做的是修接口。
    """

    class Boom:
        def __init__(self):
            self.n = 0

        def __call__(self, ctx):
            self.n += 1
            if self.n == 2:
                raise TimeoutError("连接超时")
            return _v(3)

    with pytest.raises(lj.JudgeError, match="第 2/2 次评价失败"):
        lj.double_judge(Boom(), {}, vocab=VOCAB)


def test_judge_error_passes_through_untouched():
    """已有的 JudgeError 不二次包装，否则错误信息会被套两层看不出来。"""

    def bad(ctx):
        raise lj.JudgeError("原始信息")

    with pytest.raises(lj.JudgeError, match="^原始信息$"):
        lj.double_judge(bad, {}, vocab=VOCAB)


def test_non_verdict_return_is_rejected():
    with pytest.raises(lj.JudgeError, match="应返回 JudgeVerdict"):
        lj.double_judge(lambda ctx: 3, {}, vocab=VOCAB)


# ---------------------------------------------------------------------------
# 阈值必须由调用方显式给
# ---------------------------------------------------------------------------


def test_threshold_has_no_default():
    """`needs_arbitration` 不许自带默认阈值。

    任何默认值都会变成事实上的拍板；B-04 要先出分布再定线。
    """
    res = lj.double_judge(lj.StubJudge([_v(2), _v(3)]), {}, vocab=VOCAB)
    with pytest.raises(TypeError):
        res.needs_arbitration()  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", [-0.1, 1.1, 2])
def test_threshold_out_of_range_is_loud(bad):
    res = lj.double_judge(lj.StubJudge([_v(2), _v(3)]), {}, vocab=VOCAB)
    with pytest.raises(lj.JudgeError, match="threshold"):
        res.needs_arbitration(bad)


# ---------------------------------------------------------------------------
# 解析：不做猜测式修复
# ---------------------------------------------------------------------------


def test_parse_verdict_rejects_missing_field():
    with pytest.raises(lj.JudgeError, match="缺字段"):
        lj.parse_verdict('{"rating": 3}')


def test_parse_verdict_rejects_non_json():
    with pytest.raises(lj.JudgeError, match="不是合法 JSON"):
        lj.parse_verdict("我觉得这个提交还行，给三分吧")


def test_parse_verdict_ignores_extra_keys_but_not_bad_types():
    """多给键不算错（模型爱加 `confidence`），但 `patterns` 类型错必须报。"""
    v = lj.parse_verdict('{"rating": 3, "suspected_patterns": ["no_retry"], "confidence": 0.9}')
    assert v.rating == 3 and v.patterns == ("no_retry",)
    with pytest.raises(lj.JudgeError, match="字符串数组"):
        lj.parse_verdict('{"rating": 3, "suspected_patterns": "no_retry"}')


def test_parse_verdict_rejects_legacy_patterns_key():
    """只认 `suspected_patterns`，不兼容旧名 `patterns`。

    round-79 真实踩到过：prompt 要求输出 `suspected_patterns`，解析器还在找 `patterns`，
    七个用例一起红。当时最容易的"修法"是让解析器两个名字都认——那会让两套命名
    悄悄并存，以后没人知道 prompt 到底要求哪个。所以这里把"不兼容"钉死。
    """
    with pytest.raises(lj.JudgeError, match="缺字段"):
        lj.parse_verdict('{"rating": 3, "patterns": ["no_retry"]}')


def test_parse_verdict_accepts_json_fence():
    """```json 围栏是模型的习惯，不算格式错。"""
    v = lj.parse_verdict('```json\n{"rating": 2, "suspected_patterns": []}\n```')
    assert v.rating == 2
    assert v.patterns == ()


def test_parse_verdict_still_validates_range():
    with pytest.raises(lj.JudgeError, match="越界"):
        lj.parse_verdict('{"rating": 7, "suspected_patterns": []}')


# ---------------------------------------------------------------------------
# 桩
# ---------------------------------------------------------------------------


def test_stub_judge_exhausted_is_loud():
    """预设用尽必须报错——静默重复最后一条会让测试假绿。

    同时验证它被包成 `第 2/2 次评价失败`：桩自己抛的也是 JudgeError，
    但 `double_judge` 只对「非 JudgeError」做包装，所以这里应当看到桩的原文。
    """
    stub = lj.StubJudge([_v(3)])
    with pytest.raises(lj.JudgeError, match="预设结果已用尽"):
        lj.double_judge(stub, {}, runs=2, vocab=VOCAB)
    assert len(stub.calls) == 2, "第二次也要真的被调用过，不能提前退出"


def test_stub_judge_rejects_empty_queue():
    with pytest.raises(lj.JudgeError, match="至少需要一条"):
        lj.StubJudge([])


def test_stub_judge_records_every_call():
    """桩要能证明「评了两次」，而不是复用了第一次结果。"""
    stub = lj.StubJudge([_v(3), _v(3)])
    ctx = {"task_id": "dbg-001", "answer": {"valid_count": 17}}
    lj.double_judge(stub, ctx, vocab=VOCAB)
    assert stub.calls == [ctx, ctx], "两次评价必须各自拿到完整上下文"


# ---------------------------------------------------------------------------
# 提示词的两条硬规则（2026-11-27 真跑实测后写进来的）
# ---------------------------------------------------------------------------


def test_prompt_forbids_docking_a_grade_for_missing_process():
    """「过程缺失不降档」这一句删掉，同一份材料的一致率从 1.00 掉回 0.50。

    实测（`judge_agreement_experiment.py`，2026-11-27）：
    - 补这句之前：`raw-no-trace`（有产物、无过程）四次评价 = [2, 2, 3, 2]，一致率 0.50；
    - 补上之后：四次都是 4 档，一致率 1.00，rationale 里直接引用了这条规则。

    这不是"让模型别想太多"：缺过程已经由门禁层处理（那次不计入长期掌握度），
    judge 再降一档就是和按产物判分的那一层直接矛盾。
    """
    assert "过程缺失不降档" in lj.JUDGE_SYSTEM_PROMPT
    assert "由另一层决定" in lj.JUDGE_SYSTEM_PROMPT


def test_default_max_tokens_leaves_room_for_reasoning():
    """4096 被思考吃光过：`raw-no-trace` 第 2 次评价 `finish_reason=length`、正文为空。

    这个参数是**上限不是花销**，给大不额外花钱，给小会把任务直接判死。
    """
    judge = lj.LLMJudge(object(), [])
    assert judge.max_tokens >= 8192
