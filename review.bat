@echo off
rem review.bat - regenerate the external review packet in one click.
rem
rem Workflow this replaces:
rem   read the dev output -> judge what is worth forwarding -> reword it -> paste -> read back
rem Workflow now:
rem   double-click this -> copy docs\review-packet.md -> paste to the external reviewer
rem
rem ASCII-only on purpose: cmd.exe parses UTF-8 CJK bytes as GBK and can turn
rem them into redirections, which has already created stray files in this repo.
rem
rem IMPORTANT: do NOT regenerate just to make snapshot-sha equal HEAD after
rem committing the packet. The snapshot is pinned to the generation anchor on
rem purpose; a regeneration would only move the anchor one commit back again.
rem A green --check means the content is still true; that is the criterion.
rem
rem Default mode really runs the full test suite (~38s) and records the honest
rem pass/fail counts in the packet. For local iteration only, call the script
rem with --fast; NEVER hand off a packet generated with --fast.

cd /d "%~dp0"

set PY="%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

echo ==================================================
echo regenerating docs\review-packet.md from the repo
echo ==================================================
%PY% src\arena\review_packet.py
if not "%ERRORLEVEL%"=="0" (
    echo.
    echo FAILED - nothing was handed off. Fix the message above and re-run.
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo sanity check - does the content still match the repo?
echo ==================================================
%PY% src\arena\review_packet.py --check >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    echo OK: content hash verified - safe to hand off.
) else (
    echo WARN: content hash mismatch. Either the file was edited by hand, or the
    echo       repo moved between the two runs. Re-run this script.
)

echo.
echo ==================================================
echo next steps
echo ==================================================
echo   1. open  docs\review-packet.md
echo   2. copy the WHOLE file, paste it to the external reviewer
echo   3. paste the answers back - you only need to decide the P/Q items
echo.
echo Manual edits go to docs\review-packet-manual.md, never to review-packet.md
echo (the latter is generated; the check above will flag hand edits).
echo.
pause
