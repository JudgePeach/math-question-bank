import os
import io
import sys
import uuid
import json
import time
import re
import signal
import datetime
import threading
import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import secrets
from typing import List, Optional
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response, Header
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import Question, QuestionCurriculum, Paper, PaperQuestion, get_db, init_db
from paper_helper import build_latex_document, build_answer_sheet_latex, compile_tex_to_pdf, create_tex_zip_package, create_full_bundle_zip_package, collect_referenced_images
from sync_helper import export_database_to_files
from mathbank.curriculums import (
    build_default_metadata,
    get_curriculum_preset,
    load_curriculum,
)
from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    ILLUSTRATION_BOX_PROMPT,
    build_ai_solve_prompts,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_paper_selection_prompts,
    build_pdf_parse_system_prompt,
    build_tikz_correction_prompt,
    build_tikz_draw_prompt,
)
from mathbank.paths import (
    DATA_BACKUP_DIR,
    ENV_FILE,
    PROJECT_ROOT,
    STATIC_CSS_DIR,
    STATIC_DIR,
    STATIC_JS_DIR,
    SYSTEM_GENERATED_DIR,
    TEST_UPLOADS_DIR,
    UPLOADS_DIR,
)

# Load environment variables
load_dotenv(ENV_FILE)

def parse_model_and_effort(model_str: str):
    """
    解析带推理强度的模型名称字符串。
    例如:
    'gpt-5.6-sol:high' -> ('gpt-5.6-sol', 'high')
    'gpt-5.6-sol(high)' -> ('gpt-5.6-sol', 'high')
    'ZHONGZHAN_GPT/gpt-5.6-sol:high' -> ('ZHONGZHAN_GPT/gpt-5.6-sol', 'high')
    'gpt-5.6-sol' -> ('gpt-5.6-sol', None)
    """
    if not model_str:
        return "", None
    model_str = str(model_str).strip()
    
    # 匹配括号如 (high)
    match_paren = re.search(r'^(.*?)\((high|medium|low|xhigh|max|default)\)$', model_str, re.IGNORECASE)
    if match_paren:
        return match_paren.group(1).strip(), match_paren.group(2).lower()
        
    if ":" in model_str:
        parts = model_str.rsplit(":", 1)
        potential_effort = parts[1].strip().lower()
        if potential_effort in ["high", "medium", "low", "xhigh", "max", "default"]:
            return parts[0].strip(), potential_effort
            
    return model_str, None

# Unique server instance ID generated per process launch/restart
SERVER_INSTANCE_ID = str(uuid.uuid4())

# Initialize DB
init_db()

def heal_database_curriculum_names():
    from database import SessionLocal
    db = SessionLocal()
    try:
        mappings = {
            "选择性必修一": "选修一",
            "选择性必修二": "选修二",
            "选择性必修三": "选修三",
            "必修第一册": "必修一",
            "必修第二册": "必修二",
            "必修第三册": "必修三",
            "必修第四册": "必修四",
        }
        updated_questions = 0
        for old, new in mappings.items():
            res = db.query(Question).filter(Question.category_compulsory == old).update(
                {Question.category_compulsory: new}, synchronize_session=False
            )
            updated_questions += res
            
        updated_mappings = 0
        for old, new in mappings.items():
            res = db.query(QuestionCurriculum).filter(QuestionCurriculum.compulsory == old).update(
                {QuestionCurriculum.compulsory: new}, synchronize_session=False
            )
            updated_mappings += res

        # 清理在主表 questions 及镜像表 question_curriculums 中残留的不属于各自大纲小节列表的旧章名/错位知识点
        curr = get_current_curriculum()
        healed_know_count = 0
        all_qs = db.query(Question).all()
        for q in all_qs:
            comp = q.category_compulsory
            chap = q.category_chapter
            know = q.category_knowledge
            if know:
                valid_knows = curr.get(comp, {}).get(chap, [])
                if know not in valid_knows:
                    q.category_knowledge = ""
                    healed_know_count += 1

        all_qcs = db.query(QuestionCurriculum).all()
        for qc in all_qcs:
            if qc.knowledge:
                try:
                    c_tree = load_curriculum(qc.version_code)
                except ValueError:
                    c_tree = curr
                valid_knows = c_tree.get(qc.compulsory, {}).get(qc.chapter, [])
                if qc.knowledge not in valid_knows:
                    qc.knowledge = ""
                    healed_know_count += 1
            
        if updated_questions > 0 or updated_mappings > 0 or healed_know_count > 0:
            db.commit()
            print(f"[Self-Healing DB] Migrated {updated_questions} questions, {updated_mappings} mappings, and cleaned {healed_know_count} mismatched knowledge values.")
    except Exception as e:
        db.rollback()
        print(f"[Self-Healing DB Error] Failed to run database book names migration: {e}")
    finally:
        db.close()

import sys
# 检测是否在单元测试环境下运行
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"
UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)

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
    token_dir = str(SYSTEM_GENERATED_DIR)
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

def recalibrate_usage_counts():
    """自动校准全库题目的引用频次 usage_count，修正由于历史删除试卷遗留的计数差异"""
    try:
        from database import SessionLocal, Question, PaperQuestion
        from sqlalchemy import func
        db = SessionLocal()
        try:
            counts = db.query(PaperQuestion.question_id, func.count(PaperQuestion.id)).group_by(PaperQuestion.question_id).all()
            ref_map = dict(counts)
            questions = db.query(Question).all()
            changed = False
            for q in questions:
                actual_ref = ref_map.get(q.id, 0)
                if (q.usage_count or 0) != actual_ref:
                    q.usage_count = actual_ref
                    changed = True
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[Usage Calibration Error] {e}")

def start_startup_cleanup():
    # 稍等 2.5 秒，让主服务先启动完毕并打开浏览器，不占用首屏加载时间
    time.sleep(2.5)
    clean_orphaned_images()
    recalibrate_usage_counts()

# 仅在非测试环境下启动静默自愈清理后台守护线程
if not IS_TESTING:
    threading.Thread(target=start_startup_cleanup, daemon=True).start()


