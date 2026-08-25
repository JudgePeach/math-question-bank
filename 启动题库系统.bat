@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "main.py" (
    echo =================================================
    echo [错误] 启动失败：未在当前目录找到 main.py。
    echo 请先将 ZIP 全部解压到普通文件夹，再运行本脚本。
    echo =================================================
    pause
    exit /b 1
)

echo =================================================
echo      本地数学题库教研系统 (MathBank) Windows 启动器
echo =================================================

set "PORTABLE_RUNTIME=0"
if exist "python\python.exe" goto use_portable_python
if exist "venv\Scripts\python.exe" goto use_venv_python
goto create_venv

:use_portable_python
set "PORTABLE_RUNTIME=1"
set "PYTHON_EXE=%CD%\python\python.exe"
for %%d in (msvcp140.dll vcruntime140.dll vcruntime140_1.dll) do (
    if not exist "python\%%d" (
        echo [错误] 便携运行库缺少 python\%%d。
        echo 这不是题库数据损坏；请使用包含完整 VC++ 运行库的新版覆盖所有同名文件。
        pause
        exit /b 1
    )
)
goto verify_python

:use_venv_python
set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
goto verify_python

:create_venv
set "BOOTSTRAP_CMD="
where python >nul 2>&1
if errorlevel 1 goto try_python3
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto try_python3
set "BOOTSTRAP_CMD=python"
goto bootstrap_found

:try_python3
where python3 >nul 2>&1
if errorlevel 1 goto try_py
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
set "BOOTSTRAP_CMD=python3"
goto bootstrap_found

:try_py
where py >nul 2>&1
if errorlevel 1 goto no_supported_python
py -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto no_supported_python
set "BOOTSTRAP_CMD=py"
goto bootstrap_found

:no_supported_python
echo [错误] 未检测到 Python 3.10 或更高版本。
echo 请从 python.org 安装受支持版本，并启用 Add Python to PATH。
pause
exit /b 1

:bootstrap_found
echo 首次运行，正在创建本项目专用虚拟环境...
%BOOTSTRAP_CMD% -m venv "%CD%\venv"
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败，请检查 Python 安装与目录权限。
    pause
    exit /b 1
)
set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"

:verify_python
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前运行环境低于 Python 3.10，无法启动本项目。
    echo 如使用本地 venv，请删除该 venv 后用新版 Python 重建。
    pause
    exit /b 1
)

if not exist ".system_generated" mkdir ".system_generated"
if not exist ".system_generated" (
    echo [错误] 无法创建运行状态目录 .system_generated。
    pause
    exit /b 1
)
set "MATHBANK_EXPECTED_ROOT=%CD%"
set "MATHBANK_PYTHON_EXE=%PYTHON_EXE%"
set "MATHBANK_PID_FILE=%CD%\.system_generated\server.pid"
set "MATHBANK_IDENTITY_FILE=%CD%\.system_generated\server.identity"
set "MATHBANK_OUT_LOG=%CD%\.system_generated\server.out.log"
set "MATHBANK_ERR_LOG=%CD%\.system_generated\server.error.log"
set "MATHBANK_PROBE_LOG=%CD%\.system_generated\probe.log"

call :stop_owned_server
if errorlevel 1 (
    echo [错误] 状态文件指向无法验证身份的运行中进程，启动器不会终止该进程。
    echo 请检查 .system_generated\server.pid 与 server.identity。
    pause
    exit /b 1
)
del /f /q "%MATHBANK_PID_FILE%" "%MATHBANK_IDENTITY_FILE%" >nul 2>&1

set "LEGACY_STOP_FAILED=0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr LISTENING ^| findstr ":8000"') do (
    call :stop_verified_legacy_server %%a
    if errorlevel 1 set "LEGACY_STOP_FAILED=1"
)
if "%LEGACY_STOP_FAILED%"=="1" (
    echo [错误] 端口 8000 已被无法安全确认身份的进程占用。
    echo 启动器不会终止陌生进程，请手动关闭占用者后重试。
    pause
    exit /b 1
)

