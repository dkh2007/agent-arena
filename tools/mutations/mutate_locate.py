"""第二套骨架（B-05b 定位题）的变异测试。判定三档（由 `_runner.judge()` 统一给，**只认 `rc == 1`**）：`DETECTED` / `INVALID` / `MISSED`。

**`INVALID` 不算抓住**——变异脚本自己写错锚点会让它看起来像"抓住了"。
上一轮（round-85）就栽在这里：复用同一个 `PYTEST_DEBUG_TEMPROOT`，pytest 开场清理旧目录
被沙箱拦掉，整场 INTERNALERROR，输出里既没有 failed 也没有断言信息。

跑法：  python tools/mutations/mutate_locate.py
"""

from __future__ import annotations

from pathlib import Path

# 站到仓根再干活：脚本里全是相对路径（src/… / tests/…），在别的目录直接跑会
# FileNotFoundError。round-86 收尾时实测踩到（用户要求给完整路径，才发现"完整路径"
# 反而跑不通）。这里按"往上找 src/arena"定位仓根，从 .git/ 或 tools/mutations/ 调用都成立。
import os as _os

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "arena").is_dir())
_os.chdir(_ROOT)

from _runner import run_mutations  # 同目录；判定只有一处实现

R = Path("src/arena/reference_agent.py")
M = Path("src/arena/mock_tools.py")
E = Path("src/arena/assertion_evaluator.py")
T = "tests/test_locate_skeleton.py"

MUTATIONS = [
    # 投影只进 evidence、不参与判定，所以端到端用例抓不住它（首跑 MISSED 就是这个原因）。
    # 这条变异必须打**单元**测试：改坏它分数不变，只有报告里的"读了哪几个文件"变成 None。
    ("M1 投影退回写死 offset（报告里的路径序列变成 None）",
     E, '    if isinstance(item, dict) and len(item) == 1:\n        return next(iter(item.values()))',
     '    if isinstance(item, dict):\n        return item.get("offset")',
     f"{T}::test_projected_scalar_takes_the_only_value"),

    ("M2 取第一条 ERROR（干扰项就赢了）",
     R, "    match = _ERROR_LINE.search(lines[-1])",
     "    match = _ERROR_LINE.search(lines[0])",
     f"{T}::test_diagnosis_matches_the_declared_expectation"),

    ("M3 根因映射表把 TypeError 写成 off_by_one",
     R, '    "TypeError": "type_mismatch",',
     '    "TypeError": "off_by_one",',
     f"{T}::test_diagnosis_matches_the_declared_expectation"),

    ("M4 行号少算一行（0 基当 1 基）",
     R, "        if needle in line:\n            return i + 1",
     "        if needle in line:\n            return i",
     f"{T}::test_diagnosis_matches_the_declared_expectation"),

    ("M5 日志行用 match 而不是 search（跳过不了时间戳前缀）",
     R, "    match = _ERROR_LINE.search(lines[-1])",
     "    match = _ERROR_LINE.match(lines[-1])",
     f"{T}::test_diagnosis_matches_the_declared_expectation"),

    ("M6 取不到路径时返回 200 空内容（假装读到了空文件）",
     M, '''        key = params.get(spec.lookup)
        if key not in spec.rows:
            return 404, {
                "error": "file_not_found",
                "detail": f"{spec.lookup}={key!r} 不在可读范围内",
                "available": sorted(spec.rows),
            }''',
     '''        key = params.get(spec.lookup)
        if key not in spec.rows:
            return 200, {"path": key, "content": "", "lines": 0, "bytes": 0}''',
     f"{T}::test_unknown_path_is_a_404_that_lists_what_is_readable"),

    ("M7 允许 lookup 与 pagination 同时声明（两种回放形态混用）",
     M, '            raise MockConfigError(f"{name}: mock 不能同时声明 lookup 与 pagination")',
     "            pass",
     f"{T}::test_lookup_and_pagination_cannot_coexist"),

    ("M8 不校验缺陷形态与骨架配套（接错形态静默当正确）",
     R, "        allowed = _TEMPLATE_FLAWS[self._template]",
     "        allowed = FLAWS",
     f"{T}::test_flaw_must_belong_to_the_template"),

    ("M9 不校验 locate 路径是否在夹具里（拖到运行期才 404）",
     R, '        unreadable = [f for f in ("log_path", "source_path") if locate[f] not in files]',
     "        unreadable = []",
     f"{T}::test_locate_path_not_in_fixture_is_loud"),

    ("M10 no_retry 形态不把重试压到 0（形态名存实亡）",
     R, '        self.retries = 0 if flaw == "no_retry" else retries',
     "        self.retries = retries",
     f"{T}::test_giving_up_on_503_is_classified_as_no_retry"),

    ("M11 read_file 的行数按字节数算（回放形状失真）",
     M, '            "lines": len(text.splitlines()),',
     '            "lines": len(text),',
     f"{T}::test_read_file_replays_the_fixture_by_path"),
]

def main() -> int:
    """判定与还原全交给 `_runner`：本文件只负责"变异长什么样"。

    以前这里有一份自己的判定代码，窄看只是重复；实际是两套规则会分叉——旧那套
    把 rc=2/3/4 也当 `DETECTED`，于是节点名写错时会报"抓住了"（round-89 M14 实测）。
    """
    return run_mutations(MUTATIONS, temp_prefix="pt-locate-")

if __name__ == "__main__":
    raise SystemExit(main())
