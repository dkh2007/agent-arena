@echo off
rem verify.bat - one-click verification for agent-arena
rem Usage: double-click, or run from cmd. Level-1 (pytest) + Level-2 (git state).
rem Path note: uses the isolated venv (pytest/fsrs/pyyaml live only there), never the system Python.

cd /d "%~dp0"

echo ==================================================
echo [1/2] pytest - code behavior matches claims?
echo ==================================================
"%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m pytest tests\ -v
set PYEXIT=%ERRORLEVEL%

echo.
echo ==================================================
echo [2/2] git - commits and working tree state
echo ==================================================
git log --oneline -5
echo.
git status --short
if "%PYEXIT%"=="0" (
    echo.
    echo RESULT: ALL GREEN - claims verified.
) else (
    echo.
    echo RESULT: pytest FAILED - paste the error output back.
)
echo.
pause