if exist "RELEASE-MANIFEST.json" (
    echo 正在校验并完成 Release 覆盖升级...
    "%PYTHON_EXE%" -B -m scripts.release_overlay --platform windows-x64
    if errorlevel 1 (
        echo [错误] Release 覆盖升级校验失败。
        echo 请重新解压完整新版，将其内容全部合并覆盖到原目录后再试。
        pause
        exit /b 1
    )
)

echo 正在检查运行环境依赖是否完整...
if not exist "requirements.txt" (
    echo [错误] 缺少 requirements.txt，无法校验运行依赖。
    pause
    exit /b 1
)
if "%PORTABLE_RUNTIME%"=="1" goto verify_portable_dependencies

set "REQUIREMENTS_STAMP=%CD%\.system_generated\requirements.sha256"
set "REQUIREMENTS_HASH_TEMP=%CD%\.system_generated\.requirements.current.tmp"
set "MATHBANK_REQUIREMENTS_FILE=%CD%\requirements.txt"
set "MATHBANK_REQUIREMENTS_HASH_TEMP=%REQUIREMENTS_HASH_TEMP%"
"%PYTHON_EXE%" -c "import hashlib, os, pathlib; source=pathlib.Path(os.environ['MATHBANK_REQUIREMENTS_FILE']); target=pathlib.Path(os.environ['MATHBANK_REQUIREMENTS_HASH_TEMP']); target.write_text(hashlib.sha256(source.read_bytes()).hexdigest() + '\n', encoding='ascii')" >nul 2>&1
if errorlevel 1 (
    echo [错误] 无法计算 requirements.txt 摘要。
    pause
    exit /b 1
)
set "REQUIREMENTS_HASH="
set /p REQUIREMENTS_HASH=<"%REQUIREMENTS_HASH_TEMP%"
del /f /q "%REQUIREMENTS_HASH_TEMP%" >nul 2>&1
if not defined REQUIREMENTS_HASH (
    echo [错误] requirements.txt 摘要为空。
    pause
    exit /b 1
)
set "INSTALLED_REQUIREMENTS_HASH="
if exist "%REQUIREMENTS_STAMP%" set /p INSTALLED_REQUIREMENTS_HASH=<"%REQUIREMENTS_STAMP%"
set "NEEDS_DEPENDENCY_INSTALL=0"
if not "%INSTALLED_REQUIREMENTS_HASH%"=="%REQUIREMENTS_HASH%" set "NEEDS_DEPENDENCY_INSTALL=1"
"%PYTHON_EXE%" -c "import fastapi, uvicorn, sqlalchemy, greenlet, colorama, multipart, dotenv, requests, PIL, docx, lxml, defusedxml, olefile, exceptiongroup, sniffio; import pymupdf as fitz; import pdf_inspector" >nul 2>&1
if errorlevel 1 set "NEEDS_DEPENDENCY_INSTALL=1"
"%PYTHON_EXE%" -m pip check >nul 2>&1
if errorlevel 1 set "NEEDS_DEPENDENCY_INSTALL=1"
if "%NEEDS_DEPENDENCY_INSTALL%"=="0" goto dependencies_ready

echo 检测到依赖缺失、冲突或锁文件已变化，正在按 requirements.txt 同步...
"%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto dependency_install_failed
"%PYTHON_EXE%" -m pip check
if errorlevel 1 goto dependency_check_failed
>"%REQUIREMENTS_STAMP%.tmp" echo %REQUIREMENTS_HASH%
move /y "%REQUIREMENTS_STAMP%.tmp" "%REQUIREMENTS_STAMP%" >nul
if errorlevel 1 (
    echo [错误] 无法原子更新依赖锁摘要。
    pause
    exit /b 1
)
goto dependencies_ready

:dependency_install_failed
echo [错误] 依赖安装失败，请检查网络连接或代理设置后重试。
pause
exit /b 1

