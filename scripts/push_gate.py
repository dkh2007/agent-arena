#!/usr/bin/env python3
"""Pre-push gate, rule 11 (docs/review-response-v1.md 50.4).

For EVERY commit in <upstream>..HEAD, scan three surfaces:
  - every blob in that commit's full tree
  - the added lines of that commit's diff
  - the commit message

Only a zero-hit result yields PUSH_GATE=CLEAN. Scanning the worktree,
the HEAD tree alone, or just the last commit does NOT qualify.

All output is ASCII: verify.bat runs under cmd.exe (GBK), which mangles CJK.

Self-hit rule: this file must not contain any needle as a contiguous
literal, or the gate would block its own commit forever. Needles are
therefore built at runtime: forbidden words come from review_packet.py,
local paths / username come from USERPROFILE, and secret prefixes are
written as adjacent literals (Python joins them, the source does not).
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANON_SOURCE = ROOT / "src" / "arena" / "review_packet.py"
SELF = Path(__file__).resolve()

# Adjacent literals are intentional. A contiguous literal here would make
# this file hit its own gate; runtime values stay exact after concatenation.
#
# 只有"凭据文件标记"这类本身就有意义的放这里；**令牌前缀不放**——
# 它们必须连一个足够长的随机串一起才算数（见 SECRET_PATTERNS）。
SECRET_PREFIXES = (
    "-----" "BEGIN",
    "PASS" "WORD=",
    "P" "AT=",
    "Bea" "rer ",
)

# 令牌形状：前缀 + 足够长的 base62 串 + 左边界不是字母数字。
#
# 为什么需要左边界和长度（2026-11-22 实测）：裸前缀匹配会把 `task-authoring-log.yaml`
# 判成 OpenAI 密钥（"ta|sk-"），于是一份全是文档改动的提交被闸门拦下 37 处。
# 那次是真·假阳性：`git log -p --all | grep 'sk-[A-Za-z0-9]\{16,\}'` 命中 0。
# 收紧后判据变成"看起来真的是一把钥匙"：前面不能紧跟字母数字，后面至少 20 位随机串
# （OpenAI 的 key ≥ 40 位，GitHub PAT ≥ 36 位，20 是留足余量的下界）。
_TOKEN_TAIL = r"[A-Za-z0-9_\-]{20,}"
_LEFT_EDGE = r"(?<![A-Za-z0-9])"
SECRET_PATTERNS = (
    re.compile(_LEFT_EDGE + "s" "k-" + _TOKEN_TAIL),
    re.compile(_LEFT_EDGE + "g" "hp_" + _TOKEN_TAIL),
    re.compile(_LEFT_EDGE + "github" "_pat_" + _TOKEN_TAIL),
)

EXIT_CLEAN = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2
EXIT_TIMEOUT = 3


class GateError(RuntimeError):
    """The gate could not decide. Never degrade this to CLEAN."""


def run(cmd: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError("TIMEOUT: " + " ".join(cmd[:3])) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise GateError(
            "git command failed: " + " ".join(cmd[:3])
            + " rc=" + str(proc.returncode)
            + (" | " + detail[0][:120] if detail else "")
        )
    return proc.stdout


def forbidden_words() -> tuple[str, ...]:
    """Single source of truth: reuse review_packet.py's own FORBIDDEN."""
    text = ANON_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^FORBIDDEN = (.+)$", text, re.M)
    if not match:
        raise GateError("FORBIDDEN not found in review_packet.py")
    try:
        words = ast.literal_eval(match.group(1))
    except (ValueError, SyntaxError) as exc:
        raise GateError("FORBIDDEN is malformed") from exc
    if not isinstance(words, (tuple, list)) or not words or not all(
        isinstance(w, str) for w in words
    ):
        raise GateError("FORBIDDEN is empty or has non-string entries")
    return tuple(words)


def needles() -> tuple[list[str], str]:
    """Derive local-path and username needles without hardcoding either."""
    home = (os.environ.get("USERPROFILE") or str(Path.home())).rstrip("\\/")
    parent = os.path.dirname(home)
    users_dir = os.path.basename(parent)
    drive = os.path.splitdrive(home)[0].rstrip(":").lower() or "c"
    sep = "\\"
    forward = home.replace(sep, "/")
    parent_forward = parent.replace(sep, "/")
    paths = [
        parent + sep,
        parent_forward + "/",
        "/" + drive + "/" + users_dir + "/",
        home + sep,
        forward + "/",
    ]
    return paths, os.path.basename(home)


