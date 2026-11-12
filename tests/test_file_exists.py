"""S3-3-3：R1 file_exists 执行器 + Q13(a) 产物输入契约的约束测试。

守的是四件结构性事实：
1. 产物本体只从 `raw.artifacts` 读，**绝不**从 trace 里 `write_report` 的自述推断；
2. 判定 / 期望 / 分类规则从任务 YAML 派生，坏配置在接触产物之前就抛；
3. 「平台没采集到产物」判 unscored，「学生没交产物」判 fail —— 两者不混；
4. 五份夹具的 R1 现算结果与最终报告基线一致，且输出能直接喂 `build_report()`。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arena import assertion_evaluator as ae  # noqa: E402
from arena import diagnosis_report as dr  # noqa: E402
from arena import trace_pipeline as tp  # noqa: E402

TASK_PATH = ROOT / "tasks" / "dbg-001-order-report.yaml"
FIXTURES = ROOT / "fixtures"
TASK = dr.load_task(TASK_PATH)
TARGET = "/workspace/report.json"
_MISSING = object()


def spec() -> dict:
    return copy.deepcopy(ae.load_r1_spec())


def task() -> dict:
    return yaml.safe_load(TASK_PATH.read_text(encoding="utf-8"))


def art(content=_MISSING) -> dict:
    return {} if content is _MISSING else {TARGET: content}


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def run(content=_MISSING, s: dict | None = None, vocab=None) -> dict:
    return ae.evaluate_assertion(s or spec(), None, vocab=vocab, artifacts=art(content))


# ---------------------------------------------------------------------------
# 契约来自 YAML，代码里没有第二份任务常量
# ---------------------------------------------------------------------------


def test_r1_contract_comes_from_yaml():
    s, t = spec(), task()
    y = next(r for r in t["rubric_gate"] if r["id"] == "R1_file_exists")
    assert s == y
    assert s["type"] == "file_exists"
    assert s["target"] == TARGET
    assert s["options"] == {"non_empty": True}
    cases = s["error_pattern_detection"]["cases"]
    assert {c["pattern"] for c in cases} == set(s["error_patterns"])
    assert s["error_pattern_detection"]["probe"] == f"file_exists({TARGET})"


def test_expected_follows_yaml_options():
    r = run("{}")
    assert r["evidence"]["expected"] == {"exists": True, "non_empty": True}
    assert r["evidence"]["expected"]["non_empty"] is spec()["options"]["non_empty"]


def test_classification_follows_yaml_case_order():
    """把两条 case 的 pattern 对调，结果必须跟着变——证明不是代码里写死的。"""
    s = spec()
    cases = s["error_pattern_detection"]["cases"]
    cases[0]["pattern"], cases[1]["pattern"] = cases[1]["pattern"], cases[0]["pattern"]
    assert run(_MISSING, s)["error_patterns"] == ["empty_output_file"]
    assert run("", s)["error_patterns"] == ["no_output_file"]


def test_classification_source_is_precise():
    """TD-004 同款：来源必须点名命中了哪些键，不能只写「case」。"""
    assert run(_MISSING)["evidence"]["extra"]["classification_source"] == "case:exists"
    assert run("")["evidence"]["extra"]["classification_source"] == "case:exists,non_empty"


# ---------------------------------------------------------------------------
# 判定语义
# ---------------------------------------------------------------------------


def test_missing_target_is_fail_with_yaml_pattern():
    r = run()
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["no_output_file"]
    assert r["evidence"]["observed"] == {"exists": False, "non_empty": False, "size_bytes": 0}
    assert "reason_code" not in r


def test_empty_string_is_fail_with_empty_output_file():
    r = run("")
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["empty_output_file"]
    assert r["evidence"]["observed"] == {"exists": True, "non_empty": False, "size_bytes": 0}


def test_non_empty_is_pass_without_classification():
    r = run("{\"valid_count\": 17}")
    assert r["state"] == "pass"
    assert not r.get("error_patterns")
    assert "classification_source" not in (r["evidence"].get("extra") or {})
    assert "reason_code" not in r


def test_size_bytes_is_utf8_byte_length():
    r = run("订单")
    assert r["state"] == "pass"
    assert r["evidence"]["observed"]["size_bytes"] == len("订单".encode("utf-8")) == 6


def test_non_empty_means_byte_length_not_stripped_text():
    """YAML 只写 non_empty，没写「非空白」：按字面判字节数 > 0，不擅自 strip。"""
    assert run("   ")["state"] == "pass"


def test_content_is_not_parsed_as_json():
    """内容对不对是 R2 的事：R1 不解析 JSON，越界会让职责糊掉。"""
    assert run("this is not json")["state"] == "pass"


# ---------------------------------------------------------------------------
# 数据不可信 ≠ 没交产物
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, [], "oops", 42, True])
def test_artifacts_container_not_object_is_unscored(bad):
    r = ae.evaluate_assertion(spec(), None, artifacts=bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"
    assert not r.get("error_patterns")


@pytest.mark.parametrize("bad", [None, 123, {}, [], True])
def test_artifact_value_not_string_is_unscored(bad):
    r = run(bad)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"
    assert not r.get("error_patterns")


def test_missing_artifacts_field_is_unscored_not_fail():
    """没传 artifacts 是平台采集缺失，不能记成「学生没交作业」。"""
    r = ae.evaluate_assertion(spec(), None)
    assert r["state"] == "unscored"
    assert r["reason_code"] == "artifact_invalid"


# ---------------------------------------------------------------------------
# P3：产物与 trace 是两份独立证据
# ---------------------------------------------------------------------------


def test_r1_never_infers_artifact_from_trace():
    """伪造 trace 声称写了文件，artifacts 里没有 —— 仍判 fail。Q13(b) 正是被这条堵掉的。"""
    forged = {"tool_calls": [
        {"name": "write_report", "params": {"path": TARGET, "content": "{}"},
         "status": "success", "result_summary": "written"}
    ]}
    r = ae.evaluate_assertion(spec(), forged, artifacts={})
    assert r["state"] == "fail"
    assert r["error_patterns"] == ["no_output_file"]


def test_r1_does_not_depend_on_trace_being_reported():
    r = ae.evaluate_assertion(spec(), None, artifacts=art("{}"))
    assert r["state"] == "pass"


def test_no_trace_fixture_r1_is_not_rewritten():
    """R1 不是 trace_* 前缀，不受 build_report 的强制改写影响（对比 TD-007 的 R5）。"""
    data = raw("raw-no-trace")
    computed = ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"])
    report = next(a for a in tp.run_fixture("raw-no-trace", TASK)["assertions"]
                  if a["id"] == "R1_file_exists")
    assert computed["state"] == report["state"] == "pass"
    assert "reason_code" not in report


def test_fake_retry_artifact_and_trace_disagree_on_purpose():
    """刻意保留的不一致：trace 里没有 write_report，产物却存在。

    这是 Q13(a) 说的「产物与轨迹两份独立证据」，留给 P3 的 R6 做交叉比对；
    S3-3-3 不做一致性检查，但必须有测试钉住这份素材还在。
    """
    data = raw("raw-fake-retry")
    calls = data["trace"]["tool_calls"]
    assert not any(c["name"] == "write_report" for c in calls)
    assert TARGET in data["artifacts"]
    assert ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"])["state"] == "pass"


# ---------------------------------------------------------------------------
# 坏 spec：接触任何产物之前就失败
# ---------------------------------------------------------------------------


def _bad_specs() -> list[tuple[dict, str]]:
    out = []

    def add(mutate, label):
        s = spec()
        mutate(s)
        out.append((s, label))

    add(lambda s: s.update(target=""), "target 空")
    add(lambda s: s.pop("options"), "缺 options")
    add(lambda s: s["options"].update(non_empty=False), "不支持的 non_empty")
    add(lambda s: s["options"].update(non_empty="true"), "non_empty 非 bool")
    add(lambda s: s["error_pattern_detection"].update(cases=[]), "cases 空")
    add(lambda s: s.pop("error_pattern_detection"), "缺分类规则")
    add(lambda s: s["error_pattern_detection"].update(cases=[{}]), "case 非规则对象")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(match={}), "空 match")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(match={"exits": False}), "拼错键")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(match={"exists": "false"}), "值非 bool")
    add(lambda s: s["error_pattern_detection"]["cases"][0].update(pattern="未声明"), "pattern 未声明")
    add(lambda s: s["error_pattern_detection"].update(default="未声明"), "default 未声明")
    return out


@pytest.mark.parametrize("bad,label", _bad_specs(), ids=[x[1] for x in _bad_specs()])
def test_bad_spec_is_rejected(bad, label):
    with pytest.raises(ae.EvaluatorError):
        ae.evaluate_assertion(bad, None, artifacts=art())


def test_bad_spec_fails_before_looking_at_artifacts():
    """artifacts 本身合法（会走 unscored），配置错仍必须抛，不许被早退分支吞掉。"""
    s = spec()
    s["error_pattern_detection"]["cases"][0]["match"] = {}
    with pytest.raises(ae.EvaluatorError, match="缺非空 match"):
        ae.evaluate_assertion(s, None, artifacts=art())


def test_load_r1_spec_validates_at_load_time(tmp_path):
    t = task()
    next(r for r in t["rubric_gate"]
         if r["id"] == "R1_file_exists")["error_pattern_detection"]["cases"][0]["match"] = {}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(t, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ae.EvaluatorError, match="缺非空 match"):
        ae.load_r1_spec(p)


def test_missing_case_refuses_to_invent_pattern():
    """fail 却没规则命中：抛错，不静默编错误模式，也不退回某个默认值。"""
    s = spec()
    s["error_pattern_detection"]["cases"] = [
        c for c in s["error_pattern_detection"]["cases"] if c["match"].get("exists") is not False
    ]
    with pytest.raises(ae.EvaluatorError, match="未命中任何 case"):
        ae.evaluate_assertion(s, None, artifacts=art())


def test_pattern_not_in_vocabulary_is_rejected():
    with pytest.raises(ae.EvaluatorError, match="未登记进错误模式词表"):
        ae.evaluate_assertion(spec(), None, vocab=set(), artifacts=art())


# ---------------------------------------------------------------------------
# 分发入口
# ---------------------------------------------------------------------------


def test_dispatch_routes_file_exists():
    r = ae.evaluate_assertion(spec(), None, artifacts=art("{}"))
    assert r["id"] == "R1_file_exists" and r["state"] == "pass"


def test_r3_entry_rejects_r1_spec():
    with pytest.raises(NotImplementedError, match="只实现 trace_sequence"):
        ae.evaluate(spec(), None)


def test_file_exists_entry_rejects_wrong_type():
    """分发器按 type 拦未知类型；执行器自己也守一道，不依赖唯一调用方。"""
    s = spec()
    s["type"] = "json_field_equals"
    with pytest.raises(ae.EvaluatorError, match="只接受 file_exists"):
        ae.evaluate_file_exists(s, art())


def test_unimplemented_type_does_not_fake_result():
    s = spec()
    # S3-3-5 起五条断言都已实现，改用虚构 type 锁「未知类型拒绝伪造结果」。
    s["type"] = "future_assertion_type"
    with pytest.raises(NotImplementedError, match="不要伪造结果"):
        ae.evaluate_assertion(s, None, artifacts=art("{}"))


def test_supported_types_match_current_dispatch():
    assert set(ae.SUPPORTED_TYPES) == {
        "trace_sequence", "trace_final_status", "file_exists",
        "json_field_equals", "trace_param_schema_valid"}


# ---------------------------------------------------------------------------
# 完成判据：五夹具 R1 state 与最终报告基线一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_every_fixture_declares_artifacts_explicitly(name):
    """缺 artifacts 字段与「没交产物」是两件事，不允许靠 .get() 默认值混过去。"""
    data = raw(name)
    assert isinstance(data.get("artifacts"), dict), name
    assert all(isinstance(v, str) for v in data["artifacts"].values()), name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r1_matches_report_baseline(name):
    data = raw(name)
    computed = ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"])
    report = next(a for a in tp.run_fixture(name, TASK)["assertions"]
                  if a["id"] == "R1_file_exists")
    handwritten = next(a for a in data["assertion_results"] if a["id"] == "R1_file_exists")
    assert computed["state"] == report["state"] == handwritten["state"], name
    assert computed.get("error_patterns", []) == report["error_patterns"], name
    assert computed.get("error_patterns", []) == handwritten.get("error_patterns", []), name


@pytest.mark.parametrize("name", tp.FIXTURE_NAMES)
def test_r1_output_can_be_fed_to_build_report(name):
    """现算结果可直接替换手写结果并过 schema，且分数序不动。"""
    data = copy.deepcopy(raw(name))
    before = tp.run_fixture(name, TASK)["score"]["ratio"]
    computed = ae.evaluate_assertion(spec(), data["trace"], artifacts=data["artifacts"])
    data["assertion_results"] = [
        computed if a["id"] == "R1_file_exists" else a for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)
    dr.validate_report(report)
    r1 = next(a for a in report["assertions"] if a["id"] == "R1_file_exists")
    assert r1["state"] == computed["state"]
    assert report["score"]["ratio"] == before


def test_unscored_artifact_reason_code_is_in_dto():
    """TD-008 已销项（§20.2 第 1 项）：`artifact_invalid` 已扩入 DTO 枚举。

    S3-3-3 时这条是**红灯证据**（断言「不在枚举」+ `validate_report` 抛错）；
    S3-3-6 按人拍板扩枚举后**反转成正向锁，函数保留不删除**（§22.6 A 类）。
    判定语义没变：产物采集故障仍判 unscored、不归因、不产出错误模式。
    """
    computed = ae.evaluate_assertion(spec(), None, artifacts=None)
    assert computed["state"] == "unscored"
    assert computed["reason_code"] == "artifact_invalid"
    assert not computed.get("error_patterns")

    schema = dr.load_schema()
    allowed = schema["properties"]["assertions"]["items"]["properties"]["reason_code"]["enum"]
    assert "artifact_invalid" in allowed

    data = copy.deepcopy(raw("raw-perfect"))
    data["assertion_results"] = [
        computed if a["id"] == "R1_file_exists" else a for a in data["assertion_results"]
    ]
    report = dr.build_report(TASK, data)  # 组装器不拦
    dr.validate_report(report)  # schema 也不再拦：已收录
    r1 = next(a for a in report["assertions"] if a["id"] == "R1_file_exists")
    assert r1["state"] == "unscored" and r1["error_patterns"] == []
    assert r1["reason_code"] == "artifact_invalid"
    assert report["attribution_policy"]["unscored"]["counts_toward_mastery"] is False
