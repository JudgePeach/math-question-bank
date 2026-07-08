import os
import io
import uuid
import json
import time
import re
import signal
import datetime
import threading
import requests
import secrets
from typing import List, Optional
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response, Header
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import Question, get_db, init_db
from sync_helper import export_database_to_files

# Load environment variables
load_dotenv()

# Initialize DB
init_db()

import sys
# 检测是否在单元测试环境下运行
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"

def robust_request_post(url, **kwargs):
    """发送 POST 请求，并在发生 Proxy/Connection 错误时自动尝试禁用代理重试"""
    # 如果目标 URL 是国内知名 API（如阿里百炼、SiliconFlow 等），
    # 且 kwargs 中没有明确指定 proxies，直接在首次请求时默认禁用代理，避免代理延迟、握手失败或超时！
    is_domestic = any(domain in url.lower() for domain in [
        "aliyuncs.com", 
        "siliconflow"
    ])
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}
        
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.RequestException as e:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise e
        print(f"[Robust Network] POST request to {url} failed: {str(e)}. Retrying with proxies bypassed...")
        new_kwargs = kwargs.copy()
        new_kwargs["proxies"] = {"http": None, "https": None}
        return requests.post(url, **new_kwargs)

def robust_request_get(url, **kwargs):
    """发送 GET 请求，并在发生 Proxy/Connection 错误时自动尝试禁用代理重试"""
    is_domestic = any(domain in url.lower() for domain in [
        "aliyuncs.com", 
        "siliconflow"
    ])
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}
        
    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.RequestException as e:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise e
        print(f"[Robust Network] GET request to {url} failed: {str(e)}. Retrying with proxies bypassed...")
        new_kwargs = kwargs.copy()
        new_kwargs["proxies"] = {"http": None, "https": None}
        return requests.get(url, **new_kwargs)

def load_or_create_local_token() -> str:
    token_dir = ".system_generated"
    os.makedirs(token_dir, exist_ok=True)
    token_file = os.path.join(token_dir, "local_token")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            print(f"[Security] Failed to read persistent token: {e}")
            
    # Generate new token
    token = secrets.token_hex(16)
    try:
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(token)
    except Exception as e:
        print(f"[Security] Failed to write persistent token: {e}")
    return token

LOCAL_TOKEN = load_or_create_local_token()

app = FastAPI(title="本地化数学题库管理系统 API")

# Enable CORS for local development (restrict allowed origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Heartbeat & Security Middleware -----------------
LAST_ACTIVE_TIME = time.time()

@app.middleware("http")
async def security_and_heartbeat_middleware(request: Request, call_next):
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    
    # Verify local security token for modifying operations
    if request.method in ("POST", "PUT", "DELETE"):
        if request.url.path != "/api/heartbeat":
            token = request.headers.get("X-Local-Token")
            if not token or token != LOCAL_TOKEN:
                print(f"[Security Alert] Blocked {request.method} {request.url.path} - X-Local-Token: '{token}', Expected: '{LOCAL_TOKEN}'")
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Forbidden: Invalid or missing local token."}
                )
                
    response = await call_next(request)
    return response

@app.post("/api/heartbeat")
def api_heartbeat():
    global LAST_ACTIVE_TIME
    LAST_ACTIVE_TIME = time.time()
    return {"status": "success", "timestamp": LAST_ACTIVE_TIME}

def watchdog_loop():
    global LAST_ACTIVE_TIME
    # 1小时闲置超时 (3600秒)
    TIMEOUT_LIMIT = 3600
    while True:
        time.sleep(15) # 每 15 秒轻量巡检一次
        elapsed = time.time() - LAST_ACTIVE_TIME
        if elapsed > TIMEOUT_LIMIT:
            print(f"[Watchdog] 检测到网页已关闭且超过 1 小时无任何动作 (已静默 {int(elapsed)} 秒)，正在自动安全关闭题库程序...")
            # 优雅向自身发送 SIGINT 信号退出
            os.kill(os.getpid(), signal.SIGINT)
            break

# 启动看门狗后台守护线程 (daemon=True 确保主线程消亡时其也随之释放)
# threading.Thread(target=watchdog_loop, daemon=True).start()

# ----------------- 启动自愈：后台静默清理孤儿临时图片 -----------------
def clean_orphaned_images():
    """扫描 static/uploads 目录，安全删除超过 1 小时未被数据库中任何题目引用的孤儿图片"""
    try:
        from database import SessionLocal, Question
        db = SessionLocal()
        try:
            # 1. 搜集数据库中所有题目引用的图片路径
            questions = db.query(Question._image_paths).all()
            referenced_images = set()
            for (img_paths_str,) in questions:
                if img_paths_str:
                    try:
                        paths = json.loads(img_paths_str)
                        for path in paths:
                            referenced_images.add(path.lstrip("/").lower())
                    except Exception:
                        pass
                        
            # 2. 遍历本地图片目录
            upload_dir = UPLOAD_DIR
            if not os.path.exists(upload_dir):
                return
                
            cleaned_count = 0
            now = time.time()
            one_hour_seconds = 3600
            
            for filename in os.listdir(upload_dir):
                # 忽略隐藏/系统文件
                if filename.startswith("."):
                    continue
                
                local_rel_path = f"{UPLOAD_DIR_REL}/{filename}".lower()
                if local_rel_path not in referenced_images:
                    full_path = os.path.join(upload_dir, filename)
                    # 安全红线：仅物理清理创建/修改时间超过 1 小时的临时或残留垃圾文件
                    try:
                        mtime = os.path.getmtime(full_path)
                        if now - mtime > one_hour_seconds:
                            os.remove(full_path)
                            cleaned_count += 1
                    except Exception:
                        pass
                        
            if cleaned_count > 0:
                print(f"[Storage Cleanup] 成功检测并清除 {cleaned_count} 个超过 1 小时未引用的孤儿图片，硬盘瘦身成功！")
        finally:
            db.close()
    except Exception as e:
        print(f"[Storage Cleanup Error] 执行静默图片净化时发生异常: {str(e)}")

def start_startup_cleanup():
    # 稍等 2.5 秒，让主服务先启动完毕并打开浏览器，不占用首屏加载时间
    time.sleep(2.5)
    clean_orphaned_images()

# 仅在非测试环境下启动静默自愈清理后台守护线程
if not IS_TESTING:
    threading.Thread(target=start_startup_cleanup, daemon=True).start()


# Ensure directories exist
UPLOAD_DIR = UPLOAD_DIR_REL
os.makedirs(UPLOAD_DIR, exist_ok=True)
TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

# PDF tasks management global state
import threading
PDF_TASKS = {}
PDF_TASKS_LOCK = threading.Lock()

def get_seq_mapping(db: Session):
    all_q = db.query(Question.id).order_by(Question.id.asc()).all()
    return {q_id: idx + 1 for idx, (q_id,) in enumerate(all_q)}

# ----------------- Static Files & Index -----------------

@app.get("/")
def read_index():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Inject dynamic cache-busting version parameter based on file mtime
        js_files = ["api.js", "editor.js", "ocr.js", "import.js"]
        for js in js_files:
            js_path = os.path.join("static", "js", js)
            mtime = int(os.path.getmtime(js_path)) if os.path.exists(js_path) else 0
            # Replace template version parameter
            html_content = html_content.replace(f"/static/js/{js}?v=1.0.1", f"/static/js/{js}?v={mtime}")
            # Also handle plain scripts references if they exist
            html_content = html_content.replace(f'src="/static/js/{js}"', f'src="/static/js/{js}?v={mtime}"')
            
        # Inject dynamic cache-busting version parameter for app.css
        css_path = os.path.join("static", "css", "app.css")
        css_mtime = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0
        html_content = html_content.replace('/static/css/app.css', f'/static/css/app.css?v={css_mtime}')
            
        # Inject the token directly into index.html to bypass any cookie blocking policies
        token_script = f'<script>window.__localToken = "{LOCAL_TOKEN}";</script>'
        html_content = html_content.replace('<head>', f'<head>\n    {token_script}')
            
        res = HTMLResponse(content=html_content)
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        
        res.set_cookie(
            key="local_token",
            value=LOCAL_TOKEN,
            httponly=False,  # JavaScript must be able to read this cookie to send it back via headers
            samesite="lax",
            secure=False
        )
        return res
    return JSONResponse(
        content={"status": "error", "message": "static/index.html not found. Please create it."},
        status_code=404
    )

@app.get("/favicon.ico", include_in_schema=False)
def read_favicon():
    favicon_path = os.path.join("static", "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    # Fallback to PNG if ico is somehow missing
    favicon_png_path = os.path.join("static", "favicon.png")
    if os.path.exists(favicon_png_path):
        return FileResponse(favicon_png_path)
    return JSONResponse(
        content={"status": "error", "message": "favicon not found."},
        status_code=404
    )

# ----------------- Upload API -----------------

@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        # Generate safe unique filename
        ext = os.path.splitext(file.filename)[1]
        if not ext:
            ext = ".png" # default to png
        
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        
        with open(filepath, "wb") as f:
            f.write(await file.read())
            
        relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
        return {
            "status": "success",
            "file_path": relative_path,
            "filename": file.filename
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"文件上传失败: {str(e)}"},
            status_code=500
        )

# ----------------- OCR API -----------------

def auto_crop_image(image):
    try:
        from PIL import ImageOps, ImageStat
        # 估算灰度均值，判断主色调（暗色背景还是亮色背景）
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        mean_val = stat.mean[0]
        
        if mean_val < 100:  # 偏暗，可能含有大面积黑边背景
            bbox = image.getbbox()
            if bbox:
                # 留出 8 像素的边距以防文字贴边影响识别
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
        elif mean_val > 220:  # 偏亮，可能含有大面积白边背景
            inverted = ImageOps.invert(image.convert("RGB"))
            bbox = inverted.getbbox()
            if bbox:
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
    except Exception as e:
        print(f"[Auto Crop] 裁剪失败，返回原图. Error: {str(e)}")
    return image


def ocr_via_siliconflow(image_path: str, api_key: str, model_name: str = "Qwen/Qwen3.5-4B", include_illustration_box: bool = False) -> str:
    """调用 SiliconFlow 官方 API 进行多模态图文公式识别 (使用 Qwen3.5-4B / Qwen2.5-VL 等)"""
    import base64
    import requests
    
    print(f"[OCR Flow] 正在向 SiliconFlow 提交多模态识别任务: {image_path} (模型: {model_name})...")
    
    # 对图片进行 Base64 编码
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")
        
    url = "https://api.siliconflow.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    
    prompt_text = (
        "请精确识别并提取这幅图像中的**所有文字与数学公式**。必须完整转录，不得遗漏或删减图像中的任何字符（包括方括号、题目来源如 '[2025 · 江苏淮安高一期末]' 等）。\n"
        "直接输出图像内容的转录结果，绝对不要夹带任何你个人的说明、开场白、回复语或解释。\n"
        "【排版格式与 LaTeX 语法关键准则 (极重要)】：\n"
        "1. **公式级包裹**：所有 LaTeX 数学命令（如 \\overrightarrow, \\cos, \\sin, \\theta, \\cdot, \\alpha, \\beta, \\gamma, \\Delta 等）以及所有代数式、方程、集合、平面向量符号，**必须并且只能**包裹在 LaTeX 标记中（行内公式使用 $...$，行间/独立公式使用 $$...$$）。绝对不能让任何带有反斜杠 `\\` 的 LaTeX 语法暴露在普通文本中。例如，普通文本中不能出现 `\\overrightarrow{AB}`，而必须写为 `$\\overrightarrow{AB}$`。\n"
        "2. **严禁使用 \\text 语法**：不要在 LaTeX 公式中使用 `\\text{...}` 来包裹大段中文或题目来源。普通的中文叙述和文字必须作为普通的文本直接输出在 LaTeX 块外部。例如，绝对不能输出 `$\\text{江苏淮安高一期末}$`，而必须写为普通的文本：`[2025 · 江苏淮安高一期末]`；绝对不能输出 `$\\text{已知在直角坐标系中}$`，而必须写为 `已知在直角坐标系中`。\n"
        "3. **变量/点/坐标包裹**：所有的几何点符号（如 $A$, $B$, $C$, $D$, $O$, $P$ 等）、所有单个字母变量（如 $x$, $y$, $m$, $n$ 等）以及所有的坐标表达式（如 $(1,2)$, $(3,3)$, $(x,y)$ 等）均需严格包裹在单美元符号 $...$ 中。\n"
        "4. **严禁整段包裹**：不要将普通的中文文本、题目描述或整段话包裹在 LaTeX 标记中。\n"
        "5. **精精确保留排版结构**：务必精精确保留原图的换行、段落以及选项（A、B、C、D）的对齐排版。\n"
        "6. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格（如 `\\,` 或 `\\!` 等），确保数学公式的标准纯净。"
    )
    if include_illustration_box:
        prompt_text += "\n7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含立体几何、平面几何、函数图像、平面向量等几何插图。如果包含，**请务必在输出的文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数（例如插图在整张图偏右侧，可以输出为 `[ILLUSTRATION_BOX: 10, 45, 90, 95]`；如果整张图没有插图，则绝对不要输出 `[ILLUSTRATION_BOX: ...]`）。"

    # 构造 SiliconFlow 的多模态内容消息
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }
    
    max_retries = 3
    timeout = 240
    response = None
    last_error = None
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"[OCR Flow] 正在进行第 {attempt + 1}/{max_retries} 次重试 SiliconFlow 请求...")
            response = robust_request_post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                break
            else:
                last_error = f"HTTP 状态码: {response.status_code}，详情: {response.text}"
                if response.status_code in [500, 502, 503, 504, 429] or "timeout" in response.text.lower():
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    raise RuntimeError(last_error)
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"请求 SiliconFlow 失败 (已尝试 {max_retries} 次): {last_error}")
                
    if not response or response.status_code != 200:
        raise RuntimeError(f"SiliconFlow API 识别失败: {last_error}")
        
    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(f"SiliconFlow 返回的数据中未包含 Choices 结果: {str(res_json)}")
    except Exception as e:
        raise RuntimeError(f"解析 SiliconFlow 响应数据失败: {str(e)}")


def ocr_via_ali_bailian(image_path: str, api_key: str, model_name: str = "qwen3-vl-flash", include_illustration_box: bool = False) -> str:
    """调用阿里云百炼 (Alibaba Bailian) 官方 API 进行多模态图文公式识别 (使用 qwen3-vl-flash 等)"""
    import base64
    import requests
    
    print(f"[OCR Flow] 正在向阿里云百炼 (Alibaba Bailian) 提交多模态识别任务: {image_path} (模型: {model_name})...")
    
    # 对图片进行 Base64 编码
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")
        
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    
    prompt_text = (
        "请精确识别并提取这幅图像中的**所有文字与数学公式**。必须完整转录，不得遗漏、删减或改写图像中的任何字符（包括方括号、题目来源如 '[2025 · 武汉二中高一月考]'、定义说明、前言等大段文字）。\n"
        "直接输出图像内容的转录结果，绝对不要夹带任何你个人的说明、开场白、回复语或解释（例如，不要包含 '这是识别结果：' 等多余的 AI 聊天文字）。\n"
        "【排版格式与 LaTeX 语法关键准则】：\n"
        "1. **严禁整段包裹**：绝对不要将普通的中文文本、题目描述或整段话包裹在 LaTeX 标记（如 `$$...$$` 或 `$...$`）中。普通的中文叙述和文字必须作为普通的文本直接输出。\n"
        "2. **严禁滥用 \\text 语法**：不要在 LaTeX 公式中使用 `\\text{...}` 来包裹大段的中文描述。所有的中文文字都应该写在 LaTeX 块外部。例如，不要输出 `$\\text{已知集合 } B$`，而应该输出 `已知集合 $B$`。\n"
        "3. **公式级包裹**：仅对纯数学符号、代数式、集合、方程等数学对象使用 LaTeX。行内变量/符号（如 $A$、$x$、$-7$ 等）使用单美元符号 `$...$`；独立的一行长公式或复杂等式才使用双美元符号 `$$...$$`。\n"
        "4. **精确保留排版结构**：务必精确保留原图的换行、段落以及选项（A、B、C、D）的对齐排版。\n"
        "5. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格（如 `\\,` 或 `\\!` 等），确保数学公式的标准纯净。\n"
        "6. **保留所有中文文字**：在转录过程中必须百分之百保留题目中的叙述文字，例如定义段落和前言介绍，严禁只输出最后一句问句。"
    )
    if include_illustration_box:
        prompt_text += "\n7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含立体几何、平面几何、函数图像、平面向量等几何插图。如果包含，**请务必在输出的文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数（例如插图在整张图偏右侧，可以输出为 `[ILLUSTRATION_BOX: 10, 45, 90, 95]`；如果整张图没有插图，则绝对不要输出 `[ILLUSTRATION_BOX: ...]`）。"

    # 构造阿里云百炼的多模态内容消息
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }
    
    max_retries = 3
    timeout = 240
    response = None
    last_error = None
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"[OCR Flow] 正在进行第 {attempt + 1}/{max_retries} 次重试阿里云百炼请求...")
            response = robust_request_post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                break
            else:
                last_error = f"HTTP 状态码: {response.status_code}，详情: {response.text}"
                if response.status_code in [500, 502, 503, 504, 429] or "timeout" in response.text.lower():
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    raise RuntimeError(last_error)
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"请求阿里云百炼失败 (已尝试 {max_retries} 次): {last_error}")
                
    if not response or response.status_code != 200:
        raise RuntimeError(f"阿里云百炼 API 识别失败: {last_error}")
        
    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(f"阿里云百炼返回的数据中未包含 Choices 结果: {str(res_json)}")
    except Exception as e:
        raise RuntimeError(f"解析阿里云百炼响应数据失败: {str(e)}")


