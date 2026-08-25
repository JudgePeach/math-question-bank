#!/bin/bash

# MathBank macOS launcher. It stops only the recorded instance or a listener
# whose process tree, working directory, and project files all verify MathBank.

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

find_supported_python() {
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        candidate_path=$(command -v "$candidate" 2>/dev/null || true)
        if [ -n "$candidate_path" ] && python_is_supported "$candidate_path"; then
            printf '%s\n' "$candidate_path"
            return 0
        fi
    done
    return 1
}

process_cwd() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | sed -n '1p'
}

process_executable() {
    lsof -a -p "$1" -d txt -Fn 2>/dev/null | sed -n 's/^n//p' | sed -n '1p'
}

expected_python_executable() {
    "$PYTHON_BIN" -c '
import os
from pathlib import Path
import sys

framework_executable = (
    Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
)
candidate = framework_executable if framework_executable.is_file() else Path(sys._base_executable)
print(os.path.realpath(candidate))
' 2>/dev/null
}

is_mathbank_project_root() {
    candidate_root=$1
    [ -n "$candidate_root" ] || return 1
    [ -f "$candidate_root/main.py" ] || return 1
    [ -f "$candidate_root/requirements.txt" ] || return 1
    [ -f "$candidate_root/static/index.html" ] || return 1
    [ -f "$candidate_root/启动题库系统.command" ] || return 1
    grep -q 'FastAPI' "$candidate_root/main.py" 2>/dev/null || return 1
    grep -q 'MathBank' "$candidate_root/static/index.html" 2>/dev/null || return 1
}

find_verified_mathbank_owner() {
    inspected_pid=$1
    expected_root=$(process_cwd "$inspected_pid")
    is_mathbank_project_root "$expected_root" || return 1
    inspection_count=0

    while [ "$inspection_count" -lt 6 ]; do
        case "$inspected_pid" in
            ''|*[!0-9]*|0|1) return 1 ;;
        esac
        inspected_cwd=$(process_cwd "$inspected_pid")
        [ "$inspected_cwd" = "$expected_root" ] || return 1
        inspected_command=$(ps -p "$inspected_pid" -o command= 2>/dev/null) || return 1
        case " $inspected_command " in
            *" -m uvicorn main:app "*)
                printf '%s\n' "$inspected_pid"
                return 0
                ;;
        esac
        inspected_pid=$(ps -p "$inspected_pid" -o ppid= 2>/dev/null | tr -d '[:space:]')
        inspection_count=$((inspection_count + 1))
    done
    return 1
}

listener_pids() {
    lsof -n -P -t -iTCP:8000 -sTCP:LISTEN 2>/dev/null | sort -u
}

stop_verified_legacy_mathbank_listeners() {
    port_pids=$(listener_pids)
    [ -n "$port_pids" ] || return 0

    verified_owners=""
    for port_pid in $port_pids; do
        verified_owner=$(find_verified_mathbank_owner "$port_pid") || \
            fail "端口 8000 已被其他进程占用 (PID: $(echo "$port_pids" | tr '\n' ' '))；启动器不会终止无法确认身份的进程。"
        verified_owners="$verified_owners
$verified_owner"
    done
    verified_owners=$(printf '%s\n' "$verified_owners" | sed '/^[[:space:]]*$/d' | sort -u)

    echo "检测到另一份或旧版 MathBank 仍在运行，正在安全停止 (PID: $(echo "$verified_owners" | tr '\n' ' '))..."
    for verified_owner in $verified_owners; do
        kill -0 "$verified_owner" 2>/dev/null || continue
        rechecked_owner=$(find_verified_mathbank_owner "$verified_owner") || \
            fail "旧版 MathBank 进程身份在停止前发生变化 ($verified_owner)，已取消操作。"
        [ "$rechecked_owner" = "$verified_owner" ] || \
            fail "旧版 MathBank 进程身份核验不一致 ($verified_owner)，已取消操作。"
        if ! kill -TERM "$verified_owner" 2>/dev/null && kill -0 "$verified_owner" 2>/dev/null; then
            fail "无法停止已确认的旧版 MathBank 进程 ($verified_owner)。"
        fi
    done

    wait_count=0
    while [ -n "$(listener_pids)" ] && [ "$wait_count" -lt 20 ]; do
        sleep 0.25
        wait_count=$((wait_count + 1))
    done
    remaining_pids=$(listener_pids)
    [ -z "$remaining_pids" ] || \
        fail "旧版 MathBank 未在 5 秒内退出 (PID: $(echo "$remaining_pids" | tr '\n' ' '))；请在原页面使用电源键关闭后重试。"
}

mkdir -p "$SYSTEM_DIR" || fail "无法创建运行状态目录。"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"

is_owned_server() {
    server_pid=$1
    case "$server_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ -f "$IDENTITY_FILE" ] || return 1
    [ "$(cat "$IDENTITY_FILE" 2>/dev/null)" = "$PROJECT_ID" ] || return 1
    kill -0 "$server_pid" 2>/dev/null || return 1

    server_command=$(ps -p "$server_pid" -o command= 2>/dev/null) || return 1
    case " $server_command " in
        *" -m uvicorn main:app "*) ;;
        *) return 1 ;;
    esac

    server_executable=$(process_executable "$server_pid") || return 1
    expected_executable=$(expected_python_executable) || return 1
    [ -n "$server_executable" ] && [ "$server_executable" = "$expected_executable" ] || return 1

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
stop_verified_legacy_mathbank_listeners

