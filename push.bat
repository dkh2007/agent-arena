@echo off
rem push.bat - push local commits to GitHub, auto-detecting the working path.
rem   Path A: direct / accelerator mode. Watt Toolkit makes github.com resolve to
rem           127.0.0.1, so the sandbox egress proxy must be bypassed.
rem   Path B: via sandbox egress proxy (pypi works, github is flaky/blocked).
rem Criterion: ls-remote returns refs. Neither path -> network layer, not the repo.
rem Non-destructive: push only, never force.

setlocal
cd /d "%~dp0"
set GIT_TERMINAL_PROMPT=0

echo [0] DNS for github.com
nslookup github.com 2>nul | findstr /i "Address"

echo.
echo [1] probe: default path (sandbox egress proxy)
git ls-remote --heads origin >nul 2>&1
if not errorlevel 1 goto PUSH_DEFAULT
echo     FAIL

echo.
echo [2] probe: bypass proxy (use this after starting the accelerator)
set "no_proxy=github.com,*.github.com,127.0.0.1,localhost"
set "NO_PROXY=github.com,*.github.com,127.0.0.1,localhost"
git -c http.proxy= -c http.version=HTTP/1.1 ls-remote --heads origin >nul 2>&1
if not errorlevel 1 goto PUSH_BYPASS

echo     FAIL
echo.
echo ==================================================
echo Both paths FAILED. This is the network, not the repo.
echo   Evidence: proxy -> pypi 200 / github 502-or-000; direct TCP 443 refused.
echo   Fix: start Watt Toolkit, enable GitHub acceleration, wait for it to
echo        report connected, then run this script again.
echo ==================================================
goto END

:PUSH_DEFAULT
echo     OK
echo.
echo [3] pushing (default path)
git push -u origin main
goto END

:PUSH_BYPASS
echo     OK
echo.
echo [3] pushing (proxy bypassed)
git -c http.proxy= -c http.version=HTTP/1.1 push -u origin main
goto END

:END
echo.
echo ---------------- state ----------------
git status -sb
echo.
git log --oneline -3
echo.
pause
endlocal