def ocr_via_zhongzhan(image_path: str, api_key: str, base_url: str, model_name: str, include_illustration_box: bool = False) -> str:
    """调用中转站 (OpenAI 兼容) API 进行多模态图文公式识别"""
    import base64
    import requests
    
    print(f"[OCR Flow] 正在向中转站 (OpenAI 兼容) 提交多模态识别任务: {image_path} (模型: {model_name})...")
    
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")
        
    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    
    prompt = (
        "请精确识别并提取这幅图像中的**所有文字与数学公式**。必须完整转录，不得遗漏、删减或改写图像中的任何字符（包括方括号、题目来源如 '[2025 · 武汉二中高一月考]'、定义说明、前言等大段文字）。\n"
        "直接输出图像内容的转录结果，绝对不要夹带任何你个人的说明、开场白、回复语或解释。\n"
        "【排版格式与 LaTeX 语法关键准则】：\n"
        "1. **严禁整段包裹**：绝对不要将普通的中文文本、题目描述或整段话包裹在 LaTeX 标记（如 `$$...$$` 或 `$...$`）中。普通的中文叙述和文字必须作为普通的文本直接输出。\n"
        "2. **严禁滥用 \\text 语法**：不要在 LaTeX 公式中使用 `\\text{...}` 来包裹大段的中文描述。所有的中文文字都应该写在 LaTeX 块外部。\n"
        "3. **公式级包裹**：仅对纯数学符号、代数式、集合、方程等数学对象使用 LaTeX. 行内变量/符号（如 $A$、$x$ 等）使用单美元符号 `$...$`。\n"
        "4. **精精确保留排版结构**：务必精精确保留原图的换行、段落以及选项的对齐排版。\n"
        "5. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格，确保数学公式的标准纯净。\n"
        "6. **保留所有中文文字**：在转录过程中必须百分之百保留题目中的叙述文字，严禁只输出最后一句问句。"
    )
    if include_illustration_box:
        prompt += "\n7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含立体几何、平面几何、函数图像、平面向量等几何插图。如果包含，**请务必在输出的文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数。"
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }
    
    max_retries = 3
    timeout = 240
    response = None
    last_error = None
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"[OCR Flow] 正在进行第 {attempt + 1}/{max_retries} 次重试中转站请求...")
            response = robust_request_post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 200:
                break
            else:
                last_error = f"HTTP 状态码: {response.status_code}，详情: {response.text}"
                if response.status_code in [500, 502, 503, 504, 429] or "timeout" in response.text.lower():
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    raise RuntimeError(last_error)
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise RuntimeError(f"请求中转站失败 (已尝试 {max_retries} 次): {last_error}")
                
    if not response or response.status_code != 200:
        raise RuntimeError(f"中转站 API 识别失败: {last_error}")
        
    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(f"中转站返回的数据中未包含 Choices 结果: {str(res_json)}")
    except Exception as e:
        raise RuntimeError(f"解析中转站响应数据失败: {str(e)}")


def draw_tikz_via_high_model(image_path: str, prefer_draw: str, latex_content: str = None) -> str:
    """使用指定的高级绘图模型（多模态或纯文本自适应）生成 TikZ 代码"""
    import base64
    import requests
    import re
    
    is_zhongzhan_gpt = prefer_draw.startswith("ZHONGZHAN_GPT/") or prefer_draw.startswith("ZHONGZHAN/")
    is_zhongzhan_claude = prefer_draw.startswith("ZHONGZHAN_CLAUDE/")
    is_zhongzhan = is_zhongzhan_gpt or is_zhongzhan_claude
    is_bailian = prefer_draw.startswith("BAILIAN/")
    
    if is_zhongzhan or is_bailian:
        if is_bailian:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            base_url = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_label = "阿里百炼"
            model_name = prefer_draw.split("/", 1)[1]
            if model_name == "qwen3.7-max":
                model_name = "qwen-max"
        elif is_zhongzhan_gpt:
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
            base_url = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")
            provider_label = "中转站 (GPT)"
            model_name = prefer_draw.split("/", 1)[1]
        else:
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            base_url = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_label = "中转站 (Claude)"
            model_name = prefer_draw.split("/", 1)[1]
            
        if not api_key:
            print(f"[High Model Draw] 未配置 {provider_label} 密钥，降级跳过。")
            return None
        base_url = base_url.rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
    else:
        api_key = os.getenv("SILICONFLOW_API_KEY")
        if not api_key:
            print("[High Model Draw] 未配置 SILICONFLOW_API_KEY，无法调用 SiliconFlow 高级绘图，降级跳过。")
            return None
        model_name = prefer_draw
        url = "https://api.siliconflow.cn/v1/chat/completions"

    # 判断是否为多模态模型 (名称中含 'vl', 'gpt', 'claude'，或者只要是中转站/阿里百炼我们一般默认为多模态)
    is_multimodal = is_zhongzhan or is_bailian or "vl" in model_name.lower() or "thinking" in model_name.lower()
    
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }

    if is_multimodal:
        # 多模态图文输入模式
        try:
            with open(image_path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[High Model Draw] 读取裁剪小图 Base64 失败: {str(e)}")
            return None
            
        prompt = (
            "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是从试卷题目中分割裁剪出来的几何插图局部。\n"
            "另外，这道数学几何题目的完整题干文本如下，请务必作为绘图逻辑参考：\n"
            f"```latex\n{latex_content if latex_content else '暂无题干'}\n```\n"
            "请使用标准的 LaTeX TikZ 几何绘图语言，将这幅几何图形高精度重新绘制一遍。\n"
            "【绘图重要规范与自愈提示】：\n"
            "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊、问候或说明文字。\n"
            "2. 结合题干文本（如提及线线垂直、线面平行以及几何点的真实名称）来理解和校正剪切图中可能缺失、磨损或由于裁剪漏掉的字母。例如，如果题干提到 PA 垂直于面 ABC，但插图顶部顶点上面没有字母，请根据题意在顶部顶点标注为 'P'（绝对不要随意编造非题干中提及的字母，如 D 等）。\n"
            "3. 仔细识别并使用 `\\node` 或 `label` 标在对应的物理位置。重要被遮挡线条请使用 `dashed` 虚线绘制。不要在大片空白处留多余线头。"
        )
        
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_image}"
                }
            }
        ]
    else:
        # 纯文本推理模式 (如 Qwen3.5-397B)，我们把题目题干文字作为逻辑来源
        if not latex_content:
            print("[High Model Draw] 纯文本高级绘图模型未获得输入文本，跳过。")
            return None
            
        prompt = (
            "你是一个 LaTeX/TikZ 几何绘图专家。已知有一道数学几何题目，其文字描述和公式如下：\n"
            f"```latex\n{latex_content}\n```\n"
            "请仔细分析该题目中各几何元素之间的逻辑关系（如线面垂直、平行、坐标位置、夹角等），"
            "编写出一段最精确、美观的 LaTeX TikZ 代码来绘制这道题目的示意插图。\n"
            "【绘图重要规范】：\n"
            "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊或说明文字。\n"
            "2. 绘图比例和字母标注位置要协调美观，重要被遮挡线条请使用 `dashed` 虚线绘制。"
        )
        content_payload = prompt

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": content_payload
            }
        ],
        "stream": False
    }

    try:
        response = robust_request_post(url, headers=headers, json=payload, timeout=120)
        if response.status_code == 200:
            res_json = response.json()
            choices = res_json.get("choices", [])
            if choices:
                ai_message = choices[0].get("message", {}).get("content", "")
                match = re.search(r"\\begin{tikzpicture}.*?\\end{tikzpicture}", ai_message, re.DOTALL | re.IGNORECASE)
                if match:
                    return match.group(0)
                match_block = re.search(r"```(?:latex)?(.*?)```", ai_message, re.DOTALL | re.IGNORECASE)
                if match_block:
                    code = match_block.group(1).strip()
                    if "tikzpicture" not in code:
                        code = f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"
                    return code
                return ai_message.strip()
        else:
            print(f"[High Model Draw Error] 接口返回 HTTP {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[High Model Draw Error] 大模型请求发生异常: {str(e)}")
    return None


@app.post("/api/ocr")
def ocr_formula(
    file: UploadFile = File(...),
    engine: str = Form(None),
    skip_tikz: bool = Form(False)
):
    temp_filepath = None
    try:
        # 同步读取文件字节
        file_bytes = file.file.read()
        
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        
        # 1. 运行自适应图像去噪/自动切边预处理
        image = auto_crop_image(image)
        
        # 将裁剪后的图片保存为持久化 OCR 文件，未来可作为题目配图
        filename = f"ocr_original_{uuid.uuid4().hex[:12]}.png"
        temp_filepath = os.path.join(UPLOAD_DIR, filename)
        image.save(temp_filepath, format="PNG")
        
        # 确定调用的具体引擎。
        # 临时传参 engine 取值: default, siliconflow, simpletex, ali_bailian
        if not engine or engine == "default":
            engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
            
        print(f"[OCR Flow] 当前决策分配识图引擎: {engine}")
        
        latex_content = None
        confidence = 0.95
        provider = ""
        
        # ----------------- 引擎 1: SiliconFlow (Qwen3.5-4B) 多模态识图 -----------------
        if engine == "siliconflow":
            sf_key = os.getenv("SILICONFLOW_API_KEY")
            sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3.5-4B")
            if sf_key and sf_key.strip():
                try:
                    latex_content = ocr_via_siliconflow(temp_filepath, sf_key, model_name=sf_model, include_illustration_box=True)
                    confidence = 0.99
                    provider = f"SiliconFlow ({sf_model})"
                except Exception as e:
                    print(f"[SiliconFlow 识别失败] 发生异常: {str(e)}")
            else:
                print("[OCR Flow Warning] 未配置 SILICONFLOW_API_KEY，SiliconFlow 引擎无法启动！")
                
        # ----------------- 引擎 2: 阿里百炼 (qwen3-vl-flash) 多模态识图 -----------------
        elif engine == "ali_bailian":
            ali_key = os.getenv("ALI_BAILIAN_API_KEY")
            ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3-vl-flash")
            if ali_key and ali_key.strip():
                try:
                    latex_content = ocr_via_ali_bailian(temp_filepath, ali_key, model_name=ali_model, include_illustration_box=True)
                    confidence = 0.99
                    provider = f"阿里云百炼 ({ali_model})"
                except Exception as e:
                    print(f"[阿里云百炼 识别失败] 发生异常: {str(e)}")
            else:
                print("[OCR Flow Warning] 未配置 ALI_BAILIAN_API_KEY，阿里云百炼引擎无法启动！")

        # ----------------- 引擎 2.5: 中转站 多模态识图 -----------------
        elif engine in ["zhongzhan", "zhongzhan_gpt", "zhongzhan_claude"]:
            if engine == "zhongzhan_claude":
                zz_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
                zz_base_url = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
                zz_model = os.getenv("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
                provider_label = "中转站 (Claude)"
            else:
                zz_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
                zz_base_url = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")
                zz_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
                provider_label = "中转站 (GPT)"
                
            if zz_key and zz_key.strip():
                try:
                    latex_content = ocr_via_zhongzhan(temp_filepath, zz_key, zz_base_url, model_name=zz_model, include_illustration_box=True)
                    confidence = 0.99
                    provider = f"{provider_label} ({zz_model})"
                except Exception as e:
                    print(f"[{provider_label} 识别失败] 发生异常: {str(e)}")
            else:
                print(f"[OCR Flow Warning] 未配置 {provider_label} 密钥，中转站引擎无法启动！")

        if not latex_content:
            raise RuntimeError("当前分配的识图引擎均无法启动或识别失败。请检查右上角「API设置」中是否正确配置了 硅基流动(SiliconFlow) 或是 阿里百炼(Alibaba Bailian) 的 API Key。")

        # 成功，返回且进一步清洗
        if latex_content:
            # 过滤干扰字符
            latex_content = latex_content.replace("\\,", "").replace("\\!", "")

        # ----------------- 双阶段多模态识图与高级 TikZ 绘图模型联动 -----------------
        tikz_code_from_high_model = None
        tikz_image_path = None
        
        if latex_content:
            import re
            # 提取可能由默认模型标注的示意图 Bounding Box 标记
            box_match = re.search(r"\[ILLUSTRATION_BOX:\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]", latex_content, re.IGNORECASE)
            if box_match:
                # 第一步先确保擦除标记，防止乱入题干文本框
                latex_content = re.sub(r"\[ILLUSTRATION_BOX:.*?\]", "", latex_content).strip()
                
                if not skip_tikz:
                    try:
                        # 不再执行物理分割裁剪，直接将整张原始题目截图发送给高级视觉绘图模型进行图形分析与重画
                        prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
                        print(f"[Illustration Draw] 检测到插图标记，直接将整张原图送往高级模型 {prefer_draw} 进行 TikZ 解析绘图...")
                        
                        tikz_code_from_high_model = draw_tikz_via_high_model(
                            temp_filepath, # 传入整图
                            prefer_draw,
                            latex_content=latex_content
                        )
                    except Exception as draw_err:
                        print(f"[Illustration Draw Fail] 高级多模态模型整图分析绘图失败: {str(draw_err)}")
                else:
                    print("[Illustration Draw] 检测到插图标记，但由于已勾选跳过，故未调用高级绘图模型进行 TikZ 绘制")
            else:
                # 剔除可能存在的由于大模型幻觉或者部分输出造成的残缺标记
                latex_content = re.sub(r"\[ILLUSTRATION_BOX:.*?\]", "", latex_content).strip()

        # 如果高级模型成功生成了 TikZ 代码，我们在后台自动进行编译预览，并格式化追加到 latex 文本中！
        if tikz_code_from_high_model:
            try:
                print(f"[Illustration Draw] 高级绘图模型成功输出 TikZ 源码！正在开始编译为预览图...")
                compiled_path = compile_tikz_to_png(tikz_code_from_high_model)
                if compiled_path:
                    tikz_image_path = compiled_path
                    # 自动在题干文本的尾部追加 Markdown 插图引用
                    latex_content += f"\n\n![]({compiled_path})"
                    print(f"[Illustration Draw] 编译成功: {compiled_path}")
            except Exception as compile_err:
                print(f"[Illustration Draw] 编译高级模型生成的 TikZ 失败: {str(compile_err)}")

        # 将 temp_filepath 置为 None，避免在 finally 块中被删除
        saved_filepath = temp_filepath
        temp_filepath = None

        return {
            "status": "success",
            "latex": latex_content,
            "confidence": confidence,
            "provider": provider,
            "image_path": f"/{UPLOAD_DIR_REL}/{os.path.basename(saved_filepath)}",
            "tikz_code": tikz_code_from_high_model,
            "tikz_image_path": tikz_image_path
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=500
        )
    finally:
        # 确保清理临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception as e_cleanup:
                print(f"[OCR Flow Cleanup Error] 无法删除临时文件 {temp_filepath}: {str(e_cleanup)}")

# ----------------- DeepSeek AI Solve API -----------------

@app.post("/api/ai/solve")
async def ai_solve(
    content: str = Form(...),
    question_type: str = Form("detailed_answer"),
    ocr_result: str = Form(""),
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form("deepseek-v4-pro"),
    stream: str = Form("false")
):
    # 动态解析模型所属的服务商前缀与真实模型名
    model_lower = model.lower()
    api_key = None
    api_base = None
    model_name = model
    provider_name = "DeepSeek"
    
    if "/" in model:
        parts = model.split("/", 1)
        prefix = parts[0].upper()
        model_name = parts[1]
        
        if prefix == "SILICONFLOW":
            api_key = os.getenv("SILICONFLOW_API_KEY")
            api_base = "https://api.siliconflow.cn/v1"
            provider_name = "硅基流动 (SILICONFLOW_API_KEY)"
        elif prefix == "BAILIAN":
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼 (ALI_BAILIAN_API_KEY)"
            if model_name == "qwen3.7-max":
                model_name = "qwen-max"
        elif prefix == "DEEPSEEK":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek (DEEPSEEK_API_KEY)"
        elif prefix == "ZHONGZHAN_GPT":
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY")
            api_base = os.getenv("ZHONGZHAN_GPT_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 A (ZHONGZHAN_GPT_API_KEY)"
        elif prefix == "ZHONGZHAN_CLAUDE":
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            api_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 B (ZHONGZHAN_CLAUDE_API_KEY)"
    else:
        # 向后兼容传统无前缀模式
        if "qwen" in model_lower:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼 (ALI_BAILIAN_API_KEY)"
            model_name = "qwen-max" if model == "qwen3.7-max" else model
        else:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek (DEEPSEEK_API_KEY)"
            model_name = model

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法智能解答！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        url = f"{api_base.rstrip('/')}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        type_mapping = {
            "single_choice": "单选题",
            "multi_choice": "多选题",
            "fill_in_blank": "填空题",
            "detailed_answer": "解答题"
        }
        type_str = type_mapping.get(question_type, "数学题")
        
        is_choice_or_blank = question_type in ["single_choice", "multi_choice", "fill_in_blank"]
        
        if is_choice_or_blank:
            first_block_header = "\\\\textbf{【参考答案】}"
            format_rules = (
                "3. 你的输出内容必须且仅包含以下三个结构化板块（使用 LaTeX 粗体格式，绝对禁止使用 Markdown 的 ** 双星号加粗语法）：\n"
                "   - \\\\textbf{【参考答案】}：直接给出最简练、准确的最终选项字母（如 A、B、C、D）或填空答案内容。\n"
                "   - \\\\textbf{【解析过程】}：写出本题细致的推导和求解过程与解析步骤，方便师生理清思路。\n"
                "   - \\\\textbf{【核心知识点】}：列出解答本题用到的关键数学公式、定理或思想方法。"
            )
        else:
            first_block_header = "\\\\textbf{【规范解答】}"
            format_rules = (
                "3. 你的输出内容必须且仅包含以下三个结构化板块（使用 LaTeX 粗体格式，绝对禁止使用 Markdown 的 ** 双星号加粗语法）：\n"
                "   - \\\\textbf{【规范解答】}：给出符合高考/正式试卷卷面书写规范的标准解答步骤。注意书写格式要标准且严密，文字要凝练，不要有任何闲话废话或多余的过渡词，供学生参考标准卷面得分步骤。\n"
                "   - \\\\textbf{【解析思路】}：给出解答本题背后的核心解析思路与破题关窍。解析长短请视该题目的真实难度而定，不要面面俱到，直接点出解答最核心的要点即可，言简意赅，切忌冗长。\n"
                "   - \\\\textbf{【核心知识点】}：列出解答本题用到的关键数学原理、定理或思想方法。"
            )
            
        system_instructions = (
            "你是一位极其严谨的、资深的高中数学教研专家。请解答用户输入的高中数学题目。特别注意：这必须是一道符合高中数学大纲要求的题目，你的解题思路、方法和技巧绝对不能超出中国普通高中阶段的水平（严禁使用大学高等数学、微积分、高等代数、洛必达法则、泰勒展开、拉格朗日中值定理等超出高中阶段教学大纲的大学方法，必须完全采用符合高中知识体系和认知范围的常规或技巧性方法）。\n"
            "【输出核心准则】\n"
            "1. 你的回答必须直接、干净地从下面的结构化板块开始。严禁包含任何前言、导语、引入承接句或问候语（例如“你好！”、“下面是解析：”等）。\n"
            f"2. 你的回答必须直接以“{first_block_header}”作为第一个字符开始输出。严禁在结尾包含任何总结、客套话或多余的尾注段落。\n"
            "【输出格式要求】\n"
            "1. 必须使用标准的 LaTeX 语法书写所有的数学公式。行内公式使用 $...$ 或 \\( ... \\)，行间公式使用 $$\\n...\\n$$ 或 \\[ ... \\]。\n"
            "2. 排版优雅，逻辑步骤条理清晰，推理严密，没有任何废话。\n"
            f"{format_rules}\n"
            "4. 绝不要带有任何无关的字句，直接输出这三个板块。"
        )
        
        user_prompt = f"题目类型: {type_str}\n"
        if ocr_result.strip():
            user_prompt += f"已有的 OCR 识别解析/草稿内容如下，请在此基础上进行润色、修正、细化或简化，并生成最终解答步骤：\n{ocr_result}\n\n"
        if custom_prompt:
            user_prompt += f"补充引导指令: {custom_prompt}\n"
        user_prompt += f"题干内容:\n{content}"
        
        # Double max_tokens to 16384, but cap at 8192 for Alibaba Bailian compatible-mode endpoints
        max_output_tokens = 8192 if (api_base and "aliyuncs.com" in api_base.lower()) else 16384
            
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_output_tokens
        }
        
        # Configure thinking parameter if specified (only for DeepSeek models/endpoints, excluding legacy models that don't support it)
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        is_siliconflow = api_base and "siliconflow" in api_base.lower()
        
        is_bailian = api_base and "aliyuncs.com" in api_base.lower()
        if is_bailian:
            # Connect the front-end '深度思考' toggle button to Alibaba Bailian's 'enable_thinking' API parameter
            if thinking == "enabled":
                data["enable_thinking"] = True
            else:
                data["enable_thinking"] = False

        if is_siliconflow:
            # Native R1 models on SiliconFlow do not use enable_thinking (they are always reasoning)
            # Other models (V3, V4 Pro, Flash, etc.) use enable_thinking and reasoning_effort
            if "r1" not in model_name.lower():
                is_deepseek = False  # Bypass OpenAI standard thinking parameter
                if thinking == "enabled":
                    data["enable_thinking"] = True
                    if "v4" in model_name.lower():
                        data["reasoning_effort"] = "max"
                else:
                    data["enable_thinking"] = False

        # Support OpenAI reasoning models (gpt-5, o1, o3, etc.) on transit APIs
        is_openai_reasoning = ("gpt-5" in model_name.lower() or "o1" in model_name.lower() or "o3" in model_name.lower())
        if is_openai_reasoning:
            is_deepseek = False  # Bypass DeepSeek thinking parameter
            if thinking == "enabled":
                data["reasoning_effort"] = "high"    # Maximum mathematical depth and verification
            else:
                data["reasoning_effort"] = "medium"  # Balanced speed and analytical quality
        
        if is_deepseek and thinking in ["enabled", "disabled"]:
            data["thinking"] = {"type": thinking}
            
        # When thinking mode is active, temperature is ignored/deprecated by DeepSeek.
        # But when thinking is disabled or non-DeepSeek model, specify it.
        if not is_deepseek or thinking == "disabled":
            data["temperature"] = 0.2
            
        if stream == "true":
            def event_generator():
                data["stream"] = True
                try:
                    response = robust_request_post(url, headers=headers, json=data, timeout=300, stream=True)
                    if response.status_code != 200:
                        error_msg = f"{provider_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}"
                        yield f"data: {json.dumps({'status': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                        return
                    
                    reasoning_count = 0
                    content_count = 0
                    
                    for line in response.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8").strip()
                        if line_str.startswith("data:"):
                            data_content = line_str[5:].strip()
                            if data_content == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_content)
                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""
                                
                                if reasoning:
                                    reasoning_count += 1
                                if content_piece:
                                    content_count += 1
                                    
                                if reasoning or content_piece:
                                    yield f"data: {json.dumps({'status': 'processing', 'reasoning': reasoning, 'content': content_piece, 'reasoning_count': reasoning_count, 'content_count': content_count}, ensure_ascii=False)}\n\n"
                            except Exception:
                                continue
                    yield f"data: {json.dumps({'status': 'done'}, ensure_ascii=False)}\n\n"
                except requests.exceptions.Timeout:
                    friendly_msg = (
                        f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
                        f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
                    )
                    yield f"data: {json.dumps({'status': 'error', 'message': friendly_msg}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'AI 解析生成出错: {str(e)}'}, ensure_ascii=False)}\n\n"
            
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # Generous 300 seconds timeout (5 minutes) for high-school math reasoning and network proxies
        response = robust_request_post(url, headers=headers, json=data, timeout=300)
        
        if response.status_code != 200:
            raise Exception(f"{provider_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}")
            
        res_json = response.json()
        
        msg_obj = res_json.get("choices", [{}])[0].get("message", {})
        ai_message = msg_obj.get("content") or ""
        reasoning_content = msg_obj.get("reasoning_content") or ""
        
        # Robust fallback: if content is empty but reasoning is present, use reasoning as explanation
        if not ai_message and reasoning_content:
            ai_message = f"【深度思考推理过程】\n{reasoning_content}\n\n【参考解析】已成功生成推理步骤。如果需要标准的三板块排版，请尝试在控制面板中关闭「AI 深度思考推理」再次生成。"
            
        if not ai_message:
            print("[DEBUG Solve API] API Request Data:", json.dumps(data, ensure_ascii=False))
            print("[DEBUG Solve API] HTTP Status:", response.status_code)
            print("[DEBUG Solve API] Raw Response Text:", response.text)
            raise Exception(f"{provider_name} 返回了空消息，请检查 API 或账户余额。")
            
        return {
            "status": "success",
            "solution": ai_message
        }
    except requests.exceptions.Timeout:
        friendly_msg = (
            f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
            f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
        )
        return JSONResponse(
            content={"status": "error", "message": friendly_msg},
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 解析生成出错: {str(e)}"},
            status_code=500
        )

