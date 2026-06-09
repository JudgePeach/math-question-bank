#!/bin/bash
# 自动定位到当前脚本所在文件夹
cd "$(dirname "$0")"

# 保存当前会话的 tty，用于后续精确关闭自己所在的窗口
CURRENT_TTY=$(tty)

echo "================================================="
echo "     本地数学题库教研系统 (MathBank) Mac 启动器"
echo "================================================="

# 检测并创建虚拟环境
if [ ! -d "venv" ]; then
    echo "检测到首次运行，正在为您初始化本地运行环境..."
    
    # 检查 python3 是否安装
    if ! command -v python3 &>/dev/null; then
        echo "❌ 未检测到系统安装了 python3！"
        echo "请先在 macOS 中安装 Python 3 (推荐 3.8+)。"
        echo "提示：您可以从 App Store 安装 Xcode Command Line Tools，或者直接访问 python.org 下载安装。"
        echo "================================================="
        read -n 1 -r -p "按任意键退出..."
        exit 1
    fi
    
    echo "正在创建虚拟环境 (venv)..."
    python3 -m venv venv
    if [ ! -d "venv" ]; then
        echo "❌ 创建虚拟环境失败，请检查目录读写权限或 Python 3 安装是否完整。"
        echo "================================================="
        read -n 1 -r -p "按任意键退出..."
        exit 1
    fi
    
    echo "正在升级 pip 并安装项目依赖，首次启动可能需要数十秒，请稍候..."
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
    
    if [ $? -ne 0 ]; then
        echo "❌ 依赖包安装失败，请检查网络连接或代理设置后重试。"
        echo "================================================="
        # 清除不完整的 venv 目录以允许下次重试
        rm -rf venv
        read -n 1 -r -p "按任意键退出..."
        exit 1
    fi
    echo "🎉 环境依赖初始化成功！"
    echo "================================================="
    echo ""
fi

# 自动检测并强力清理霸占 8000 端口的残余 Python/Uvicorn 僵尸进程，确保 100% 启动成功
PORT_PID=$(lsof -t -i:8000)
if [ ! -z "$PORT_PID" ]; then
    echo "检测到端口 8000 被上一次未完全释放的残余进程 ($PORT_PID) 占用。"
    echo "正在为您安全释放端口并清理运行环境..."
    for pid in $PORT_PID; do
        # 尝试获取并清理父进程（例如在 reload 模式下的 Uvicorn 监听主进程）
        PPID_VAL=$(ps -o ppid= -p $pid 2>/dev/null | tr -d ' ')
        if [ ! -z "$PPID_VAL" ] && [ "$PPID_VAL" -ne 1 ]; then
            kill -9 $PPID_VAL &>/dev/null
        fi
        kill -9 $pid &>/dev/null
    done
    sleep 0.5
fi

# 双重保险：清理任何带有 uvicorn main:app 标识的残留 python 进程
REDUNDANT_PIDS=$(ps aux | grep -i 'uvicorn main:app' | grep -v grep | awk '{print $2}')
if [ ! -z "$REDUNDANT_PIDS" ]; then
    kill -9 $REDUNDANT_PIDS &>/dev/null
    sleep 0.5
fi

echo "正在本地加载环境并为您启动服务..."
echo "服务启动后，将在浏览器中自动打开: http://127.0.0.1:8000"
echo "================================================="
echo ""

# 建立存放日志的隐藏目录并清理上一次日志
mkdir -p .system_generated
rm -f .system_generated/server.log

# 🟢 使用 venv 中的 python 将 uvicorn 服务发送到后台静默运行，并输出重定向到 server.log，防阻塞
nohup ./venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 >.system_generated/server.log 2>&1 &
disown $! 2>/dev/null

# 🔄 自适应端口健康检查，最大等待 10 秒（每 0.5 秒检测一次）
echo "正在探测后台服务启动状态，等待就绪..."
TIMEOUT=20
COUNTER=0
SERVICE_READY=0

while [ $COUNTER -lt $TIMEOUT ]; do
    # 使用 curl 探测 /api/questions 端口响应，加上 || true 防御 Network Refused 报错
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/questions || echo "000")
    if [ "$HTTP_STATUS" = "200" ]; then
        echo "🎉 后台服务已成功拉起，题库 API 响应正常！"
        SERVICE_READY=1
        break
    fi
    sleep 0.5
    COUNTER=$((COUNTER + 1))
done

if [ $SERVICE_READY -eq 0 ]; then
    echo "⚠️ 探测服务启动超时 (已等待 10 秒)，尝试直接拉起浏览器..."
fi

# 🟢 服务已完全就绪，拉起默认浏览器展示 MathBank UI
open "http://127.0.0.1:8000"

# 🟢 自动检测当前运行环境，并极其优雅地只关闭当前这一个终端窗口会话，决不影响其他正在工作的终端窗口
echo "================================================="
echo "🎉 MathBank 服务已成功转入后台静默运行！"
echo "正在为您自动收起并关闭此终端窗口..."
echo "================================================="
sleep 0.5

if [ "$TERM_PROGRAM" = "iTerm.app" ]; then
    # 通过当前进程的 PID 向上查找父进程所在的 iTerm 会话，精确关闭自己所在的标签页
    # 避免误关用户正在使用的其他 iTerm 会话
    osascript -e "
        tell application \"iTerm\"
            set myPID to $$
            repeat with w in windows
                repeat with t in tabs of w
                    repeat with s in sessions of t
                        try
                            if (tty of s) is \"$CURRENT_TTY\" then
                                close s
                                return
                            end if
                        end try
                    end repeat
                end repeat
            end repeat
        end tell
    " &
else
    osascript -e 'tell application "Terminal" to close first window' &
fi

exit 0