LEGACY_VENV_BACKUP=""
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    HOST_PYTHON=$(find_supported_python) || \
        fail "未检测到 Python 3.10 或更高版本，请先安装后重试。"
    echo "检测到首次运行，正在使用 $HOST_PYTHON 创建 Python 虚拟环境..."
    "$HOST_PYTHON" -m venv "$SCRIPT_DIR/venv" || \
        fail "创建虚拟环境失败，请检查目录权限与 Python 安装。"
elif [ ! -x "$PYTHON_BIN" ] || ! python_is_supported "$PYTHON_BIN"; then
    HOST_PYTHON=$(find_supported_python) || \
        fail "现有虚拟环境低于 Python 3.10，且未找到可用于自动重建的 Python 3.10+。请先安装新版 Python。"
    LEGACY_VENV_BACKUP="$SYSTEM_DIR/venv-python-legacy-$$"
    echo "检测到旧版 Python 虚拟环境，正在使用 $HOST_PYTHON 自动重建..."
    mv "$SCRIPT_DIR/venv" "$LEGACY_VENV_BACKUP" || \
        fail "无法备份旧虚拟环境，自动重建已取消。"
    if ! "$HOST_PYTHON" -m venv "$SCRIPT_DIR/venv"; then
        [ ! -e "$SCRIPT_DIR/venv" ] || \
            mv "$SCRIPT_DIR/venv" "$SYSTEM_DIR/venv-rebuild-failed-$$" 2>/dev/null || true
        mv "$LEGACY_VENV_BACKUP" "$SCRIPT_DIR/venv" 2>/dev/null || true
        fail "自动重建虚拟环境失败；旧环境已保留，请检查目录权限与 Python 安装。"
    fi
fi

PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
[ -x "$PYTHON_BIN" ] || fail "虚拟环境不完整：缺少 venv/bin/python。"
python_is_supported "$PYTHON_BIN" || \
    fail "虚拟环境仍低于 Python 3.10，无法启动。"

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
    "import fastapi, uvicorn, sqlalchemy, greenlet, colorama, multipart, dotenv, requests, PIL, docx, lxml, defusedxml, olefile, exceptiongroup, sniffio; import pymupdf as fitz; import pdf_inspector" \
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

PORT_PIDS=$(listener_pids)
if [ -n "$PORT_PIDS" ]; then
    fail "端口 8000 在启动前再次被占用 (PID: $(echo "$PORT_PIDS" | tr '\n' ' '))；请重试。"
fi

rm -f "$LOG_FILE" "$DIR/.system_generated/probe.log"
echo "正在启动服务: http://127.0.0.1:8000"
nohup "$PYTHON_BIN" -u -m uvicorn main:app --host 127.0.0.1 --port 8000 \
    >"$LOG_FILE" 2>&1 &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" >"$PID_FILE"
printf '%s\n' "$PROJECT_ID" >"$IDENTITY_FILE"
disown "$SERVER_PID" 2>/dev/null || true

echo "正在探测后台服务启动状态，等待就绪..."
SERVICE_READY=0
if "$PYTHON_BIN" - "$SERVER_PID" "$DIR/.system_generated/probe.log" >/dev/null 2>&1 <<'PY'
import os
import sys
from pathlib import Path
import time
import urllib.error
import urllib.request

server_pid = int(sys.argv[1])
probe_log_path = Path(sys.argv[2])
deadline = time.monotonic() + 60.0
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last_probe = "服务尚未响应"
while time.monotonic() < deadline:
    remaining = deadline - time.monotonic()
    try:
        with opener.open(
            "http://127.0.0.1:8000/healthz",
            timeout=max(0.05, min(2.0, remaining)),
        ) as response:
            if response.status == 200:
                raise SystemExit(0)
            body = response.read().decode("utf-8", errors="replace")
            last_probe = f"HTTP {response.status} - {body}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        last_probe = f"HTTP {exc.code} ({exc.reason}): {body}"
    except Exception as exc:
        last_probe = f"{type(exc).__name__}: {exc}"
    try:
        os.kill(server_pid, 0)
    except OSError:
        last_probe = f"进程已提前退出 [PID: {server_pid}] - {last_probe}"
        try:
            probe_log_path.write_text(last_probe, encoding="utf-8")
        except OSError:
            pass
        break
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(0.5, remaining))
try:
    probe_log_path.write_text(f"探测超时 (已等待 60 秒): {last_probe}", encoding="utf-8")
except OSError:
    pass
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
    echo "============================================"
    echo "❌ 服务未能通过健康检查，浏览器不会打开。"
    if [ -f "$DIR/.system_generated/probe.log" ]; then
        echo "---------------- 探针诊断信息 ----------------"
        cat "$DIR/.system_generated/probe.log"
        echo ""
    fi
    echo "---------------- 最近服务日志 ----------------"
    tail -n 80 "$LOG_FILE" 2>/dev/null || echo "未找到服务日志。"
    echo "============================================"
    pause_if_interactive
    exit 1
fi

echo "🎉 后台服务已就绪，正在打开浏览器。"
if [ -n "$LEGACY_VENV_BACKUP" ] && [ -d "$LEGACY_VENV_BACKUP" ]; then
    "$PYTHON_BIN" - "$LEGACY_VENV_BACKUP" >/dev/null 2>&1 <<'PY' || \
        echo "⚠️ 旧虚拟环境备份未能自动清理：$LEGACY_VENV_BACKUP"
import pathlib
import shutil
import sys

backup = pathlib.Path(sys.argv[1])
shutil.rmtree(backup)
PY
fi
open "http://127.0.0.1:8000" || \
    echo "⚠️ 无法自动打开浏览器，请手动访问 http://127.0.0.1:8000"
echo "服务 PID 已记录在 .system_generated/server.pid。"
exit 0
