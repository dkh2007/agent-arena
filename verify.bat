@echo off
rem verify.bat - one-click verification for agent-arena
rem Usage: double-click, or run from cmd.
rem   verify.bat            interactive: pause at the end so the window stays open
rem   verify.bat /nopause   unattended/CI: no key prompt; exit code 0 only if all checks pass
rem   verify.bat /fast     parallel test run (pytest -n 4 --dist loadfile, ~3.5 min vs ~16 min)
rem   [1] pytest tests\        - product code behavior matches claims
rem   [2] pytest spike\        - upstream A2E defect sentinel (skips if A2E source absent)
rem   [3] DTO + full pipeline - 5 fixtures, schema, all 5 assertions recomputed from raw
rem   [4] review packet        - is docs/review-packet.md still true to the repo? (soft check)
rem   [5] git + push gate      - commits, tree state, pre-push content gate (rule 11)
rem Path note: prefers the project-local .venv from README, then this machine's
rem shared WorkBuddy venv, then PATH python. Fails early if deps are missing.
rem Encoding note: this file is ASCII-only and every check it calls prints ASCII verdicts.
rem   cmd.exe parses UTF-8 CJK bytes as GBK and can even turn them into redirections.

cd /d "%~dp0"

rem Unattended mode: no trailing pause, and the exit code carries the verdict.
rem Why it exists: without it this script cannot be run by CI or by another script
rem (the pause blocks forever), so "did anyone ever run the whole thing?" stayed open.
if /i "%~1"=="/nopause" set NOPAUSE=1
rem /fast is opt-in on purpose: the serial run stays the default so that "serial and
rem parallel agree" keeps being re-checked by the command people actually type.
if /i "%~1"=="/fast" set FASTOPT=-n 4 --dist loadfile

rem --- Python discovery (portable) ------------------------------------------
rem A fresh collaborator follows README and gets .venv\Scripts\python.exe.
rem Hard-coding one author's path here made every check red on every other
rem machine - including the collaborator who just pulled this repo.
set PY=
if exist ".venv\Scripts\python.exe" set PY=".venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set PY="%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY set PY=python
rem Early fail with plain ASCII: a wrong interpreter or missing deps otherwise
rem paints all five checks red for the wrong reason.
%PY% -c "import pytest, fsrs, yaml, jsonschema, jmespath" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python at %PY% cannot import required packages.
    echo        Fix: python -m venv .venv
    echo        Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    if not defined NOPAUSE pause
    exit /b 1
)
rem A2E lives one level up from this repo (repo itself stays free of the 80MB upstream tree)
set A2EPY=..\tools\A2E-main\server\.venv\Scripts\python.exe

echo ==================================================
echo [1/5] pytest tests\ + jmespath expressiveness
echo ==================================================
rem jmespath test belongs here: it needs only our own venv, no upstream A2E
%PY% -m pytest tests\ spike\test_jmespath_expressiveness.py %FASTOPT% -v
set PYEXIT=%ERRORLEVEL%

echo.
echo ==================================================
echo [2/5] pytest spike\ - upstream A2E defect still present?
echo ==================================================
if exist "%A2EPY%" (
    rem -p no:postgresql is required: upstream venv ships pytest-postgresql but libpq is missing
    %A2EPY% -m pytest spike\test_a2e_semantics.py -v -p no:postgresql
    rem Runtime form on purpose: inside a block %ERRORLEVEL% is expanded when the
    rem block is READ, i.e. it would capture the value from BEFORE this pytest run.
    rem That made the spike verdict silently always 0 - a green that could not go red.
    set SPIKE_EXIT=0
    if errorlevel 1 set SPIKE_EXIT=1
) else (
    echo SKIP: A2E source not present at ..\tools\A2E-main\server
    set SPIKE_EXIT=0
)

echo.
echo ==================================================
echo [3/5] DTO + full pipeline - fixtures, schema, R3 from raw trace
echo ==================================================
rem Each fixture is a fake dbg-001 submission; a non-zero exit means the report
rem violated an invariant (not just "ugly output"), see docs/diagnosis-report-schema.md
%PY% src\arena\diagnosis_report.py --fixture all
set DTO_EXIT=%ERRORLEVEL%
rem S3-3-6: run the SAME five fixtures through recompute_all() -> report.
rem If this disagrees with the line above, the wiring is broken.
%PY% src\arena\trace_pipeline.py --fixture all
set PIPE_EXIT=%ERRORLEVEL%
if not "%PIPE_EXIT%"=="0" set DTO_EXIT=1

echo.
echo ==================================================
echo [4/5] review packet - still true to the repo? (soft check)
echo ==================================================
rem Soft on purpose: a stale packet does not break the code, only the hand-off.
rem --check replays the recorded test-result line and does NOT rerun tests:
rem test health is what [1/5] above is for. Keep those two duties separate.
rem Output is suppressed so only the ASCII verdict shows (the script speaks Chinese).
%PY% src\arena\review_packet.py --check >nul 2>&1
set REVIEW_EXIT=%ERRORLEVEL%
if "%REVIEW_EXIT%"=="0" (
    echo OK: docs/review-packet.md matches the repo.
) else (
    echo STALE: packet out of sync - run before sending it to an external reviewer:
    echo        %PY% src\arena\review_packet.py
    echo        (run with --check to see the exact diff)
)

echo.
echo ==================================================
echo [5/5] git - commits and working tree state
echo ==================================================
git log --oneline -5
echo.
git status -sb

echo.
echo [5b] push gate - every commit in origin..HEAD (rule 11)
rem Hard gate: BLOCKED means do NOT push. An empty range reports NO_COMMITS,
rem which is explicitly NOT CLEAN - there is nothing to certify.
%PY% scripts\push_gate.py
set GATE_EXIT=%ERRORLEVEL%

echo.
rem Verdict travels in the exit code: 0 only when every hard check passed.
rem The packet check stays soft (REVIEW_EXIT only adds a note), by design - a stale
rem packet breaks the hand-off, not the code.
set RC=1
if "%PYEXIT%"=="0" if "%SPIKE_EXIT%"=="0" if "%DTO_EXIT%"=="0" if "%GATE_EXIT%"=="0" (
    set RC=0
    echo RESULT: ALL GREEN - claims verified.
    if "%REVIEW_EXIT%"=="1" echo NOTE: review packet is stale ^(see [4/5]^) - regenerate before handing it off.
) else (
    echo RESULT: a check FAILED - paste the full output back.
    echo   ^(spike failing means upstream A2E behaviour CHANGED - re-evaluate, do not "fix" the test blindly^)
    echo   ^(PUSH_GATE=BLOCKED means a pending commit contains forbidden content - do not push^)
)
echo.
if not defined NOPAUSE pause
exit /b %RC%
