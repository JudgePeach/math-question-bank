#!/bin/bash

# MathBank macOS launcher. It owns only the PID recorded together with this
# exact project path; an unrelated listener on port 8000 is never terminated.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 1
cd "$SCRIPT_DIR" || exit 1

SYSTEM_DIR="$SCRIPT_DIR/.system_generated"
PID_FILE="$SYSTEM_DIR/server.pid"
IDENTITY_FILE="$SYSTEM_DIR/server.identity"
LOG_FILE="$SYSTEM_DIR/server.log"
PROJECT_ID="$SCRIPT_DIR"

echo "================================================="
echo "     本地数学题库教研系统 (MathBank) Mac 启动器"
echo "================================================="

pause_if_interactive() {
    if [ -t 0 ]; then
        read -r -n 1 -p "按任意键退出..." _unused
        echo ""
    fi
}

fail() {
    echo "❌ $1"
    pause_if_interactive
    exit 1
}

python_is_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1
}

if [ ! -d "$SCRIPT_DIR/venv" ]; then
    command -v python3 >/dev/null 2>&1 || \
        fail "未检测到 Python。请安装 Python 3.10 或更高版本。"
    python_is_supported "$(command -v python3)" || \
        fail "检测到的 Python 版本低于 3.10，请先升级。"

    echo "检测到首次运行，正在创建 Python 虚拟环境..."
    python3 -m venv "$SCRIPT_DIR/venv" || \
        fail "创建虚拟环境失败，请检查目录权限与 Python 安装。"
fi

PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
[ -x "$PYTHON_BIN" ] || fail "虚拟环境不完整：缺少 venv/bin/python。"
python_is_supported "$PYTHON_BIN" || \
    fail "现有虚拟环境使用 Python 3.10 以下版本，请删除 venv 后用新版 Python 重建。"

mkdir -p "$SYSTEM_DIR" || fail "无法创建运行状态目录。"

is_owned_server() {
    server_pid=$1
    case "$server_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ -f "$IDENTITY_FILE" ] || return 1
    [ "$(cat "$IDENTITY_FILE" 2>/dev/null)" = "$PROJECT_ID" ] || return 1
    kill -0 "$server_pid" 2>/dev/null || return 1

    server_command=$(ps -p "$server_pid" -o command= 2>/dev/null) || return 1
    case "$server_command" in
        *"$PYTHON_BIN"*"-m uvicorn main:app"*) ;;
        *) return 1 ;;
    esac

    # lsof is available on supported macOS versions. Confirming cwd protects
    # against PID reuse by another project with a similar command line.
    server_cwd=$(lsof -a -p "$server_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    [ "$server_cwd" = "$PROJECT_ID" ]
}

