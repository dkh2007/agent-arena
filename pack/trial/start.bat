@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo [失败] 这台电脑上没找到 Python。
  echo   去 python.org 下 3.10 或更新的版本，装的时候**勾上** "Add python.exe to PATH"，
  echo   装完关掉这个窗口，重新双击 start.bat。
  pause
  exit /b 1
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo [失败] 这台电脑的 Python 太旧（需要 3.10 或更新）。
  echo   先跑 "%PY% --version" 看一眼版本；太旧就去 python.org 装个新的。
  echo   不换版本也能跑，但会报一堆看不懂的语法错——所以这里直接停。
  pause
  exit /b 1
)

%PY% -c "import yaml, jsonschema" >nul 2>nul
if errorlevel 1 (
  echo 第一次运行，先装两个依赖（要联网，几十秒）...
  %PY% -m pip install --quiet pyyaml jsonschema
  if errorlevel 1 (
    echo.
    echo [失败] 依赖没装上。多半是没联网，或者这台机器没装 Python。
    echo   没装 Python：去 python.org 下 3.10 以上版本，装的时候勾上 "Add to PATH"。
    echo   装完重新双击这个文件。
    pause
    exit /b 1
  )
)

echo.
echo 服务起来后，屏幕上有题面和一个 http://127.0.0.1:8765 的地址。
echo 用你自己的 Agent 对着它做题；做完回到这个窗口按 Ctrl+C 停止。
echo 过程会自动记进 calls.jsonl，把这个文件发回来就行。
echo.
%PY% scripts\lab.py --task dbg-001-order-report --log calls.jsonl
pause
