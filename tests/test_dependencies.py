"""依赖可复现性冒烟（Q8 拍板：放 CI，不进评审包生成流程）。

职责边界：
- `src/arena/review_packet.py` 只采集仓库事实，不联网、不装包、不因依赖失效而拒绝生成。
- 本文件由 CI 在 `pip install -r requirements.txt` 之后执行；本地可直接跑：
      pytest tests/test_dependencies.py -q
  它覆盖两件事：pin 的版本真的装上了、核心模块真的能 import（不联网）。

为什么不用 `pip index versions`：那需要网络、国内不稳定，且「版本存在」
远不等于「能装、能 import、能复现」。用已安装环境的元数据比对，判据更强且离线。
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "requirements.txt"
SRC = ROOT / "src"


def _dependency_lines() -> list[str]:
    return [
        stripped
        for raw in REQ.read_text(encoding="utf-8").splitlines()
        if (stripped := raw.split("#", 1)[0].strip())
    ]


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _dependency_lines():
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][A-Za-z0-9.!+]*)", line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def test_requirements_are_all_pinned():
    """漏 pin 版本等于没锁环境，CI 会随上游升级悄悄漂移。"""
    lines = _dependency_lines()
    assert lines, "requirements.txt 里没有任何依赖行"
    assert len(_pins()) == len(lines), "存在未 pin 版本、或格式不被识别的依赖行"


def test_pinned_versions_match_installed():
    """把版本号写错（例如那个不存在的 fsrs 5.2.4）时，这条必须红。"""
    for name, want in _pins().items():
        try:
            got = md.version(name)
        except md.PackageNotFoundError:
            pytest.fail(f"{name} 未安装；CI 需先执行 pip install -r requirements.txt")
        assert got == want, f"{name} pin={want}，实际安装={got}（版本不存在或环境未同步）"


def test_core_modules_importable():
    """依赖装上了不等于能 import；顺带验仓库自己的两个模块。"""
    for mod in ("fsrs", "jmespath", "jsonschema", "yaml"):
        importlib.import_module(mod)
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    for mod in ("arena.diagnosis_report", "arena.review_packet"):
        importlib.import_module(mod)