:dependency_check_failed
echo [错误] 依赖一致性检查失败，请查看上方 pip check 输出。
pause
exit /b 1

:verify_portable_dependencies
"%PYTHON_EXE%" -u -c "import ctypes, pathlib, sys; runtime=pathlib.Path(sys.executable).resolve().parent; ctypes.WinDLL(str(runtime / 'msvcp140.dll')); import fastapi, uvicorn, sqlalchemy, greenlet, colorama, multipart, dotenv, requests, PIL, docx, lxml, defusedxml, olefile, exceptiongroup, sniffio; import pymupdf as fitz; import pdf_inspector"
if errorlevel 1 (
    echo [错误] 便携运行库或 Python 模块导入失败，上方是实际错误。
    echo 请保留该错误信息；不要仅按 ZIP 损坏重复下载。
    pause
    exit /b 1
)

:dependencies_ready
set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr LISTENING ^| findstr ":8000"') do set "PORT_PID=%%a"
if defined PORT_PID (
    echo [错误] 端口 8000 已被其他进程占用 [PID: %PORT_PID%]。
    echo 为避免误杀陌生进程，启动器不会自动终止它。
    pause
    exit /b 1
)

del /f /q "%MATHBANK_OUT_LOG%" "%MATHBANK_ERR_LOG%" "%MATHBANK_PROBE_LOG%" >nul 2>&1
echo 正在启动服务: http://127.0.0.1:8000
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $arguments=@('-u','-m','uvicorn','main:app','--host','127.0.0.1','--port','8000'); $process=Start-Process -FilePath $env:MATHBANK_PYTHON_EXE -ArgumentList $arguments -WorkingDirectory $env:MATHBANK_EXPECTED_ROOT -WindowStyle Hidden -RedirectStandardOutput $env:MATHBANK_OUT_LOG -RedirectStandardError $env:MATHBANK_ERR_LOG -PassThru; try { Set-Content -LiteralPath $env:MATHBANK_IDENTITY_FILE -Value $env:MATHBANK_EXPECTED_ROOT -Encoding UTF8; Set-Content -LiteralPath $env:MATHBANK_PID_FILE -Value $process.Id -Encoding Ascii } catch { $registrationError=$_; if ($process -and !$process.HasExited) { try { $process.Kill(); $process.WaitForExit(5000) | Out-Null } catch { Write-Warning ('无法回收已启动进程 PID ' + $process.Id + ': ' + $_.Exception.Message) } }; if ($process -and !$process.HasExited) { try { Set-Content -LiteralPath $env:MATHBANK_IDENTITY_FILE -Value $env:MATHBANK_EXPECTED_ROOT -Encoding UTF8; Set-Content -LiteralPath $env:MATHBANK_PID_FILE -Value $process.Id -Encoding Ascii } catch {}; Write-Error ('后台进程仍在运行，请手动终止 PID ' + $process.Id); throw $registrationError }; Remove-Item -LiteralPath $env:MATHBANK_PID_FILE,$env:MATHBANK_IDENTITY_FILE -Force -ErrorAction SilentlyContinue; throw $registrationError }"
if errorlevel 1 (
    echo [错误] 无法创建后台服务进程。
    pause
    exit /b 1
)