# Ensure directories exist
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
    index_path = str(STATIC_DIR / "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Inject dynamic cache-busting version parameter based on file mtime
        js_files = ["api.js", "editor.js", "ocr.js", "import.js", "paper.js"]
        for js in js_files:
            js_path = str(STATIC_JS_DIR / js)
            mtime = int(os.path.getmtime(js_path)) if os.path.exists(js_path) else 0
            # Replace template version parameter
            html_content = html_content.replace(f"/static/js/{js}?v=1.0.1", f"/static/js/{js}?v={mtime}")
            # Also handle plain scripts references if they exist
            html_content = html_content.replace(f'src="/static/js/{js}"', f'src="/static/js/{js}?v={mtime}"')
            
        # Inject dynamic cache-busting version parameter for app.css and favicon assets
        css_path = str(STATIC_CSS_DIR / "app.css")
        css_mtime = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0
        html_content = html_content.replace('/static/css/app.css', f'/static/css/app.css?v={css_mtime}')

        fav_path = str(STATIC_DIR / "favicon.png")
        fav_mtime = int(os.path.getmtime(fav_path)) if os.path.exists(fav_path) else 0
        html_content = html_content.replace('/static/favicon.png', f'/static/favicon.png?v={fav_mtime}')
            
        # Inject the token and server_instance_id directly into index.html to bypass any cookie blocking policies
        token_script = f'<script>window.__localToken = "{LOCAL_TOKEN}"; window.__serverInstanceId = "{SERVER_INSTANCE_ID}";</script>'
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
    favicon_path = str(STATIC_DIR / "favicon.ico")
    if os.path.exists(favicon_path):
        res = FileResponse(favicon_path, media_type="image/x-icon")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    favicon_png_path = str(STATIC_DIR / "favicon.png")
    if os.path.exists(favicon_png_path):
        res = FileResponse(favicon_png_path, media_type="image/png")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon not found."},
        status_code=404
    )

@app.get("/favicon.svg", include_in_schema=False)
def read_favicon_svg():
    svg_path = str(STATIC_DIR / "favicon.svg")
    if os.path.exists(svg_path):
        res = FileResponse(svg_path, media_type="image/svg+xml")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon.svg not found."},
        status_code=404
    )