# ----------------- Save ENV Settings from UI -----------------

@app.get("/api/settings")
def get_settings():
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    sf_key = os.getenv("SILICONFLOW_API_KEY", "")
    ali_key = os.getenv("ALI_BAILIAN_API_KEY", "")
    
    # 兼容老版 ZHONGZHAN 环境变量
    zz_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
    zz_gpt_base = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "")
    zz_gpt_ocr_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
    
    zz_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
    zz_claude_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "")
    zz_claude_ocr_model = os.getenv("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
    
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3.5-4B")
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3-vl-flash")
    prefer_solve_model = os.getenv("PREFER_SOLVE_MODEL", "deepseek-v4-pro")
    prefer_parse_model = os.getenv("PREFER_PARSE_MODEL", "deepseek-v4-flash")
    prefer_classify_model = os.getenv("PREFER_CLASSIFY_MODEL") or os.getenv("DEEPSEEK_CLASSIFY_MODEL", "deepseek-v4-flash")
    prefer_draw_model = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    
    masked_ds = ""
    if ds_key:
        masked_ds = ds_key[:4] + "••••" + ds_key[-4:] if len(ds_key) > 8 else "••••••••"
        
    masked_sf = ""
    if sf_key:
        masked_sf = sf_key[:4] + "••••" + sf_key[-4:] if len(sf_key) > 8 else "••••••••"
        
    masked_ali = ""
    if ali_key:
        masked_ali = ali_key[:4] + "••••" + ali_key[-4:] if len(ali_key) > 8 else "••••••••"

    masked_zz_gpt = ""
    if zz_gpt_key:
        masked_zz_gpt = zz_gpt_key[:4] + "••••" + zz_gpt_key[-4:] if len(zz_gpt_key) > 8 else "••••••••"
        
    masked_zz_claude = ""
    if zz_claude_key:
        masked_zz_claude = zz_claude_key[:4] + "••••" + zz_claude_key[-4:] if len(zz_claude_key) > 8 else "••••••••"
        
    return {
        "deepseek_key": masked_ds,
        "siliconflow_key": masked_sf,
        "ali_bailian_key": masked_ali,
        "zhongzhan_gpt_key": masked_zz_gpt,
        "zhongzhan_gpt_base_url": zz_gpt_base,
        "zhongzhan_gpt_ocr_model": zz_gpt_ocr_model,
        "zhongzhan_claude_key": masked_zz_claude,
        "zhongzhan_claude_base_url": zz_claude_base,
        "zhongzhan_claude_ocr_model": zz_claude_ocr_model,
        "prefer_engine": prefer_engine,
        "siliconflow_model": sf_model,
        "ali_bailian_model": ali_model,
        "prefer_solve_model": prefer_solve_model,
        "prefer_parse_model": prefer_parse_model,
        "prefer_classify_model": prefer_classify_model,
        "prefer_draw_model": prefer_draw_model
    }

@app.post("/api/settings/save")
async def save_settings(
    deepseek_key: str = Form(""),
    siliconflow_key: str = Form(""),
    ali_bailian_key: str = Form(""),
    zhongzhan_gpt_key: str = Form(""),
    zhongzhan_gpt_base_url: str = Form(""),
    zhongzhan_gpt_ocr_model: str = Form(""),
    zhongzhan_claude_key: str = Form(""),
    zhongzhan_claude_base_url: str = Form(""),
    zhongzhan_claude_ocr_model: str = Form(""),
    prefer_engine: str = Form("siliconflow"),
    siliconflow_model: str = Form("Qwen/Qwen3.5-4B"),
    ali_bailian_model: str = Form("qwen3-vl-flash"),
    prefer_solve_model: str = Form("deepseek-v4-pro"),
    prefer_parse_model: str = Form("deepseek-v4-flash"),
    prefer_classify_model: str = Form("deepseek-v4-flash"),
    prefer_draw_model: str = Form("Qwen/Qwen3-VL-32B-Instruct")
):
    try:
        # If masked, preserve current key
        if "••••" in deepseek_key:
            deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if "••••" in siliconflow_key:
            siliconflow_key = os.getenv("SILICONFLOW_API_KEY", "")
        if "••••" in ali_bailian_key:
            ali_bailian_key = os.getenv("ALI_BAILIAN_API_KEY", "")
        if "••••" in zhongzhan_gpt_key:
            zhongzhan_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
        if "••••" in zhongzhan_claude_key:
            zhongzhan_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
            
        # Read current .env
        env_lines = []
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        keys_replaced = {
            "DEEPSEEK_API_KEY": False,
            "SILICONFLOW_API_KEY": False,
            "ALI_BAILIAN_API_KEY": False,
            "ZHONGZHAN_GPT_API_KEY": False,
            "ZHONGZHAN_GPT_BASE_URL": False,
            "ZHONGZHAN_GPT_OCR_MODEL": False,
            "ZHONGZHAN_CLAUDE_API_KEY": False,
            "ZHONGZHAN_CLAUDE_BASE_URL": False,
            "ZHONGZHAN_CLAUDE_OCR_MODEL": False,
            "OCR_PREFER_ENGINE": False,
            "SILICONFLOW_OCR_MODEL": False,
            "ALI_BAILIAN_OCR_MODEL": False,
            "PREFER_SOLVE_MODEL": False,
            "PREFER_PARSE_MODEL": False,
            "PREFER_CLASSIFY_MODEL": False,
            "PREFER_DRAW_MODEL": False
        }
        new_lines = []
        
        for line in env_lines:
            line_strip = line.strip()
            # Skip old Pix2Text settings to clean .env
            if line_strip.startswith("PIX2TEXT_API_KEY=") or line_strip.startswith("PIX2TEXT_SERVER_TYPE="):
                continue
                
            if line_strip.startswith("DEEPSEEK_API_KEY="):
                new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
                keys_replaced["DEEPSEEK_API_KEY"] = True
            elif line_strip.startswith("SILICONFLOW_API_KEY="):
                new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
                keys_replaced["SILICONFLOW_API_KEY"] = True
            elif line_strip.startswith("ALI_BAILIAN_API_KEY="):
                new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
                keys_replaced["ALI_BAILIAN_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_API_KEY="):
                new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
                keys_replaced["ZHONGZHAN_GPT_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
                keys_replaced["ZHONGZHAN_GPT_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
                keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_API_KEY="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"] = True
            elif line_strip.startswith("OCR_PREFER_ENGINE="):
                new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
                keys_replaced["OCR_PREFER_ENGINE"] = True
            elif line_strip.startswith("SILICONFLOW_OCR_MODEL="):
                new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
                keys_replaced["SILICONFLOW_OCR_MODEL"] = True
            elif line_strip.startswith("ALI_BAILIAN_OCR_MODEL="):
                new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
                keys_replaced["ALI_BAILIAN_OCR_MODEL"] = True
            elif line_strip.startswith("PREFER_SOLVE_MODEL="):
                new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
                keys_replaced["PREFER_SOLVE_MODEL"] = True
            elif line_strip.startswith("PREFER_PARSE_MODEL="):
                new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
                keys_replaced["PREFER_PARSE_MODEL"] = True
            elif line_strip.startswith("PREFER_CLASSIFY_MODEL=") or line_strip.startswith("DEEPSEEK_CLASSIFY_MODEL="):
                new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
                keys_replaced["PREFER_CLASSIFY_MODEL"] = True
            elif line_strip.startswith("PREFER_DRAW_MODEL="):
                new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
                keys_replaced["PREFER_DRAW_MODEL"] = True
            else:
                new_lines.append(line)
                
        # Append keys if not replaced
        if not keys_replaced["DEEPSEEK_API_KEY"]:
            new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
        if not keys_replaced["SILICONFLOW_API_KEY"]:
            new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
        if not keys_replaced["ALI_BAILIAN_API_KEY"]:
            new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
        if not keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
        if not keys_replaced["OCR_PREFER_ENGINE"]:
            new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
        if not keys_replaced["SILICONFLOW_OCR_MODEL"]:
            new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
        if not keys_replaced["ALI_BAILIAN_OCR_MODEL"]:
            new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
        if not keys_replaced["PREFER_SOLVE_MODEL"]:
            new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
        if not keys_replaced["PREFER_PARSE_MODEL"]:
            new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
        if not keys_replaced["PREFER_CLASSIFY_MODEL"]:
            new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
        if not keys_replaced["PREFER_DRAW_MODEL"]:
            new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
            
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        # Clean current process env
        os.environ.pop("PIX2TEXT_API_KEY", None)
        os.environ.pop("PIX2TEXT_SERVER_TYPE", None)
        
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key
        os.environ["SILICONFLOW_API_KEY"] = siliconflow_key
        os.environ["ALI_BAILIAN_API_KEY"] = ali_bailian_key
        os.environ["ZHONGZHAN_GPT_API_KEY"] = zhongzhan_gpt_key
        os.environ["ZHONGZHAN_GPT_BASE_URL"] = zhongzhan_gpt_base_url
        os.environ["ZHONGZHAN_GPT_OCR_MODEL"] = zhongzhan_gpt_ocr_model
        os.environ["ZHONGZHAN_CLAUDE_API_KEY"] = zhongzhan_claude_key
        os.environ["ZHONGZHAN_CLAUDE_BASE_URL"] = zhongzhan_claude_base_url
        os.environ["ZHONGZHAN_CLAUDE_OCR_MODEL"] = zhongzhan_claude_ocr_model
        
        os.environ["OCR_PREFER_ENGINE"] = prefer_engine
        os.environ["SILICONFLOW_OCR_MODEL"] = siliconflow_model
        os.environ["ALI_BAILIAN_OCR_MODEL"] = ali_bailian_model
        os.environ["PREFER_SOLVE_MODEL"] = prefer_solve_model
        os.environ["PREFER_PARSE_MODEL"] = prefer_parse_model
        os.environ["PREFER_CLASSIFY_MODEL"] = prefer_classify_model
        os.environ["PREFER_DRAW_MODEL"] = prefer_draw_model
        
        return {"status": "success", "message": "API 与首选大模型配置已成功保存并即时生效！"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"保存配置失败: {str(e)}"},
            status_code=500
        )

# ----------------- TikZ Render & AI Correction API -----------------

def compile_tikz_to_png(tikz_code: str) -> str:
    """
    编译 TikZ 代码为 PNG 并存放在静态资源目录中。
    如果编译成功，返回相对路径（如 /static/uploads/tikz_xxx.png）。
    如果编译失败，抛出 Exception 详细说明原因。
    """
    import shutil
    import uuid
    import subprocess
    import os
    import platform

    # 1. 检查 xelatex
    # macOS 特有处理：如果系统是 macOS 且标准 MacTeX 路径存在，确保其在 PATH 中，防止 GUI/后台进程环境变量丢失
    if platform.system() == "Darwin":
        mactex_bin = "/Library/TeX/texbin"
        if os.path.exists(mactex_bin) and mactex_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.path.pathsep + mactex_bin

    if not shutil.which("xelatex"):
        raise RuntimeError("系统未检测到 'xelatex' 编译器。请确保您的系统已安装 MacTeX/TeX Live 并将其加入 PATH。")

    # 2. 检查 PyMuPDF (fitz)
    try:
        import fitz
    except ImportError:
        raise RuntimeError("Python 环境中未安装 'pymupdf'，无法将 PDF 转换为图像，请运行 'pip install pymupdf' 安装。")

    # 3. 创建临时文件夹
    temp_dir = os.path.join(UPLOAD_DIR, ".tikz_temp")
    os.makedirs(temp_dir, exist_ok=True)

    unique_id = uuid.uuid4().hex
    tex_path = os.path.join(temp_dir, f"{unique_id}.tex")
    pdf_path = os.path.join(temp_dir, f"{unique_id}.pdf")
    png_path = os.path.join(temp_dir, f"{unique_id}.png")
    aux_path = os.path.join(temp_dir, f"{unique_id}.aux")
    log_path = os.path.join(temp_dir, f"{unique_id}.log")

    # 拼装完整的 TeX 模板
    tex_content = f"""\\documentclass[tikz, border=2mm]{{standalone}}
\\usepackage{{ctex}}
\\usepackage{{amsmath}}
\\usepackage{{amssymb}}
\\usepackage{{tikz}}
\\usepackage{{pgfplots}}
\\pgfplotsset{{compat=1.16}}
\\usetikzlibrary{{patterns}}
\\usetikzlibrary{{calc,positioning,intersections,arrows}}
\\usetikzlibrary{{shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,shapes.callouts,decorations.pathreplacing,decorations.markings}}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

    try:
        # 写入临时 tex 文件
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        # 调用 xelatex 编译
        result = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "-output-directory", temp_dir, tex_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )

        if result.returncode != 0:
            # 尝试提取编译错误原因
            log_content = ""
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                        # 找到包含 ! 的报错行
                        error_lines = [line.strip() for line in lines if line.startswith("!")]
                        if error_lines:
                            log_content = "\n".join(error_lines[:3])
                except Exception:
                    pass
            error_msg = log_content if log_content else "LaTeX 语法错误，编译失败。"
            raise RuntimeError(f"编译错误: {error_msg}")

        if not os.path.exists(pdf_path):
            raise RuntimeError("编译未生成 PDF 文件。")

        # 使用 fitz 将 PDF 转换成 PNG
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            raise RuntimeError("生成的 PDF 文件为空。")
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=150)
        pix.save(png_path)
        doc.close()

        if not os.path.exists(png_path):
            raise RuntimeError("PDF 转换 PNG 失败。")

        # 将最终生成的图片拷贝到 uploads 目录下
        final_filename = f"tikz_{unique_id}.png"
        final_dest = os.path.join(UPLOAD_DIR, final_filename)
        shutil.copy2(png_path, final_dest)

        # 返回相对路径
        return f"/{UPLOAD_DIR_REL}/{final_filename}"

    except subprocess.TimeoutExpired:
        raise RuntimeError("编译超时 (15秒)，可能是您的 TikZ 绘图循环出现了死循环。")
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        # 清理临时文件
        for temp_file in [tex_path, pdf_path, png_path, aux_path, log_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

@app.post("/api/render_tikz")
def render_tikz_endpoint(tikz_code: str = Form(...)):
    """接收 TikZ 代码并编译成静态 PNG，返回其相对路径"""
    try:
        image_path = compile_tikz_to_png(tikz_code)
        return {"status": "success", "image_path": image_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/correct_tikz")
def correct_tikz_endpoint(
    tikz_code: str = Form(...),
    original_image_path: str = Form(...),
    user_prompt: str = Form(None)
):
    """利用用户指定的高级绘图模型进行 TikZ 纠错，支持人工指导意见注入"""
    import base64
    import requests
    
    # 动态读取高级绘图模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    is_zhongzhan = prefer_draw.startswith("ZHONGZHAN/")
    
    if is_zhongzhan:
        zhongzhan_key = os.getenv("ZHONGZHAN_API_KEY")
        if not zhongzhan_key:
            raise HTTPException(
                status_code=400,
                detail="未配置中转站 API Key (ZHONGZHAN_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = zhongzhan_key.strip()
        model_name = prefer_draw.split("/", 1)[1]
        base_url = os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        print(f"[TikZ Correction] 启用中转站高级模型进行纠错: {model_name}, Base URL: {url}")
    else:
        # SiliconFlow 路由配置
        sf_key = os.getenv("SILICONFLOW_API_KEY")
        if not sf_key:
            raise HTTPException(
                status_code=400,
                detail="未配置 硅基流动 API Key (SILICONFLOW_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = sf_key.strip()
        model_name = prefer_draw
        url = "https://api.siliconflow.cn/v1/chat/completions"
        print(f"[TikZ Correction] 启用 SiliconFlow 高级模型进行纠错: {model_name}")

    # 对原始截图进行 Base64 编码
    clean_original_path = original_image_path.lstrip("/")
    if not os.path.exists(clean_original_path):
        raise HTTPException(status_code=400, detail=f"找不到原始题目图片: {original_image_path}")

    try:
        with open(clean_original_path, "rb") as f:
            encoded_original = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取原始图片失败: {str(e)}")

    # 尝试编译当前的 TikZ 代码
    rendered_image_path = None
    compile_error_log = None
    try:
        rendered_image_path = compile_tikz_to_png(tikz_code)
    except Exception as e:
        compile_error_log = str(e)

    # 构造请求头部
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # 视觉比对模式（编译成功，获取到两张图）
    if rendered_image_path:
        clean_rendered_path = rendered_image_path.lstrip("/")
        try:
            with open(clean_rendered_path, "rb") as f:
                encoded_rendered = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取渲染出的 TikZ 图片失败: {str(e)}")

        prompt = (
            "你是一个 LaTeX/TikZ 几何绘图专家和视觉审稿人。下面为你提供两张图片：\n"
            "第一张（Image A）是手绘或试卷中的原始题目插图，第二张（Image B）是我使用 TikZ 渲染出来的插图。\n"
            "另外，这是我目前用于绘制第二张图的 TikZ 代码：\n"
            "```latex\n"
            f"{tikz_code}\n"
            "```\n"
            "请完成以下任务：\n"
            "1. 仔细对比两张图，找出不一致的地方（如几何点位置、夹角大小、实线/虚线、字母标注、箭头方向等拓扑细节）。\n"
            "2. 针对性修改我的 TikZ 绘图代码，使其生成的图形能够 100% 还原原始插图 (Image A)。\n"
            "3. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分，或者包含它们）。请确保不输出任何与代码无关的开场白或闲聊文字。"
        )
        if user_prompt and user_prompt.strip():
            prompt += f"\n\n【人工修改和纠错指导意见】：\n用户指出了当前图形的以下具体错误或修改意见，请你在生成修正代码时务必优先且绝对遵循这一意见：\n{user_prompt.strip()}"

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_rendered}"
                }
            }
        ]
        
        # 临时创建的渲染图在使用后也可以删除，以节省磁盘
        try:
            os.remove(clean_rendered_path)
        except Exception:
            pass

    # 报错自愈模式（编译失败，只有原始图 + 报错日志）
    else:
        prompt = (
            "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是原始题目的正确几何插图。\n"
            "我试图用 TikZ 绘制它，但我的代码在编译时报错了。\n"
            "这是我当前编写的代码：\n"
            "```latex\n"
            f"{tikz_code}\n"
            "```\n"
            f"编译器的具体报错日志如下：\n"
            f"```text\n"
            f"{compile_error_log}\n"
            "```\n"
            "请完成以下任务：\n"
            "1. 结合原始图片以及报错日志，找出代码中的语法错误或逻辑死循环。\n"
            "2. 修正这些语法错误，使其能通过 LaTeX 编译，并精准绘制出原始图中的几何图形。\n"
            "3. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分，或者包含它们）。请确保不输出任何与代码无关的开场白或闲聊文字。"
        )
        if user_prompt and user_prompt.strip():
            prompt += f"\n\n【人工修改和纠错指导意见】：\n用户指出了当前图形的以下具体错误或修改意见，请你在生成修正代码时务必优先且绝对遵循这一意见：\n{user_prompt.strip()}"

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            }
        ]

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": content_payload
            }
        ],
        "stream": False
    }

    try:
        response = robust_request_post(url, headers=headers, json=payload, timeout=90)
        if response.status_code != 200:
            raise RuntimeError(f"大模型接口返回错误 HTTP {response.status_code}: {response.text}")
        
        res_json = response.json()
        choices = res_json.get("choices", [])
        if not choices:
            raise RuntimeError("大模型返回结果为空 choices")
            
        ai_message = choices[0].get("message", {}).get("content", "")
        
        # 使用正则从大模型的回答中抓取 ```latex ... ``` 里面的内容
        match = re.search(r"```(?:latex)?(.*?)```", ai_message, re.DOTALL | re.IGNORECASE)
        corrected_code = match.group(1).strip() if match else ai_message.strip()
        
        return {
            "status": "success",
            "corrected_code": corrected_code,
            "mode": "visual_diff" if rendered_image_path else "error_recovery"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 纠错请求失败: {str(e)}")

@app.post("/api/ai/draw_tikz_from_image")
def draw_tikz_from_image_endpoint(
    image_path: str = Form(...),
    latex_content: str = Form(None),
    x_local_token: str = Header(None, alias="X-Local-Token")
):
    """根据指定的题目图片，调用高级多模态模型生成对应的 LaTeX TikZ 代码"""
    # 鉴权
    local_token = os.getenv("LOCAL_TOKEN", "")
    if local_token and x_local_token != local_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    # 清洗并解析物理路径
    clean_path = image_path.lstrip("/")
    physical_path = os.path.join(os.getcwd(), clean_path)
    
    if not os.path.exists(physical_path):
        raise HTTPException(status_code=404, detail=f"找不到指定的插图物理文件: {image_path}")
        
    # 动态读取绘图高级模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "Qwen/Qwen3-VL-32B-Instruct"
    
    try:
        print(f"[API Draw TikZ] 正在调用高级模型 {prefer_draw} 对插图 {image_path} 进行多模态 TikZ 绘图分析...")
        tikz_code = draw_tikz_via_high_model(
            physical_path,
            prefer_draw,
            latex_content=latex_content
        )
        
        if not tikz_code:
            raise RuntimeError(f"多模态高级模型 {prefer_draw} 未能生成有效的 TikZ 代码")
            
        return {
            "status": "success",
            "tikz_code": tikz_code
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"AI 识图绘图失败: {str(e)}")

# ----------------- Questions Management API -----------------

@app.get("/api/questions")
def list_questions(
    q: str = None,
    compulsory: str = None,
    chapter: str = None,
    knowledge: str = None,
    qtype: str = None,
    difficulty: str = None,
    source: str = None,
    db: Session = Depends(get_db)
):
    query = db.query(Question)
    
    # Check if searching for a specific display sequence number
    target_id_by_seq = None
    if q:
        clean_q = q.strip()
        if clean_q.startswith("#"):
            clean_q = clean_q[1:]
        if clean_q.isdigit():
            seq_val = int(clean_q)
            all_q_asc = db.query(Question.id).order_by(Question.id.asc()).all()
            if 1 <= seq_val <= len(all_q_asc):
                target_id_by_seq = all_q_asc[seq_val - 1][0]

    if q:
        if target_id_by_seq is not None:
            query = query.filter(
                (Question.id == target_id_by_seq) |
                (Question.content.like(f"%{q}%")) | 
                (Question.source.like(f"%{q}%")) |
                (Question.answer_markdown.like(f"%{q}%")) |
                (Question.review.like(f"%{q}%")) |
                (Question.tags.like(f"%{q}%"))
            )
        else:
            query = query.filter(
                (Question.content.like(f"%{q}%")) | 
                (Question.source.like(f"%{q}%")) |
                (Question.answer_markdown.like(f"%{q}%")) |
                (Question.review.like(f"%{q}%")) |
                (Question.tags.like(f"%{q}%"))
            )
    if compulsory:
        query = query.filter(Question.category_compulsory == compulsory)
    if chapter:
        query = query.filter(Question.category_chapter == chapter)
    if knowledge:
        query = query.filter(Question.category_knowledge == knowledge)
    if qtype:
        query = query.filter(Question.question_type == qtype)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if source:
        query = query.filter(Question.source.like(f"%{source}%"))
        
    questions = query.order_by(Question.created_at.desc()).all()
    seq_map = get_seq_mapping(db)
    return [{**item.to_summary_dict(), "seq_num": seq_map.get(item.id)} for item in questions]

@app.get("/api/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    seq_map = get_seq_mapping(db)
    q_dict = q.to_dict()
    q_dict["seq_num"] = seq_map.get(q.id)
    return q_dict

@app.post("/api/questions")
def create_question(
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    db: Session = Depends(get_db)
):
    try:
        # Validate json array format
        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
        # 自动晋升临时图片
        content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
            content, answer_markdown, parsed_img_paths
        )
        
        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter
            
        db_question = Question(
            content=content,
            question_type=question_type,
            category_compulsory=category_compulsory,
            category_chapter=category_chapter,
            category_knowledge=category_knowledge,
            difficulty=difficulty,
            source=source,
            answer_markdown=answer_markdown,
            review=review,
            tikz_code=tikz_code,
            tags=tags
        )
        db_question.image_paths = parsed_img_paths
        
        # Handle related question association (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related:
                g2 = q_related.association_group_id
                if not g2:
                    new_grp = str(uuid.uuid4())
                    q_related.association_group_id = new_grp
                    db_question.association_group_id = new_grp
                else:
                    db_question.association_group_id = g2
        
        db.add(db_question)
        db.commit()
        db.refresh(db_question)
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        background_tasks.add_task(export_database_to_files)
        
        seq_map = get_seq_mapping(db)
        q_dict = db_question.to_dict()
        q_dict["seq_num"] = seq_map.get(db_question.id)
        
        return {"status": "success", "question": q_dict}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"保存题目失败: {str(e)}")

@app.put("/api/questions/{question_id}")
def update_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    try:
        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
        # 自动晋升临时图片
        content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
            content, answer_markdown, parsed_img_paths
        )
        
        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter
            
        db_question.content = content
        db_question.question_type = question_type
        db_question.category_compulsory = category_compulsory
        db_question.category_chapter = category_chapter
        db_question.category_knowledge = category_knowledge
        db_question.difficulty = difficulty
        db_question.source = source
        db_question.answer_markdown = answer_markdown
        db_question.review = review
        db_question.tikz_code = tikz_code
        db_question.tags = tags
        # Clean up removed images from disk to prevent storage leaks
        old_images = db_question.image_paths
        removed_images = set(old_images) - set(parsed_img_paths)
        for img_path in removed_images:
            rel_path = img_path.lstrip("/")
            if rel_path.startswith(f"{UPLOAD_DIR_REL}/") and os.path.exists(rel_path):
                try:
                    os.remove(rel_path)
                except Exception:
                    pass

        db_question.image_paths = parsed_img_paths
        
        # Handle related question association updates (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related and q_related.id != db_question.id:
                g1 = db_question.association_group_id
                g2 = q_related.association_group_id
                
                if not g1 and not g2:
                    new_grp = str(uuid.uuid4())
                    db_question.association_group_id = new_grp
                    q_related.association_group_id = new_grp
                elif g1 and not g2:
                    q_related.association_group_id = g1
                elif not g1 and g2:
                    db_question.association_group_id = g2
                else:
                    if g1 != g2:
                        db.query(Question).filter(Question.association_group_id == g1).update(
                            {Question.association_group_id: g2}, synchronize_session=False
                        )
                        db_question.association_group_id = g2
        
        db.commit()
        db.refresh(db_question)
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        background_tasks.add_task(export_database_to_files)
        
        seq_map = get_seq_mapping(db)
        q_dict = db_question.to_dict()
        q_dict["seq_num"] = seq_map.get(db_question.id)
        
        return {"status": "success", "question": q_dict}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"更新题目失败: {str(e)}")

@app.get("/api/questions/{question_id}/associated")
def get_associated_questions(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")
        
    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return []
        
    associated = db.query(Question).filter(
        Question.association_group_id == grp,
        Question.id != question_id
    ).all()
    
    seq_map = get_seq_mapping(db)
    return [{**item.to_dict(), "seq_num": seq_map.get(item.id)} for item in associated]

@app.post("/api/questions/{question_id}/associate")
def associate_questions_endpoint(
    background_tasks: BackgroundTasks,
    question_id: int,
    target_id: int = Form(...),
    db: Session = Depends(get_db)
):
    q1 = db.query(Question).filter(Question.id == question_id).first()
    q2 = db.query(Question).filter(Question.id == target_id).first()
    if not q1 or not q2:
        raise HTTPException(status_code=404, detail="未找到对应题目")
        
    if q1.id == q2.id:
        raise HTTPException(status_code=400, detail="不能自己和自己关联")
        
    g1 = q1.association_group_id
    g2 = q2.association_group_id
    
    try:
        if not g1 and not g2:
            new_grp = str(uuid.uuid4())
            q1.association_group_id = new_grp
            q2.association_group_id = new_grp
        elif g1 and not g2:
            q2.association_group_id = g1
        elif not g1 and g2:
            q1.association_group_id = g2
        else:
            if g1 != g2:
                db.query(Question).filter(Question.association_group_id == g1).update(
                    {Question.association_group_id: g2}, synchronize_session=False
                )
                q1.association_group_id = g2
                
        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        background_tasks.add_task(export_database_to_files)
        
        return {"status": "success", "message": "关联成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"关联失败: {str(e)}")

@app.delete("/api/questions/{question_id}/associated")
def remove_association(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Remove a question from its association group (bidirectional)."""
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")

    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return {"status": "success", "message": "该题目无关联关系"}

    try:
        # Clear this question's group ID
        q.association_group_id = ""

        # If only one other question remains in the group, clear its group too (no point in a group of one)
        remaining = db.query(Question).filter(
            Question.association_group_id == grp,
            Question.id != question_id
        ).all()

        if len(remaining) == 1:
            remaining[0].association_group_id = ""

        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        background_tasks.add_task(export_database_to_files)
        
        return {"status": "success", "message": "已成功解除所有关联"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"解除关联失败: {str(e)}")

@app.delete("/api/questions/{question_id}")
def delete_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    try:
        # Delete associated images from disk to clean up storage if they exist
        # Only delete files under /static/uploads/
        for img_path in db_question.image_paths:
            # normalize and strip prefix slash
            rel_path = img_path.lstrip("/")
            if rel_path.startswith(f"{UPLOAD_DIR_REL}/") and os.path.exists(rel_path):
                try:
                    os.remove(rel_path)
                except Exception:
                    pass
                    
        db.delete(db_question)
        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        background_tasks.add_task(export_database_to_files)
        
        return {"status": "success", "message": "题目删除成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除题目失败: {str(e)}")

# ----------------- Category Hierarchy Autocomplete API -----------------

RENJIAO_A_CURRICULUM = {
    "必修一": {
        "1. 集合与常用逻辑用语": [
            "1.1 集合的概念",
            "1.2 集合间的基本关系",
            "1.3 集合的基本运算",
            "1.4 充分条件与必要条件",
            "1.5 全称量词与存在量词"
        ],
        "2. 一元二次函数、方程和不等式": [
            "2.1 等式性质与不等式性质",
            "2.2 基本不等式",
            "2.3 二次函数与一元二次方程、不等式"
        ],
        "3. 函数的概念与性质": [
            "3.1 函数的概念及其表示",
            "3.2 函数的基本性质",
            "3.3 幂函数",
            "3.4 函数的应用(一)"
        ],
        "4. 指数函数与对数函数": [
            "4.1 指数",
            "4.2 指数函数",
            "4.3 对数",
            "4.4 对数函数",
            "4.5 函数的应用(二)"
        ],
        "5. 三角函数": [
            "5.1 任意角和弧度制",
            "5.2 三角函数的概念",
            "5.3 诱导公式",
            "5.4 三角函数的图象与性质",
            "5.5 三角恒等变换",
            "5.6 函数y=Asin(wx+φ)",
            "5.7 三角函数的应用"
        ]
    },
    "必修二": {
        "6. 平面向量及其应用": [
            "6.1 平面向量的概念",
            "6.2 平面向量的运算",
            "6.3 平面向量基本定理及坐标表示",
            "6.4 平面向量的应用"
        ],
        "7. 复数": [
            "7.1 复数的概念",
            "7.2 复数的四则运算",
            "7.3 复数的三角表示"
        ],
        "8. 立体几何初步": [
            "8.1 基本立体图形",
            "8.2 立体图形的直观图",
            "8.3 简单几何体的表面积与体积",
            "8.4 空间点、直线、平面之间的位置关系",
            "8.5 空间直线、平面的平行",
            "8.6 空间直线、平面的垂直"
        ],
        "9. 统计": [
            "9.1 随机抽样",
            "9.2 用样本估计总体",
            "9.3 统计案例"
        ],
        "10. 概率": [
            "10.1 随机事件与概率",
            "10.2 事件的相互独立性",
            "10.3 频率与概率"
        ]
    },
    "选择性必修一": {
        "1. 空间向量与立体几何": [
            "1.1 空间向量及其运算",
            "1.2 空间向量基本定理",
            "1.3 空间向量及其运算的坐标表示",
            "1.4 空间向量的应用"
        ],
        "2. 直线和圆的方程": [
            "2.1 直线的倾斜角和斜率",
            "2.2 直线的方程",
            "2.3 直线的交点坐标与距离公式",
            "2.4 圆的方程",
            "2.5 直线与圆、圆与圆的位置关系"
        ],
        "3. 圆锥曲线的方程": [
            "3.1 椭圆",
            "3.2 双曲线",
            "3.3 抛物线"
        ]
    },
    "选择性必修二": {
        "4. 数列": [
            "4.1 数列的概念",
            "4.2 等差数列",
            "4.3 等比数列",
            "4.4 数学归纳法"
        ],
        "5. 一元函数的导数及其应用": [
            "5.1 导数的概念及其意义",
            "5.2 导数的运算",
            "5.3 导数在研究函数中的应用"
        ]
    },
    "选择性必修三": {
        "6. 计数原理": [
            "6.1 分类加法计数原理与分步乘法计数原理",
            "6.2 排列与组合",
            "6.3 二项式定理"
        ],
        "7. 随机变量及其分布": [
            "7.1 条件概率与全概率公式",
            "7.2 离散型随机变量及其分布列",
            "7.3 离散型随机变量的数字特征",
            "7.4 二项分布与超几何分布",
            "7.5 正态分布"
        ],
        "8. 成对数据的统计分析": [
            "8.1 成对数据的统计相关性",
            "8.2 一元线性回归模型及其应用",
            "8.3 列联表与独立性检验"
        ]
    }
}

METADATA_FILE = "data_backup/custom_metadata.json"
METADATA_CACHE = {}

def get_current_curriculum():
    return METADATA_CACHE.get("curriculum", RENJIAO_A_CURRICULUM)

def load_or_init_metadata():
    global METADATA_CACHE
    default_metadata = {
        "question_types": [
            {"value": "single_choice", "label": "单选题"},
            {"value": "multi_choice", "label": "多选题"},
            {"value": "fill_in_blank", "label": "填空题"},
            {"value": "detailed_answer", "label": "解答题"}
        ],
        "difficulties": [
            {"value": "easy_error", "label": "易错题", "color": "text-green-600 bg-green-50 border-green-200"},
            {"value": "challenge", "label": "挑战题", "color": "text-red-600 bg-red-50 border-red-200"},
            {"value": "qiangji", "label": "强基题", "color": "text-purple-600 bg-purple-50 border-purple-200"}
        ],
        "curriculum": RENJIAO_A_CURRICULUM
    }
    
    # Ensure backup directory exists
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Verify schema
                if isinstance(loaded, dict) and "question_types" in loaded and "difficulties" in loaded and "curriculum" in loaded:
                    METADATA_CACHE = loaded
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    return
        except Exception as e:
            print(f"[Metadata Warning] Error loading {METADATA_FILE}: {e}. Overwriting with default.")
            
    # Self-heal / initialize
    try:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_metadata, f, ensure_ascii=False, indent=2)
        print(f"[Metadata] Initialized default metadata at {METADATA_FILE}")
    except Exception as e:
        print(f"[Metadata Error] Could not write default metadata: {e}")
        
    METADATA_CACHE = default_metadata