echo 正在探测后台服务启动状态，等待就绪...
set "SERVICE_READY=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $raw=(Get-Content -LiteralPath $env:MATHBANK_PID_FILE -Raw).Trim(); if ($raw -notmatch '^\d+$') { exit 1 }; $serverPid=[int]$raw; $watch=[Diagnostics.Stopwatch]::StartNew(); $lastProbe='服务尚未响应'; while ($watch.Elapsed.TotalSeconds -lt 60) { $remainingMs=[Math]::Max(1,[Math]::Min(2000,[int]((60-$watch.Elapsed.TotalSeconds)*1000))); try { $request=[Net.HttpWebRequest]::Create('http://127.0.0.1:8000/healthz'); $request.Proxy=$null; $request.KeepAlive=$false; $request.Timeout=$remainingMs; $request.ReadWriteTimeout=$remainingMs; $response=$request.GetResponse(); $statusCode=[int]$response.StatusCode; if ($statusCode -eq 200) { $response.Close(); exit 0 }; $reader=New-Object IO.StreamReader($response.GetResponseStream()); $body=$reader.ReadToEnd(); $response.Close(); $lastProbe='HTTP ' + $statusCode + ' - ' + $body } catch [System.Net.WebException] { $webEx=$_.Exception; if ($webEx.Response) { $resp=[System.Net.HttpWebResponse]$webEx.Response; $statusCode=[int]$resp.StatusCode; $statusDescription=$resp.StatusDescription; $reader=New-Object IO.StreamReader($resp.GetResponseStream()); $body=$reader.ReadToEnd(); $resp.Close(); $lastProbe='HTTP ' + $statusCode + ' (' + $statusDescription + '): ' + $body } else { $lastProbe=$webEx.Message } } catch { $lastProbe=$_.Exception.Message }; if ($null -eq (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) { $lastProbe='进程已提前退出 [PID: ' + $serverPid + '] - ' + $lastProbe; if ($env:MATHBANK_PROBE_LOG) { Set-Content -LiteralPath $env:MATHBANK_PROBE_LOG -Value $lastProbe -Encoding UTF8 }; exit 1 }; $remainingMs=[int]((60-$watch.Elapsed.TotalSeconds)*1000); if ($remainingMs -le 0) { break }; Start-Sleep -Milliseconds ([Math]::Min(500,$remainingMs)) }; $lastProbe='探测超时 (已等待 60 秒): ' + $lastProbe; if ($env:MATHBANK_PROBE_LOG) { Set-Content -LiteralPath $env:MATHBANK_PROBE_LOG -Value $lastProbe -Encoding UTF8 }; exit 1" >nul 2>&1
if not errorlevel 1 (
    set "SERVICE_READY=1"
)

:health_complete
if "%SERVICE_READY%"=="1" goto start_browser
call :stop_owned_server
if errorlevel 1 (
    echo [警告] 后台进程未能被安全确认停止；PID 与身份状态文件已保留。
) else (
    del /f /q "%MATHBANK_PID_FILE%" "%MATHBANK_IDENTITY_FILE%" >nul 2>&1
)
echo ================================================
echo [错误] 服务未能通过健康检查，浏览器不会打开。
if exist "%MATHBANK_PROBE_LOG%" (
    echo ---------------- 探针诊断信息 ----------------
    type "%MATHBANK_PROBE_LOG%"
    echo.
)
if exist "%MATHBANK_ERR_LOG%" (
    echo ---------------- 标准错误日志 ----------------
    type "%MATHBANK_ERR_LOG%"
)
if exist "%MATHBANK_OUT_LOG%" (
    echo ---------------- 标准输出日志 ----------------
    type "%MATHBANK_OUT_LOG%"
)
echo ================================================
pause
exit /b 1

:start_browser
echo [成功] 后台服务已就绪，正在打开浏览器。
start "" "http://127.0.0.1:8000"
echo 服务 PID 已记录在 .system_generated\server.pid。
ping 127.0.0.1 -n 2 >nul
exit /b 0

