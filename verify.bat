@echo off
rem verify.bat - one-click verification for agent-arena
rem Usage: double-click, or run from cmd.
rem   [1] pytest tests\        - product code behavior matches claims
rem   [2] pytest spike\        - upstream A2E defect sentinel (skips if A2E source absent)
rem   [3] git                  - commits and working tree state
rem Path note: uses the isolated venv (pytest/fsrs/pyyaml live only there), never the system Python.

cd /d "%~dp0"

set PY="%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
rem A2E lives one level up from this repo (repo itself stays free of the 80MB upstream tree)
set A2EPY=..\tools\A2E-main\server\.venv\Scripts\python.exe

echo ==================================================
echo [1/3] pytest tests\ + jmespath expressiveness
echo ==================================================
rem jmespath test belongs here: it needs only our own venv, no upstream A2E
%PY% -m pytest tests\ spike\test_jmespath_expressiveness.py -v
set PYEXIT=%ERRORLEVEL%

echo.
echo ==================================================
echo [2/3] pytest spike\ - upstream A2E defect still present?
echo ==================================================
if exist "%A2EPY%" (
    rem -p no:postgresql is required: upstream venv ships pytest-postgresql but libpq is missing
    %A2EPY% -m pytest spike\test_a2e_semantics.py -v -p no:postgresql
    set SPIKE_EXIT=%ERRORLEVEL%
) else (
    echo SKIP: A2E source not present at ..\tools\A2E-main\server
    set SPIKE_EXIT=0
)

echo.
echo ==================================================
echo [3/3] git - commits and working tree state
echo ==================================================
git log --oneline -5
echo.
git status --short

echo.
if "%PYEXIT%"=="0" if "%SPIKE_EXIT%"=="0" (
    echo RESULT: ALL GREEN - claims verified.
) else (
    echo RESULT: a check FAILED - paste the full output back.
    echo   ^(spike failing means upstream A2E behaviour CHANGED - re-evaluate, do not "fix" the test blindly^)
)
echo.
pause