# Load metadata on startup
load_or_init_metadata()

@app.get("/api/config/metadata")
def get_metadata_config():
    return METADATA_CACHE

@app.post("/api/config/metadata")
def save_metadata_config(payload: dict, db: Session = Depends(get_db)):
    global METADATA_CACHE
    # Validation
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求 Payload 格式错误")
        
    for field in ["question_types", "difficulties", "curriculum"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"元数据配置缺少核心字段: '{field}'")
            
    # Simple validate question_types and difficulties lists
    if not isinstance(payload["question_types"], list) or not isinstance(payload["difficulties"], list):
        raise HTTPException(status_code=400, detail="question_types 或 difficulties 必须是数组列表")
        
    if not isinstance(payload["curriculum"], dict):
        raise HTTPException(status_code=400, detail="curriculum 必须是字典对象")
        
    # Write to file
    try:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        METADATA_CACHE = payload
        print(f"[Metadata] Saved new custom metadata to {METADATA_FILE}")
        
        # Trigger export in background to update AI library with new mappings
        from sync_helper import export_database_to_files
        export_database_to_files(db)
        
        return {"status": "success", "message": "元数据配置保存成功！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {str(e)}")

# ----------------- DB Statistics API -----------------

@app.get("/api/stats")
def get_db_stats(db: Session = Depends(get_db)):
    try:
        total = db.query(Question).count()
        easy_error = db.query(Question).filter(Question.difficulty == "easy_error").count()
        challenge = db.query(Question).filter(Question.difficulty == "challenge").count()
        qiangji = db.query(Question).filter(Question.difficulty == "qiangji").count()
        
        # Cascaded Stage & Chapter Counts
        rows = db.query(
            Question.category_compulsory,
            Question.category_chapter
        ).all()
        
        comp_chap_stats = {}
        for comp, chap in rows:
            comp_val = comp or "未分类"
            chap_val = chap or "未分章节"
            if comp_val not in comp_chap_stats:
                comp_chap_stats[comp_val] = {}
            if chap_val not in comp_chap_stats[comp_val]:
                comp_chap_stats[comp_val][chap_val] = 0
            comp_chap_stats[comp_val][chap_val] += 1
            
        # Daily additions in local time (UTC+8)
        date_rows = db.query(Question.created_at).all()
        daily_adds = {}
        for (created_at,) in date_rows:
            if created_at:
                # Convert UTC to UTC+8 local time
                local_time = created_at + datetime.timedelta(hours=8)
                date_str = local_time.strftime("%Y-%m-%d")
                daily_adds[date_str] = daily_adds.get(date_str, 0) + 1
                
        return {
            "status": "success",
            "total_count": total,
            "easy_error_count": easy_error,
            "challenge_count": challenge,
            "qiangji_count": qiangji,
            "compulsory_chapter_counts": comp_chap_stats,
            "daily_adds": daily_adds
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"获取统计数据失败: {str(e)}"},
            status_code=500
        )

