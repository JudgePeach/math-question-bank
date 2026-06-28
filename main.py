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
from typing import List
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
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
    # 如果目标 URL 是国内知名 API（如阿里百炼、SimpleTex、SiliconFlow 等），
    # 且 kwargs 中没有明确指定 proxies，直接在首次请求时默认禁用代理，避免代理延迟、握手失败或超时！
    is_domestic = any(domain in url.lower() for domain in [
        "aliyuncs.com", 
        "simpletex.net", 
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
        "simpletex.net", 
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


def ocr_via_siliconflow(image_path: str, api_key: str, model_name: str = "Qwen/Qwen3.5-4B") -> str:
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
    
    # 构造 SiliconFlow 的多模态内容消息
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请精确识别并提取这幅图像中的**所有文字与数学公式**。必须完整转录，不得遗漏或删减图像中的任何字符（包括方括号、题目来源如 '[2025 · 江苏淮安高一期末]' 等）。\n"
                            "直接输出图像内容的转录结果，绝对不要夹带任何你个人的说明、开场白、回复语或解释。\n"
                            "【排版格式与 LaTeX 语法关键准则 (极重要)】：\n"
                            "1. **公式级包裹**：所有 LaTeX 数学命令（如 \\overrightarrow, \\cos, \\sin, \\theta, \\cdot, \\alpha, \\beta, \\gamma, \\Delta 等）以及所有代数式、方程、集合、平面向量符号，**必须并且只能**包裹在 LaTeX 标记中（行内公式使用 $...$，行间/独立公式使用 $$...$$）。绝对不能让任何带有反斜杠 `\\` 的 LaTeX 语法暴露在普通文本中。例如，普通文本中不能出现 `\\overrightarrow{AB}`，而必须写为 `$\\overrightarrow{AB}$`。\n"
                            "2. **严禁使用 \\text 语法**：不要在 LaTeX 公式中使用 `\\text{...}` 来包裹大段中文或题目来源。普通的中文叙述和文字必须作为普通的文本直接输出在 LaTeX 块外部。例如，绝对不能输出 `$\\text{江苏淮安高一期末}$`，而必须写为普通的文本：`[2025 · 江苏淮安高一期末]`；绝对不能输出 `$\\text{已知在直角坐标系中}$`，而必须写为 `已知在直角坐标系中`。\n"
                            "3. **变量/点/坐标包裹**：所有的几何点符号（如 $A$, $B$, $C$, $D$, $O$, $P$ 等）、所有单个字母变量（如 $x$, $y$, $m$, $n$ 等）以及所有的坐标表达式（如 $(1,2)$, $(3,3)$, $(x,y)$ 等）均需严格包裹在单美元符号 $...$ 中。\n"
                            "4. **严禁整段包裹**：不要将普通的中文文本、题目描述或整段话包裹在 LaTeX 标记中。\n"
                            "5. **精精确保留排版结构**：务必精精确保留原图的换行、段落以及选项（A、B、C、D）的对齐排版。\n"
                            "6. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格（如 `\\,` 或 `\\!` 等），确保数学公式的标准纯净。\n"
                            "7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含立体几何、平面几何、函数图像、平面向量等几何插图。如果包含，**请务必在输出的文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数（例如插图在整张图偏右侧，可以输出为 `[ILLUSTRATION_BOX: 10, 45, 90, 95]`；如果整张图没有插图，则绝对不要输出 `[ILLUSTRATION_BOX: ...]`）。"
                        )
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
    
    try:
        response = robust_request_post(url, headers=headers, json=payload, timeout=120)
    except Exception as e:
        raise RuntimeError(f"请求 SiliconFlow 失败: {str(e)}")
        
    if response.status_code != 200:
        raise RuntimeError(f"SiliconFlow API 识别失败，HTTP 状态码: {response.status_code}，详情: {response.text}")
        
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


def ocr_via_ali_bailian(image_path: str, api_key: str, model_name: str = "qwen3-vl-flash") -> str:
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
    
    # 构造阿里云百炼的多模态内容消息
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请精确识别并提取这幅图像中的**所有文字与数学公式**。必须完整转录，不得遗漏、删减或改写图像中的任何字符（包括方括号、题目来源如 '[2025 · 武汉二中高一月考]'、定义说明、前言等大段文字）。\n"
                            "直接输出图像内容的转录结果，绝对不要夹带任何你个人的说明、开场白、回复语或解释（例如，不要包含 '这是识别结果：' 等多余的 AI 聊天文字）。\n"
                            "【排版格式与 LaTeX 语法关键准则】：\n"
                            "1. **严禁整段包裹**：绝对不要将普通的中文文本、题目描述或整段话包裹在 LaTeX 标记（如 `$$...$$` 或 `$...$`）中。普通的中文叙述和文字必须作为普通的文本直接输出。\n"
                            "2. **严禁滥用 \\text 语法**：不要在 LaTeX 公式中使用 `\\text{...}` 来包裹大段的中文描述。所有的中文文字都应该写在 LaTeX 块外部。例如，不要输出 `$\\text{已知集合 } B$`，而应该输出 `已知集合 $B$`。\n"
                            "3. **公式级包裹**：仅对纯数学符号、代数式、集合、方程等数学对象使用 LaTeX。行内变量/符号（如 $A$、$x$、$-7$ 等）使用单美元符号 `$...$`；独立的一行长公式或复杂等式才使用双美元符号 `$$...$$`。\n"
                            "4. **精确保留排版结构**：务必精确保留原图的换行、段落以及选项（A、B、C、D）的对齐排版。\n"
                            "5. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格（如 `\\,` 或 `\\!` 等），确保数学公式的标准纯净。\n"
                            "6. **保留所有中文文字**：在转录过程中必须百分之百保留题目中的叙述文字，例如定义段落和前言介绍，严禁只输出最后一句问句。\n"
                            "7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含立体几何、平面几何、函数图像、平面向量等几何插图。如果包含，**请务必在输出 the 文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数（例如插图在整张图偏右侧，可以输出为 `[ILLUSTRATION_BOX: 10, 45, 90, 95]`；如果整张图没有插图，则绝对不要输出 `[ILLUSTRATION_BOX: ...]`）。"
                        )
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
    
    try:
        response = robust_request_post(url, headers=headers, json=payload, timeout=120)
    except Exception as e:
        raise RuntimeError(f"请求阿里云百炼失败: {str(e)}")
        
    if response.status_code != 200:
        raise RuntimeError(f"阿里云百炼 API 识别失败，HTTP 状态码: {response.status_code}，详情: {response.text}")
        
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