:stop_verified_legacy_server
set "MATHBANK_LEGACY_PID=%~1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $raw=$env:MATHBANK_LEGACY_PID; if ($raw -notmatch '^\d+$') { exit 3 }; $process=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $raw) -ErrorAction SilentlyContinue; if ($null -eq $process) { exit 0 }; $legacyExe=Join-Path $env:MATHBANK_EXPECTED_ROOT 'python\python.exe'; if (!(Test-Path -LiteralPath $legacyExe)) { exit 3 }; $sameExe=[string]::Equals([IO.Path]::GetFullPath([string]$process.ExecutablePath),[IO.Path]::GetFullPath($legacyExe),[System.StringComparison]::OrdinalIgnoreCase); $sameCommand=([string]$process.CommandLine -match '(?i)(?:^|\s)-m\s+uvicorn\s+main:app(?:\s|$)'); if (!$sameExe -or !$sameCommand) { exit 3 }; $versionRequest=[Net.HttpWebRequest]::Create('http://127.0.0.1:8000/api/version'); $versionRequest.Proxy=$null; $versionRequest.KeepAlive=$false; $versionRequest.Timeout=2000; $versionResponse=$versionRequest.GetResponse(); $reader=New-Object IO.StreamReader($versionResponse.GetResponseStream()); $versionBody=$reader.ReadToEnd(); $versionResponse.Close(); $versionInfo=$versionBody | ConvertFrom-Json; if ([string]$versionInfo.repo -ne 'JudgePeach/math-question-bank') { exit 3 }; $tokenPath=Join-Path $env:MATHBANK_EXPECTED_ROOT '.system_generated\local_token'; if (!(Test-Path -LiteralPath $tokenPath)) { exit 3 }; $token=(Get-Content -LiteralPath $tokenPath -Raw).Trim(); if (!$token) { exit 3 }; Write-Host ('[提示] 已识别旧版 MathBank 服务 [PID: ' + $raw + ']，正在请求关闭...'); $shutdownRequest=[Net.HttpWebRequest]::Create('http://127.0.0.1:8000/api/shutdown'); $shutdownRequest.Proxy=$null; $shutdownRequest.KeepAlive=$false; $shutdownRequest.Timeout=2000; $shutdownRequest.Method='POST'; $shutdownRequest.Headers.Add('X-Local-Token',$token); $shutdownRequest.ContentLength=0; $shutdownResponse=$shutdownRequest.GetResponse(); $shutdownResponse.Close(); for ($attempt=0; $attempt -lt 20; $attempt++) { Start-Sleep -Milliseconds 250; if ($null -eq (Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue)) { exit 0 } }; exit 4"
exit /b %errorlevel%

:stop_owned_server
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; if (!(Test-Path -LiteralPath $env:MATHBANK_PID_FILE)) { exit 0 }; $raw=(Get-Content -LiteralPath $env:MATHBANK_PID_FILE -Raw).Trim(); if ($raw -notmatch '^\d+$') { exit 3 }; $process=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $raw) -ErrorAction SilentlyContinue; if ($null -eq $process) { exit 0 }; if (!(Test-Path -LiteralPath $env:MATHBANK_IDENTITY_FILE)) { exit 3 }; $savedRoot=(Get-Content -LiteralPath $env:MATHBANK_IDENTITY_FILE -Raw).Trim(); $sameRoot=[string]::Equals($savedRoot,$env:MATHBANK_EXPECTED_ROOT,[System.StringComparison]::OrdinalIgnoreCase); $actualExe=[IO.Path]::GetFullPath([string]$process.ExecutablePath); $portableExe=Join-Path $savedRoot 'python\python.exe'; $venvExe=Join-Path $savedRoot 'venv\Scripts\python.exe'; $sameExe=((Test-Path -LiteralPath $portableExe) -and [string]::Equals($actualExe,[IO.Path]::GetFullPath($portableExe),[System.StringComparison]::OrdinalIgnoreCase)) -or ((Test-Path -LiteralPath $venvExe) -and [string]::Equals($actualExe,[IO.Path]::GetFullPath($venvExe),[System.StringComparison]::OrdinalIgnoreCase)); $sameCommand=([string]$process.CommandLine -match '(?i)-m\s+uvicorn\s+main:app(?:\s|$)'); if (!$sameRoot -or !$sameExe -or !$sameCommand) { exit 3 }; Stop-Process -Id ([int]$raw); for ($attempt=0; $attempt -lt 20; $attempt++) { Start-Sleep -Milliseconds 250; if ($null -eq (Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue)) { exit 0 } }; exit 4"
exit /b %errorlevel%