@app.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    # Initialize with predefined curriculum
    hierarchy = {}
    for comp, chapters in get_current_curriculum().items():
        hierarchy[comp] = {}
        for chap, sections in chapters.items():
            hierarchy[comp][chap] = list(sections)
            
    # Also fetch any custom entries from DB
    results = db.query(
        Question.category_compulsory,
        Question.category_chapter,
        Question.category_knowledge
    ).distinct().all()
    
    for comp, chap, know in results:
        if not comp:
            continue
        if comp not in hierarchy:
            hierarchy[comp] = {}
        if not chap:
            continue
        if chap not in hierarchy[comp]:
            hierarchy[comp][chap] = []
        if know and know not in hierarchy[comp][chap]:
            hierarchy[comp][chap].append(know)
            
    return hierarchy

# ----------------- AI Auto-Classification API -----------------

@app.post("/api/ai/classify")
async def ai_classify(content: str = Form(...)):
    classify_model = (
        os.getenv("PREFER_CLASSIFY_MODEL") 
        or os.getenv("DEEPSEEK_CLASSIFY_MODEL") 
        or os.getenv("PREFER_PARSE_MODEL") 
        or "deepseek-v4-flash"
    )
    
    api_key = None
    api_base = None
    model_name = classify_model
    
    if "/" in classify_model:
        parts = classify_model.split("/", 1)
        prefix = parts[0].upper()
        model_name = parts[1]
        
        if prefix == "SILICONFLOW":
            api_key = os.getenv("SILICONFLOW_API_KEY")
            api_base = "https://api.siliconflow.cn/v1"
        elif prefix == "BAILIAN":
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            if model_name == "qwen3.7-max":
                model_name = "qwen-max"
        elif prefix == "DEEPSEEK":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        elif prefix == "ZHONGZHAN_GPT":
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY")
            api_base = os.getenv("ZHONGZHAN_GPT_BASE_URL", "https://api.openai.com/v1")
        elif prefix == "ZHONGZHAN_CLAUDE":
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            api_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
    else:
        model_lower = classify_model.lower()
        if "qwen" in model_lower:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            if classify_model == "qwen3.7-max":
                model_name = "qwen-max"
        else:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")

    if not api_key:
        provider_name = "DeepSeek"
        if "/" in classify_model:
            prefix = classify_model.split("/", 1)[0].upper()
            if prefix == "SILICONFLOW": provider_name = "硅基流动 (SILICONFLOW_API_KEY)"
            elif prefix == "BAILIAN": provider_name = "阿里百炼 (ALI_BAILIAN_API_KEY)"
            elif prefix == "DEEPSEEK": provider_name = "DeepSeek (DEEPSEEK_API_KEY)"
            elif prefix == "ZHONGZHAN_GPT": provider_name = "中转站 A (ZHONGZHAN_GPT_API_KEY)"
            elif prefix == "ZHONGZHAN_CLAUDE": provider_name = "中转站 B (ZHONGZHAN_CLAUDE_API_KEY)"
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法自动智能分类！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        url = f"{api_base.rstrip('/')}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Build curriculum text for instructions
        curriculum_text = ""
        for book, chapters in get_current_curriculum().items():
            curriculum_text += f"- {book}: {list(chapters.keys())}\n"
            
        system_instructions = (
            "你是一个专门为教材分类的 AI 专家。请分析以下输入的题目，将其归入特定的教材体系中。\n"
            "【可选教材范围及各章名称】:\n"
            f"{curriculum_text}\n"
            "【分类规则】:\n"
            "1. 仔细阅读并推导题目考点。\n"
            "2. 必须在上面的可选教材范围中为本题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
            "3. 你的输出必须是一个合法的 JSON 字符串，包含且仅包含以下两个 key，不要有任何多余的 Markdown 标记、代码块或解释文字：\n"
            "{\n"
            '  "compulsory": "学段名称",\n'
            '  "chapter": "具体章节名称"\n'
            "}\n"
            "不要包含 ```json ``` 标记，只输出最干净的 JSON。"
        )
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": f"题目内容:\n{content}"}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": 512
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL, excluding legacy models that don't support it
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        if is_deepseek:
            data["thinking"] = {
                "type": "disabled"
            }
        
        
        response = robust_request_post(url, headers=headers, json=data, timeout=30)
        if response.status_code != 200:
            raise Exception(f"{provider_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}")
            
        res_json = response.json()
        ai_message = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        # Strip potential markdown formatting if returned
        if ai_message.startswith("```"):
            lines = ai_message.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_message = "\n".join(lines).strip()
            
        result = json.loads(ai_message)
        compulsory = result.get("compulsory", "")
        chapter = result.get("chapter", "")
        qtype = result.get("question_type", "")
        
        # Map common Chinese/English variations of question type
        qtype_mapping = {
            "single_choice": "single_choice",
            "multi_choice": "multi_choice",
            "fill_in_blank": "fill_in_blank",
            "detailed_answer": "detailed_answer",
            "单选题": "single_choice",
            "多选题": "multi_choice",
            "填空题": "fill_in_blank",
            "解答题": "detailed_answer"
        }
        resolved_qtype = qtype_mapping.get(qtype, "detailed_answer")
        
        # Verification: make sure returned values exist in get_current_curriculum()
        curr = get_current_curriculum()
        if compulsory in curr and chapter in curr[compulsory]:
            return {
                "status": "success",
                "compulsory": compulsory,
                "chapter": chapter,
                "question_type": resolved_qtype
            }
        else:
            # Fallback dynamically to the first available category book/chapter
            first_comp = list(curr.keys())[0] if curr else "必修一"
            first_chap = list(curr[first_comp].keys())[0] if curr and first_comp in curr and curr[first_comp] else "1. 集合与常用逻辑用语"
            return {
                "status": "success",
                "compulsory": first_comp,
                "chapter": first_chap,
                "question_type": resolved_qtype,
                "is_fallback": True,
                "raw_recommendation": f"{compulsory} -> {chapter}"
            }
            
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 智能分类失败: {str(e)}"},
            status_code=500
        )

