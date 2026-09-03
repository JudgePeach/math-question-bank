@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "main.py" goto missing_main
if exist "RELEASE-MANIFEST.json" goto use_release
goto use_source

:use_release
if not exist "python\python.exe" goto missing_runtime
goto use_portable

:use_portable
set "MISSING_DLL="
for %%d in (msvcp140.dll vcruntime140.dll vcruntime140_1.dll) do if not exist "python\%%d" set "MISSING_DLL=%%d"
if defined MISSING_DLL goto missing_dll
set "PYTHON_EXE=%CD%\python\python.exe"
"%PYTHON_EXE%" -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto unsupported_portable_python
set "MATHBANK_PORTABLE_RUNTIME=1"
goto launch

:use_source
set "MATHBANK_PORTABLE_RUNTIME="
if exist "venv\Scripts\python.exe" goto use_source_venv
if not exist "python\python.exe" goto find_source_python
set "MISSING_DLL="
for %%d in (msvcp140.dll vcruntime140.dll vcruntime140_1.dll) do if not exist "python\%%d" set "MISSING_DLL=%%d"
if not defined MISSING_DLL goto use_portable

:find_source_python
set "BOOTSTRAP_CMD="
where python >nul 2>&1
if errorlevel 1 goto try_python3
python -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto try_python3
set "BOOTSTRAP_CMD=python"
goto create_source_venv

:try_python3
where python3 >nul 2>&1
if errorlevel 1 goto try_py
python3 -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto try_py
set "BOOTSTRAP_CMD=python3"
goto create_source_venv

:try_py
where py >nul 2>&1
if errorlevel 1 goto missing_source_python
py -3 -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto missing_source_python
set "BOOTSTRAP_CMD=py -3"

:create_source_venv
echo [setup] Creating the project Python environment...
%BOOTSTRAP_CMD% -m venv "%CD%\venv"
if errorlevel 1 goto source_venv_failed

:use_source_venv
set "PYTHON_EXE=%CD%\venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import sys;raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto unsupported_source_python

:launch
"%PYTHON_EXE%" -B -m scripts.windows_launcher %*
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if "%LAUNCHER_EXIT%"=="0" goto launcher_done
if "%MATHBANK_NO_PAUSE%"=="1" goto launcher_done
pause

:launcher_done
exit /b %LAUNCHER_EXIT%

:missing_main
echo [E_PROJECT_ROOT] main.py was not found beside this launcher.
echo Extract or clone the complete MathBank project before running this file.
goto bootstrap_error

:missing_runtime
echo [E_RUNTIME] The embedded python\python.exe runtime is missing.
echo Use the complete MathBank-Windows-x64.zip package.
goto bootstrap_error

:missing_dll
echo [E_RUNTIME_DLL] Missing python\%MISSING_DLL%.
echo Copy every file from the complete Windows package over this folder.
goto bootstrap_error

:unsupported_portable_python
echo [E_PYTHON_VERSION] The embedded Python runtime cannot run or is unsupported.
goto bootstrap_error

:missing_source_python
echo [E_SOURCE_PYTHON] Python 3.10 or newer was not found.
echo Install Python from python.org, then run this launcher again.
goto bootstrap_error

:source_venv_failed
echo [E_SOURCE_VENV] The project Python environment could not be created.
goto bootstrap_error

:unsupported_source_python
echo [E_SOURCE_PYTHON_VERSION] The project venv requires Python 3.10 or newer.
echo Remove the venv folder and run this launcher again to rebuild it.

:bootstrap_error
if "%MATHBANK_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