def ocr_via_zhongzhan(image_path: str, api_key: str, base_url: str, model_name: str) -> str:
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
        "3. **公式级包裹**：仅对纯数学符号、代数式、集合、方程等数学对象使用 LaTeX。行内变量/符号（如 $A$、$x$ 等）使用单美元符号 `$...$`。\n"
        "4. **精精确保留排版结构**：务必精精确保留原图的换行、段落以及选项的对齐排版。\n"
        "5. **过滤干扰符**：省略公式与汉字之间干扰渲染的薄空格，确保数学公式的标准纯净。\n"
        "6. **保留所有中文文字**：在转录过程中必须百分之百保留题目中的叙述文字，严禁只输出最后一句问句。\n"
        "7. **几何插图区域识别 (极重要)**：请仔细观察图像中是否包含几何插图。如果包含，**请务必在输出的文本末尾**追加输出该插图在整张图片中的归一化百分比坐标包围框，格式严格为：`[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`。其中四个数值代表插图在图片中占用的百分比比例，范围为 0 到 100 之间的整数。"
    )
    
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
    
    try:
        response = robust_request_post(url, headers=headers, json=payload, timeout=120)
    except Exception as e:
        raise RuntimeError(f"请求中转站多模态 OCR 失败: {str(e)}")
        
    if response.status_code != 200:
        raise RuntimeError(f"中转站多模态 OCR 识别失败，HTTP 状态码: {response.status_code}，详情: {response.text}")
        
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
    
    if is_zhongzhan:
        if is_zhongzhan_gpt:
            api_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
            base_url = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")
            provider_label = "中转站 (GPT)"
        else:
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            base_url = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_label = "中转站 (Claude)"
            
        if not api_key:
            print(f"[High Model Draw] 未配置 {provider_label} 密钥，降级跳过。")
            return None
        model_name = prefer_draw.split("/", 1)[1]
        base_url = base_url.rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
    else:
        api_key = os.getenv("SILICONFLOW_API_KEY")
        if not api_key:
            print("[High Model Draw] 未配置 SILICONFLOW_API_KEY，无法调用 SiliconFlow 高级绘图，降级跳过。")
            return None
        model_name = prefer_draw
        url = "https://api.siliconflow.cn/v1/chat/completions"

    # 判断是否为多模态模型 (名称中含 'vl', 'gpt', 'claude'，或者只要是中转站我们一般默认为多模态)
    is_multimodal = is_zhongzhan or "vl" in model_name.lower() or "thinking" in model_name.lower()
    
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
    engine: str = Form(None)
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
                    latex_content = ocr_via_siliconflow(temp_filepath, sf_key, model_name=sf_model)
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
                    latex_content = ocr_via_ali_bailian(temp_filepath, ali_key, model_name=ali_model)
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
                    latex_content = ocr_via_zhongzhan(temp_filepath, zz_key, zz_base_url, model_name=zz_model)
                    confidence = 0.99
                    provider = f"{provider_label} ({zz_model})"
                except Exception as e:
                    print(f"[{provider_label} 识别失败] 发生异常: {str(e)}")
            else:
                print(f"[OCR Flow Warning] 未配置 {provider_label} 密钥，中转站引擎无法启动！")

        # ----------------- 引擎 3: SimpleTex 云端 API (如果是首选，或者其它多模态引擎识别失败时作为兜底/并存选项) -----------------
        if not latex_content:
            if engine in ["siliconflow", "ali_bailian", "zhongzhan", "zhongzhan_gpt", "zhongzhan_claude"]:
                print(f"[OCR Flow Auto-Fallback] {engine} 识别未成功，正在自动无缝降级至 SimpleTex 引擎兜底...")
            
            simpletex_token = os.getenv("SIMPLETEX_TOKEN")
            if simpletex_token and simpletex_token.strip():
                try:
                    with open(temp_filepath, "rb") as f:
                        img_bytes = f.read()
                        
                    headers = {
                        "token": simpletex_token.strip()
                    }
                    files = {
                        "file": ("image.png", img_bytes, "image/png")
                    }
                    data = {
                        "rec_mode": "auto"
                    }
                    
                    response = robust_request_post(
                        "https://server.simpletex.net/api/latex_ocr",
                        files=files,
                        data=data,
                        headers=headers,
                        timeout=12
                    )
                    
                    if response.status_code == 200:
                        res_json = response.json()
                        if res_json.get("status") is True:
                            res_data = res_json.get("res", {})
                            latex_content = res_data.get("latex", "")
                            confidence = res_data.get("conf", 1.0)
                            provider = "SimpleTex"
                        else:
                            print(f"[SimpleTex API 异常] 返回 status 为 false: {res_json.get('error', '未知错误')}")
                    else:
                        print(f"[SimpleTex 网络异常] 状态码: {response.status_code}")
                except Exception as e:
                    print(f"[SimpleTex 请求失败] 发生异常: {str(e)}")
            else:
                print("[OCR Flow Warning] 未配置 SIMPLETEX_TOKEN，SimpleTex 引擎未启动！")

        if not latex_content:
            raise RuntimeError("当前分配的识图引擎均无法启动或识别失败。请检查右上角「API设置」中是否正确配置了 硅基流动(SiliconFlow)、阿里百炼(Alibaba Bailian) 或是 SimpleTex 的 API Key。")

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
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form("deepseek-v4-pro")
):
    # 动态解析模型所属的服务商前缀与真实模型名
    model_lower = model.lower()
    api_key = None
    api_base = None
    model_name = model
    
    if "/" in model:
        parts = model.split("/", 1)
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
        # 向后兼容传统无前缀模式
        if "qwen" in model_lower:
            api_key = os.getenv("ALI_BAILIAN_API_KEY")
            api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            model_name = "qwen-max" if model == "qwen3.7-max" else model
        else:
            api_key = os.getenv("DEEPSEEK_API_KEY")
            api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
            model_name = model

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key！请在控制面板中填写后重试。"
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
        
        system_instructions = (
            "你是一位极其严谨的、资深的高中数学教研专家。请解答用户输入的高中数学题目。特别注意：这必须是一道符合高中数学大纲要求的题目，你的解题思路、方法和技巧绝对不能超出中国普通高中阶段的水平（严禁使用大学高等数学、微积分、高等代数、洛必达法则、泰勒展开、拉格朗日中值定理等超出高中阶段教学大纲的大学方法，必须完全采用符合高中知识体系和认知范围的常规或技巧性方法）。\n"
            "【输出核心准则】\n"
            "1. 你的回答必须直接、干净地从下面的结构化板块开始。严禁包含任何前言、导语、引入承接句或问候语（例如“你好！”、“下面是解析：”等）。\n"
            "2. 你的回答必须直接以“**【参考答案】**”作为第一个字符开始输出。严禁在结尾包含任何总结、客套话或多余的尾注段落。\n"
            "【输出格式要求】\n"
            "1. 必须使用标准的 LaTeX 语法书写所有的数学公式。行内公式使用 $...$ 或 \\( ... \\)，行间公式使用 $$\\n...\\n$$ 或 \\[ ... \\]。\n"
            "2. 排版优雅，逻辑步骤条理清晰，推理严密，没有任何废话。\n"
            "3. 你的输出内容必须且仅包含以下三个结构化板块（使用 markdown 格式）：\n"
            "   - **【参考答案】**：直接给出最简练、准确的最终答案。\n"
            "   - **【详细解析】**：分步骤地写出详尽推导过程。如果有分类讨论或多种解法，请逐一清晰列出。如果本题涉及几何图像，你可以适度使用 TikZ 绘图代码描述（置于 ```tikz ... ``` 代码块中，供系统前端解析）。\n"
            "   - **【核心知识点】**：列出解答本题用到的关键数学原理、定理或核心思想方法。\n"
            "4. 绝不要带有任何无关的字句，直接输出这三个板块。"
        )
        
        user_prompt = f"题目类型: {type_str}\n"
        if custom_prompt:
            user_prompt += f"补充引导指令: {custom_prompt}\n"
        user_prompt += f"题干内容:\n{content}"
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 8192
        }
        
        # Configure thinking parameter if specified (only for DeepSeek models/endpoints)
        is_deepseek = "deepseek" in model.lower() or "deepseek" in api_base.lower()
        if is_deepseek and thinking in ["enabled", "disabled"]:
            data["thinking"] = {"type": thinking}
            
        # When thinking mode is active, temperature is ignored/deprecated by DeepSeek.
        # But when thinking is disabled or non-DeepSeek model, specify it.
        if not is_deepseek or thinking == "disabled":
            data["temperature"] = 0.2
            
        # Generous 180 seconds timeout for high-school math reasoning and network proxies
        response = robust_request_post(url, headers=headers, json=data, timeout=180)
        
        if response.status_code != 200:
            raise Exception(f"DeepSeek API 响应错误, HTTP 状态码: {response.status_code}, 内容: {response.text}")
            
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
            raise Exception("DeepSeek 返回了空消息，请检查 API 或账户余额。")
            
        return {
            "status": "success",
            "solution": ai_message
        }
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

    # 1. 检查 xelatex
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
                (Question.review.like(f"%{q}%"))
            )
        else:
            query = query.filter(
                (Question.content.like(f"%{q}%")) | 
                (Question.source.like(f"%{q}%")) |
                (Question.answer_markdown.like(f"%{q}%")) |
                (Question.review.like(f"%{q}%"))
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
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    db: Session = Depends(get_db)
):
    try:
        # Validate json array format
        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
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
            tikz_code=tikz_code
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
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    try:
        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
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
    for comp, chapters in RENJIAO_A_CURRICULUM.items():
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
        for book, chapters in RENJIAO_A_CURRICULUM.items():
            curriculum_text += f"- {book}: {list(chapters.keys())}\n"
            
        system_instructions = (
            "你是一个专门为中国高中数学教材分类的 AI 专家。请分析以下输入的数学题目，将其归入【人教版A】的高中数学教材体系。\n"
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
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL
        is_deepseek = "deepseek" in model_name.lower() or "deepseek" in api_base.lower()
        if is_deepseek:
            data["thinking"] = {
                "type": "disabled"
            }
        
        
        response = robust_request_post(url, headers=headers, json=data, timeout=30)
        if response.status_code != 200:
            raise Exception(f"DeepSeek 接口错误: HTTP {response.status_code}")
            
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
        
        # Verification: make sure returned values exist in RENJIAO_A_CURRICULUM
        if compulsory in RENJIAO_A_CURRICULUM and chapter in RENJIAO_A_CURRICULUM[compulsory]:
            return {
                "status": "success",
                "compulsory": compulsory,
                "chapter": chapter
            }
        else:
            # Fallback to general default
            return {
                "status": "success",
                "compulsory": "必修一",
                "chapter": "1. 集合与常用逻辑用语",
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

@app.post("/api/ai/parse-paper")
async def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(...),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false")
):
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    model_lower = parse_model.lower()
    is_qwen = "qwen" in model_lower
    
    if is_qwen:
        api_key = os.getenv("ALI_BAILIAN_API_KEY")
        if not api_key:
            return JSONResponse(
                content={
                    "status": "error", 
                    "message": "阿里百炼 API Key (ALI_BAILIAN_API_KEY) 未配置，无法自动智能拆解试卷！请在工作台右上角进行配置后重试。"
                },
                status_code=400
            )
        api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if parse_model == "qwen3.7-max":
            parse_model = "qwen-max"
    else:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return JSONResponse(
                content={
                    "status": "error", 
                    "message": "DeepSeek API Key (DEEPSEEK_API_KEY) 未配置！请在工作台右上角进行配置后重试。"
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
        for book, chapters in RENJIAO_A_CURRICULUM.items():
            curriculum_text += f"- {book}: {list(chapters.keys())}\n"
            
        if generate_answers_bool:
            answer_generation_rule = (
                "   - 如果试卷中只有题干没有答案，请根据题干自动生成详尽的解答步骤与解析，填入 `answer_markdown` 字段。特别注意：由于这些题目均为高中数学题，自动生成的解答过程、解题思路和技巧绝对不能超出普通高中阶段水平，严禁使用任何高等数学、微积分、洛必达法则、泰勒公式、拉格朗日中值定理等大学超纲方法，必须完全采用符合高中知识体系和认知范围的常规或技巧性方法。\n"
            )
        else:
            answer_generation_rule = (
                "   - 如果试卷中只有题干没有答案，请将 `answer_markdown` 字段保持为空字符串 `\"\"`。**绝对不要**主动为没有答案的题目生成任何解答、解析、推导步骤或最终答案，保持该字段留空。只有当试卷源码本来就包含该题的参考答案或解析时，才进行对应提取。\n"
            )

        system_instructions = (
            "你是一位极其严谨的高中数学教研专家与 LaTeX 排版大师。请阅读并解析用户输入的【整张高中数学试卷 LaTeX 源码】，将其智能拆解为独立的数学题目列表，并分析每一题的属性。\n"
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
            "   - **禁止输出字面量 `\\n` 字符**：在 `content` 或 `answer_markdown` 的字符串内部换行时，直接在 JSON 字段里输出真实的换行符（回车换行），绝对不要输出转义后的字面量 `\\n`（即双斜杠字符 `\\\\n` 或斜杠加n），防止页面上直接显示出带有物理字符 `\\n` 的尴尬情况。\n"
            "9. **你的输出必须是一个合法的 JSON 对象，其根键为 `\"questions\"`，对应的值为一个 JSON 数组（包含以下结构化对象）。不要有任何多余的 Markdown 标记、代码块或解释文字**：\n"
            "{\n"
            "  \"questions\": [\n"
            "    {\n"
            '      "content": "题干内容，包含 LaTeX 排版公式，且保留图片排版占位标记 (例如 ![插图](filename.png))",\n'
            '      "answer_markdown": "该题的答案与详细解析过程，使用标准 LaTeX 与 Markdown 排版",\n'
            '      "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
            '      "category_compulsory": "人教A学段名称",\n'
            '      "category_chapter": "人教A章节名称",\n'
            '      "difficulty": "easy_error / challenge / qiangji",\n'
            '      "referenced_images": ["引用的原始插图文件名1.png", "fig2.jpg"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "注意：只输出最干净的 JSON，千万不要包含 ```json ``` 等 Markdown 代码块标记！如果试卷中没有插图，referenced_images 数组留空。"
        )
        
        data = {
            "model": parse_model,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": latex_content}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": 8192
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL
        is_deepseek = "deepseek" in parse_model.lower() or "deepseek" in api_base.lower()
        if is_deepseek:
            data["thinking"] = {
                "type": "disabled"
            }
        
        
        response = robust_request_post(url, headers=headers, json=data, timeout=180)
        if response.status_code != 200:
            raise Exception(f"DeepSeek 接口错误: HTTP {response.status_code}")
            
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
            q["source"] = paper_title
            
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

# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", StaticFiles(directory="static"), name="static")
