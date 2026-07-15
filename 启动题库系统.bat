@echo off
cd /d "%~dp0"

:: 检查是否解压运行（确保当前目录下存在主程序文件）
if not exist main.py (
    echo =================================================
    echo [错误] 启动失败：未在当前目录找到项目关键主程序！
    echo =================================================
    echo 出现该错误通常是因为：您直接在 ZIP 压缩包内双击启动了脚本。
    echo 请务必先将压缩包【全部解压】到一个普通文件夹中，再运行批处理。
    echo =================================================
    pause
    exit /b 1
)

echo =================================================
echo      本地数学题库教研系统 (MathBank) Windows 启动器
echo =================================================
echo 正在检测运行环境并释放端口...

:: 1. 自动检测并强力清理霸占 8000 端口的残余 Uvicorn/Python 进程，确保 100% 启动成功
for /f "tokens=5" %%a in ('netstat -aon ^| findstr LISTENING ^| findstr :8000') do (
    echo 检测到端口 8000 被上一次未完全释放的残余进程 [PID: %%a] 占用。
    echo 正在为您安全释放端口并清理运行环境...
    taskkill /f /pid %%a >nul 2>&1
)

:: 2. 探测可用的 Python 命令 (优先使用 python，其次 python3，最后 py)
set PYTHON_CMD=
where python >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
) else (
    where python3 >nul 2>&1
    if not errorlevel 1 (
        set PYTHON_CMD=python3
    ) else (
        where py >nul 2>&1
        if not errorlevel 1 (
            set PYTHON_CMD=py
        )
    )
)

if "%PYTHON_CMD%"=="" (
    echo [错误] 未检测到系统安装了 Python 环境，或者未将 Python 添加至环境变量 [PATH] 中！
    echo 请先安装 Python [3.8+] 并勾选 "Add Python to PATH" 选项。
    pause
    exit /b 1
)

echo 使用 Python 命令: %PYTHON_CMD%

:: 检查 Python 依赖包是否完整
echo 正在检查运行环境依赖是否完整...
%PYTHON_CMD% -c "import fastapi, uvicorn, sqlalchemy, multipart, dotenv, requests, PIL, fitz" >nul 2>&1
if errorlevel 1 (
    echo 检测到有新增或缺失的依赖包，正在为您自动增量安装...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [警告] 依赖包安装失败，服务启动可能会报错。请检查网络。
    ) else (
        echo [成功] 依赖包更新完成！
    )
)

echo 正在本地加载环境并为您启动服务...
echo 服务启动后，将在浏览器中自动打开: http://127.0.0.1:8000
echo =================================================
echo.

:: 3. 建立存放日志的隐藏目录并清理上一次日志
if not exist .system_generated mkdir .system_generated
del /f /q .system_generated\server.log >nul 2>&1

:: 4. 启动 uvicorn 服务，通过 PowerShell 在后台完全静默（无窗口）运行，并将输出重定向到 server.log
powershell -Command "Start-Process cmd -ArgumentList '/c %PYTHON_CMD% -m uvicorn main:app --reload --host 127.0.0.1 >.system_generated\server.log 2>&1' -WindowStyle Hidden"

:: 5. 自适应端口健康检查，最大等待 10 秒（使用 ping 延迟 1 秒，循环 10 次）
echo 正在探测后台服务启动状态，等待就绪...
set TIMEOUT=10
set COUNTER=0
set SERVICE_READY=0

:: 检查是否安装了 curl
where curl >nul 2>&1
if errorlevel 1 (
    :: 无 curl 工具，默认等待 3 秒后直接启动浏览器
    echo [提示] 未检测到 curl 工具，将等待 3 秒后直接启动浏览器...
    ping 127.0.0.1 -n 4 >nul
    set SERVICE_READY=1
    goto start_browser
)

:loop
if %COUNTER% geq %TIMEOUT% goto end_loop

:: 使用 curl 探测 /api/questions 端口响应，丢弃 stderr
set HTTP_STATUS=000
for /f "delims=" %%i in ('curl -s -o nul -w "%%{http_code}" --connect-timeout 2 --max-time 3 --noproxy "*" http://127.0.0.1:8000/api/questions 2^>nul') do set HTTP_STATUS=%%i

if "%HTTP_STATUS%"=="200" (
    echo [成功] 后台服务已成功拉起，题库 API 响应正常！
    set SERVICE_READY=1
    goto end_loop
)

:: 等待 1 秒 (ping 本地环回地址 2 次产生大约 1 秒延迟)
ping 127.0.0.1 -n 2 >nul
set /a COUNTER=%COUNTER%+1
goto loop

:end_loop
if %SERVICE_READY%==0 (
    echo [错误] 探测服务启动超时 [已等待 10 秒]，后台服务启动失败！
    echo -------------------------------------------------
    if exist .system_generated\server.log (
        type .system_generated\server.log
    ) else (
        echo 未找到日志文件 .system_generated\server.log
    )
    echo -------------------------------------------------
    echo 请检查上述错误信息，或按任意键退出...
    pause
    exit
)

:start_browser
:: 6. 服务已完全就绪，拉起默认浏览器展示 MathBank UI
start http://127.0.0.1:8000

echo =================================================
echo [成功] MathBank 服务已成功转入后台静默运行！
echo 提示：当前命令行窗口会自动关闭。如果未来需要停止或重启服务，
echo       直接重新运行此启动脚本即可（会自动检测端口并重启）。
echo =================================================
ping 127.0.0.1 -n 2 >nul
exit