# ----------------- LaTeX Batch Paper Import APIs -----------------

@app.post("/api/upload/batch")
async def upload_batch_images(files: List[UploadFile] = File(...)):
    try:
        mapping = {}
        for file in files:
            ext = os.path.splitext(file.filename)[1]
            if not ext:
                ext = ".png"
            filename = f"{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            
            with open(filepath, "wb") as f:
                f.write(await file.read())
                
            relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
            mapping[file.filename] = relative_path
            
        return {
            "status": "success",
            "mapping": mapping
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"批量图片上传失败: {str(e)}"},
            status_code=500
        )


def parse_paper_text_internal(
    latex_content: str,
    generate_answers_bool: bool
) -> list:
    """内部通用函数：调用选定的 LLM 接口，将 LaTeX 试卷内容解析拆分为结构化 JSON 卡片"""
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    api_key = None
    api_base = None
    model_name = parse_model
    provider_name = "DeepSeek"
    
    if "/" in parse_model:
        parts = parse_model.split("/", 1)
        prefix = parts[0].upper()
        model_name = parts[1]
        
        if prefix == "SILICONFLOW":
            api_key = os.getenv("SILICONFLOW_API_KEY")
            api_base = "https://api.siliconflow.cn/v1"
            provider_name = "硅基流动"
        elif prefix == "BAILIAN":
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼"
            if model_name == "qwen3.7-max":
                model_name = "qwen-max"
        elif prefix == "DEEPSEEK":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek"
        elif prefix == "ZHONGZHAN_GPT":
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY")
            api_base = os.getenv("ZHONGZHAN_GPT_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 A"
        elif prefix == "ZHONGZHAN_CLAUDE":
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            api_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 B"
    else:
        model_lower = parse_model.lower()
        if "qwen" in model_lower:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼"
            model_name = "qwen-max" if parse_model == "qwen3.7-max" else parse_model
        else:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek"
            model_name = parse_model

    if not api_key:
        provider_friendly = provider_name
        if "/" in parse_model:
            prefix = parse_model.split("/", 1)[0].upper()
            if prefix == "SILICONFLOW": provider_friendly = "硅基流动 (SILICONFLOW_API_KEY)"
            elif prefix == "BAILIAN": provider_friendly = "阿里百炼 (ALI_BAILIAN_API_KEY)"
            elif prefix == "DEEPSEEK": provider_friendly = "DeepSeek (DEEPSEEK_API_KEY)"
            elif prefix == "ZHONGZHAN_GPT": provider_friendly = "中转站 A (ZHONGZHAN_GPT_API_KEY)"
            elif prefix == "ZHONGZHAN_CLAUDE": provider_friendly = "中转站 B (ZHONGZHAN_CLAUDE_API_KEY)"
        raise ValueError(f"未配置对应的 API Key ({provider_friendly})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")
        
    url = f"{api_base.rstrip('/')}/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Build curriculum text for instructions
    curriculum_text = ""
    for book, chapters in get_current_curriculum().items():
        curriculum_text += f"- {book}: {list(chapters.keys())}\n"
        
    if generate_answers_bool:
        answer_generation_rule = (
            "   - 如果试卷中只有题干没有答案，请根据题干自动生成详尽的解答步骤与解析，填入 `answer_markdown` 字段。特别注意：自动生成的解答过程、解题思路和技巧绝对不能超出本阶段水平，必须完全采用符合本知识体系和认知范围的常规或技巧性方法。\n"
        )
    else:
        answer_generation_rule = (
            "   - **【极其重要：绝对不生成答案解析】**：当前用户【未开启】自动生成答案解析功能。你必须将所有题目的 `answer_markdown` 字段设为绝对的空字符串 `\"\"`！**绝对不准**主动为任何题目生成、推导、猜测、计算或瞎编任何解答和解析过程！只有且仅当输入的原始试卷 LaTeX 源码中，原本就明确且显式写着该题的原版参考答案或解析文本（你会看到明显的“【答案】”、“解析：”、“解：”、“\\quad”等字样且包含具体的解题步骤），你才可以对其进行提取。并且如果该答案确实是从原试卷源码中提取的原版参考答案或原版解析，你必须在 `answer_markdown` 的最开头原样打上 `[EXTRACTED_ORIGINAL]` 标记。如果没有这个标记，或者该标记之后没有任何具体内容，我们将视其为大模型自作主张瞎编并清空。所以，若原试卷不含答案，你必须将该字段保持留空为 `\"\"`！\n"
        )

    system_instructions = (
        "你原是一位极其严谨的教研专家与 LaTeX 排版大师。请阅读并解析用户输入的【整张试卷 LaTeX 源码】，将其智能拆解为独立的题目列表，并分析每一题属性。\n"
        "【可选教材范围及各章名称】:\n"
        f"{curriculum_text}\n"
        "【分类规则】:\n"
        "1. 仔细阅读并拆解试卷中的每一道题目。请保留题目中的 LaTeX 公式和格式。\n"
        "2. 为每道题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
        "3. 如果题目包含图片引用标签（形如 \\includegraphics{filename.png} 或类似标记，或文字描述引用的图片名），请将其提取并放入 `referenced_images` 字段中。\n"
        "   - **【极其重要：关于 /static/uploads/tmp/pdf_crop_ 路径的保护规则】**：如果输入的试卷 LaTeX 源码中存在以 `/static/uploads/tmp/pdf_crop_` 开头的临时裁剪插图 URL（例如 `![](/static/uploads/tmp/pdf_crop_xxx.png)`），你**必须 100% 完整且原样保留该完整 URL**，绝对不可将其修改、缩写或重命名为任何其他名称（如 `图1.png`、`图2.png`、`image1.png` 等）！同时必须将此原始完整路径 `/static/uploads/tmp/pdf_crop_xxx.png` 放入 `referenced_images` 数组中。\n"
        "4. 判断题目类型：single_choice（单选题，若选项是 A/B/C/D 形式，建议保留选项在 `content` 尾部，并归类为 single_choice）、multi_choice（多选题）、fill_in_blank（填空题）、detailed_answer（解答题）。\n"
        "5. 判断题目难度等级，由于是私人题库，请将其归类为：easy_error（易错题）、challenge（挑战题）或 qiangji（强基题）。\n"
        "6. **智能识别答案与解析**：试卷中可能只包含题干，也可能同时包含答案 and 解析。请仔细辨别：\n"
        "   - 如果试卷中包含答案（如参考答案、解答过程等），请将其提取到 `answer_markdown` 字段。\n"
        f"{answer_generation_rule}"
        "7. **【题干智能去答案规范】**：很多时候，输入的试卷 LaTeX 源码中，某些选择题或填空题的题干部分直接保留了答案（例如：选择题括号中直接写了答案字母如“（ B ）”、“(C)”；填空题下划线命令内部直接填了答案数字或符号如“\\underline{\\quad 7 \\quad}”、“\\underline{x^2}”）：\n"
        "   - 必须主动识别并清除这些题干中保留的答案，还原为纯净的空占位符！\n"
        "   - 对于选择题，将括号内代表答案的字母剥离清除，还原为干净的括号（如“（  ）”或“（ ）”）。\n"
        "   - 对于填空题，将下划线中代表答案的文本挖空，替换为纯粹的 LaTeX 空白占位符（如“\\underline{\\quad\\quad}”或“\\underline{\\quad \\quad}”）。\n"
        "   - 将提取出来的答案字符（如“B”或“7”）作为最终参考答案，醒目融入在该题的 `answer_markdown` 字段最开始处，然后再呈现详细解析步骤。\n"
        "8. **【排版与字符格式规范】（极其重要）**：\n"
        "   - **推荐使用标准的 LaTeX 列表与排版环境**：为了方便用户直接复制高价值的 LaTeX 源码，推荐在需要列表、段落或编号排版时输出标准的 LaTeX 语法环境，如 `\\begin{itemize}`, `\\end{itemize}`, `\\item`, `\\begin{enumerate}`, `\\end{enumerate}`, `\\begin{center}`, `\\end{center}`。LaTeX 标记（如 `$` 或 `$$`）应该包围所有纯数学公式。\n"
        "   - **【加粗文本排版规范】**：在输出需要加粗的结构化文本时，**绝对禁止**使用 Markdown 的双星号 `**加粗文本**` 语法，必须且只能使用 LaTeX 标准的 `\\\\textbf{加粗文本}` 语法。\n"
        "   - **禁止输出字面量 `\\n` 字符**：在 `content` 或 `answer_markdown` 的字符串内部换行时，直接在 JSON 字段里输出真实的换行符（回车换行），绝对不要输出转义后的字面量 `\\n`（即双斜杠字符 `\\\\n` 或斜杠加n），防止页面上直接显示出带有物理字符 `\\n` 的尴尬情况。\n"
        "9. **【出处智能提取规范】**：仔细辨认题干开头（如“1. (2024·上海·高考真题) 已知...”中的“(2024·上海·高考真题)”)或结尾是否包含年份、考试来源等括号标注的出处信息：\n"
        "   - 若有，必须将其完整提取至 `source` 字段中（去除外层括号），并在 `content` 字段中彻底剥离删除该出处标注以及前面的题号前缀（如“10.”、“1.”），只保留纯净的题目内容。\n"
        "   - 若无特定出处，则 `source` 字段设为 null 或不填。\n"
        "10. **你的输出必须是一个合法的 JSON 对象，其根键为 `\"questions\"`，对应的值为一个 JSON 数组（包含以下结构化对象）。不要有任何多余的 Markdown 标记、代码块或解释文字**：\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        "      \"content\": \"题干内容，包含 LaTeX 排版公式，且保留图片排版占位标记 (如果有 /static/uploads/tmp/pdf_crop_ 临时路径，必须 100% 完整原样保留，绝对不得修改路径或重命名！例如：![插图](/static/uploads/tmp/pdf_crop_xxx.png))\",\n"
        "      \"answer_markdown\": \"该题的答案与详细解析过程，使用标准 LaTeX 与 Markdown 排版\",\n"
        "      \"question_type\": \"single_choice / multi_choice / fill_in_blank / detailed_answer\",\n"
        "      \"category_compulsory\": \"人教A学段名称\",\n"
        "      \"category_chapter\": \"人教A章节名称\",\n"
        "      \"difficulty\": \"easy_error / challenge / qiangji\",\n"
        "      \"source\": \"提取出的具体出处（如 2019·全国·高考真题），没有则填 null\",\n"
        "      \"referenced_images\": [\"/static/uploads/tmp/pdf_crop_xxx.png\", \"引用的原始插图文件名1.png\"]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "注意：只输出最干净的 JSON，千万不要包含 ```json ``` 等 Markdown 代码块标记！如果试卷中没有插图，referenced_images 数组留空。"
    )
    
    max_output_tokens = 65536

    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": latex_content}
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0.2,
        "max_tokens": max_output_tokens
    }
    
    is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
    if is_deepseek:
        data["thinking"] = {
            "type": "disabled"
        }
    
    response = robust_request_post(url, headers=headers, json=data, timeout=180)
    if response.status_code != 200:
        raise Exception(f"{provider_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}")
        
    res_json = response.json()
    raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
    
    parsed_data = json.loads(raw_ai_text)
    
    if isinstance(parsed_data, dict):
        if "questions" in parsed_data and isinstance(parsed_data["questions"], list):
            parsed_questions = parsed_data["questions"]
        elif "data" in parsed_data and isinstance(parsed_data["data"], list):
            parsed_questions = parsed_data["data"]
        else:
            parsed_questions = None
            for key, val in parsed_data.items():
                if isinstance(val, list):
                    parsed_questions = val
                    break
                if parsed_questions is None:
                    parsed_questions = [parsed_data]
    elif isinstance(parsed_data, list):
        parsed_questions = parsed_data
    else:
        raise Exception("AI 返回的 JSON 格式不正确，期望是一个数组或包含 questions 列表的对象。")
        
    # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
    for q in parsed_questions:
        ans = q.get("answer_markdown", "")
        if not ans:
            q["answer_markdown"] = ""
            continue
        if not generate_answers_bool:
            if "[EXTRACTED_ORIGINAL]" in ans:
                q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
            else:
                q["answer_markdown"] = ""
        else:
            q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
        
    return parsed_questions