stop_previous_owned_server() {
    [ -f "$PID_FILE" ] || return 0
    old_pid=$(tr -d '[:space:]' <"$PID_FILE" 2>/dev/null)
    if is_owned_server "$old_pid"; then
        echo "正在停止本项目上一次启动的服务 (PID: $old_pid)..."
        kill -TERM "$old_pid" 2>/dev/null || return 1
        wait_count=0
        while kill -0 "$old_pid" 2>/dev/null && [ "$wait_count" -lt 20 ]; do
            sleep 0.25
            wait_count=$((wait_count + 1))
        done
        if kill -0 "$old_pid" 2>/dev/null; then
            echo "❌ 本项目旧服务未在 5 秒内退出；为避免误杀，启动器不会强制终止它。"
            return 1
        fi
    elif [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "❌ PID 文件指向无法验证身份的运行中进程 ($old_pid)，不会终止该进程。"
        return 1
    fi
    rm -f "$PID_FILE" "$IDENTITY_FILE"
    return 0
}

stop_previous_owned_server || fail "无法安全停止上一次服务，请查看状态文件。"

if [ -f "$SCRIPT_DIR/RELEASE-MANIFEST.json" ]; then
    echo "正在校验并完成 Release 覆盖升级..."
    "$PYTHON_BIN" -B -m scripts.release_overlay --platform macos || \
        fail "Release 覆盖升级校验失败。请重新解压完整新版，将其内容全部合并覆盖到原目录后再试。"
fi

[ -f "$SCRIPT_DIR/requirements.txt" ] || fail "缺少 requirements.txt，无法校验运行依赖。"
REQUIREMENTS_STAMP="$SYSTEM_DIR/requirements.sha256"
REQUIREMENTS_HASH=$("$PYTHON_BIN" -c \
    'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())' \
    2>/dev/null) || fail "无法计算 requirements.txt 摘要。"
INSTALLED_HASH=$(cat "$REQUIREMENTS_STAMP" 2>/dev/null || true)

echo "正在检查运行环境依赖是否完整..."
NEEDS_DEPENDENCY_INSTALL=0
[ "$INSTALLED_HASH" = "$REQUIREMENTS_HASH" ] || NEEDS_DEPENDENCY_INSTALL=1
if ! "$PYTHON_BIN" -c \
    "import fastapi, uvicorn, sqlalchemy, greenlet, colorama, multipart, dotenv, requests, PIL, fitz, docx, lxml, defusedxml, olefile, exceptiongroup, sniffio; import pdf_inspector" \
    >/dev/null 2>&1; then
    NEEDS_DEPENDENCY_INSTALL=1
fi
if ! "$PYTHON_BIN" -m pip check >/dev/null 2>&1; then
    NEEDS_DEPENDENCY_INSTALL=1
fi

if [ "$NEEDS_DEPENDENCY_INSTALL" -eq 1 ]; then
    echo "检测到依赖缺失、冲突或锁文件已变化，正在按 requirements.txt 同步..."
    "$PYTHON_BIN" -m pip install --disable-pip-version-check -r requirements.txt || \
        fail "依赖安装失败，请检查网络连接或代理设置后重试。"
    "$PYTHON_BIN" -m pip check || \
        fail "依赖一致性检查失败，请查看上方 pip check 输出。"
    STAMP_TEMP="$REQUIREMENTS_STAMP.tmp.$$"
    printf '%s\n' "$REQUIREMENTS_HASH" >"$STAMP_TEMP" || \
        fail "无法写入依赖锁摘要。"
    mv -f "$STAMP_TEMP" "$REQUIREMENTS_STAMP" || \
        fail "无法原子更新依赖锁摘要。"
fi

PORT_PIDS=$(lsof -n -P -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null | sort -u)
if [ -n "$PORT_PIDS" ]; then
    fail "端口 8000 已被其他进程占用 (PID: $(echo "$PORT_PIDS" | tr '\n' ' '))；启动器不会终止陌生进程。"
fi

rm -f "$LOG_FILE"
echo "正在启动服务: http://127.0.0.1:8000"
nohup "$PYTHON_BIN" -m uvicorn main:app --host 127.0.0.1 --port 8000 \
    >"$LOG_FILE" 2>&1 &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" >"$PID_FILE"
printf '%s\n' "$PROJECT_ID" >"$IDENTITY_FILE"
disown "$SERVER_PID" 2>/dev/null || true

echo "正在探测后台服务启动状态，等待就绪..."
SERVICE_READY=0
if "$PYTHON_BIN" - "$SERVER_PID" >/dev/null 2>&1 <<'PY'
import os
import sys
import time
import urllib.request

server_pid = int(sys.argv[1])
deadline = time.monotonic() + 10.0
while time.monotonic() < deadline:
    remaining = deadline - time.monotonic()
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/healthz",
            timeout=max(0.05, min(0.4, remaining)),
        ) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception:
        pass
    try:
        os.kill(server_pid, 0)
    except OSError:
        break
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(0.5, remaining))
raise SystemExit(1)
PY
then
    SERVICE_READY=1
fi

if [ "$SERVICE_READY" -ne 1 ]; then
    if is_owned_server "$SERVER_PID"; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$IDENTITY_FILE"
    echo "❌ 服务未能通过健康检查，浏览器不会打开。"
    echo "---------------- 最近日志 ----------------"
    tail -n 80 "$LOG_FILE" 2>/dev/null || echo "未找到服务日志。"
    echo "--------------------------------------------"
    pause_if_interactive
    exit 1
fi

echo "🎉 后台服务已就绪，正在打开浏览器。"
open "http://127.0.0.1:8000" || \
    echo "⚠️ 无法自动打开浏览器，请手动访问 http://127.0.0.1:8000"
echo "服务 PID 已记录在 .system_generated/server.pid。"
exit 0
