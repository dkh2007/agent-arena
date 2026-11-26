#!/usr/bin/env sh
# macOS / Linux：双击可能不执行，建议在终端里 `sh start.sh`
cd "$(dirname "$0")" || exit 1
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! $PY --version >/dev/null 2>&1; then
  echo "[失败] 这台电脑上没找到 Python 3。装个 3.10 或更新的版本再来。"
  exit 1
fi

if ! $PY -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >/dev/null 2>&1; then
  echo "[失败] Python 太旧（$($PY --version 2>&1)）。需要 3.10 或更新。"
  exit 1
fi

if ! $PY -c "import yaml, jsonschema" >/dev/null 2>&1; then
  echo "第一次运行，先装两个依赖（要联网，几十秒）..."
  if ! $PY -m pip install --quiet pyyaml jsonschema; then
    echo
    echo "[失败] 依赖没装上。多半是没联网，或者这台机器没装 Python 3.10+。"
    exit 1
  fi
fi

echo
echo "服务起来后，屏幕上有题面和一个 http://127.0.0.1:8765 的地址。"
echo "用你自己的 Agent 对着它做题；做完回到这个窗口按 Ctrl+C 停止。"
echo "过程会自动记进 calls.jsonl，把这个文件发回来就行。"
echo
exec $PY scripts/lab.py --task dbg-001-order-report --log calls.jsonl
