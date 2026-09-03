@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "main.py" goto missing_main
if not exist "python\python.exe" goto missing_runtime

set "MISSING_DLL="
for %%d in (msvcp140.dll vcruntime140.dll vcruntime140_1.dll) do if not exist "python\%%d" set "MISSING_DLL=%%d"
if defined MISSING_DLL goto missing_dll

set "PYTHON_EXE=%CD%\python\python.exe"
"%PYTHON_EXE%" -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto unsupported_python

set "MATHBANK_PORTABLE_RUNTIME=1"
"%PYTHON_EXE%" -B -m scripts.windows_launcher %*
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if "%LAUNCHER_EXIT%"=="0" goto launcher_done
if "%MATHBANK_NO_PAUSE%"=="1" goto launcher_done
pause

:launcher_done
exit /b %LAUNCHER_EXIT%

:missing_main
echo [E_PROJECT_ROOT] main.py was not found beside this launcher.
echo Extract the complete Windows ZIP before running this file.
goto bootstrap_error

:missing_runtime
echo [E_RUNTIME] The embedded python\python.exe runtime is missing.
echo Use the complete MathBank-Windows-x64.zip package.
goto bootstrap_error

:missing_dll
echo [E_RUNTIME_DLL] Missing python\%MISSING_DLL%.
echo Copy every file from the complete Windows package over this folder.
goto bootstrap_error

:unsupported_python
echo [E_PYTHON_VERSION] The embedded Python runtime cannot run or is unsupported.

:bootstrap_error
if "%MATHBANK_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
