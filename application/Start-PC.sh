#!/bin/sh
# IWIN PC 控制台与实时服务启动脚本（兼容 Linux GUI 桌面与 SSH 终端）
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="$(which python3 2>/dev/null || which python 2>/dev/null || echo "python3")"

# 如果处于 SSH 或无桌面环境 ($DISPLAY 为空)，自动回退到终端无头模式
if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    echo "=========================================================="
    echo "提示：当前处于无图形桌面环境 (\$DISPLAY 为空，如 SSH 登录)。"
    echo "正在以【终端无头模式】启动 IWIN 实时服务（默认监听 ROV UDP 5600）..."
    echo "=========================================================="
    exec "$PYTHON_BIN" "communication/pc/lan_server.py" "$@"
else
    # 有桌面环境，启动图形控制台
    exec "$PYTHON_BIN" "communication/pc/pc_control.pyw" "$@"
fi