@app.post("/api/ai/parse-paper")
async def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(...),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false")
):
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    
    api_key = None
    api_base = None
    model_name = parse_model
    provider_name = "DeepSeek"
    
    if "/" in parse_model:
        parts = parse_model.split("/", 1)
        prefix = parts[0].upper()
        model_name = parts[1]
        
        if prefix == "SILICONFLOW":
            api_key = os.getenv("SILICONFLOW_API_KEY")
            api_base = "https://api.siliconflow.cn/v1"
            provider_name = "硅基流动"
        elif prefix == "BAILIAN":
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼"
            if model_name == "qwen3.7-max":
                model_name = "qwen-max"
        elif prefix == "DEEPSEEK":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek"
        elif prefix == "ZHONGZHAN_GPT":
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY")
            api_base = os.getenv("ZHONGZHAN_GPT_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 A"
        elif prefix == "ZHONGZHAN_CLAUDE":
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            api_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_name = "中转站 B"
    else:
        model_lower = parse_model.lower()
        if "qwen" in model_lower:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            provider_name = "阿里百炼"
            model_name = "qwen-max" if parse_model == "qwen3.7-max" else parse_model
        else:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            provider_name = "DeepSeek"
            model_name = parse_model

    if not api_key:
        provider_friendly = provider_name
        if "/" in parse_model:
            prefix = parse_model.split("/", 1)[0].upper()
            if prefix == "SILICONFLOW": provider_friendly = "硅基流动 (SILICONFLOW_API_KEY)"
            elif prefix == "BAILIAN": provider_friendly = "阿里百炼 (ALI_BAILIAN_API_KEY)"
            elif prefix == "DEEPSEEK": provider_friendly = "DeepSeek (DEEPSEEK_API_KEY)"
            elif prefix == "ZHONGZHAN_GPT": provider_friendly = "中转站 A (ZHONGZHAN_GPT_API_KEY)"
            elif prefix == "ZHONGZHAN_CLAUDE": provider_friendly = "中转站 B (ZHONGZHAN_CLAUDE_API_KEY)"
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_friendly})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        
    try:
        image_mapping = json.loads(image_mapping_json)
    except Exception as e:
        image_mapping = {}

    try:
        url = f"{api_base.rstrip('/')}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Build curriculum text for instructions
        curriculum_text = ""
        for book, chapters in get_current_curriculum().items():
            curriculum_text += f"- {book}: {list(chapters.keys())}\n"
            
        if generate_answers_bool:
            answer_generation_rule = (
                "   - 如果试卷中只有题干没有答案，请根据题干自动生成详尽的解答步骤与解析，填入 `answer_markdown` 字段。特别注意：自动生成的解答过程、解题思路和技巧绝对不能超出本阶段水平，必须完全采用符合本知识体系和认知范围的常规或技巧性方法。\n"
            )
        else:
            answer_generation_rule = (
                "   - **【极其重要：绝对不生成答案解析】**：当前用户【未开启】自动生成答案解析功能。你必须将所有题目的 `answer_markdown` 字段设为绝对的空字符串 `\"\"`！**绝对不准**主动为任何题目生成、推导、猜测、计算或瞎编任何解答和解析过程！只有且仅当输入的原始试卷 LaTeX 源码中，本来就明确且显式写着该题的原版参考答案或解析文本（你会看到明显的“【答案】”、“解析：”、“解：”、“\\quad”等字样且包含具体的解题步骤），你才可以对其进行提取。并且如果该答案确实是从原试卷源码中提取的原版参考答案或原版解析，你必须在 `answer_markdown` 的最开头原样打上 `[EXTRACTED_ORIGINAL]` 标记。如果没有这个标记，或者该标记之后没有任何具体内容，我们将视其为大模型自作主张瞎编并清空。所以，若原试卷不含答案，你必须将该字段保持留空为 `\"\"`！\n"
            )

        system_instructions = (
            "你是一位极其严谨的教研专家与 LaTeX 排版大师。请阅读并解析用户输入的【整张试卷 LaTeX 源码】，将其智能拆解为独立的题目列表，并分析每一题属性。\n"
            "【可选教材范围及各章名称】:\n"
            f"{curriculum_text}\n"
            "【分类规则】:\n"
            "1. 仔细阅读并拆解试卷中的每一道题目。请保留题目中的 LaTeX 公式和格式。\n"
            "2. 为每道题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
            "3. 如果题目包含图片引用标签（形如 \\includegraphics{filename.png} 或类似标记，或文字描述引用的图片名），请将其提取并放入 `referenced_images` 字段中。\n"
            "4. 判断题目类型：single_choice（单选题，若选项是 A/B/C/D 形式，建议保留选项在 `content` 尾部，并归类为 single_choice）、multi_choice（多选题）、fill_in_blank（填空题）、detailed_answer（解答题）。\n"
            "5. 判断题目难度等级，由于是私人题库，请将其归类为：easy_error（易错题）、challenge（挑战题）或 qiangji（强基题）。\n"
            "6. **智能识别答案与解析**：试卷中可能只包含题干，也可能同时包含答案和解析。请仔细辨别：\n"
            "   - 如果试卷中包含答案（如参考答案、解答过程等），请将其提取到 `answer_markdown` 字段。\n"
            f"{answer_generation_rule}"
            "7. **【题干智能去答案规范】**：很多时候，输入的试卷 LaTeX 源码中，某些选择题或填空题的题干部分直接保留了答案（例如：选择题括号中直接写了答案字母如“（ B ）”、“(C)”；填空题下划线命令内部直接填了答案数字或符号如“\\underline{\\quad 7 \\quad}”、“\\underline{x^2}”）：\n"
            "   - 必须主动识别并清除这些题干中保留的答案，还原为纯净的空占位符！\n"
            "   - 对于选择题，将括号内代表答案的字母剥离清除，还原为干净的括号（如“（  ）”或“（ ）”）。\n"
            "   - 对于填空题，将下划线中代表答案的文本挖空，替换为纯粹的 LaTeX 空白占位符（如“\\underline{\\quad\\quad}”或“\\underline{\\quad \\quad}”）。\n"
            "   - 将提取出来的答案字符（如“B”或“7”）作为最终参考答案，醒目融入在该题的 `answer_markdown` 字段最开始处，然后再呈现详细解析步骤。\n"
            "8. **【排版与字符格式规范】（极其重要）**：\n"
            "   - **推荐使用标准的 LaTeX 列表与排版环境**：为了方便用户直接复制高价值的 LaTeX 源码，推荐在需要列表、段落或编号排版时输出标准的 LaTeX 语法环境，如 `\\begin{itemize}`, `\\end{itemize}`, `\\item`, `\\begin{enumerate}`, `\\end{enumerate}`, `\\begin{center}`, `\\end{center}`。LaTeX 标记（如 `$` 或 `$$`）应该包围所有纯数学公式。\n"
            "   - **【加粗文本排版规范】**：在输出需要加粗的结构化文本时，**绝对禁止**使用 Markdown 的双星号 `**加粗文本**` 语法，必须且只能使用 LaTeX 标准的 `\\\\textbf{加粗文本}` 语法。\n"
            "   - **禁止输出字面量 `\\n` 字符**：在 `content` 或 `answer_markdown` 的字符串内部换行时，直接在 JSON 字段里输出真实的换行符（回车换行），绝对不要输出转义后的字面量 `\\n`（即双斜杠字符 `\\\\n` 或斜杠加n），防止页面上直接显示出带有物理字符 `\\n` 的尴尬情况。\n"
            "9. **【出处智能提取规范】**：仔细辨认题干开头（如“1. (2024·上海·高考真题) 已知...”中的“(2024·上海·高考真题)”)或结尾是否包含年份、考试来源等括号标注的出处信息：\n"
            "   - 若有，必须将其完整提取至 `source` 字段中（去除外层括号），并在 `content` 字段中彻底剥离删除该出处标注以及前面的题号前缀（如“10.”、“1.”），只保留纯净的题目内容。\n"
            "   - 若无特定出处，则 `source` 字段设为 null 或不填。\n"
            "10. **你的输出必须是一个合法的 JSON 对象，其根键为 `\"questions\"`，对应的值为一个 JSON 数组（包含以下结构化对象）。不要有任何多余的 Markdown 标记、代码块或解释文字**：\n"
            "{\n"
            "  \"questions\": [\n"
            "    {\n"
            '      "content": "题干内容，包含 LaTeX 排版公式，且保留图片排版占位标记 (例如 ![插图](filename.png))",\n'
            '      "answer_markdown": "该题的答案与详细解析过程，使用标准 LaTeX 与 Markdown 排版",\n'
            '      "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
            '      "category_compulsory": "人教A学段名称",\n'
            '      "category_chapter": "人教A章节名称",\n'
            '      "difficulty": "easy_error / challenge / qiangji",\n'
            '      "source": "提取出的具体出处（如 2019·全国·高考真题），没有则填 null",\n'
            '      "referenced_images": ["引用的原始插图文件名1.png", "fig2.jpg"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "注意：只输出最干净的 JSON，千万不要包含 ```json ``` 等 Markdown 代码块标记！如果试卷中没有插图，referenced_images 数组留空。"
        )
        
        max_output_tokens = 65536

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": latex_content}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": max_output_tokens
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL, excluding legacy models that don't support it
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        if is_deepseek:
            data["thinking"] = {
                "type": "disabled"
            }
        
        
        response = robust_request_post(url, headers=headers, json=data, timeout=180)
        if response.status_code != 200:
            raise Exception(f"{provider_name} 接口错误: HTTP {response.status_code}, 内容: {response.text}")
            
        res_json = response.json()
        raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
        
        # Parse JSON robustly
        parsed_data = json.loads(raw_ai_text)
        
        if isinstance(parsed_data, dict):
            if "questions" in parsed_data and isinstance(parsed_data["questions"], list):
                parsed_questions = parsed_data["questions"]
            elif "data" in parsed_data and isinstance(parsed_data["data"], list):
                parsed_questions = parsed_data["data"]
            else:
                parsed_questions = None
                for key, val in parsed_data.items():
                    if isinstance(val, list):
                        parsed_questions = val
                        break
                if parsed_questions is None:
                    parsed_questions = [parsed_data]
        elif isinstance(parsed_data, list):
            parsed_questions = parsed_data
        else:
            raise Exception("AI 返回的 JSON 格式不正确，期望是一个数组或包含 questions 列表的对象。")
        
        # Translate referenced_images to server paths
        import re
        for q in parsed_questions:
            # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
            ans = q.get("answer_markdown", "")
            if not ans:
                q["answer_markdown"] = ""
            else:
                if not generate_answers_bool:
                    if "[EXTRACTED_ORIGINAL]" in ans:
                        q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
                    else:
                        q["answer_markdown"] = ""
                else:
                    q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()

            # 智能提取出处双重保险：AI 提取优先，若 AI 未提取则尝试正则从 content 中提取
            extracted_source = q.get("source")
            content_str = q.get("content", "")
            
            # 正则匹配题干开头形如 "10. (2019·全国·高考真题)已知..." 的出处
            # group(1): 题号前缀, group(2): 左括号, group(3): 出处内容, group(4): 右括号
            prefix_match = re.match(r'^(\s*(?:\d+[\.、\s]*)?)([\(（])([^\(（\)）\s]{4,})([\)）])', content_str)
            if prefix_match:
                if not extracted_source:
                    extracted_source = prefix_match.group(3).strip()
                # 剔除题干中的出处括号及前面的题号前缀，保持题干纯净
                to_remove = prefix_match.group(1) + prefix_match.group(2) + prefix_match.group(3) + prefix_match.group(4)
                content_str = content_str.replace(to_remove, "", 1).strip()
                # 移除可能残存的开头符号（如句点或顿号）
                content_str = re.sub(r'^[\s、\.．]+', '', content_str)
                q["content"] = content_str
                
            q["source"] = (extracted_source or paper_title).strip()
            
            # Clean up double-escaped literal \n in fields
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    text = q[field]
                    # Replace literal "\n" safely using negative lookahead (so it doesn't touch commands like \normalsize or \nabla)
                    text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                    q[field] = text
            
            # Map images
            mapped_images = []
            ref_imgs = q.get("referenced_images", [])
            for ref_name in ref_imgs:
                # Direct match or fuzzy match
                found_path = None
                for orig_name, serv_path in image_mapping.items():
                    if ref_name == orig_name or ref_name in orig_name or orig_name in ref_name:
                        found_path = serv_path
                        break
                if found_path:
                    mapped_images.append(found_path)
                    # Replace reference name in content to standard relative path markdown
                    if ref_name in q["content"]:
                        q["content"] = q["content"].replace(ref_name, found_path)
                    
            q["image_paths"] = mapped_images
            
            # If AI didn't map it in content text but referenced it, append it to content
            for img_path in mapped_images:
                if img_path not in q["content"]:
                    q["content"] += f"\n\n![插图]({img_path})\n\n"
                    
        return {
            "status": "success",
            "questions": parsed_questions
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"试卷解析失败: {str(e)}"},
            status_code=500
        )

@app.get("/api/sources")
def get_sources(db: Session = Depends(get_db)):
    results = db.query(Question.source).distinct().all()
    sources = []
    for r in results:
        val = r[0]
        if val and val.strip():
            sources.append(val.strip())
            
    # Sort alphabetically (case-insensitive)
    sources.sort(key=str.lower)
    return sources

@app.post("/api/shutdown")
def shutdown_server():
    import signal
    def stop_server():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    import threading
    threading.Thread(target=stop_server).start()
    
    return {"status": "success", "message": "题库系统正在关闭中..."}


# ----------------- Storage Promotion Engine -----------------

def promote_question_temp_assets(content: str, answer_markdown: str, image_paths_list: list) -> tuple:
    """物理移动临时图片到永久目录，并更新题干、解析和图片路径列表中的引用"""
    import shutil
    import re
    
    updated_paths = []
    mapping = {} # Map from tmp relative path to promoted relative path
    
    for path in image_paths_list:
        if not path:
            continue
        normalized_path = os.path.normpath(path.lstrip("/"))
        expected_prefix = os.path.normpath(os.path.join(UPLOAD_DIR_REL, "tmp")).lower()
        
        if normalized_path.lower().startswith(expected_prefix):
            filename = os.path.basename(normalized_path)
            src_path = os.path.join(os.getcwd(), normalized_path)
            dest_rel_path = f"/{UPLOAD_DIR_REL}/{filename}"
            dest_path = os.path.join(os.getcwd(), UPLOAD_DIR_REL, filename)
            
            if os.path.exists(src_path):
                try:
                    shutil.move(src_path, dest_path)
                    updated_paths.append(dest_rel_path)
                    mapping[path] = dest_rel_path
                    print(f"[Storage Promotion] 成功升级插图: {path} -> {dest_rel_path}")
                except Exception as e_move:
                    print(f"[Storage Promotion Fail] 无法移动 {path}: {str(e_move)}")
                    updated_paths.append(path)
            else:
                updated_paths.append(path)
        else:
            updated_paths.append(path)
            
    new_content = content
    new_answer = answer_markdown
    for old_p, new_p in mapping.items():
        if old_p in new_content:
            new_content = new_content.replace(old_p, new_p)
        if old_p in new_answer:
            new_answer = new_answer.replace(old_p, new_p)
            
    # Check for any other tmp paths inside content/answer_markdown
    for text_val in [new_content, new_answer]:
        for match in re.finditer(r'/static/uploads(?:_test|/test_uploads|/uploads)?/tmp/pdf_crop_[a-zA-Z0-9_]+\.png', text_val):
            matched_path = match.group(0)
            if matched_path not in mapping:
                normalized_path = os.path.normpath(matched_path.lstrip("/"))
                filename = os.path.basename(normalized_path)
                src_path = os.path.join(os.getcwd(), normalized_path)
                dest_rel_path = f"/{UPLOAD_DIR_REL}/{filename}"
                dest_path = os.path.join(os.getcwd(), UPLOAD_DIR_REL, filename)
                
                if os.path.exists(src_path):
                    try:
                        shutil.move(src_path, dest_path)
                        mapping[matched_path] = dest_rel_path
                        if matched_path in new_content:
                            new_content = new_content.replace(matched_path, dest_rel_path)
                        if matched_path in new_answer:
                            new_answer = new_answer.replace(matched_path, dest_rel_path)
                        if dest_rel_path not in updated_paths:
                            updated_paths.append(dest_rel_path)
                    except Exception:
                        pass
                        
    return new_content, new_answer, updated_paths


@app.post("/api/ai/manual-crop-pdf")
def manual_crop_pdf(payload: dict):
    """用户在前端手动拖拽框选后，后端根据坐标裁剪 PDF 页面的特定区域"""
    try:
        task_id = payload.get("task_id")
        page_index = int(payload.get("page_index", 0))
        ymin = float(payload.get("ymin", 0))
        xmin = float(payload.get("xmin", 0))
        ymax = float(payload.get("ymax", 0))
        xmax = float(payload.get("xmax", 0))
        
        img_filename = f"pdf_page_{task_id}_{page_index}.png"
        img_filepath = os.path.join(TMP_UPLOAD_DIR, img_filename)
        
        if not os.path.exists(img_filepath):
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 页面图片！"},
                status_code=404
            )
            
        from PIL import Image
        img = Image.open(img_filepath)
        w, h = img.size
        
        # Convert percentage to pixels
        left = (xmin / 100.0) * w
        top = (ymin / 100.0) * h
        right = (xmax / 100.0) * w
        bottom = (ymax / 100.0) * h
        
        left = max(0, min(left, w - 1))
        top = max(0, min(top, h - 1))
        right = max(left + 1, min(right, w))
        bottom = max(top + 1, min(bottom, h))
        
        cropped = img.crop((left, top, right, bottom))
        
        crop_filename = f"pdf_crop_{task_id}_{uuid.uuid4().hex[:12]}.png"
        crop_filepath = os.path.join(TMP_UPLOAD_DIR, crop_filename)
        cropped.save(crop_filepath, format="PNG")
        
        img_url = f"/{UPLOAD_DIR_REL}/tmp/{crop_filename}"
        return {"status": "success", "image_path": img_url}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=500
        )


