import os
import shutil
import subprocess
import urllib.request
import zipfile
import ssl

# Bypass SSL verification to avoid certificate errors on macOS/Windows
ssl._create_default_https_context = ssl._create_unverified_context

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(DIST_DIR, "mathbank-windows")
PYTHON_DIR = os.path.join(BUILD_DIR, "python")
WHEELS_DIR = os.path.join(DIST_DIR, "wheels")
SITE_PACKAGES = os.path.join(PYTHON_DIR, "site-packages")

PYTHON_ZIP_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
PYTHON_ZIP_PATH = os.path.join(DIST_DIR, "python_embed.zip")

def clean_directories():
    print("🧹 Cleaning old directories...")
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(PYTHON_DIR, exist_ok=True)
    os.makedirs(WHEELS_DIR, exist_ok=True)
    os.makedirs(SITE_PACKAGES, exist_ok=True)

def download_python():
    print(f"📥 Downloading portable Windows Python from {PYTHON_ZIP_URL}...")
    urllib.request.urlretrieve(PYTHON_ZIP_URL, PYTHON_ZIP_PATH)
    print("📦 Extracting Python...")
    with zipfile.ZipFile(PYTHON_ZIP_PATH, 'r') as zip_ref:
        zip_ref.extractall(PYTHON_DIR)

def configure_python_path():
    print("⚙️ Configuring python310._pth...")
    pth_file = os.path.join(PYTHON_DIR, "python310._pth")
    if os.path.exists(pth_file):
        with open(pth_file, "r") as f:
            content = f.read()
        
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            # Uncomment import site
            if line.strip() == "#import site":
                new_lines.append("import site")
            else:
                new_lines.append(line)
            
            # Insert site-packages relative path
            if line.strip() == ".":
                new_lines.append("site-packages")
        
        with open(pth_file, "w") as f:
            f.write("\n".join(new_lines) + "\n")

def download_and_extract_wheels():
    print("📥 Downloading Windows wheels for requirements...")
    requirements_file = os.path.join(BASE_DIR, "requirements.txt")
    
    # Use pip to download windows amd64 wheels
    cmd = [
        "pip", "download",
        "--only-binary=:all:",
        "--platform", "win_amd64",
        "--python-version", "3.10",
        "--implementation", "cp",
        "--abi", "cp310",
        "-d", WHEELS_DIR,
        "-r", requirements_file
    ]
    print(f"Running command: {' '.join(cmd)}")
    subprocess.check_call(cmd)
    
    print("📦 Extracting wheels into site-packages...")
    for file in os.listdir(WHEELS_DIR):
        if file.endswith(".whl"):
            file_path = os.path.join(WHEELS_DIR, file)
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(SITE_PACKAGES)

def copy_app_files():
    print("📂 Copying application files...")
    files_to_copy = [
        "main.py",
        "database.py",
        "sync_helper.py",
        "search_questions.py",
        ".env.example"
    ]
    for f in files_to_copy:
        src = os.path.join(BASE_DIR, f)
        dst = os.path.join(BUILD_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            
    # Copy static folder
    shutil.copytree(
        os.path.join(BASE_DIR, "static"),
        os.path.join(BUILD_DIR, "static"),
        dirs_exist_ok=True
    )

def create_launcher():
    print("📝 Creating launcher batch file...")
    launcher_content = """@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =================================================
echo      本地数学题库教研系统 (MathBank) 便携版
echo =================================================
echo 正在释放端口...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr LISTENING ^| findstr :8000') do (
    echo 检测到端口 8000 被占用，正在释放端口...
    taskkill /f /pid %%a >nul 2>&1
)

echo 正在启动后台服务...
:: 使用内置的便携式 Python 运行服务，输出重定向到日志文件
if not exist .system_generated mkdir .system_generated
del /f /q .system_generated\\server.log >nul 2>&1
start /min "MathBank Server" cmd /c "python\\python.exe -m uvicorn main:app --host 127.0.0.1 >.system_generated\\server.log 2>&1"

echo 正在探测服务启动状态...
set TIMEOUT=10
set COUNTER=0
set SERVICE_READY=0

:loop
if %COUNTER% geq %TIMEOUT% goto end_loop

python\\python.exe -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/questions')" >nul 2>&1
if not errorlevel 1 (
    echo 🎉 服务已成功启动！
    set SERVICE_READY=1
    goto end_loop
)

ping 127.0.0.1 -n 2 >nul
set /a COUNTER=%COUNTER%+1
goto loop

:end_loop
if %SERVICE_READY%==0 (
    echo ⚠️ 服务启动超时，尝试直接拉起浏览器...
)

start http://127.0.0.1:8000
exit
"""
    launcher_path = os.path.join(BUILD_DIR, "启动题库系统.bat")
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(launcher_content)

def zip_release():
    print("🤐 Zipping Windows release package...")
    zip_filename = os.path.join(DIST_DIR, "MathBank-Windows-x64")
    shutil.make_archive(zip_filename, 'zip', BUILD_DIR)
    print(f"🎉 Windows Zip file created: {zip_filename}.zip")

def zip_macos_release():
    print("🤐 Zipping macOS release package...")
    macos_build_dir = os.path.join(DIST_DIR, "mathbank-macos")
    os.makedirs(macos_build_dir, exist_ok=True)
    
    # Copy source files
    files_to_copy = [
        "main.py",
        "database.py",
        "sync_helper.py",
        "search_questions.py",
        ".env.example",
        "requirements.txt",
        "启动题库系统.command"
    ]
    for f in files_to_copy:
        src = os.path.join(BASE_DIR, f)
        dst = os.path.join(macos_build_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            
    # Copy static folder
    shutil.copytree(
        os.path.join(BASE_DIR, "static"),
        os.path.join(macos_build_dir, "static"),
        dirs_exist_ok=True
    )
    
    # Make sure launcher is executable in build directory
    launcher_path = os.path.join(macos_build_dir, "启动题库系统.command")
    if os.path.exists(launcher_path):
        os.chmod(launcher_path, 0o755)

    # Zip macOS folder
    zip_filename = os.path.join(DIST_DIR, "MathBank-macOS")
    shutil.make_archive(zip_filename, 'zip', macos_build_dir)
    print(f"🎉 macOS Zip file created: {zip_filename}.zip")
    
    # Cleanup temp macos folder
    shutil.rmtree(macos_build_dir, ignore_errors=True)

def cleanup_temp():
    print("🧹 Cleaning up temporary files...")
    if os.path.exists(PYTHON_ZIP_PATH):
        os.remove(PYTHON_ZIP_PATH)
    shutil.rmtree(WHEELS_DIR, ignore_errors=True)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)

def main():
    try:
        clean_directories()
        download_python()
        configure_python_path()
        download_and_extract_wheels()
        copy_app_files()
        create_launcher()
        zip_release()
        zip_macos_release()
        cleanup_temp()
        print("🚀 Windows & macOS Release Packages Built Successfully!")
    except Exception as e:
        print(f"❌ Error during packaging: {e}")

if __name__ == "__main__":
    main()