def tag_text(text: str, words: tuple[str, ...], paths: list[str], username: str) -> list[str]:
    tags: list[str] = []
    if any(w in text for w in words):
        tags.append("FORBIDDEN")
    if any(p in text for p in paths):
        tags.append("ABS_PATH")
    if any(s in text for s in SECRET_PREFIXES):
        tags.append("SECRET")
    if not tags or "SECRET" not in tags:
        if any(p.search(text) for p in SECRET_PATTERNS):
            tags.append("SECRET")
    if username and username in text:
        tags.append("USERNAME")
    return tags


def self_audit(words: tuple[str, ...], paths: list[str], username: str) -> list[str]:
    """Guard against the gate blocking its own commit."""
    return tag_text(SELF.read_text(encoding="utf-8"), words, paths, username)


def scan_commit(
    sha: str, words: tuple[str, ...], paths: list[str], username: str,
    cache: dict[str, str],
) -> tuple[list[tuple[str, str, str]], int, int]:
    hits: list[tuple[str, str, str]] = []
    nfiles = 0

    # -z keeps paths with spaces intact; meta is exactly 3 fields.
    rows = run(["git", "ls-tree", "-r", "-z", sha]).split("\0")
    for row in rows:
        if "\t" not in row:
            continue
        meta, path = row.split("\t", 1)
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        nfiles += 1
        blob = parts[2]
        if blob not in cache:
            cache[blob] = run(["git", "cat-file", "blob", blob])
        tags = tag_text(cache[blob], words, paths, username)
        if tags:
            hits.append((path, "TREE", ",".join(tags)))

    # git show works for the root commit too (unlike "git diff sha^ sha").
    # Merge commits show no combined diff here; their tree blobs still count.
    patch = run([
        "git", "show", "--format=", "--no-ext-diff", "--no-color",
        "--no-renames", sha,
    ])
    added = [ln for ln in patch.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    tags = tag_text("\n".join(added), words, paths, username)
    if tags:
        hits.append(("(diff added lines)", "DIFF", ",".join(tags)))

    message = run(["git", "log", "-1", "--format=%B", sha])
    tags = tag_text(message, words, paths, username)
    if tags:
        hits.append(("(commit message)", "MSG", ",".join(tags)))
    return hits, nfiles, len(added)


def upstream_scope() -> tuple[str, list[str]]:
    try:
        upstream = run([
            "git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        ]).strip()
    except GateError as exc:
        raise GateError("no upstream for HEAD; refuse to report CLEAN") from exc
    label = upstream + "..HEAD"
    return label, run(["git", "rev-list", label]).split()


def verdict_for_empty_range(label: str) -> str:
    """Empty scope is NEVER CLEAN: there is nothing to certify."""
    print("SCAN_SCOPE=" + label)
    print("COMMITS_IN_RANGE=0")
    print("PUSH_GATE=NO_COMMITS")
    print("NOTE: empty range is NOT CLEAN; no push was certified")
    return "NO_COMMITS"


def run_gate(label: str, shas: list[str]) -> int:
    words = forbidden_words()
    paths, username = needles()
    audit = self_audit(words, paths, username)
    if audit:
        print("SELF_AUDIT_HITS=" + ",".join(audit))
        print("PUSH_GATE=ERROR")
        print("ERROR_DETAIL=gate source contains a needle; would self-block")
        return EXIT_ERROR

    if not shas:
        verdict_for_empty_range(label)
        return EXIT_CLEAN

    cache: dict[str, str] = {}
    total = 0
    print("SCAN_SCOPE=" + label)
    print("COMMITS_IN_RANGE=" + str(len(shas)))
    for sha in shas:
        hits, nfiles, nadded = scan_commit(sha, words, paths, username, cache)
        short = run(["git", "rev-parse", "--short", sha]).strip()
        subject = run(["git", "log", "-1", "--format=%s", sha]).strip()[:60]
        # subject may contain CJK; keep stdout ASCII-only.
        subject = subject.encode("ascii", "replace").decode("ascii")
        print("  " + short + " | files=" + str(nfiles)
              + " | added=" + str(nadded) + " | hits=" + str(len(hits))
              + " | " + subject)
        for item in hits[:5]:
            print("     " + item[0].encode("ascii", "replace").decode("ascii")
                  + " | " + item[1] + " | " + item[2])
        total += len(hits)
    print("TOTAL_HITS=" + str(total))
    print("PUSH_GATE=" + ("BLOCKED" if total else "CLEAN"))
    return EXIT_BLOCKED if total else EXIT_CLEAN


def self_test(dirty: str | None) -> int:
    words = forbidden_words()
    paths, username = needles()

    audit = self_audit(words, paths, username)
    print("SELF_AUDIT_HITS=" + str(len(audit)) + (" " + ",".join(audit) if audit else ""))

    cache: dict[str, str] = {}
    head_sha = run(["git", "rev-parse", "HEAD"]).strip()
    pos, _, _ = scan_commit(head_sha, words, paths, username, cache)
    print("POSITIVE_CURRENT_HEAD=" + ("BLOCKED" if pos else "CLEAN")
          + " hits=" + str(len(pos)) + " expected=CLEAN")

    empty_ok = True
    if not dirty:
        print("NEGATIVE_CONTROL=SKIPPED (missing --dirty)")
    else:
        try:
            full = run(["git", "rev-parse", "--verify", dirty + "^{commit}"]).strip()
            neg, _, _ = scan_commit(full, words, paths, username, cache)
            print("NEGATIVE_KNOWN_DIRTY=" + ("BLOCKED" if neg else "CLEAN")
                  + " hits=" + str(len(neg)) + " expected=BLOCKED")
            empty_ok = bool(neg)
        except GateError as exc:
            print("NEGATIVE_CONTROL=ERROR " + str(exc)[:120])
            empty_ok = False

    # Constructive check: empty scope must not be reported as CLEAN.
    buf = io.StringIO()
    with redirect_stdout(buf):
        verdict_for_empty_range("self-test")
    empty_verdict_ok = "PUSH_GATE=NO_COMMITS" in buf.getvalue() \
        and "PUSH_GATE=CLEAN" not in buf.getvalue()
    print("EMPTY_RANGE_NOT_CLEAN=" + str(empty_verdict_ok))

    ok = (not audit) and (not pos) and empty_ok and empty_verdict_ok and bool(dirty)
    print("SELF_TEST_PASS=" + str(ok))
    return EXIT_CLEAN if ok else EXIT_ERROR


def main() -> int:
    parser = argparse.ArgumentParser(description="pre-push content gate (rule 11)")
    parser.add_argument("--commit", help="scan exactly one commit (ten-minute control)")
    parser.add_argument("--self-test", action="store_true", help="run built-in controls")
    parser.add_argument("--dirty", help="known-dirty sha for the negative control")
    parser.add_argument(
        "--range", dest="range_spec",
        help="diagnostic only: scan an explicit revision range; "
             "the release gate always uses upstream..HEAD",
    )
    args = parser.parse_args()

    try:
        if args.range_spec and args.commit:
            raise GateError("--range and --commit are mutually exclusive")
        if args.self_test:
            return self_test(args.dirty)
        if args.range_spec:
            shas = run(["git", "rev-list", args.range_spec]).split()
            return run_gate("range:" + args.range_spec, shas)
        if args.commit:
            full = run(["git", "rev-parse", "--verify", args.commit + "^{commit}"]).strip()
            return run_gate("commit:" + args.commit, [full])
        label, shas = upstream_scope()
        return run_gate(label, shas)
    except GateError as exc:
        print("PUSH_GATE=ERROR")
        print("ERROR_DETAIL=" + str(exc).encode("ascii", "replace").decode("ascii")[:200])
        return EXIT_TIMEOUT if str(exc).startswith("TIMEOUT") else EXIT_ERROR
    except (OSError, UnicodeError) as exc:
        print("PUSH_GATE=ERROR")
        print("ERROR_DETAIL=" + type(exc).__name__ + ": "
              + str(exc).encode("ascii", "replace").decode("ascii")[:160])
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