def extract_title_from_latex(latex: str) -> str:
    """从 LaTeX 源码中尝试自动提取试卷标题"""
    if not latex:
        return ""
    import re
    
    def clean_latex(txt: str) -> str:
        # 移除字体大小命令等
        txt = re.sub(r'\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt|heiti|kaishu|fangsong|songti)', '', txt)
        # 解包 textbf 等
        txt = re.sub(r'\\text(bf|it|sf|tt)?\s*\{([^}]+)\}', r'\2', txt)
        txt = txt.replace('{', '').replace('}', '').replace('\\\\', '\n').strip()
        lines = [line.strip() for line in txt.split('\n') if line.strip()]
        if lines:
            return lines[0][:60]
        return ""

    # 1. 尝试匹配 \title{...}
    match = re.search(r'\\title\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    # 2. 尝试匹配 \chead{...}
    match = re.search(r'\\chead\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned and "页" not in cleaned and "绝密" not in cleaned:
            return cleaned
            
    # 3. 尝试匹配 \begin{center} ... \end{center} 头部区域
    top_part = latex[:1500]
    match = re.search(r'\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}', top_part)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    return ""


# ----------------- PDF Import & AI Parsing Backend Logic -----------------

def ocr_pdf_page_image(image_path: str) -> str:
    """自动选择已配置的 VLM 识别引擎进行单页识别，支持故障转移（Fallback）与兜底识别"""
    sf_key = os.getenv("SILICONFLOW_API_KEY")
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3.5-4B")
    
    ali_key = os.getenv("ALI_BAILIAN_API_KEY")
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3-vl-flash")
    
    zz_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
    zz_base_url = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")
    zz_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
    
    errors = []
    
    # 根据用户首选的识图引擎调整尝试的顺序
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    
    engines_to_try = []
    if prefer_engine == "siliconflow":
        if sf_key and sf_key.strip():
            engines_to_try.append(("SiliconFlow", "siliconflow", sf_key, sf_model))
        if ali_key and ali_key.strip():
            engines_to_try.append(("阿里云百炼", "ali_bailian", ali_key, ali_model))
        if zz_key and zz_key.strip():
            engines_to_try.append(("中转站", "zhongzhan", zz_key, zz_base_url, zz_model))
    elif prefer_engine == "ali_bailian":
        if ali_key and ali_key.strip():
            engines_to_try.append(("阿里云百炼", "ali_bailian", ali_key, ali_model))
        if sf_key and sf_key.strip():
            engines_to_try.append(("SiliconFlow", "siliconflow", sf_key, sf_model))
        if zz_key and zz_key.strip():
            engines_to_try.append(("中转站", "zhongzhan", zz_key, zz_base_url, zz_model))
    else:  # zhongzhan 或其他
        if zz_key and zz_key.strip():
            engines_to_try.append(("中转站", "zhongzhan", zz_key, zz_base_url, zz_model))
        if sf_key and sf_key.strip():
            engines_to_try.append(("SiliconFlow", "siliconflow", sf_key, sf_model))
        if ali_key and ali_key.strip():
            engines_to_try.append(("阿里云百炼", "ali_bailian", ali_key, ali_model))
            
    # 如果列表为空，按可用性加入有配置的引擎作为候选
    if not engines_to_try:
        if sf_key and sf_key.strip():
            engines_to_try.append(("SiliconFlow", "siliconflow", sf_key, sf_model))
        if ali_key and ali_key.strip():
            engines_to_try.append(("阿里云百炼", "ali_bailian", ali_key, ali_model))
        if zz_key and zz_key.strip():
            engines_to_try.append(("中转站", "zhongzhan", zz_key, zz_base_url, zz_model))
        
    if not engines_to_try:
        raise ValueError("未配置任何识图 Key，请在右上角「API设置」面板中配置 硅基流动、阿里百炼 或 中转站 API 密钥。")
        
    for engine_info in engines_to_try:
        label, engine_type = engine_info[0], engine_info[1]
        try:
            print(f"[PDF OCR Flow] 正在尝试调用识图引擎: {label}...")
            if engine_type == "siliconflow":
                return ocr_via_siliconflow(image_path, engine_info[2], model_name=engine_info[3])
            elif engine_type == "ali_bailian":
                return ocr_via_ali_bailian(image_path, engine_info[2], model_name=engine_info[3])
            elif engine_type == "zhongzhan":
                return ocr_via_zhongzhan(image_path, engine_info[2], engine_info[3], model_name=engine_info[4])
        except Exception as e_single:
            err_msg = f"{label} 出错: {str(e_single)}"
            print(f"[PDF OCR Flow Warning] {err_msg}")
            errors.append(err_msg)
            
    # 如果全部都失败了，抛出包含所有尝试错误细节的汇总异常
    raise RuntimeError("所有配置的识图引擎均尝试失败。详情:\n" + "\n".join(errors))


def process_ocr_illustrations(text: str, page_image_path: str, task_id: str) -> str:
    """(已关闭 AI 自动插图裁剪) 仅进行安全标签清洗，擦除任何潜在的视觉定位标签或 box 坐标标记，返回纯净 OCR 结果"""
    import re
    if not text:
        return text
    
    # 1. 擦除 Qwen 视觉定位标签: <|box_start|>(ymin,xmin,ymax,xmax)<|box_end|>
    cleaned = re.sub(r"(?i)<\|box_start\|>.*?<\|box_end\|>", "", text)
    
    # 2. 擦除 ILLUSTRATION_BOX 标签: [ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]
    cleaned = re.sub(r"(?i)\[ILLUSTRATION_BOX:.*?\]", "", cleaned)
    cleaned = re.sub(r"(?i)ILLUSTRATION_BOX\s*[:：\(（\[\s]*[^\]\)\n\r]+[\s\]\)]*", "", cleaned)
    
    return cleaned.strip()


def find_source_page_by_overlap(q_text: str, ocr_results: list) -> int:
    """利用 3-shingle（三字符切片）特征重合度，计算题目最可能所属的 PDF 原始物理页码"""
    if not q_text or not ocr_results:
        return 0
    
    import re
    def clean_for_compare(t: str) -> str:
        # 仅保留中文字符、英文字母和数字，过滤掉干扰公式渲染的标点符号
        return "".join(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', t))
        
    cleaned_q = clean_for_compare(q_text)
    if not cleaned_q:
        return 0
        
    best_page = 0
    max_overlap = -1
    
    for idx, page_text in enumerate(ocr_results):
        if not page_text:
            continue
        cleaned_page = clean_for_compare(page_text)
        
        # 构建 3-shingle 切片集合
        if len(cleaned_q) >= 3:
            shingles_q = set(cleaned_q[i:i+3] for i in range(len(cleaned_q)-2))
        else:
            shingles_q = {cleaned_q}
            
        if len(cleaned_page) >= 3:
            shingles_page = set(cleaned_page[i:i+3] for i in range(len(cleaned_page)-2))
        else:
            shingles_page = {cleaned_page}
            
        overlap = len(shingles_q.intersection(shingles_page))
        if overlap > max_overlap:
            max_overlap = overlap
            best_page = idx
            
    return best_page


def post_process_pdf_parsed_questions(parsed_questions: list, paper_title: str, task_id: str = None, ocr_results: list = None) -> list:
    """PDF 专属解析卡片后处理：正则搜寻 /tmp/ 下的图片，以及将未解析的图n占位符智能映射回真实的裁剪插图图片，
    最后将其灌入 image_paths 数组中，并在 content 中静默清除以配合布局展示。支持文本重合度兜底映射，防大模型删除路径！"""
    import re
    import os
    import glob

    # 1. 搜集该 PDF 任务在 tmp 文件夹中生成的所有物理裁剪图片，按生成时间（mtime）进行排序
    task_crop_urls = []
    if task_id:
        crop_pattern = os.path.join(TMP_UPLOAD_DIR, f"pdf_crop_{task_id}_*.png")
        crop_files = glob.glob(crop_pattern)
        crop_files.sort(key=lambda x: os.path.getmtime(x))
        task_crop_urls = [f"/{UPLOAD_DIR_REL}/tmp/{os.path.basename(f)}" for f in crop_files]
        print(f"[PDF PostProcess] 发现任务 {task_id} 的实际裁剪图片 {len(task_crop_urls)} 张: {task_crop_urls}")

    # 2. 顺序提取出所有题目中未成功解析的插图占位符（例如 图1.png, 图2.png, 图1, 图2 等，特征是不以 /static/ 开头的图片引用路径）
    placeholders_in_order = []
    placeholder_seen = set()
    
    # 匹配 Markdown 图片格式: ![alt](url)
    md_pattern = r'!\[.*?\]\(([^)]+)\)'
    # 匹配 LaTeX 图片格式: \includegraphics[...]{path}
    latex_pattern = r'\\includegraphics(?:\[.*?\])?\{([^}]+)\}'
    
    for q in parsed_questions:
        for field in ["content", "answer_markdown"]:
            text_val = q.get(field, "")
            if isinstance(text_val, str):
                # 提取 Markdown 图片占位符
                for m in re.finditer(md_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)
                # 提取 LaTeX 图片占位符
                for m in re.finditer(latex_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)

    # 3. 建立占位符与物理裁剪图片路径的 1-to-1 映射关系
    mapping = {}
    for idx, ph in enumerate(placeholders_in_order):
        if idx < len(task_crop_urls):
            mapping[ph] = task_crop_urls[idx]
    if mapping:
        print(f"[PDF PostProcess] 成功建立占位符修复映射: {mapping}")

    # 4. 对每个题目卡片进行字段修补、占位符替换与资源晋升准备
    for q in parsed_questions:
        q["source"] = (q.get("source") or paper_title).strip()
        
        # 清理多余的双重转义 \n
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                text = q[field]
                text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                q[field] = text

        # 智能替换 Markdown 和 LaTeX 字段中的图片占位符
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                # 替换已建立映射的非标准路径
                for ph, real_url in mapping.items():
                    if ph in q[field]:
                        q[field] = q[field].replace(ph, real_url)
                        # 如果是 LaTeX 的 \includegraphics 语法，顺带转换为 Markdown 图片语法以供前端预览渲染
                        latex_img_pattern = r'\\includegraphics(?:\[.*?\])?\{' + re.escape(real_url) + r'\}'
                        q[field] = re.sub(latex_img_pattern, f'![插图]({real_url})', q[field])

        # 寻找本题正文中夹带的所有临时图片 URL (注意：UUID 中含有 -，所以 regex 必须支持 [a-zA-Z0-9_-]+)
        found_crops = set()
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                for match in re.finditer(r'/static/uploads(?:_test|/test_uploads|/uploads)?/tmp/pdf_crop_[a-zA-Z0-9_-]+\.png', q[field]):
                    found_crops.add(match.group(0))
                    
        # 顺带检查 referenced_images 属性并应用修复映射
        ref_imgs = q.get("referenced_images", [])
        for ref in ref_imgs:
            mapped_ref = mapping.get(ref, ref)
            if "pdf_crop_" in mapped_ref:
                filename = os.path.basename(mapped_ref)
                found_crops.add(f"/{UPLOAD_DIR_REL}/tmp/{filename}")
                
        # 灌入 image_paths 作为独立配图卡片关联
        q["image_paths"] = list(found_crops)

    # 5. 极致兜底机制：如果大模型在拆题时完全删除了图片占位标记或路径，导致最终题目关联的图片为空，
    # 我们利用 3-shingle 文本重合度，将原始 PDF 物理页面产生的物理插图自动关联绑定回拆分出的题目！
    if ocr_results and task_id:
        page_crops = {}
        for p_idx, page_text in enumerate(ocr_results):
            # 获取当前页生成的所有 pdf_crop_ 临时文件 URL
            urls_on_page = re.findall(r'/static/uploads(?:_test|/test_uploads|/uploads)?/tmp/pdf_crop_[a-zA-Z0-9_-]+\.png', page_text or "")
            page_crops[p_idx] = list(set(urls_on_page))
            
        print(f"[PDF PostProcess Failsafe] 每页识别到的插图关系: {page_crops}")
        
        for q in parsed_questions:
            if not q.get("image_paths"):
                p_source = find_source_page_by_overlap(q.get("content", ""), ocr_results)
                crops = page_crops.get(p_source, [])
                if crops:
                    q["image_paths"] = crops
                    print(f"[PDF PostProcess Failsafe] 成功通过重合度，将第 {p_source + 1} 页的插图 {crops} 兜底分配给题目: {q.get('content')[:40]}...")

    # 6. 从 content 题干中静默移除已经绑定至 image_paths 内部的占位图片语法，以避免重叠渲染
    for q in parsed_questions:
        found_crops = q.get("image_paths", [])
        if "content" in q and isinstance(q["content"], str):
            for crop_url in found_crops:
                q["content"] = re.sub(r'!\[.*?\]\(' + re.escape(crop_url) + r'\)', '', q["content"])
            q["content"] = q["content"].strip()
            
    return parsed_questions


def run_pdf_parsing_task(task_id: str, file_bytes: bytes, filename: str, generate_answers: bool = False, page_range: str = None):
    """异步 PDF 试卷分析后台主流程：栅格化 -> 并行 OCR -> 自动裁剪 -> 大模型拆题"""
    import concurrent.futures
    import re
    
    try:
        import fitz
    except ImportError:
        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id] = {
                "status": "error",
                "progress": 0,
                "error": "本地 Python 环境未安装 PyMuPDF，请通过 pip install pymupdf 安装依赖！"
            }
        return
        
    try:
        # 暂存 PDF 文件
        tmp_pdf_path = os.path.join(TMP_UPLOAD_DIR, f"{task_id}.pdf")
        with open(tmp_pdf_path, "wb") as f:
            f.write(file_bytes)
            
        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id] = {
                "status": "processing_images",
                "progress": 10,
                "log": "已接收文件，正在渲染 PDF 高清页面..."
            }
            
        doc = fitz.open(tmp_pdf_path)
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")
            
        # 解析指定的页码范围 (1-indexed -> 0-indexed)
        target_page_indices = parse_page_range(page_range, total_pages)
        total_target_pages = len(target_page_indices)
        
        page_images = []
        page_urls = []
        for page_num in target_page_indices:
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_filename = f"pdf_page_{task_id}_{page_num}.png"
            img_filepath = os.path.join(TMP_UPLOAD_DIR, img_filename)
            pix.save(img_filepath)
            page_images.append(img_filepath)
            page_urls.append(f"/{UPLOAD_DIR_REL}/tmp/{img_filename}")
            
        try:
            os.remove(tmp_pdf_path)
        except Exception:
            pass

        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id] = {
                "status": "ocr_extraction",
                "progress": 30,
                "log": f"共 {total_pages} 页，指定解析 {total_target_pages} 页。正在并行发起多模态 VLM 进行图文公式转译与插图定位...",
                "page_images": page_urls
            }

        # 并行 OCR 解析
        ocr_results = [None] * total_target_pages
        
        def ocr_worker(local_idx, img_path):
            try:
                raw_text = ocr_pdf_page_image(img_path)
                real_page_num = target_page_indices[local_idx] + 1
                # 注入 Debug 原始文本回显
                print(f"[DEBUG OCR Raw Response] 目标第 {real_page_num} 页的原始返回结果:\n{raw_text}\n" + "-"*40)
                return local_idx, raw_text, None
            except Exception as e_ocr:
                return local_idx, "", str(e_ocr)
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(total_target_pages, 8)) as executor:
            futures = [executor.submit(ocr_worker, i, page_images[i]) for i in range(total_target_pages)]
            
            completed = 0
            for fut in concurrent.futures.as_completed(futures):
                local_idx, text, err = fut.result()
                if err:
                    real_page_num = target_page_indices[local_idx] + 1
                    raise RuntimeError(f"解析第 {real_page_num} 页出错: {err}")
                
                # 清洗配图并进行物理裁剪
                processed_text = process_ocr_illustrations(text, page_images[local_idx], task_id)
                ocr_results[local_idx] = processed_text
                
                completed += 1
                prog = 30 + int((completed / total_target_pages) * 40)
                with PDF_TASKS_LOCK:
                    # 保持 page_images 的返回，使前端随时可用
                    PDF_TASKS[task_id].update({
                        "progress": prog,
                        "log": f"多模态转译进度: {completed} / {total_target_pages} 页已完成..."
                    })

        # 拼接全文 LaTeX 源码
        full_latex_content = "\n\n".join(ocr_results)

        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id].update({
                "status": "ai_splitting",
                "progress": 80,
                "log": "公式转译完成！正在调用文本大模型拆解题目与标注属性..."
            })

        # 执行拆题分析
        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_latex_content)
        if auto_title:
            paper_title = auto_title
            
        parsed_questions = parse_paper_text_internal(full_latex_content, generate_answers)
        
        # 题库卡片后处理与插图深度关联
        final_questions = post_process_pdf_parsed_questions(parsed_questions, paper_title, task_id, ocr_results)
        
        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id].update({
                "status": "completed",
                "progress": 100,
                "log": "完成！已为您提取并拆分全部题目卡片。",
                "data": final_questions
            })
            
    except Exception as ex:
        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id] = {
                "status": "error",
                "progress": 0,
                "error": f"PDF 智能拆解解析失败: {str(ex)}"
            }


def parse_page_range(range_str: str, total_pages: int) -> list:
    """
    解析用户输入的页码范围字符串（1-indexed），转换为包含 0-indexed 页面索引的列表。
    支持格式如 "1-5", "1,3,5", "1-3,5,7-9"。
    """
    if not range_str:
        return list(range(total_pages))
    
    pages = set()
    parts = range_str.replace(" ", "").split(",")
    for part in parts:
        if not part:
            continue
        if "-" in part:
            sub_parts = part.split("-")
            if len(sub_parts) == 2:
                try:
                    start = int(sub_parts[0])
                    end = int(sub_parts[1])
                    start_idx = max(0, start - 1)
                    end_idx = min(total_pages - 1, end - 1)
                    if start_idx <= end_idx:
                        for p in range(start_idx, end_idx + 1):
                            pages.add(p)
                except ValueError:
                    pass
        else:
            try:
                p = int(part)
                p_idx = p - 1
                if 0 <= p_idx < total_pages:
                    pages.add(p_idx)
            except ValueError:
                pass
                
    result = sorted(list(pages))
    return result if result else list(range(total_pages))


# ----------------- PDF Upload & Task Routing Endpoints -----------------

@app.post("/api/upload/pdf-task")
async def upload_pdf_task(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    page_range: Optional[str] = Form(None)
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        if not file.filename.lower().endswith(".pdf"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .pdf 格式！"},
                status_code=400
            )
            
        # 限制文件大小（例如：30MB）
        content = await file.read()
        if len(content) > 30 * 1024 * 1024:
            return JSONResponse(
                content={"status": "error", "message": "PDF 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=400
            )
            
        task_id = str(uuid.uuid4())
        
        # 初始化任务状态
        with PDF_TASKS_LOCK:
            PDF_TASKS[task_id] = {
                "status": "pending",
                "progress": 0,
                "log": "任务已排队，正在准备运行异步切片分析..."
            }
            
        # 运行异步后台任务
        background_tasks.add_task(
            run_pdf_parsing_task,
            task_id,
            content,
            file.filename,
            generate_answers_bool,
            page_range
        )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 PDF 解析任务失败: {str(e)}"},
            status_code=500
        )


@app.get("/api/tasks/{task_id}/status")
def get_pdf_task_status(task_id: str):
    with PDF_TASKS_LOCK:
        task = PDF_TASKS.get(task_id)
        if not task:
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的任务 ID！"},
                status_code=404
            )
        return task


@app.post("/api/ai/clear-temp-crops")
def clear_temp_crops(payload: dict):
    """物理删除传递来的未入库临时裁剪图片路径"""
    try:
        paths = payload.get("paths", [])
        removed_count = 0
        for path in paths:
            # 严格安全边界过滤：必须是 UPLOAD_DIR 目录下的 tmp 子目录，且只包含 uuid 和文件名
            # 防止恶意攻击者进行任意文件删除 (Directory Traversal)
            normalized_path = os.path.normpath(path.lstrip("/"))
            # 检查是否以 tmp 路径为前缀且位于 static/uploads 中
            expected_prefix = os.path.normpath(os.path.join(UPLOAD_DIR_REL, "tmp")).lower()
            if normalized_path.lower().startswith(expected_prefix):
                full_path = os.path.join(os.getcwd(), normalized_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                        removed_count += 1
                    except Exception:
                        pass
        return {"status": "success", "message": f"成功物理清除 {removed_count} 张废弃插图图片。"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"清理临时插图出错: {str(e)}"},
            status_code=500
        )


# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", StaticFiles(directory="static"), name="static")