@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def read_apple_touch_icon():
    for name in ["apple-touch-icon.png", "favicon.png"]:
        icon_path = str(STATIC_DIR / name)
        if os.path.exists(icon_path):
            res = FileResponse(icon_path, media_type="image/png")
            res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            res.headers["Pragma"] = "no-cache"
            res.headers["Expires"] = "0"
            return res
    return JSONResponse(
        content={"status": "error", "message": "apple touch icon not found."},
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


def ocr_via_siliconflow(image_path: str, api_key: str, model_name: str = "Qwen/Qwen3-VL-8B-Instruct", include_illustration_box: bool = False) -> str:
    """调用 SiliconFlow 官方 API 进行多模态图文公式识别 (使用 Qwen3-VL-8B / Qwen3-VL-32B 等)"""
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
    
    prompt_text = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt_text += ILLUSTRATION_BOX_PROMPT

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
    
    prompt_text = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt_text += ILLUSTRATION_BOX_PROMPT

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
    
    prompt = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt += ILLUSTRATION_BOX_PROMPT
    
    clean_model, explicit_effort = parse_model_and_effort(model_name)
    if clean_model:
        model_name = clean_model

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

    if explicit_effort and explicit_effort != "default":
        payload["reasoning_effort"] = explicit_effort.lower()
        payload["enable_thinking"] = True
    
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
            provider_label = "中转站 A"
            model_name = prefer_draw.split("/", 1)[1]
        else:
            api_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
            base_url = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1")
            provider_label = "中转站 B"
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
            
        prompt = build_tikz_draw_prompt(latex_content, multimodal=True)
        
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
            
        prompt = build_tikz_draw_prompt(latex_content, multimodal=False)
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
    import re
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
        
        # ----------------- 引擎 1: SiliconFlow (Qwen3-VL-8B-Instruct) 多模态识图 -----------------
        if engine == "siliconflow":
            sf_key = os.getenv("SILICONFLOW_API_KEY")
            sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
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
                provider_label = "中转站 B"
            else:
                zz_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
                zz_base_url = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")
                zz_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
                provider_label = "中转站 A"
                
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
            # 自动清洗规范化下划线/连续划线/任何 \underline 变体为标准的 \fillin 宏
            latex_content = normalize_fillin_macro(latex_content)

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
        
        system_instructions, user_prompt = build_ai_solve_prompts(
            question_type=question_type,
            content=content,
            ocr_result=ocr_result,
            custom_prompt=custom_prompt,
        )

        # Double max_tokens to 16384, but cap at 8192 for Alibaba Bailian compatible-mode endpoints
        max_output_tokens = 8192 if (api_base and "aliyuncs.com" in api_base.lower()) else 16384
            
        clean_model, explicit_effort = parse_model_and_effort(model_name)
        if clean_model:
            model_name = clean_model

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
            
        # Overwrite with explicit reasoning_effort parameter if set via 7:3 UI layout
        if explicit_effort and explicit_effort != "default":
            data["reasoning_effort"] = explicit_effort.lower()
            data["enable_thinking"] = True
            
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
                                
                                # 优先读取接口可能返回的官方 usage 统计
                                usage = chunk_json.get("usage")
                                if usage and isinstance(usage, dict):
                                    c_tok = usage.get("completion_tokens")
                                    r_tok = usage.get("completion_tokens_details", {}).get("reasoning_tokens") if isinstance(usage.get("completion_tokens_details"), dict) else None
                                    if c_tok is not None:
                                        content_count = max(content_count, c_tok)
                                    if r_tok is not None:
                                        reasoning_count = max(reasoning_count, r_tok)

                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""
                                
                                # 针对不同模型的流式数据块进行动态 Token 数量估算（兼容大 Chunk 输出模型如 Gemini Flash）
                                if reasoning:
                                    cjk_c = sum(1 for c in reasoning if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(reasoning) - cjk_c
                                    reasoning_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                if content_piece:
                                    cjk_c = sum(1 for c in content_piece if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(content_piece) - cjk_c
                                    content_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                    
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
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
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
    siliconflow_model: str = Form("Qwen/Qwen3-VL-8B-Instruct"),
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
        if ENV_FILE.exists():
            with ENV_FILE.open("r", encoding="utf-8") as f:
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
            
        with ENV_FILE.open("w", encoding="utf-8") as f:
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
                    with open(log_path, "rb") as lf:
                        raw_log = lf.read()
                        try:
                            log_text = raw_log.decode("utf-8")
                        except UnicodeDecodeError:
                            log_text = raw_log.decode("gbk", errors="replace")
                        lines = log_text.splitlines()
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
    is_zhongzhan_gpt = prefer_draw.startswith("ZHONGZHAN_GPT/") or prefer_draw.startswith("ZHONGZHAN/")
    is_zhongzhan_claude = prefer_draw.startswith("ZHONGZHAN_CLAUDE/")
    is_bailian = prefer_draw.startswith("BAILIAN/")
    is_siliconflow = prefer_draw.startswith("SILICONFLOW/")
    
    if is_bailian:
        api_key = os.getenv("ALI_BAILIAN_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="未配置 阿里百炼 API Key (ALI_BAILIAN_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = api_key.strip()
        model_name = prefer_draw.split("/", 1)[1]
        if model_name == "qwen3.7-max":
            model_name = "qwen-max"
        base_url = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        print(f"[TikZ Correction] 启用阿里百炼高级模型进行纠错: {model_name}, Base URL: {url}")
    elif is_zhongzhan_gpt:
        zhongzhan_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY")
        if not zhongzhan_key:
            raise HTTPException(
                status_code=400,
                detail="未配置中转站 A (ZHONGZHAN_GPT_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = zhongzhan_key.strip()
        model_name = prefer_draw.split("/", 1)[1]
        base_url = (os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        print(f"[TikZ Correction] 启用中转站 A 高级模型进行纠错: {model_name}, Base URL: {url}")
    elif is_zhongzhan_claude:
        zhongzhan_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY")
        if not zhongzhan_key:
            raise HTTPException(
                status_code=400,
                detail="未配置中转站 B (ZHONGZHAN_CLAUDE_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = zhongzhan_key.strip()
        model_name = prefer_draw.split("/", 1)[1]
        base_url = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        print(f"[TikZ Correction] 启用中转站 B 高级模型进行纠错: {model_name}, Base URL: {url}")
    elif is_siliconflow:
        sf_key = os.getenv("SILICONFLOW_API_KEY")
        if not sf_key:
            raise HTTPException(
                status_code=400,
                detail="未配置 硅基流动 API Key (SILICONFLOW_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = sf_key.strip()
        model_name = prefer_draw.split("/", 1)[1]
        url = "https://api.siliconflow.cn/v1/chat/completions"
        print(f"[TikZ Correction] 启用 SiliconFlow 高级模型进行纠错: {model_name}")
    else:
        # SiliconFlow 无前缀默认路由
        sf_key = os.getenv("SILICONFLOW_API_KEY")
        if not sf_key:
            raise HTTPException(
                status_code=400,
                detail="未配置 硅基流动 API Key (SILICONFLOW_API_KEY)！请在设置面板中配置后重试。"
            )
        api_key = sf_key.strip()
        model_name = prefer_draw
        url = "https://api.siliconflow.cn/v1/chat/completions"
        print(f"[TikZ Correction] 启用 SiliconFlow 默认模型进行纠错: {model_name}")

    # 对原始截图进行 Base64 编码
    clean_original_path = os.path.join(str(PROJECT_ROOT), original_image_path.lstrip("/"))
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
        clean_rendered_path = os.path.join(str(PROJECT_ROOT), rendered_image_path.lstrip("/"))
        try:
            with open(clean_rendered_path, "rb") as f:
                encoded_rendered = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取渲染出的 TikZ 图片失败: {str(e)}")

        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            rendered_comparison=True,
        )

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
        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            compile_error_log=compile_error_log,
            rendered_comparison=False,
        )

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
    physical_path = os.path.join(str(PROJECT_ROOT), clean_path)
    
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
    search: str = None,
    compulsory: str = None,
    category_compulsory: str = None,
    chapter: str = None,
    category_chapter: str = None,
    knowledge: str = None,
    category_knowledge: str = None,
    qtype: str = None,
    question_type: str = None,
    difficulty: str = None,
    source: str = None,
    db: Session = Depends(get_db)
):
    search_q = q or search
    comp_val = compulsory or category_compulsory
    chap_val = chapter or category_chapter
    know_val = knowledge or category_knowledge
    type_val = qtype or question_type

    query = db.query(Question)
    
    # Check if searching for a specific display sequence number
    target_id_by_seq = None
    if search_q:
        clean_q = search_q.strip()
        if clean_q.startswith("#"):
            clean_q = clean_q[1:]
        if clean_q.isdigit():
            seq_val = int(clean_q)
            all_q_asc = db.query(Question.id).order_by(Question.id.asc()).all()
            if 1 <= seq_val <= len(all_q_asc):
                target_id_by_seq = all_q_asc[seq_val - 1][0]

    if search_q:
        if target_id_by_seq is not None:
            query = query.filter(
                (Question.id == target_id_by_seq) |
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
        else:
            query = query.filter(
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
    if comp_val:
        query = query.filter(Question.category_compulsory == comp_val)
    if chap_val:
        query = query.filter(Question.category_chapter == chap_val)
    if know_val:
        query = query.filter(Question.category_knowledge == know_val)
    if type_val:
        query = query.filter(Question.question_type == type_val)
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

def normalize_fillin_macro(text: str) -> str:
    """将题干中的任何下划线格式（\\underline{...}、\\fillin[...]、连续划线 ___）一律统一规范化为最纯粹的 \\fillin 宏"""
    if not text or not isinstance(text, str):
        return text or ""
    # 1. 替换连续下划线 ___ (3个及以上) 为 \fillin
    text = re.sub(r'_{3,}', r'\\fillin', text)
    # 2. 替换任何带参数的 \fillin[...] 为纯净的 \fillin
    text = re.sub(r'\\fillin\s*\[[^\]]*?\](?:\[[^\]]*?\])?', r'\\fillin', text)
    # 3. 替换任何形式的 \underline{...} 为纯净的 \fillin
    text = re.sub(r'\\underline\s*\{[^}]*?\}', r'\\fillin', text)
    # 4. 清理可能残留的额外右花括号 }
    text = re.sub(r'\\fillin\}', r'\\fillin', text)
    return text

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
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    db: Session = Depends(get_db)
):
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

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
            figure_align=figure_align if figure_align in ["right", "center", "bottom_right"] else "right",
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
        
        # Save to active QuestionCurriculum mapping
        active_version = get_active_version_code()
        curriculum_map = QuestionCurriculum(
            question_id=db_question.id,
            version_code=active_version,
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge
        )
        db.add(curriculum_map)
        db.commit()
        
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
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

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
        if figure_align in ["right", "center", "bottom_right"]:
            db_question.figure_align = figure_align
        db_question.tags = tags
        # Clean up removed images from disk to prevent storage leaks
        old_images = db_question.image_paths
        removed_images = set(old_images) - set(parsed_img_paths)
        for img_path in removed_images:
            rel_path = img_path.lstrip("/")
            physical_path = os.path.join(str(PROJECT_ROOT), rel_path)
            if rel_path.startswith(f"{UPLOAD_DIR_REL}/") and os.path.exists(physical_path):
                try:
                    os.remove(physical_path)
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
        
        # Update or create active QuestionCurriculum mapping
        active_version = get_active_version_code()
        curriculum_map = db.query(QuestionCurriculum).filter(
            QuestionCurriculum.question_id == db_question.id,
            QuestionCurriculum.version_code == active_version
        ).first()
        if not curriculum_map:
            curriculum_map = QuestionCurriculum(
                question_id=db_question.id,
                version_code=active_version
            )
            db.add(curriculum_map)
        curriculum_map.compulsory = category_compulsory
        curriculum_map.chapter = category_chapter
        curriculum_map.knowledge = category_knowledge
        
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

@app.post("/api/questions/{question_id}/figure_align")
def update_question_figure_align(
    question_id: int,
    figure_align: str = Form("right"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    if figure_align not in ["right", "center", "bottom_right"]:
        figure_align = "right"
    db_question.figure_align = figure_align
    db.commit()
    db.refresh(db_question)
    return {"status": "success", "question_id": question_id, "figure_align": figure_align}

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
            physical_path = os.path.join(str(PROJECT_ROOT), rel_path)
            if rel_path.startswith(f"{UPLOAD_DIR_REL}/") and os.path.exists(physical_path):
                try:
                    os.remove(physical_path)
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

# Backward-compatible names; authoritative data lives in JSON resources.
RENJIAO_A_CURRICULUM = load_curriculum("A")
RENJIAO_B_CURRICULUM = load_curriculum("B")
SUJIAO_CURRICULUM = load_curriculum("S")
HUJIAO_CURRICULUM = load_curriculum("H")

METADATA_FILE = str(DATA_BACKUP_DIR / ("custom_metadata_test.json" if IS_TESTING else "custom_metadata.json"))
METADATA_CACHE = {}

def get_current_curriculum():
    return METADATA_CACHE.get("curriculum", RENJIAO_A_CURRICULUM)

def load_or_init_metadata():
    global METADATA_CACHE
    default_metadata = build_default_metadata("A")
    
    # Ensure backup directory exists
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Verify schema
                if isinstance(loaded, dict) and "question_types" in loaded and "difficulties" in loaded and "curriculum" in loaded:
                    # Self-heal metadata file (e.g. add 常规题, update simplified book names)
                    modified = False
                    has_normal = any(d.get("value") == "normal" for d in loaded.get("difficulties", []))
                    if not has_normal:
                        loaded["difficulties"].insert(1, {"value": "normal", "label": "常规题", "color": "text-blue-600 bg-blue-50 border-blue-200"})
                        modified = True
                        
                    curriculum = loaded.get("curriculum", {})
                    mappings = {
                        "选择性必修一": "选修一",
                        "选择性必修二": "选修二",
                        "选择性必修三": "选修三",
                        "必修第一册": "必修一",
                        "必修第二册": "必修二",
                        "必修第三册": "必修三",
                        "必修第四册": "必修四",
                    }
                    new_curriculum = {}
                    for comp, chapters in curriculum.items():
                        mapped_comp = mappings.get(comp, comp)
                        if mapped_comp != comp:
                            modified = True
                        new_curriculum[mapped_comp] = chapters
                    if modified:
                        loaded["curriculum"] = new_curriculum
                        try:
                            with open(METADATA_FILE, "w", encoding="utf-8") as f:
                                json.dump(loaded, f, ensure_ascii=False, indent=2)
                            print(f"[Metadata Self-Heal] Upgraded {METADATA_FILE} with simplified book names and normal difficulty.")
                        except Exception as e:
                            print(f"[Metadata Self-Heal Error] Failed to write updated metadata: {e}")
                    
                    METADATA_CACHE = loaded
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    heal_database_curriculum_names()
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
    heal_database_curriculum_names()

# Load metadata on startup
load_or_init_metadata()

def get_active_version_code() -> str:
    curriculum = METADATA_CACHE.get("curriculum", {})
    combined_chapters = ""
    for book_content in curriculum.values():
        if isinstance(book_content, dict):
            combined_chapters += " ".join(book_content.keys())
    if "第一章" in combined_chapters:
        return "B"
    if "第 1 章 集合与逻辑" in combined_chapters or "数学建模活动案例" in combined_chapters or "第 2 章 等式与不等式" in combined_chapters or "第 3 章 幂、指数与对数" in combined_chapters:
        return "H"
    if "第1章" in combined_chapters:
        return "S"
    return "A"

@app.get("/api/config/metadata")
def get_metadata_config():
    return METADATA_CACHE

@app.get("/api/config/curriculum-presets/{version}")
def get_curriculum_preset_config(version: str):
    try:
        return get_curriculum_preset(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

def route_chapter(comp: str, chap: str, know: str, target: str) -> tuple[str, str, str]:
    """跨大纲版本智能章节与小节路由翻译算法，返回 (new_compulsory, new_chapter, new_knowledge)"""
    combined = f"{comp} {chap} {know}"
    new_comp, new_chap = "", ""
    if target == "A":
        if "集合" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "2. 一元二次函数、方程和不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "4. 指数函数与对数函数"
        elif "三角函数" in combined or "三角恒等" in combined: new_comp, new_chap = "必修一", "5. 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "3. 函数的概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "复数" in combined: new_comp, new_chap = "必修二", "7. 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "8. 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "1. 空间向量与立体几何"
        elif "直线" in combined or "圆的方程" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "3. 圆锥曲线的方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "数列" in combined: new_comp, new_chap = "选修二", "4. 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "5. 一元函数的导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修三", "6. 计数原理"
        elif "概率" in combined or "随机变量" in combined or "分布" in combined: new_comp, new_chap = "选修三", "7. 随机变量及其分布"
        elif "统计" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修三", "8. 成对数据的统计分析"
        else: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
    elif target == "B":
        if "集合" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第二章 等式与不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修二", "第四章 指数函数、对数函数与幂函数"
        elif "三角函数" in combined: new_comp, new_chap = "必修三", "第七章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第三章 函数"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修四", "第九章 解三角形"
        elif "数量积" in combined or "三角恒等" in combined: new_comp, new_chap = "必修三", "第八章 向量的数量积与三角恒等变换"
        elif "平面向量" in combined: new_comp, new_chap = "必修二", "第六章 平面向量初步"
        elif "复数" in combined: new_comp, new_chap = "必修四", "第十章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修四", "第十一章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第一章 空间向量与立体几何"
        elif "直线" in combined or "圆" in combined or "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "数列" in combined: new_comp, new_chap = "选修三", "第五章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修三", "第六章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第三章 排列、组合与二项式定理"
        elif "随机变量" in combined or "条件概率" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第四章 概率与统计"
        elif "统计" in combined or "概率" in combined: new_comp, new_chap = "必修二", "第五章 统计与概率"
        else: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
    elif target == "S":
        if "集合" in combined: new_comp, new_chap = "必修一", "第1章 集合"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第2章 常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第3章 不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "第4章 指数与对数"
        elif "三角函数" in combined: new_comp, new_chap = "必修一", "第7章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第5章 函数概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "第11章 解三角形"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "第9章 平面向量"
        elif "三角恒等" in combined: new_comp, new_chap = "必修二", "第10章 三角恒等变换"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第12章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "第13章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修二", "第6章 空间向量与立体几何"
        elif "直线" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "第2章 圆与方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第3章 圆锥曲线与方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "数列" in combined: new_comp, new_chap = "选修一", "第4章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修一", "第5章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第7章 计数原理"
        elif "随机变量" in combined or "条件概率" in combined: new_comp, new_chap = "选修二", "第8章 概率"
        elif "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第9章 统计"
        elif "统计" in combined: new_comp, new_chap = "必修二", "第14章 统计"
        elif "概率" in combined: new_comp, new_chap = "必修二", "第15章 概率"
        else: new_comp, new_chap = "必修一", "第1章 集合"
    elif target == "H":
        if "集合与逻辑" in combined or ("集合" in combined and "选修" not in comp): new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第 2 章 等式与不等式"
        elif "幂、指数" in combined or "指数与对数" in combined or ("指数" in combined and "函数" not in combined) or ("对数" in combined and "函数" not in combined): new_comp, new_chap = "必修一", "第 3 章 幂、指数与对数"
        elif "幂函数" in combined or "指数函数" in combined or "对数函数" in combined: new_comp, new_chap = "必修一", "第 4 章 幂函数、指数函数与对数函数"
        elif "反函数" in combined or "函数的概念" in combined or ("函数" in combined and "三角" not in combined and "导数" not in combined and "选修" not in comp and "必修二" not in comp and "必修三" not in comp): new_comp, new_chap = "必修一", "第 5 章 函数的概念、性质及应用"
        elif "解三角形" in combined or "正弦定理" in combined or "余弦定理" in combined or "常用三角公式" in combined or ("三角" in combined and "函数" not in combined): new_comp, new_chap = "必修二", "第 6 章 三角"
        elif "三角函数" in combined: new_comp, new_chap = "必修二", "第 7 章 三角函数"
        elif "平面向量" in combined or ("向量" in combined and "空间" not in combined): new_comp, new_chap = "必修二", "第 8 章 平面向量"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第 9 章 复数"
        elif "空间直线" in combined or "空间点" in combined or ("立体几何" in combined and "空间向量" not in combined and "简单几何体" not in combined and "球" not in combined and "柱体" not in combined and "锥体" not in combined): new_comp, new_chap = "必修三", "第 10 章 空间直线与平面"
        elif "简单几何体" in combined or "柱体" in combined or "锥体" in combined or "多面体" in combined or "球" in combined: new_comp, new_chap = "必修三", "第 11 章 简单几何体"
        elif "古典概" in combined or "随机现象" in combined or ("概率" in combined and "条件概率" not in combined and "随机变量" not in combined and "分布" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 12 章 概率初步"
        elif "总体与样本" in combined or "抽样" in combined or "统计图表" in combined or ("统计" in combined and "成对" not in combined and "回归" not in combined and "列联表" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 13 章 统计"
        elif "红绿灯" in combined or "优惠券" in combined or "车辆转弯" in combined or "雨中行" in combined or "出租车" in combined or "家具" in combined or "登山" in combined or "包装彩带" in combined or "削菠萝" in combined or "高度测量" in combined or "外卖" in combined or "必修四" in comp: new_comp, new_chap = "必修四", "第 1 部分 数学建模活动案例"
        elif "平面直角坐标系中的直线" in combined or "直线与方程" in combined or ("直线" in combined and "空间" not in combined and "圆锥曲线" not in combined): new_comp, new_chap = "选修一", "第 1 章 平面直角坐标系中的直线"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined or ("圆" in combined and "圆锥曲线" in combined): new_comp, new_chap = "选修一", "第 2 章 圆锥曲线"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第 3 章 空间向量及其应用"
        elif "数列" in combined or "等差数列" in combined or "等比数列" in combined or "数学归纳法" in combined: new_comp, new_chap = "选修一", "第 4 章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "第 5 章 导数及其应用"
        elif "计数原理" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第 6 章 计数原理"
        elif "条件概率" in combined or "随机变量" in combined or "常用分布" in combined or "二项分布" in combined or "正态分布" in combined: new_comp, new_chap = "选修二", "第 7 章 概率初步（续）"
        elif "成对数据" in combined or "线性回归" in combined or "列联表" in combined or "独立性检验" in combined or "回归" in combined: new_comp, new_chap = "选修二", "第 8 章 成对数据的统计分析"
        elif "刹车距离" in combined or "易拉罐" in combined or "珠穆朗玛峰" in combined or "水葫芦" in combined or "铅球" in combined or "电梯调度" in combined or "存款计划" in combined or "民生巨变" in combined or "教室里的照明" in combined or "选修三" in comp: new_comp, new_chap = "选修三", "第 1 部分 数学建模活动案例"
        else: new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"

    active_v = get_active_version_code()
    if target == active_v:
        c_tree = METADATA_CACHE.get("curriculum", {})
    else:
        try:
            c_tree = load_curriculum(target)
        except ValueError:
            c_tree = {}
    
    valid_knows = c_tree.get(new_comp, {}).get(new_chap, [])
    new_know = know if know in valid_knows else ""
    return new_comp, new_chap, new_know

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
        source_version = get_active_version_code()
        # Detect target version
        curriculum = payload.get("curriculum", {})
        combined_chapters = ""
        for book_content in curriculum.values():
            if isinstance(book_content, dict):
                combined_chapters += " ".join(book_content.keys())
        if "第一章" in combined_chapters:
            target_version = "B"
        elif "第 1 章 集合与逻辑" in combined_chapters or "数学建模活动案例" in combined_chapters or "第 2 章 等式与不等式" in combined_chapters or "第 3 章 幂、指数与对数" in combined_chapters:
            target_version = "H"
        elif "第1章" in combined_chapters:
            target_version = "S"
        else:
            target_version = "A"

        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        METADATA_CACHE = payload
        print(f"[Metadata] Saved new custom metadata to {METADATA_FILE} (Detected version: {target_version})")
        
        # Incremental migration if curriculum version shifts
        if source_version != target_version:
            # Check and run incremental migration for all questions that do not have classifications for target_version
            all_questions = db.query(Question).all()
            for q in all_questions:
                target_map = db.query(QuestionCurriculum).filter(
                    QuestionCurriculum.question_id == q.id,
                    QuestionCurriculum.version_code == target_version
                ).first()
                if not target_map or not target_map.compulsory:
                    source_map = db.query(QuestionCurriculum).filter(
                        QuestionCurriculum.question_id == q.id,
                        QuestionCurriculum.version_code == source_version
                    ).first()
                    if source_map and source_map.compulsory:
                        new_comp, new_chap, new_know = route_chapter(
                            source_map.compulsory, source_map.chapter, source_map.knowledge, target_version
                        )
                        if not target_map:
                            target_map = QuestionCurriculum(
                                question_id=q.id,
                                version_code=target_version
                            )
                            db.add(target_map)
                        target_map.compulsory = new_comp
                        target_map.chapter = new_chap
                        target_map.knowledge = new_know
            db.commit()

        # Batch update main questions table categories with target version values
        from sqlalchemy import text
        db.execute(text("""
            UPDATE questions 
            SET category_compulsory = COALESCE((SELECT compulsory FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_chapter = COALESCE((SELECT chapter FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_knowledge = COALESCE((SELECT knowledge FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), '')
        """), {"v": target_version})
        db.commit()

        # Trigger export in background to update AI library with new mappings
        export_database_to_files(db)
        
        return {"status": "success", "message": "元数据配置保存成功！"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存元数据失败: {str(e)}")

# ----------------- DB Statistics API -----------------

@app.get("/api/stats")
def get_db_stats(db: Session = Depends(get_db)):
    try:
        total = db.query(Question).count()
        normal = db.query(Question).filter(Question.difficulty == "normal").count()
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
            
        def compulsory_sort_key(comp_name: str):
            if not comp_name or comp_name == "未分类":
                return (99, 99, comp_name or "")
            num_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6}
            is_comp = 0 if ("必修" in comp_name and "选" not in comp_name) else 1
            num = 99
            for k, v in num_map.items():
                if k in comp_name:
                    num = min(num, v)
            return (is_comp, num, comp_name)

        sorted_comp_chap_stats = {
            k: comp_chap_stats[k]
            for k in sorted(comp_chap_stats.keys(), key=compulsory_sort_key)
        }
            
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
            "normal_count": normal,
            "easy_error_count": easy_error,
            "challenge_count": challenge,
            "qiangji_count": qiangji,
            "compulsory_chapter_counts": sorted_comp_chap_stats,
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
        
        system_instructions = build_classification_system_prompt(get_current_curriculum())
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
    
    system_instructions = build_pdf_parse_system_prompt(
        get_current_curriculum(), generate_answers_bool
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
        
        system_instructions = build_import_parse_system_prompt(get_current_curriculum())

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
            src_path = os.path.join(str(PROJECT_ROOT), normalized_path)
            dest_rel_path = f"/{UPLOAD_DIR_REL}/{filename}"
            dest_path = os.path.join(UPLOAD_DIR, filename)
            
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
                src_path = os.path.join(str(PROJECT_ROOT), normalized_path)
                dest_rel_path = f"/{UPLOAD_DIR_REL}/{filename}"
                dest_path = os.path.join(UPLOAD_DIR, filename)
                
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
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    
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

@app.post("/api/paper/ai-select")
def ai_select_paper(payload: dict, db: Session = Depends(get_db)):
    """AI 智能选题：结合用户指定的 PREFER_SOLVE_MODEL 大模型与 math-teaching 教研引擎组卷"""
    try:
        prompt = payload.get("prompt", "").strip()
        question_type = payload.get("question_type", "")
        difficulty = payload.get("difficulty", "")
        compulsory = payload.get("compulsory", "")
        chapter = payload.get("chapter", "")
        knowledge = payload.get("knowledge", "")
        limit = int(payload.get("limit", 5))

        # 0. 自然语言意图智能分析 (NL Intent Parser)
        extracted_topics = []
        is_review_intent = False
        if prompt:
            is_review_intent = any(k in prompt for k in ['做过', '考过', '已抽过', '已用过', '复习', '旧题', '重做', '错题', '以往', '历史'])
            num_match = re.search(r'([一二三四五六七八九十1-9]+)\s*道', prompt)
            cn_to_num = {'一':1, '两':2, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
            if num_match:
                val = num_match.group(1)
                limit = cn_to_num.get(val, int(val) if val.isdigit() else limit)

            if not question_type:
                if '填空' in prompt: question_type = 'fill_in_blank'
                elif '单选' in prompt: question_type = 'single_choice'
                elif '多选' in prompt: question_type = 'multi_choice'
                elif '解答' in prompt: question_type = 'detailed_answer'

            known_topics = ['立体几何', '集合', '函数', '导数', '数列', '三角函数', '平面向量', '概率', '解析几何', '圆锥曲线', '复数', '不等式', '排列组合']
            extracted_topics = [t for t in known_topics if t in prompt]

        # 1. 结构化过滤基础题目池
        query = db.query(Question)
        if question_type:
            query = query.filter(Question.question_type == question_type)
        if difficulty:
            query = query.filter(Question.difficulty == difficulty)
        if compulsory:
            query = query.filter(Question.category_compulsory == compulsory)
        if chapter:
            query = query.filter(Question.category_chapter == chapter)
        if knowledge:
            query = query.filter(Question.category_knowledge == knowledge)
            
        if is_review_intent:
            # 复习/旧题模式：优先提取已使用频次高的题目
            review_query = query.filter(Question.usage_count > 0).order_by(Question.usage_count.desc(), Question.id.desc())
            candidates = review_query.limit(35).all()
            if not candidates:
                candidates = query.order_by(Question.id.desc()).limit(35).all()
        else:
            # 默认鲜活模式：优先提取从未被使用过的冷门题目
            candidates = query.order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()
            if not candidates:
                candidates = db.query(Question).order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()

        # 2. 严格按用户设置解析指定的 PREFER_SOLVE_MODEL (不进行任何跨提供商串用或自动轮询)
        target_model = os.getenv("PREFER_SOLVE_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "deepseek-chat"
        api_key = None
        api_base = None
        model_name = target_model
        provider_name = "DeepSeek"

        if "/" in target_model:
            parts = target_model.split("/", 1)
            prefix, model_name = parts[0].upper(), parts[1]
            if prefix == "SILICONFLOW":
                api_key = os.getenv("SILICONFLOW_API_KEY")
                api_base = "https://api.siliconflow.cn/v1"
                provider_name = "硅基流动"
            elif prefix == "BAILIAN":
                api_key = os.getenv("ALI_BAILIAN_API_KEY")
                api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
                provider_name = "阿里百炼"
                if model_name == "qwen3.7-max": model_name = "qwen-max"
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
            if "qwen" in target_model.lower():
                api_key = os.getenv("ALI_BAILIAN_API_KEY")
                api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
                provider_name = "阿里百炼"
                model_name = "qwen-max" if target_model == "qwen3.7-max" else target_model
            else:
                api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("ALI_BAILIAN_API_KEY") or os.getenv("SILICONFLOW_API_KEY")
                if os.getenv("DEEPSEEK_API_KEY"):
                    api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
                    provider_name = "DeepSeek"
                elif os.getenv("ALI_BAILIAN_API_KEY"):
                    api_base = os.getenv("ALI_BAILIAN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
                    provider_name = "阿里百炼"
                    model_name = "qwen-max"
                elif os.getenv("SILICONFLOW_API_KEY"):
                    api_base = "https://api.siliconflow.cn/v1"
                    provider_name = "硅基流动"
                    model_name = "Qwen/Qwen2.5-72B-Instruct"

        api_error_detail = None
        if prompt and candidates:
            if not api_key or not api_base:
                api_error_detail = f"指定的 AI 解题模型 ({target_model}) 未在配置文件 (.env) 中检测到有效的 API Key 或 Base URL。"
            else:
                candidate_items = []
                for q in candidates:
                    clean_stem = re.sub(r'[\r\n]+', ' ', q.content[:80])
                    candidate_items.append({
                        "id": q.id,
                        "question_type": q.question_type,
                        "difficulty": q.difficulty,
                        "usage_count": q.usage_count or 0,
                        "knowledge": q.category_knowledge or q.category_chapter or "通用知识点",
                        "tags": q.tags or "",
                        "stem_excerpt": clean_stem
                    })

                system_prompt, user_content = build_paper_selection_prompts(
                    teacher_prompt=prompt,
                    limit=limit,
                    candidates=candidate_items,
                    is_review_intent=is_review_intent,
                )

                try:
                    url = f"{api_base.rstrip('/')}/chat/completions"
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    payload_data = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.3
                    }
                    response = robust_request_post(url, headers=headers, json=payload_data, timeout=20)
                    if response.status_code == 200:
                        res_json = response.json()
                        raw_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        if raw_content.startswith("```"):
                            raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                            raw_content = re.sub(r"\s*```$", "", raw_content)
                        
                        parsed = json.loads(raw_content)
                        selected_ids = parsed.get("selected_ids", [])
                        ai_analysis = parsed.get("ai_analysis", "")
                        
                        if selected_ids and isinstance(selected_ids, list):
                            db_selected = db.query(Question).filter(Question.id.in_(selected_ids)).all()
                            id_map = {q.id: q for q in db_selected}
                            seq_map = get_seq_mapping(db)
                            final_questions = [{**id_map[qid].to_dict(), "seq_num": seq_map.get(qid)} for qid in selected_ids if qid in id_map]
                            
                            if final_questions:
                                return {
                                    "status": "success",
                                    "data": final_questions,
                                    "count": len(final_questions),
                                    "ai_analysis": ai_analysis,
                                    "model_used": f"{provider_name} ({model_name})",
                                    "fallback": False
                                }
                    else:
                        api_error_detail = f"{provider_name} API 响应异常 (HTTP {response.status_code}: {response.text.strip()[:150]})"
                except Exception as llm_err:
                    api_error_detail = f"{provider_name} API 请求失败: {str(llm_err)}"

        # 3. 降级本地算法（带明确错误反馈）
        fallback_questions = []
        if extracted_topics:
            for topic in extracted_topics:
                sub_query = db.query(Question)
                if question_type:
                    sub_query = sub_query.filter(Question.question_type == question_type)
                sub_query = sub_query.filter(
                    (Question.content.like(f"%{topic}%")) |
                    (Question.category_chapter.like(f"%{topic}%")) |
                    (Question.category_knowledge.like(f"%{topic}%")) |
                    (Question.tags.like(f"%{topic}%"))
                )
                order_clause = Question.usage_count.desc() if is_review_intent else Question.usage_count.asc()
                for q in sub_query.order_by(order_clause, Question.id.desc()).all():
                    if q not in fallback_questions:
                        fallback_questions.append(q)

        # 补足数量
        if len(fallback_questions) < limit:
            for q in candidates:
                if q not in fallback_questions:
                    fallback_questions.append(q)
                if len(fallback_questions) >= limit:
                    break

        seq_map = get_seq_mapping(db)
        result = [{**q.to_dict(), "seq_num": seq_map.get(q.id)} for q in fallback_questions[:limit]]
        
        topic_str = "、".join(extracted_topics) if extracted_topics else "通用知识点"
        err_banner = f"⚠️ 【AI 解题模型调用未成功】: {api_error_detail}\n系统已为您自动启动本地教研算法，根据意图（{topic_str}）在本地题库中筛选并组合了 {len(result)} 道精选题目。" if api_error_detail else f"【本地智能筛选分析】已为您自动识别意图（{topic_str}），从题库中精准挑选并组合了鲜活试题。"
        
        return {
            "status": "success",
            "data": result,
            "count": len(result),
            "ai_analysis": err_banner,
            "model_used": f"⚠️ 模型调用失败 ({target_model}) ➔ 退回本地算法" if api_error_detail else "本地算法",
            "fallback": True
        }
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"AI 智能选题失败: {str(e)}"}, status_code=500)


def post_process_pdf_parsed_questions(parsed_questions: list, paper_title: str, task_id: str = None, ocr_results: list = None) -> list:
    """PDF 专属解析卡片后处理：正则搜寻 /tmp/ 下的图片，以及将未解析的图n占位符智能映射回真实的裁剪插图图片，
    最后将其灌入 image_paths 数组中，并在 content 中静默清除以配合布局展示。支持文本重合度兜底映射，防大模型删除路径！"""
    import re
    import os
    import glob

    # 0. 规范化所有拆解题目的填空下划线为 \fillin 宏
    for q in parsed_questions:
        if q.get("content"):
            q["content"] = normalize_fillin_macro(q.get("content", ""))

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
                full_path = os.path.join(str(PROJECT_ROOT), normalized_path)
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


# ----------------- Paper Generation API Endpoints -----------------

@app.get("/api/paper/questions")
def get_paper_questions(ids: str = "", db: Session = Depends(get_db)):
    """获取指定 ID 列表的完整题目数据（组卷试题篮批量拉取）"""
    if not ids:
        return {"status": "success", "data": []}
    try:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            return {"status": "success", "data": []}
        questions = db.query(Question).filter(Question.id.in_(id_list)).all()
        q_map = {q.id: q.to_dict() for q in questions}
        result = [q_map[qid] for qid in id_list if qid in q_map]
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/paper/save")
def save_paper(payload: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """保存排版好的试卷，自增被选中题目的 usage_count"""
    try:
        title = payload.get("title", "未命名试卷").strip()
        subtitle = payload.get("subtitle", "").strip()
        paper_type = payload.get("paper_type", "exam")
        questions_payload = payload.get("questions", [])
        
        if not questions_payload:
            return JSONResponse(content={"status": "error", "message": "试卷中至少需要包含一道题目"}, status_code=400)
            
        total_score = sum(int(q.get("score", 5)) for q in questions_payload)
        
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["show_secret"] = payload.get("show_secret", True)
        meta["show_notice"] = payload.get("show_notice", True)

        paper = Paper(
            title=title,
            subtitle=subtitle,
            paper_type=paper_type,
            total_score=total_score,
            metadata_json=json.dumps(meta)
        )
        db.add(paper)
        db.flush()
        
        for idx, item in enumerate(questions_payload):
            qid = int(item.get("id"))
            score = int(item.get("score", 5))
            pq = PaperQuestion(
                paper_id=paper.id,
                question_id=qid,
                order_index=idx + 1,
                score=score
            )
            db.add(pq)
            
            q = db.query(Question).filter(Question.id == qid).first()
            if q:
                q.usage_count = (q.usage_count or 0) + 1
                
        db.commit()
        background_tasks.add_task(export_database_to_files)
        return {"status": "success", "message": "试卷保存成功！", "paper_id": paper.id}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存试卷失败: {str(e)}"}, status_code=500)

@app.get("/api/papers")
def list_papers(db: Session = Depends(get_db)):
    """获取所有历史试卷列表"""
    try:
        papers = db.query(Paper).order_by(Paper.created_at.desc()).all()
        result = []
        for p in papers:
            q_count = db.query(PaperQuestion).filter(PaperQuestion.paper_id == p.id).count()
            d = p.to_dict()
            d["question_count"] = q_count
            result.append(d)
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/papers/{paper_id}")
def get_paper_detail(paper_id: int, db: Session = Depends(get_db)):
    """获取单张试卷的详细信息及关联题目列表（用于一键载入）"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
        
        pqs = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).order_by(PaperQuestion.order_index.asc()).all()
        
        questions_list = []
        for pq in pqs:
            q = db.query(Question).filter(Question.id == pq.question_id).first()
            if q:
                questions_list.append({
                    "id": q.id,
                    "score": pq.score,
                    "question": q.to_dict()
                })
                
        result = paper.to_dict()
        result["questions"] = questions_list
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """删除指定的历史试卷，并同步扣减关联题目的 usage_count"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
            
        pqs = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).all()
        for pq in pqs:
            q = db.query(Question).filter(Question.id == pq.question_id).first()
            if q and q.usage_count:
                q.usage_count = max(0, q.usage_count - 1)
                
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
        db.delete(paper)
        db.commit()
        background_tasks.add_task(export_database_to_files)
        return {"status": "success", "message": "试卷记录已成功删除"}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/paper/export/tex")
def export_paper_tex(payload: dict, db: Session = Depends(get_db)):
    """导出 LaTeX 源码 ZIP 压缩包"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        q_ids = [int(q.get("id")) for q in questions_input if q.get("id")]
        questions_db = db.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q.to_dict() for q in questions_db}
        
        questions_data = []
        for item in questions_input:
            qid = int(item.get("id"))
            if qid in q_map:
                q_dict = dict(q_map[qid])
                if item.get("figure_align"):
                    q_dict["figure_align"] = item.get("figure_align")
                q_item = {
                    "question": q_dict,
                    "score": int(item.get("score", 5))
                }
                if item.get("solution_space"):
                    q_item["solution_space"] = item.get("solution_space")
                questions_data.append(q_item)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice)
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR)
        zip_bytes = create_tex_zip_package(title, tex_main, tex_ans, image_paths, answer_sheet_tex=tex_answer_sheet)
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        encoded_filename = quote(f"{safe_title}.zip")
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_export.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成 LaTeX 源码失败: {str(e)}"}, status_code=500)

@app.post("/api/paper/export/bundle")
def export_paper_bundle(payload: dict, db: Session = Depends(get_db)):
    """一键导出合并全套 Zip 压缩包（包含 LaTeX 源码、相关插图以及已编译好的 PDF）"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        q_ids = [int(q.get("id")) for q in questions_input if q.get("id")]
        questions_db = db.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q.to_dict() for q in questions_db}
        
        questions_data = []
        for item in questions_input:
            qid = int(item.get("id"))
            if qid in q_map:
                q_dict = dict(q_map[qid])
                if item.get("figure_align"):
                    q_dict["figure_align"] = item.get("figure_align")
                q_item = {
                    "question": q_dict,
                    "score": int(item.get("score", 5))
                }
                if item.get("solution_space"):
                    q_item["solution_space"] = item.get("solution_space")
                questions_data.append(q_item)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice)
        
        if paper_type == "exam_19":
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR)

        # Pre-compile PDFs
        main_pdf_bytes, _ = compile_tex_to_pdf(tex_main, image_paths)
        ans_pdf_bytes, _ = compile_tex_to_pdf(tex_ans, image_paths)
        if paper_type == "exam_19" and tex_answer_sheet:
            answer_sheet_pdf_bytes, _ = compile_tex_to_pdf(tex_answer_sheet, image_paths)
        else:
            answer_sheet_pdf_bytes = None

        zip_bytes = create_full_bundle_zip_package(
            title, tex_main, tex_ans, image_paths,
            answer_sheet_tex=tex_answer_sheet,
            main_pdf_bytes=main_pdf_bytes,
            ans_pdf_bytes=ans_pdf_bytes,
            answer_sheet_pdf_bytes=answer_sheet_pdf_bytes
        )
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        filename = f"{safe_title}_全套归档.zip"
        encoded_filename = quote(filename)
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_bundle.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成全套合并包失败: {str(e)}"}, status_code=500)

@app.post("/api/paper/export/pdf")
def export_paper_pdf(payload: dict, db: Session = Depends(get_db)):
    """在线静默编译生成高清 PDF"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam_19")
        target = payload.get("target", "paper")  # "paper" or "sheet"
        include_answers = payload.get("include_answers", False)
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        q_ids = [int(q.get("id")) for q in questions_input if q.get("id")]
        questions_db = db.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q.to_dict() for q in questions_db}
        
        questions_data = []
        for item in questions_input:
            qid = int(item.get("id"))
            if qid in q_map:
                q_dict = dict(q_map[qid])
                if item.get("figure_align"):
                    q_dict["figure_align"] = item.get("figure_align")
                q_item = {
                    "question": q_dict,
                    "score": int(item.get("score", 5))
                }
                if item.get("solution_space"):
                    q_item["solution_space"] = item.get("solution_space")
                questions_data.append(q_item)
                
        if target == "sheet":
            tex_content = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_content = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=include_answers, show_secret=show_secret, show_notice=show_notice)

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR)
        pdf_bytes, log_or_err = compile_tex_to_pdf(tex_content, image_paths)
        
        if pdf_bytes:
            filename = f"sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf" if target == "sheet" else f"paper_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return Response(content=pdf_bytes, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            })
        else:
            return JSONResponse(content={"status": "error", "message": log_or_err}, status_code=400)
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"编译 PDF 异常: {str(e)}"}, status_code=500)

# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
