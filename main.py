import os
import atexit
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
from contextlib import asynccontextmanager
from pathlib import Path

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

from mathbank.database import Question, QuestionCurriculum, Paper, PaperQuestion, engine, get_db, init_db
from mathbank.paper_helper import build_latex_document, build_answer_sheet_latex, compile_tex_to_pdf, create_tex_zip_package, create_full_bundle_zip_package, collect_referenced_images, build_restricted_tex_environment
from mathbank.word_export_helper import build_word_document, create_word_bundle_zip
from mathbank.runtime_components import (
    PANDOC_INSTALL_MANAGER,
    pandoc_status,
)
from mathbank.sync_helper import export_database_to_files
from mathbank.backup import acquire_runtime_lock, create_full_backup_if_due
from mathbank.health import readiness_report
from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)
from mathbank.docx_helper import extract_docx_markdown
from mathbank.content_locks import lock_visible_math, restore_visible_math
from mathbank.tex_helper import (
    MAX_TEX_BYTES,
    decode_and_prepare_tex,
    prepare_tex_source,
    tex_asset_basename,
    tex_asset_references_match,
)
from mathbank.latex_diagnostics import (
    build_local_latex_diagnostic,
    merge_ai_latex_diagnostic,
)
from mathbank.ai_json import parse_ai_json
from mathbank.ai_http import (
    post_chat_completion,
)
from mathbank.ai_providers import (
    MultimodalProviderConfig,
    apply_bailian_thinking_policy,
    inject_reasoning_effort,
    resolve_draw_provider,
    resolve_ocr_fallbacks,
    resolve_ocr_provider,
    resolve_text_provider,
)
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
    build_latex_error_explanation_prompts,
    build_paper_selection_prompts,
    build_pdf_parse_system_prompt,
    build_tikz_correction_prompt,
    build_tikz_draw_prompt,
)
from mathbank.question_types import (
    detect_structured_question_form,
    normalize_ai_question_form,
)
import shutil
from mathbank.pdf_inspector_helper import (
    is_pdf_inspector_available,
    inspect_and_extract_pdf,
    merge_pdf_page_texts,
)
from mathbank.paths import (
    DATABASE_PATH,
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
from mathbank.asset_security import (
    AssetSecurityError,
    InvalidImageError,
    MAX_OCR_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_SINGLE_IMAGE_BYTES,
    UploadTooLargeError,
    harden_private_path,
    normalize_answer_tikz_assets,
    normalize_content_tikz_assets,
    normalize_optional_upload_asset_reference,
    normalize_raster_image,
    normalize_upload_asset_reference,
    normalize_upload_asset_references,
    read_stream_limited,
    resolve_upload_asset,
    write_private_text_atomic,
)

# Load environment variables
load_dotenv(ENV_FILE)
harden_private_path(ENV_FILE)

# Unique server instance ID generated per process launch/restart
SERVER_INSTANCE_ID = str(uuid.uuid4())

# Hold an OS-backed project lock before the first database access.  The restore
# CLI takes the same lock, so a manually started uvicorn process is protected
# even when no launcher PID file exists.  Unit tests use isolated databases and
# exercise the lock helper directly instead of holding the production lock.
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()
if _RUNTIME_LOCK is not None:
    atexit.register(_RUNTIME_LOCK.close)

# Initialize DB
init_db()


def schedule_database_export(
    background_tasks: BackgroundTasks, *, operation: str
) -> None:
    """Best-effort export scheduling after a database transaction commits."""

    def run_export_safely() -> None:
        try:
            export_database_to_files()
        except Exception as exc:
            print(
                f"[Database Export] Post-commit export failed for {operation} "
                f"(type={type(exc).__name__}); the next write/startup export can retry."
            )

    try:
        background_tasks.add_task(run_export_safely)
    except Exception as exc:
        print(
            f"[Database Export] Post-commit scheduling failed for {operation} "
            f"(type={type(exc).__name__}); the next write/startup export can retry."
        )


def print_startup_diagnostics():
    """打印不扫描 PATH、不阻塞就绪的基础启动诊断。"""
    is_venv = sys.prefix != sys.base_prefix
    env_type = f"虚拟环境 ({os.path.basename(sys.prefix)})" if is_venv else "全局/系统环境"
    pdf_insp_ok = is_pdf_inspector_available()

    print("=" * 64, flush=True)
    print("      本地数学题库教研系统 (MathBank) 启动自检与诊断面板", flush=True)
    print("=" * 64, flush=True)
    print(f"  • Python 运行环境   : {sys.version.split()[0]} [{env_type}]", flush=True)
    print(f"  • Python 可执行路径 : {sys.executable}", flush=True)
    if is_venv:
        print(f"  • 虚拟环境根目录   : {sys.prefix}", flush=True)
    print(f"  • PDF Inspector 引擎: {'🚀 已就绪 (原生矢量试卷毫秒级直提)' if pdf_insp_ok else '⚠️ 未安装 (已自动平滑降级至 VLM 多模态 OCR)'}", flush=True)
    print("  • 可选排版工具   : 服务就绪后后台检测", flush=True)
    print(f"  • SQLite 本地数据库 : {DATABASE_PATH}", flush=True)
    print(f"  • 项目静态与根路径 : {PROJECT_ROOT}", flush=True)
    print("=" * 64, flush=True)


print_startup_diagnostics()


def print_optional_tool_diagnostics():
    """服务就绪后再扫描可选工具，避免慢 PATH 阻断启动。"""

    latex_engine = shutil.which("xelatex") or shutil.which("pdflatex")
    pandoc_path = os.getenv("MATHBANK_PANDOC_PATH", "").strip() or shutil.which("pandoc")
    try:
        import pymupdf  # noqa: F401

        pymupdf_status = "ready"
    except ImportError:
        pymupdf_status = "missing"
    print(
        "[Optional Tools] "
        f"latex={latex_engine or 'missing'}, "
        f"pandoc={pandoc_path or 'missing'}, "
        f"pymupdf={pymupdf_status}",
        flush=True,
    )

def heal_database_curriculum_names():
    from mathbank.database import SessionLocal
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

UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"
UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)

def load_or_create_local_token() -> str:
    token_dir = str(SYSTEM_GENERATED_DIR)
    os.makedirs(token_dir, exist_ok=True)
    harden_private_path(token_dir, directory=True)
    token_file = os.path.join(token_dir, "local_token")
    if os.path.exists(token_file):
        try:
            harden_private_path(token_file)
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            print(f"[Security] Failed to read persistent token: {e}")
            
    # Generate new token
    token = secrets.token_hex(16)
    try:
        write_private_text_atomic(token_file, token)
    except Exception as e:
        print(f"[Security] Failed to write persistent token: {e}")
    return token

LOCAL_TOKEN = load_or_create_local_token()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    """在模块完整导入后再启动低优先级维护任务。"""

    if IS_TESTING:
        yield
        return

    DOCUMENT_TASKS.start_maintenance(interval_seconds=60.0)
    threading.Thread(
        target=start_startup_cleanup,
        name="mathbank-post-startup-maintenance",
        daemon=True,
    ).start()
    try:
        yield
    finally:
        DOCUMENT_TASKS.shutdown(wait=False)


app = FastAPI(title="本地化数学题库管理系统 API", lifespan=app_lifespan)

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
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.url.path != "/api/heartbeat":
            token = request.headers.get("X-Local-Token")
            if not token or not secrets.compare_digest(token, LOCAL_TOKEN):
                print(f"[Security Alert] Blocked {request.method} {request.url.path} - invalid local token")
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
    """扫描 static/uploads 目录及其 tmp 子目录，安全彻底擦除未被数据库引用的孤儿图片与旧残留临时图片"""
    try:
        from mathbank.database import SessionLocal, Question
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
                        
            # 2. 遍历本地图片目录及 tmp 子目录
            upload_dir = UPLOAD_DIR
            if not os.path.exists(upload_dir):
                return
                
            cleaned_count = 0
            now = time.time()
            one_hour_seconds = 3600
            
            # 清理 static/uploads/ 根目录下未引用的孤儿图片
            for filename in os.listdir(upload_dir):
                full_path = os.path.join(upload_dir, filename)
                if os.path.isfile(full_path) and not filename.startswith("."):
                    local_rel_path = f"{UPLOAD_DIR_REL}/{filename}".lower()
                    if local_rel_path not in referenced_images:
                        try:
                            mtime = os.path.getmtime(full_path)
                            if now - mtime > one_hour_seconds:
                                os.remove(full_path)
                                cleaned_count += 1
                        except Exception:
                            pass

            # 清理 static/uploads/tmp/ 子目录下残留的所有旧拆卷/OCR临时切片图
            tmp_dir = os.path.join(upload_dir, "tmp")
            if os.path.exists(tmp_dir):
                for filename in os.listdir(tmp_dir):
                    full_path = os.path.join(tmp_dir, filename)
                    if os.path.isfile(full_path) and not filename.startswith("."):
                        local_rel_path = f"{UPLOAD_DIR_REL}/tmp/{filename}".lower()
                        if local_rel_path not in referenced_images:
                            try:
                                mtime = os.path.getmtime(full_path)
                                if now - mtime > 600:  # 超过 10 分钟未被使用的 tmp 切片立即清理
                                    os.remove(full_path)
                                    cleaned_count += 1
                            except Exception:
                                pass
                        
            if cleaned_count > 0:
                print(f"[Storage Cleanup] 成功检测并清除 {cleaned_count} 个残留的旧临时图片与孤儿文件，磁盘无痕瘦身成功！")
        finally:
            db.close()
    except Exception as e:
        print(f"[Storage Cleanup Error] 执行静默图片净化时发生异常: {str(e)}")

def recalibrate_usage_counts():
    """自动校准全库题目的引用频次 usage_count，修正由于历史删除试卷遗留的计数差异"""
    try:
        from mathbank.database import SessionLocal, Question, PaperQuestion
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
    # Lifespan 已确保模块完整导入；再让出短暂时间给首屏请求。
    time.sleep(2.5)
    try:
        backup_path = create_full_backup_if_due()
        if backup_path:
            print(f"[Backup] 已创建并验证每日完整备份: {backup_path.name}")
    except Exception as exc:
        print(f"[Backup Error] 每日完整备份失败: {type(exc).__name__}: {exc}")
        print_optional_tool_diagnostics()
        return
    heal_database_curriculum_names()
    clean_orphaned_images()
    recalibrate_usage_counts()
    print_optional_tool_diagnostics()


# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

# Bounded document task manager shared by PDF and Word imports.
DOCUMENT_TASKS = TaskManager(
    max_workers=2,
    max_queue=4,
    terminal_ttl_seconds=3600,
    temp_asset_cleanup=lambda paths: _delete_task_temp_assets(paths),
)
PDF_OCR_SEMAPHORE = threading.BoundedSemaphore(4)
MAX_PDF_TASK_PAGES = 80

def get_seq_mapping(db: Session, question_ids=None):
    """Map physical ID order to the user-facing contiguous sequence number."""

    if question_ids is None:
        all_q = db.query(Question.id).order_by(Question.id.asc()).all()
        return {q_id: idx + 1 for idx, (q_id,) in enumerate(all_q)}

    normalized_ids = {int(question_id) for question_id in question_ids}
    if not normalized_ids:
        return {}
    from sqlalchemy import func

    ranked = db.query(
        Question.id.label("question_id"),
        func.row_number().over(order_by=Question.id.asc()).label("seq_num"),
    ).subquery()
    rows = db.query(ranked.c.question_id, ranked.c.seq_num).filter(
        ranked.c.question_id.in_(normalized_ids)
    ).all()
    return {question_id: int(seq_num) for question_id, seq_num in rows}

# ----------------- Static Files & Index -----------------


@app.get("/healthz", include_in_schema=False)
def healthz():
    report = readiness_report(engine)
    return JSONResponse(report, status_code=200 if report["ready"] else 503)

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
def upload_image(file: UploadFile = File(...)):
    try:
        raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
        normalized = normalize_raster_image(raw)

        # Never trust the client suffix.  The server-generated extension and
        # re-encoded bytes prevent HTML/SVG/polyglot files being served same-origin.
        filename = f"{uuid.uuid4().hex}{normalized.extension}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(normalized.data)

        relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
        return {
            "status": "success",
            "file_path": relative_path,
            "filename": file.filename
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "图片过大，请上传 10MB 以内的文件。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"图片上传失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        print(f"[Upload Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": "文件上传失败，请检查文件后重试。"},
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


def ocr_via_provider(
    image_path: str,
    provider: MultimodalProviderConfig,
    include_illustration_box: bool = False,
) -> str:
    """Use one resolved multimodal provider for formula and text OCR."""
    import base64

    print(
        f"[OCR Flow] 正在向 {provider.provider_label} 提交多模态识别任务: "
        f"{image_path} (模型: {provider.model_name})..."
    )
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")

    prompt = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt += ILLUSTRATION_BOX_PROMPT

    payload = {
        "model": provider.model_name,
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

    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="ocr",
    )

    timeout = 240
    # Chat-completion POSTs are not idempotent: a read timeout can happen after
    # the provider has accepted (and billed) the request.  Do not automatically
    # send the same image two or three times.  The shared transport still
    # retries a connection-establishment failure where no response was read.
    response = post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} API 识别失败: HTTP {response.status_code}"
        )

    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(
                f"{provider.provider_label} 返回的数据中未包含 Choices 结果。"
            )
    except Exception as e:
        raise RuntimeError(
            f"解析 {provider.provider_label} 响应数据失败: {str(e)}"
        )


def extract_tikz_source(ai_message: str) -> str:
    """Extract one complete TikZ environment from a model response."""

    message = str(ai_message or "").strip()
    match = re.search(
        r"\\begin\s*\{\s*tikzpicture\s*\}.*?\\end\s*\{\s*tikzpicture\s*\}",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()

    match_block = re.search(
        r"```(?:latex|tex)?\s*(.*?)```",
        message,
        re.DOTALL | re.IGNORECASE,
    )
    if match_block:
        code = match_block.group(1).strip()
        if code and "tikzpicture" not in code.lower():
            return f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"

    raise RuntimeError("绘图模型未返回完整的 tikzpicture 源码。")


def request_tikz_completion(provider, content_payload, *, timeout: int = 120) -> str:
    """Send one TikZ model request with the shared reasoning and parser policy."""

    payload = {
        "model": provider.model_name,
        "messages": [{"role": "user", "content": content_payload}],
        "stream": False,
    }
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="draw",
    )
    response = post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} 绘图接口返回 HTTP {response.status_code}"
        )
    choices = response.json().get("choices", [])
    if not choices:
        raise RuntimeError(f"{provider.provider_label} 绘图接口未返回 choices。")
    ai_message = choices[0].get("message", {}).get("content", "")
    return extract_tikz_source(ai_message)


def draw_tikz_via_high_model(
    image_path: Optional[str],
    prefer_draw: str,
    latex_content: Optional[str] = None,
    *,
    instruction: str = "",
    existing_tikz: str = "",
    require_image_support: bool = False,
) -> Optional[str]:
    """使用指定的高级绘图模型（多模态或纯文本自适应）生成 TikZ 代码。"""
    import base64
    provider = resolve_draw_provider(prefer_draw)
    if not provider.api_key:
        print(
            f"[High Model Draw] 未配置 {provider.credential_label}，降级跳过。"
        )
        return None

    has_reference_image = bool(image_path)
    if has_reference_image and require_image_support and not provider.supports_image_input:
        raise RuntimeError(
            f"当前绘图模型 {provider.model_name} 不支持参考图输入，"
            "请在 API 设置中选择支持图像的 TikZ 绘图模型。"
        )
    use_image_input = has_reference_image and provider.supports_image_input

    if use_image_input:
        # 多模态图文输入模式
        try:
            with open(image_path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[High Model Draw] 读取裁剪小图 Base64 失败: {str(e)}")
            return None
            
        suffix = Path(image_path).suffix.lower()
        image_media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=True,
            instruction=instruction,
            existing_tikz=existing_tikz,
        )
        
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_media_type};base64,{encoded_image}"
                }
            }
        ]
    else:
        # 纯文本推理模式。带有视觉能力的模型也可以在没有参考图时走此分支。
        if not any((latex_content, instruction, existing_tikz)):
            print("[High Model Draw] 绘图模型未获得任何可用输入，跳过。")
            return None
            
        prompt = build_tikz_draw_prompt(
            latex_content,
            multimodal=False,
            instruction=instruction,
            existing_tikz=existing_tikz,
        )
        content_payload = prompt

    try:
        return request_tikz_completion(
            provider,
            content_payload,
            timeout=120,
        )
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
        # OCR route is synchronous and runs in FastAPI's worker pool.  Stream
        # only up to the endpoint cap, then fully decode/re-encode the image.
        file_bytes = read_stream_limited(file.file, MAX_OCR_IMAGE_BYTES)
        normalized = normalize_raster_image(file_bytes)
        image = Image.open(io.BytesIO(normalized.data)).convert("RGB")
        
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

        known_ocr_engines = {
            "siliconflow",
            "ali_bailian",
            "bailian",
            "zhongzhan",
            "zhongzhan_gpt",
            "zhongzhan_claude",
        }
        if engine in known_ocr_engines:
            ocr_provider = resolve_ocr_provider(engine)
            if ocr_provider.api_key and ocr_provider.api_key.strip():
                try:
                    latex_content = ocr_via_provider(
                        temp_filepath,
                        ocr_provider,
                        include_illustration_box=True,
                    )
                    confidence = 0.99
                    provider = (
                        f"{ocr_provider.provider_label} "
                        f"({ocr_provider.model_name})"
                    )
                except Exception as e:
                    print(
                        f"[{ocr_provider.provider_label} 识别失败] "
                        f"发生异常: {str(e)}"
                    )
            else:
                print(
                    f"[OCR Flow Warning] 未配置 {ocr_provider.credential_label}，"
                    "当前识图引擎无法启动！"
                )

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
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "公式识图失败: 图片不能超过 10MB。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=400,
        )
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
def ai_solve(
    content: str = Form(...),
    question_type: str = Form("detailed_answer"),
    ocr_result: str = Form(""),
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form("deepseek-v4-pro"),
    stream: str = Form("false")
):
    provider = resolve_text_provider(model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法智能解答！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        system_instructions, user_prompt = build_ai_solve_prompts(
            question_type=question_type,
            content=content,
            ocr_result=ocr_result,
            custom_prompt=custom_prompt,
        )

        # Keep the legacy fallback cap for older Bailian models. Current
        # Qwen3.7/3.8 requests are converted below to max_completion_tokens.
        max_output_tokens = 8192 if provider.provider_code == "bailian" else 16384
            
        explicit_effort = provider.reasoning_effort

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
        
        is_bailian = provider.provider_code == "bailian"
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
            
        # The 7:3 model selector may provide an explicit allowlisted effort.
        # Apply it last so it intentionally overrides the generic toggle.
        data = inject_reasoning_effort(data, explicit_effort)
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="solve",
            thinking_enabled=thinking == "enabled",
        )
            
        # When thinking mode is active, temperature is ignored/deprecated by DeepSeek.
        # But when thinking is disabled or non-DeepSeek model, specify it.
        if not is_deepseek or thinking == "disabled":
            data["temperature"] = 0.2
            
        if stream == "true":
            def event_generator():
                data["stream"] = True
                try:
                    response = post_chat_completion(
                        provider,
                        data,
                        timeout=300,
                        stream=True,
                        check_status=False,
                    )
                    if response.status_code != 200:
                        error_msg = f"{provider_name} 接口错误: HTTP {response.status_code}"
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
        response = post_chat_completion(
            provider,
            data,
            timeout=300,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        
        msg_obj = res_json.get("choices", [{}])[0].get("message", {})
        ai_message = msg_obj.get("content") or ""
        reasoning_content = msg_obj.get("reasoning_content") or ""
        
        # Robust fallback: if content is empty but reasoning is present, use reasoning as explanation
        if not ai_message and reasoning_content:
            ai_message = f"【深度思考推理过程】\n{reasoning_content}\n\n【参考解析】已成功生成推理步骤。如果需要标准的三板块排版，请尝试在控制面板中关闭「AI 深度思考推理」再次生成。"
            
        if not ai_message:
            print(
                f"[Solve API] Provider returned an empty message "
                f"(provider={provider.provider_code}, status={response.status_code})."
            )
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
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash")
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
def save_settings(
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
    ali_bailian_model: str = Form("qwen3.7-flash"),
    prefer_solve_model: str = Form("deepseek-v4-pro"),
    prefer_parse_model: str = Form("deepseek-v4-flash"),
    prefer_classify_model: str = Form("deepseek-v4-flash"),
    prefer_draw_model: str = Form("Qwen/Qwen3-VL-32B-Instruct")
):
    try:
        settings_values = {
            "deepseek_key": deepseek_key,
            "siliconflow_key": siliconflow_key,
            "ali_bailian_key": ali_bailian_key,
            "zhongzhan_gpt_key": zhongzhan_gpt_key,
            "zhongzhan_gpt_base_url": zhongzhan_gpt_base_url,
            "zhongzhan_gpt_ocr_model": zhongzhan_gpt_ocr_model,
            "zhongzhan_claude_key": zhongzhan_claude_key,
            "zhongzhan_claude_base_url": zhongzhan_claude_base_url,
            "zhongzhan_claude_ocr_model": zhongzhan_claude_ocr_model,
            "prefer_engine": prefer_engine,
            "siliconflow_model": siliconflow_model,
            "ali_bailian_model": ali_bailian_model,
            "prefer_solve_model": prefer_solve_model,
            "prefer_parse_model": prefer_parse_model,
            "prefer_classify_model": prefer_classify_model,
            "prefer_draw_model": prefer_draw_model,
        }
        if any("\r" in value or "\n" in value for value in settings_values.values()):
            raise ValueError("配置值不能包含换行符。")

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
            
        write_private_text_atomic(ENV_FILE, "".join(new_lines))
            
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

# ----------------- Version & Update Check API -----------------

def parse_version_tuple(v_str: str):
    """Parse version string like 'v2.0.1' or '2.0.1' into integer tuple for comparison."""
    if not v_str:
        return (0, 0, 0)
    cleaned = v_str.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(re.sub(r"\D", "", p) or "0"))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

@app.get("/api/version")
def get_version_info():
    """Return local version info."""
    from mathbank import __version__, GITHUB_REPO
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    return {
        "current_version": __version__,
        "repo": GITHUB_REPO,
        "is_git_repo": is_git_repo
    }

@app.get("/api/version/check-update")
def check_version_update():
    """Check for latest release on GitHub."""
    from mathbank import __version__, GITHUB_REPO
    from mathbank.ai_http import robust_request_get
    
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    current_ver = __version__
    
    result = {
        "status": "success",
        "current_version": current_ver,
        "latest_version": current_ver,
        "has_update": False,
        "release_title": "",
        "release_body": "",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/latest",
        "published_at": "",
        "assets": {},
        "is_git_repo": is_git_repo
    }
    
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MathBank-Question-Bank-App"
        }
        resp = robust_request_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            latest_tag = data.get("tag_name", "").strip()
            latest_ver = latest_tag.lstrip("vV")
            
            # Compare versions
            current_tuple = parse_version_tuple(current_ver)
            latest_tuple = parse_version_tuple(latest_ver)
            
            has_update = latest_tuple > current_tuple
            
            assets_map = {}
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                download_url = asset.get("browser_download_url", "")
                size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                download_count = asset.get("download_count", 0)
                if "macOS" in name or "mac" in name.lower() or "darwin" in name.lower():
                    assets_map["macOS"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
                elif "Windows" in name or "win" in name.lower():
                    assets_map["Windows"] = {"name": name, "url": download_url, "size_mb": size_mb, "downloads": download_count}
            
            result.update({
                "latest_version": latest_tag,
                "has_update": has_update,
                "release_title": data.get("name", "") or latest_tag,
                "release_body": data.get("body", ""),
                "release_url": data.get("html_url", result["release_url"]),
                "published_at": data.get("published_at", ""),
                "assets": assets_map
            })
        else:
            result["status"] = "warning"
            result["message"] = f"GitHub API 返回状态码: {resp.status_code}"
    except Exception as e:
        result["status"] = "warning"
        result["message"] = f"检查更新超时或失败: {str(e)}"
        
    return result

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

    # 2. 检查 PyMuPDF
    try:
        import pymupdf as fitz
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
            [
                "xelatex",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-output-directory=.",
                os.path.basename(tex_path),
            ],
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=build_restricted_tex_environment(temp_dir),
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

        # 使用 PyMuPDF 将 PDF 转换成 PNG，并确保异常路径也会关闭文档。
        with fitz.open(pdf_path) as doc:
            if len(doc) == 0:
                raise RuntimeError("生成的 PDF 文件为空。")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=150)
            pix.save(png_path)

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

    # 动态读取高级绘图模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    draw_provider = resolve_draw_provider(prefer_draw)
    if not draw_provider.api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"未配置 {draw_provider.credential_label}！"
                "请在设置面板中配置后重试。"
            ),
        )
    print(
        f"[TikZ Correction] 启用 {draw_provider.provider_label} 高级模型进行纠错: "
        f"{draw_provider.model_name}, Base URL: {draw_provider.chat_completions_url}"
    )

    # 对原始截图进行 Base64 编码
    try:
        clean_original_path = resolve_upload_asset(
            original_image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    # 视觉比对模式（编译成功，获取到两张图）
    if rendered_image_path:
        try:
            clean_rendered_path = resolve_upload_asset(
                rendered_image_path,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        except AssetSecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            clean_rendered_path.unlink()
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

    try:
        corrected_code = request_tikz_completion(
            draw_provider,
            content_payload,
            timeout=90,
        )
        return {
            "status": "success",
            "corrected_code": corrected_code,
            "mode": "visual_diff" if rendered_image_path else "error_recovery"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 纠错请求失败: {str(e)}")

@app.post("/api/ai/draw_tikz")
def draw_tikz_workbench_endpoint(
    instruction: str = Form(""),
    context: str = Form(""),
    existing_tikz: str = Form(""),
    reference_image_path: str = Form(""),
    reference_image: Optional[UploadFile] = File(None),
):
    """从文字、参考图或已有源码生成/修改 TikZ，供手动录题工作台使用。"""

    instruction = (instruction or "").strip()
    context = (context or "").strip()
    existing_tikz = (existing_tikz or "").strip()
    reference_image_path = (reference_image_path or "").strip()
    has_uploaded_reference = bool(reference_image and reference_image.filename)
    has_reference = has_uploaded_reference or bool(reference_image_path)
    if not instruction and not existing_tikz and not has_reference:
        raise HTTPException(status_code=400, detail="请输入绘图要求、上传参考图或提供已有 TikZ 源码。")
    if len(instruction) > 4000:
        raise HTTPException(status_code=400, detail="绘图要求不能超过 4000 个字符。")
    if len(context) > 30000:
        raise HTTPException(status_code=400, detail="绘图上下文不能超过 30000 个字符。")
    if len(existing_tikz) > 200000:
        raise HTTPException(status_code=400, detail="TikZ 源码不能超过 200000 个字符。")

    reference_path: Optional[Path] = None
    temporary_reference_path: Optional[Path] = None
    persisted_reference_url = ""
    try:
        if has_uploaded_reference:
            raw = read_stream_limited(reference_image.file, MAX_SINGLE_IMAGE_BYTES)
            normalized = normalize_raster_image(raw)
            temporary_reference_path = Path(TMP_UPLOAD_DIR) / (
                f"tikz_reference_{uuid.uuid4().hex}{normalized.extension}"
            )
            temporary_reference_path.write_bytes(normalized.data)
            reference_path = temporary_reference_path
        elif reference_image_path:
            normalized_reference = normalize_upload_asset_reference(
                reference_image_path,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            reference_path = resolve_upload_asset(
                normalized_reference,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
            persisted_reference_url = normalized_reference

        prefer_draw = (
            os.getenv("PREFER_DRAW_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "Qwen/Qwen3-VL-32B-Instruct"
        )
        tikz_code = draw_tikz_via_high_model(
            str(reference_path) if reference_path else None,
            prefer_draw,
            latex_content=context,
            instruction=instruction,
            existing_tikz=existing_tikz,
            require_image_support=has_reference,
        )
        if not tikz_code:
            raise RuntimeError(
                "TikZ 绘图模型未返回可用源码，请检查绘图模型与 API 密钥设置。"
            )
        if temporary_reference_path is not None:
            persisted_name = (
                f"tikz_reference_{uuid.uuid4().hex}"
                f"{temporary_reference_path.suffix.lower()}"
            )
            persisted_path = Path(UPLOAD_DIR) / persisted_name
            temporary_reference_path.replace(persisted_path)
            temporary_reference_path = None
            persisted_reference_url = f"/{UPLOAD_DIR_REL}/{persisted_name}"
        return {
            "status": "success",
            "tikz_code": tikz_code,
            "used_reference_image": has_reference,
            "reference_image_path": persisted_reference_url,
        }
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail="参考图不能超过 10MB。") from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=f"参考图无效: {str(exc)}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AI TikZ 绘图失败: {str(exc)}") from exc
    finally:
        if temporary_reference_path is not None:
            try:
                temporary_reference_path.unlink(missing_ok=True)
            except OSError:
                pass


@app.post("/api/ai/draw_tikz_from_image")
def draw_tikz_from_image_endpoint(
    image_path: str = Form(...),
    latex_content: str = Form(None),
    x_local_token: str = Header(None, alias="X-Local-Token")
):
    """根据指定的题目图片，调用高级多模态模型生成对应的 LaTeX TikZ 代码"""
    # Middleware already enforces this header for HTTP calls.  Keep the direct
    # function guard tied to the same single token source for test/internal use.
    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        physical_path = resolve_upload_asset(
            image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
        
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
    page: Optional[int] = None,
    page_size: int = 20,
    sort: str = "desc",
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
            if seq_val >= 1:
                row = (
                    db.query(Question.id)
                    .order_by(Question.id.asc())
                    .offset(seq_val - 1)
                    .limit(1)
                    .first()
                )
                if row:
                    target_id_by_seq = row[0]

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
        
    order_columns = (
        (Question.created_at.asc(), Question.id.asc())
        if str(sort).lower() == "asc"
        else (Question.created_at.desc(), Question.id.desc())
    )
    if page is not None:
        safe_page_size = max(1, min(int(page_size), 100))
        total = query.count()
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(int(page), total_pages))
        questions = (
            query.order_by(*order_columns)
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
            .all()
        )
        seq_map = get_seq_mapping(db, [item.id for item in questions])
        return {
            "items": [
                {**item.to_summary_dict(), "seq_num": seq_map.get(item.id)}
                for item in questions
            ],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
            "total_pages": total_pages,
        }

    questions = query.order_by(*order_columns).all()
    seq_map = get_seq_mapping(db, [item.id for item in questions])
    return [{**item.to_summary_dict(), "seq_num": seq_map.get(item.id)} for item in questions]

@app.get("/api/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    seq_map = get_seq_mapping(db, [q.id])
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


def committed_question_response(
    db: Session,
    db_question: Question,
    question_id: int,
    *,
    operation: str,
) -> dict:
    """Serialize a committed write without ever misreporting it as failed."""

    try:
        db.refresh(db_question)
        seq_map = get_seq_mapping(db, [question_id])
        question = db_question.to_dict()
        question["seq_num"] = seq_map.get(question_id)
        return {"status": "success", "question": question}
    except Exception as exc:
        # The durable transaction is already complete.  End any failed read
        # transaction and return enough identity for the client to continue;
        # a later list/detail refresh can obtain the full representation.
        try:
            db.rollback()
        except Exception:
            pass
        print(
            f"[Question Write] Post-commit {operation} response degraded "
            f"(type={type(exc).__name__})."
        )
        return {"status": "success", "question": {"id": question_id}}


def prepare_question_assets(
    content: str,
    answer_markdown: str,
    image_paths: str,
    content_tikz_assets: Optional[str],
    tikz_reference_image_path: str,
    answer_tikz_assets: str,
    tikz_code: str = "",
    *,
    promotion_log: list[tuple[Path, Path]],
) -> tuple[
    str,
    str,
    list[str],
    str,
    str,
    list[dict[str, str]],
    list[dict[str, str]],
]:
    """Promote and validate all visible and AI-only assets in one policy path."""

    parsed_img_paths = json.loads(image_paths) if image_paths else []
    content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
        content,
        answer_markdown,
        parsed_img_paths,
        promotion_log=promotion_log,
    )
    parsed_img_paths = normalize_upload_asset_references(
        parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    parsed_answer_tikz_assets = normalize_answer_tikz_assets(
        answer_tikz_assets,
        allowed_image_paths=parsed_img_paths,
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    if content_tikz_assets is None:
        # Compatibility for clients and existing drafts created before v5.
        parsed_content_tikz_assets: list[dict[str, str]] = []
        parsed_tikz_code = str(tikz_code or "").strip()
        parsed_tikz_reference_image_path = normalize_optional_upload_asset_reference(
            tikz_reference_image_path,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    else:
        parsed_content_tikz_assets = normalize_content_tikz_assets(
            content_tikz_assets,
            allowed_image_paths=parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        first_content_asset = (
            parsed_content_tikz_assets[0] if parsed_content_tikz_assets else {}
        )
        parsed_tikz_code = str(first_content_asset.get("tikz_code") or "")
        parsed_tikz_reference_image_path = str(
            first_content_asset.get("reference_image_path") or ""
        )
    return (
        content,
        answer_markdown,
        parsed_img_paths,
        parsed_tikz_code,
        parsed_tikz_reference_image_path,
        parsed_content_tikz_assets,
        parsed_answer_tikz_assets,
    )


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
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    db: Session = Depends(get_db)
):
    asset_promotions: list[tuple[Path, Path]] = []
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            promotion_log=asset_promotions,
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
            tikz_code=parsed_tikz_code,
            tikz_reference_image_path=parsed_tikz_reference_image_path,
            figure_align=figure_align if figure_align in ["right", "center", "bottom_right"] else "right",
            tags=tags
        )
        db_question.image_paths = parsed_img_paths
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets
        
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
        db.flush()

        # Save the question and its active curriculum mirror atomically.
        active_version = get_active_version_code()
        curriculum_map = QuestionCurriculum(
            question_id=db_question.id,
            version_code=active_version,
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge
        )
        db.add(curriculum_map)
        committed_question_id = db_question.id
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"保存题目失败: {str(e)}")

    # Everything below is compensating or response work after the durable
    # success boundary; none of it may turn the write into a misleading 400.
    schedule_database_export(background_tasks, operation="create_question")
    return committed_question_response(
        db,
        db_question,
        committed_question_id,
        operation="create_question",
    )

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
    content_tikz_assets: Optional[str] = Form(None),
    tikz_reference_image_path: str = Form(""),
    answer_tikz_assets: str = Form("[]"),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    asset_promotions: list[tuple[Path, Path]] = []
    old_images = list(db_question.image_paths)
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)

        (
            content,
            answer_markdown,
            parsed_img_paths,
            parsed_tikz_code,
            parsed_tikz_reference_image_path,
            parsed_content_tikz_assets,
            parsed_answer_tikz_assets,
        ) = prepare_question_assets(
            content,
            answer_markdown,
            image_paths,
            content_tikz_assets,
            tikz_reference_image_path,
            answer_tikz_assets,
            tikz_code,
            promotion_log=asset_promotions,
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
        db_question.tikz_code = parsed_tikz_code
        db_question.tikz_reference_image_path = parsed_tikz_reference_image_path
        if figure_align in ["right", "center", "bottom_right"]:
            db_question.figure_align = figure_align
        db_question.tags = tags
        # Physical cleanup happens only after the database commit succeeds.
        removed_images = set(old_images) - set(parsed_img_paths)

        db_question.image_paths = parsed_img_paths
        db_question.content_tikz_assets = parsed_content_tikz_assets
        db_question.answer_tikz_assets = parsed_answer_tikz_assets
        
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
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"更新题目失败: {str(e)}")

    # The question is already durably updated at this point.  Best-effort
    # cleanup and response assembly must not turn success into a false failure.
    try:
        delete_unreferenced_question_assets(db, removed_images)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit update cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )
    schedule_database_export(background_tasks, operation="update_question")
    return committed_question_response(
        db,
        db_question,
        question_id,
        operation="update_question",
    )

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
    
    seq_map = get_seq_mapping(db, [item.id for item in associated])
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
        schedule_database_export(background_tasks, operation="associate_questions")
        
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
        schedule_database_export(background_tasks, operation="remove_association")
        
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
        
    image_paths_to_check = list(db_question.image_paths)
    try:
        from sqlalchemy import func

        affected_paper_ids = [
            paper_id
            for (paper_id,) in db.query(PaperQuestion.paper_id)
            .filter(PaperQuestion.question_id == question_id)
            .distinct()
            .all()
        ]
        if affected_paper_ids:
            remaining_scores = dict(
                db.query(
                    PaperQuestion.paper_id,
                    func.coalesce(func.sum(PaperQuestion.score), 0),
                )
                .filter(
                    PaperQuestion.paper_id.in_(affected_paper_ids),
                    PaperQuestion.question_id != question_id,
                )
                .group_by(PaperQuestion.paper_id)
                .all()
            )
            for paper in db.query(Paper).filter(
                Paper.id.in_(affected_paper_ids)
            ):
                paper.total_score = int(remaining_scores.get(paper.id, 0))
        db.delete(db_question)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除题目失败: {str(e)}")

    # The database delete is complete.  Image cleanup is intentionally
    # best-effort so a locked/missing file cannot make the client believe the
    # question still exists and submit a duplicate delete.
    try:
        delete_unreferenced_question_assets(db, image_paths_to_check)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit delete cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )

    # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
    schedule_database_export(background_tasks, operation="delete_question")

    return {"status": "success", "message": "题目删除成功"}

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
                            write_private_text_atomic(
                                METADATA_FILE,
                                json.dumps(loaded, ensure_ascii=False, indent=2),
                            )
                            print(f"[Metadata Self-Heal] Upgraded {METADATA_FILE} with simplified book names and normal difficulty.")
                        except Exception as e:
                            print(f"[Metadata Self-Heal Error] Failed to write updated metadata: {e}")
                    
                    METADATA_CACHE = loaded
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    return
        except Exception as e:
            print(f"[Metadata Warning] Error loading {METADATA_FILE}: {e}. Overwriting with default.")
            
    # Self-heal / initialize
    try:
        write_private_text_atomic(
            METADATA_FILE,
            json.dumps(default_metadata, ensure_ascii=False, indent=2),
        )
        print(f"[Metadata] Initialized default metadata at {METADATA_FILE}")
    except Exception as e:
        print(f"[Metadata Error] Could not write default metadata: {e}")
        
    METADATA_CACHE = default_metadata

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
def save_metadata_config(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
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
        
    old_metadata = METADATA_CACHE
    metadata_path = Path(METADATA_FILE)
    old_file_contents = (
        metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
    )
    file_replaced = False
    transaction_committed = False

    # Update the curriculum mirror and metadata as one compensated operation.
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
        # Batch update main questions table categories with target version values
        from sqlalchemy import text
        db.flush()
        db.execute(text("""
            UPDATE questions 
            SET category_compulsory = COALESCE((SELECT compulsory FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_chapter = COALESCE((SELECT chapter FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_knowledge = COALESCE((SELECT knowledge FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), '')
        """), {"v": target_version})

        write_private_text_atomic(
            metadata_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        file_replaced = True
        db.commit()
        transaction_committed = True
    except Exception as e:
        db.rollback()
        if not transaction_committed:
            METADATA_CACHE = old_metadata
        if file_replaced and not transaction_committed:
            try:
                if old_file_contents is None:
                    metadata_path.unlink(missing_ok=True)
                else:
                    write_private_text_atomic(metadata_path, old_file_contents)
            except OSError as restore_error:
                print(
                    "[Metadata] Failed to restore metadata after DB rollback "
                    f"(type={type(restore_error).__name__})."
                )
        raise HTTPException(status_code=500, detail=f"保存元数据失败: {str(e)}")

    # Everything below is post-commit and must not change the successful save
    # into an error response or compensate already-durable database changes.
    METADATA_CACHE = payload
    print(
        f"[Metadata] Saved new custom metadata to {METADATA_FILE} "
        f"(Detected version: {target_version})"
    )
    schedule_database_export(background_tasks, operation="save_metadata")
    return {"status": "success", "message": "元数据配置保存成功！"}

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
def ai_classify(content: str = Form(...)):
    classify_model = (
        os.getenv("PREFER_CLASSIFY_MODEL") 
        or os.getenv("DEEPSEEK_CLASSIFY_MODEL") 
        or os.getenv("PREFER_PARSE_MODEL") 
        or "deepseek-v4-flash"
    )
    
    provider = resolve_text_provider(classify_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法自动智能分类！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
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
        if is_deepseek and provider.reasoning_effort in {None, "default"}:
            data["thinking"] = {
                "type": "disabled"
            }
        data = inject_reasoning_effort(data, provider.reasoning_effort)
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="classify",
        )
        
        response = post_chat_completion(
            provider,
            data,
            timeout=30,
            provider_name=provider_name,
        )
            
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
        structured_question_form = detect_structured_question_form(content)
        question_form = structured_question_form or normalize_ai_question_form(
            result.get("question_form")
        )
        question_form_source = "structure" if structured_question_form else "ai"
        
        # Verification: make sure returned values exist in get_current_curriculum()
        curr = get_current_curriculum()
        if compulsory in curr and chapter in curr[compulsory]:
            return {
                "status": "success",
                "compulsory": compulsory,
                "chapter": chapter,
                "question_form": question_form,
                "question_form_source": question_form_source,
            }
        else:
            # Fallback dynamically to the first available category book/chapter
            first_comp = list(curr.keys())[0] if curr else "必修一"
            first_chap = list(curr[first_comp].keys())[0] if curr and first_comp in curr and curr[first_comp] else "1. 集合与常用逻辑用语"
            return {
                "status": "success",
                "compulsory": first_comp,
                "chapter": first_chap,
                "question_form": question_form,
                "question_form_source": question_form_source,
                "is_fallback": True,
                "raw_recommendation": f"{compulsory} -> {chapter}"
            }
            
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 智能分类失败: {str(e)}"},
            status_code=500
        )

# ----------------- LaTeX Batch Paper Import APIs -----------------

@app.post("/api/upload/tex-source")
def upload_tex_source(file: UploadFile = File(...)):
    """Decode and inspect a single TeX source file without executing it."""
    filename = file.filename or ""
    if not filename.lower().endswith(".tex"):
        return JSONResponse(
            content={"status": "error", "message": "上传文件格式不正确，必须为 .tex 格式！"},
            status_code=400,
        )
    try:
        content = read_stream_limited(file.file, MAX_TEX_BYTES)
        result = decode_and_prepare_tex(content)
        return {
            "status": "success",
            "source": result["source"],
            "title": result["title"],
            "diagnostics": result["diagnostics"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "TeX 文件过大，请上传 5MB 以内的单文件试卷源码！"},
            status_code=413,
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)


@app.post("/api/upload/batch")
def upload_batch_images(files: List[UploadFile] = File(...)):
    try:
        if not files or len(files) > 20:
            return JSONResponse(
                content={"status": "error", "message": "配图数量必须为 1 至 20 张。"},
                status_code=400,
            )
        validated = []
        total_bytes = 0
        seen_names: set[str] = set()
        for file in files:
            original_name = tex_asset_basename(file.filename or "image") or "image"
            normalized_name = original_name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"存在重名配图 {original_name}，请保留一张或先重命名。")
            seen_names.add(normalized_name)
            try:
                raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
            except UploadTooLargeError as exc:
                raise ValueError(f"图片 {original_name} 超过 10MB。") from exc
            total_bytes += len(raw)
            if total_bytes > 50 * 1024 * 1024:
                raise ValueError("配图总大小不能超过 50MB。")
            try:
                normalized = normalize_raster_image(raw)
            except InvalidImageError as exc:
                raise ValueError(f"图片 {original_name} 不是安全的栅格图片。") from exc
            validated.append((original_name, normalized))

        mapping = {}
        for original_name, normalized in validated:
            filename = f"{uuid.uuid4().hex}{normalized.extension}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(normalized.data)
            relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
            mapping[original_name] = relative_path
            
        return {
            "status": "success",
            "mapping": mapping
        }
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        return JSONResponse(
            content={"status": "error", "message": f"批量图片上传失败: {str(e)}"},
            status_code=400
        )


def parse_paper_text_internal(
    latex_content: str,
    generate_answers_bool: bool
) -> list:
    """内部通用函数：调用选定的 LLM 接口，将 LaTeX 试卷内容解析拆分为结构化 JSON 卡片"""
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    provider = resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")

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
    if is_deepseek and provider.reasoning_effort in {None, "default"}:
        data["thinking"] = {
            "type": "disabled"
        }
    data = inject_reasoning_effort(data, provider.reasoning_effort)
    data = apply_bailian_thinking_policy(
        data,
        provider_code=provider.provider_code,
        model_name=model_name,
        task="parse",
    )
    
    response = post_chat_completion(
        provider,
        data,
        timeout=180,
        provider_name=provider_name,
    )
        
    res_json = response.json()
    raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
    
    parsed_data = parse_ai_json(raw_ai_text, raw_markdown=latex_content)
    
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
def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(""),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false")
):
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash")
    provider = resolve_text_provider(parse_model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        image_mapping = json.loads(image_mapping_json)
        if not isinstance(image_mapping, dict):
            image_mapping = {}
    except Exception:
        image_mapping = {}

    try:
        tex_result = prepare_tex_source(latex_content)
        tex_diagnostics = tex_result["diagnostics"]
        model_source, math_locks = lock_visible_math(
            tex_result["model_source"],
            "TEX_" + uuid.uuid4().hex[:16],
        )
        tex_diagnostics["math_locks_created"] = len(math_locks)
        if not paper_title.strip() and tex_result["title"]:
            paper_title = tex_result["title"]

        system_instructions = build_import_parse_system_prompt(get_current_curriculum())

        max_output_tokens = 65536

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": model_source}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": max_output_tokens
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL, excluding legacy models that don't support it
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        if is_deepseek and provider.reasoning_effort in {None, "default"}:
            data["thinking"] = {
                "type": "disabled"
            }
        data = inject_reasoning_effort(data, provider.reasoning_effort)
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="parse",
        )
        
        response = post_chat_completion(
            provider,
            data,
            timeout=180,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
        
        parsed_data = parse_ai_json(raw_ai_text, raw_markdown=model_source)
        
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

        if not parsed_questions or not all(isinstance(question, dict) for question in parsed_questions):
            raise ValueError("AI 未返回有效的题目对象列表。")
        for index, question in enumerate(parsed_questions, start=1):
            if not isinstance(question.get("content"), str) or not question["content"].strip():
                raise ValueError(f"AI 返回的第 {index} 题缺少有效题干，已停止导入以避免静默漏题。")
            if not isinstance(question.get("referenced_images"), list):
                question["referenced_images"] = []

        lock_report = restore_visible_math(parsed_questions, math_locks)
        tex_diagnostics.update(lock_report)
        tex_diagnostics["question_count_actual"] = len(parsed_questions)
        estimated_count = tex_diagnostics.get("question_count_estimate", 0)
        if estimated_count and estimated_count != len(parsed_questions):
            tex_diagnostics.setdefault("warnings", []).append(
                f"源码约识别到 {estimated_count} 道题，但模型返回 {len(parsed_questions)} 道，请重点核对是否漏题或误拆。"
            )

        for graphic_ref in tex_diagnostics.get("referenced_graphics", []):
            graphic_ref = str(graphic_ref)
            candidates = []
            for question in parsed_questions:
                content_graphics = re.findall(
                    r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}",
                    question.get("content", ""),
                )
                question_refs = content_graphics + [
                    str(value) for value in question.get("referenced_images", [])
                ]
                if any(tex_asset_references_match(graphic_ref, value) for value in question_refs):
                    candidates.append(question)
            if len(candidates) == 1 and not any(
                tex_asset_references_match(graphic_ref, existing)
                for existing in candidates[0]["referenced_images"]
            ):
                candidates[0]["referenced_images"].append(graphic_ref)
            elif not candidates:
                tex_diagnostics.setdefault("unassigned_source_images", []).append(graphic_ref)
        
        # Translate referenced_images to server paths
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
                ref_name = str(ref_name)
                # Direct match or fuzzy match
                found_path = None
                for orig_name, serv_path in image_mapping.items():
                    if tex_asset_references_match(ref_name, orig_name):
                        found_path = serv_path
                        break
                if found_path:
                    if found_path not in mapped_images:
                        mapped_images.append(found_path)
                    include_pattern = re.compile(
                        r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{\s*([^}]+?)\s*\}"
                    )
                    q["content"] = include_pattern.sub(
                        lambda match: (
                            f"![插图]({found_path})"
                            if tex_asset_references_match(ref_name, match.group(1))
                            else match.group(0)
                        ),
                        q["content"],
                    )
                else:
                    tex_diagnostics.setdefault("unmapped_images", []).append(str(ref_name))
                    
            q["image_paths"] = mapped_images
            
            # If AI didn't map it in content text but referenced it, append it to content
            for img_path in mapped_images:
                if img_path not in q["content"]:
                    q["content"] += f"\n\n![插图]({img_path})\n\n"

        unmapped_images = sorted(set(tex_diagnostics.get("unmapped_images", [])))
        unassigned_images = sorted(set(tex_diagnostics.get("unassigned_source_images", [])))
        tex_diagnostics["unmapped_images"] = unmapped_images
        tex_diagnostics["unassigned_source_images"] = unassigned_images
        if unmapped_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下 TeX 配图未找到同名上传文件：" + "、".join(unmapped_images[:8])
            )
        if unassigned_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下配图未能确定所属题目：" + "、".join(unassigned_images[:8])
            )
                    
        return {
            "status": "success",
            "questions": parsed_questions,
            "tex_diagnostics": tex_diagnostics,
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

def rollback_question_asset_promotions(promotions: list[tuple[Path, Path]]) -> None:
    """Best-effort compensation when a DB transaction rejects promoted files."""

    for source, destination in reversed(promotions):
        try:
            if destination.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        except OSError as exc:
            print(f"[Storage Rollback] Failed to restore a promoted asset: {type(exc).__name__}")


def _referenced_question_assets(db: Session) -> set[Path]:
    """Resolve every stored question image reference with one database query."""

    resolved_references: set[Path] = set()
    rows = db.query(
        Question._image_paths,
        Question.content,
        Question.answer_markdown,
    ).all()
    for raw_paths, content, answer_markdown in rows:
        references = []
        try:
            parsed = json.loads(raw_paths or "[]")
            if isinstance(parsed, list):
                references.extend(parsed)
        except (TypeError, json.JSONDecodeError):
            pass
        references.extend(
            re.findall(
                r'/static/(?:uploads|test_uploads)/[a-zA-Z0-9_./-]+',
                f"{content or ''}\n{answer_markdown or ''}",
            )
        )
        for reference in references:
            try:
                resolved = resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            except AssetSecurityError:
                continue
            resolved_references.add(resolved)
    return resolved_references


def delete_unreferenced_question_assets(db: Session, references) -> int:
    """Delete committed-away images only when no remaining question uses them."""

    candidates: set[Path] = set()
    for reference in set(references or []):
        try:
            candidates.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            )
        except AssetSecurityError:
            print("[Storage Cleanup] Skipped an invalid legacy image path.")

    if not candidates:
        return 0
    referenced = _referenced_question_assets(db)
    removed = 0
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate not in referenced:
                candidate.unlink()
                removed += 1
        except OSError:
            print("[Storage Cleanup] Skipped an unavailable legacy image path.")
    return removed


def promote_question_temp_assets(
    content: str,
    answer_markdown: str,
    image_paths_list: list,
    *,
    promotion_log: list[tuple[Path, Path]] | None = None,
) -> tuple:
    """物理移动临时图片到永久目录，并更新题干、解析和图片路径列表中的引用"""
    import shutil

    if not isinstance(image_paths_list, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")

    embedded_paths = re.findall(
        r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+',
        f"{content}\n{answer_markdown}",
    )
    all_references = [value for value in image_paths_list if value] + embedded_paths

    # Validate the complete set before moving anything.  A bad second path must
    # not leave the first path half-promoted.
    canonical_by_input: dict[str, str] = {}
    resolved_by_canonical: dict[str, Path] = {}
    for reference in all_references:
        canonical = normalize_upload_asset_reference(
            reference,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        canonical_by_input[reference] = canonical
        resolved_by_canonical.setdefault(
            canonical,
            resolve_upload_asset(
                canonical,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_root = Path(TMP_UPLOAD_DIR).resolve()
    promoted_by_canonical: dict[str, str] = {}
    for canonical, source in resolved_by_canonical.items():
        if source.parent == temp_root:
            destination_url = f"/{UPLOAD_DIR_REL}/{source.name}"
            destination = resolve_upload_asset(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
            if destination.exists():
                raise AssetSecurityError("目标插图文件已存在，已停止覆盖。")
            shutil.move(str(source), str(destination))
            if promotion_log is not None:
                promotion_log.append((source, destination))
            promoted_by_canonical[canonical] = normalize_upload_asset_reference(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
            )
        elif source.is_relative_to(upload_root):
            promoted_by_canonical[canonical] = canonical
        else:  # Defensive; resolve_upload_asset should already make this impossible.
            raise AssetSecurityError("临时插图越出了上传目录。")

    replacements: dict[str, str] = {}
    for original, canonical in canonical_by_input.items():
        promoted = promoted_by_canonical[canonical]
        replacements[original] = promoted
        replacements[canonical] = promoted

    new_content = content
    new_answer = answer_markdown
    for old_path, new_path in replacements.items():
        new_content = new_content.replace(old_path, new_path)
        new_answer = new_answer.replace(old_path, new_path)

    updated_paths: list[str] = []
    for original in image_paths_list:
        if not original:
            continue
        promoted = promoted_by_canonical[canonical_by_input[original]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    for embedded in embedded_paths:
        promoted = promoted_by_canonical[canonical_by_input[embedded]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    return new_content, new_answer, updated_paths


@app.post("/api/ai/manual-crop-pdf")
def manual_crop_pdf(payload: dict):
    """用户在前端手动拖拽框选后，后端根据坐标裁剪 PDF 页面的特定区域"""
    try:
        import math

        if not isinstance(payload, dict):
            raise ValueError("裁剪参数格式不正确。")
        try:
            task_id = str(uuid.UUID(str(payload.get("task_id", ""))))
        except (ValueError, AttributeError) as exc:
            raise ValueError("任务 ID 格式不正确。") from exc
        task = DOCUMENT_TASKS.snapshot(task_id)
        if not task or task.get("document_type") != "pdf":
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 任务！"},
                status_code=404,
            )
        if task.get("status") in {"cancelled", "error"}:
            raise ValueError("已取消或失败的 PDF 任务不能再裁剪。")
        page_index = int(payload.get("page_index", 0))
        if page_index < 0 or page_index >= MAX_PDF_TASK_PAGES:
            raise ValueError("页码越界。")
        ymin = float(payload.get("ymin", 0))
        xmin = float(payload.get("xmin", 0))
        ymax = float(payload.get("ymax", 0))
        xmax = float(payload.get("xmax", 0))
        coordinates = (ymin, xmin, ymax, xmax)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("裁剪坐标必须是有限数值。")
        if not (
            0 <= ymin < ymax <= 100
            and 0 <= xmin < xmax <= 100
        ):
            raise ValueError("裁剪坐标必须位于 0–100，且框选区域不能为空。")

        img_filename = f"pdf_page_{task_id}_{page_index}.png"
        img_filepath = Path(TMP_UPLOAD_DIR) / img_filename
        
        if not img_filepath.is_file() or img_filepath.is_symlink():
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF 页面图片！"},
                status_code=404
            )
            
        with Image.open(img_filepath) as img:
            img.load()
            w, h = img.size

            # Convert percentage to pixels.
            left = max(0, min((xmin / 100.0) * w, w - 1))
            top = max(0, min((ymin / 100.0) * h, h - 1))
            right = max(left + 1, min((xmax / 100.0) * w, w))
            bottom = max(top + 1, min((ymax / 100.0) * h, h))
            cropped = img.crop((left, top, right, bottom))

        crop_filename = f"pdf_crop_{task_id}_{uuid.uuid4().hex[:12]}.png"
        crop_filepath = Path(TMP_UPLOAD_DIR) / crop_filename
        cropped.save(crop_filepath, format="PNG")
        
        img_url = f"/{UPLOAD_DIR_REL}/tmp/{crop_filename}"
        if not DOCUMENT_TASKS.add_temp_asset(task_id, img_url):
            crop_filepath.unlink(missing_ok=True)
            raise ValueError("任务记录已过期，无法登记裁剪图片。")
        return {"status": "success", "image_path": img_url}
    except ValueError as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=400,
        )
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
    errors = []
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    providers_to_try = resolve_ocr_fallbacks(prefer_engine)

    if not providers_to_try:
        raise ValueError("未配置任何识图 Key，请在右上角「API设置」面板中配置 硅基流动、阿里百炼 或 中转站 API 密钥。")

    for ocr_provider in providers_to_try:
        label = ocr_provider.provider_label
        try:
            print(f"[PDF OCR Flow] 正在尝试调用识图引擎: {label}...")
            return ocr_via_provider(image_path, ocr_provider)
        except requests.exceptions.ReadTimeout as e_single:
            # The first provider may already have accepted the image.  Sending
            # it immediately to another provider can create a duplicate bill.
            raise RuntimeError(
                f"{label} 读取超时，请求是否已被处理尚不确定。"
                "为避免重复计费，本次未自动切换到下一家模型，请稍后手动重试。"
            ) from e_single
        except Exception as e_single:
            err_msg = f"{label} 出错: {str(e_single)}"
            print(f"[PDF OCR Flow Warning] {err_msg}")
            errors.append(err_msg)
            
    # 如果全部都失败了，抛出包含所有尝试错误细节的汇总异常
    raise RuntimeError("所有配置的识图引擎均尝试失败。详情:\n" + "\n".join(errors))


def process_ocr_illustrations(text: str) -> str:
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
        limit = max(1, min(int(payload.get("limit", 5)), 20))

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

        # 2. 解题、拆卷、分类和组卷共用同一供应商解析规则。
        # 不会因为某家 Key 缺失而静默改用另一家。
        target_model = (
            os.getenv("PREFER_SOLVE_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "deepseek-chat"
        )
        provider = resolve_text_provider(target_model)
        api_key = provider.api_key
        api_base = provider.api_base
        model_name = provider.model_name
        provider_name = provider.provider_label

        api_error_detail = None
        if prompt and candidates:
            if not api_key or not api_base:
                api_error_detail = (
                    f"指定的 AI 解题模型 ({target_model}) 未配置有效的 "
                    f"API Key 或 Base URL（{provider.credential_label}）。"
                )
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
                    payload_data = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.3
                    }
                    payload_data = inject_reasoning_effort(
                        payload_data, provider.reasoning_effort
                    )
                    payload_data = apply_bailian_thinking_policy(
                        payload_data,
                        provider_code=provider.provider_code,
                        model_name=model_name,
                        task="paper_selection",
                    )
                    response = post_chat_completion(
                        provider,
                        payload_data,
                        timeout=20,
                        provider_name=provider_name,
                    )
                    res_json = response.json()
                    raw_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                        raw_content = re.sub(r"\s*```$", "", raw_content)

                    parsed = json.loads(raw_content)
                    raw_selected_ids = parsed.get("selected_ids", [])
                    ai_analysis = parsed.get("ai_analysis", "")

                    if raw_selected_ids and isinstance(raw_selected_ids, list):
                        # The model may only rank the candidate IDs that were
                        # actually supplied after local filters.  This prevents
                        # prompt output from bypassing chapter/type constraints
                        # or selecting arbitrary records from the database.
                        allowed_ids = {question.id for question in candidates}
                        selected_ids = []
                        seen_ids = set()
                        for raw_id in raw_selected_ids:
                            try:
                                selected_id = int(raw_id)
                            except (TypeError, ValueError):
                                continue
                            if (
                                selected_id in allowed_ids
                                and selected_id not in seen_ids
                            ):
                                selected_ids.append(selected_id)
                                seen_ids.add(selected_id)
                            if len(selected_ids) >= limit:
                                break
                        db_selected = db.query(Question).filter(Question.id.in_(selected_ids)).all()
                        id_map = {q.id: q for q in db_selected}
                        seq_map = get_seq_mapping(db, selected_ids)
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

        selected_fallback = fallback_questions[:limit]
        seq_map = get_seq_mapping(db, [q.id for q in selected_fallback])
        result = [{**q.to_dict(), "seq_num": seq_map.get(q.id)} for q in selected_fallback]
        
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
                for match in re.finditer(r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+', q[field]):
                    found_crops.add(match.group(0))
                    
        # 顺带检查 referenced_images 属性并应用修复映射
        ref_imgs = q.get("referenced_images", [])
        for ref in ref_imgs:
            mapped_ref = mapping.get(ref, ref)
            if "/tmp/" in mapped_ref:
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


def run_pdf_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    page_range: str = None,
    pdf_strategy: str = "native_preferred",
):
    """PDF parsing with bounded OCR concurrency and cooperative cancellation."""

    import concurrent.futures

    temp_assets: list[str] = []
    tmp_pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.pdf"

    try:
        import pymupdf as fitz
    except ImportError:
        DOCUMENT_TASKS.fail(
            task_id,
            "本地 Python 环境未安装 PyMuPDF，请通过 pip install pymupdf 安装依赖！",
            document_type="pdf",
        )
        return

    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        tmp_pdf_path.write_bytes(file_bytes)
        DOCUMENT_TASKS.update(
            task_id,
            status="processing_images",
            progress=10,
            log="已接收文件，正在渲染 PDF 高清页面...",
            document_type="pdf",
            temp_assets=[],
        )

        page_images: list[str] = []
        page_urls: list[str] = []
        with fitz.open(tmp_pdf_path) as document:
            total_pages = len(document)
            if total_pages == 0:
                raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")
            target_page_indices = parse_page_range(page_range, total_pages)
            if len(target_page_indices) > MAX_PDF_TASK_PAGES:
                raise ValueError(
                    f"单次最多解析 {MAX_PDF_TASK_PAGES} 页，请填写较小的页码范围。"
                )

            for page_num in target_page_indices:
                DOCUMENT_TASKS.check_cancelled(task_id)
                page = document.load_page(page_num)
                estimated_pixels = int(
                    (page.rect.width / 72 * 150) * (page.rect.height / 72 * 150)
                )
                if estimated_pixels > 30_000_000:
                    raise ValueError(f"第 {page_num + 1} 页尺寸异常，已停止高清渲染。")
                pixmap = page.get_pixmap(dpi=150)
                image_filename = f"pdf_page_{task_id}_{page_num}.png"
                image_path = Path(TMP_UPLOAD_DIR) / image_filename
                pixmap.save(image_path)
                image_url = f"/{UPLOAD_DIR_REL}/tmp/{image_filename}"
                page_images.append(str(image_path))
                page_urls.append(image_url)
                temp_assets.append(image_url)
                DOCUMENT_TASKS.update(
                    task_id,
                    page_images=list(page_urls),
                    temp_assets=list(temp_assets),
                )

        tmp_pdf_path.unlink(missing_ok=True)
        DOCUMENT_TASKS.check_cancelled(task_id)
        total_target_pages = len(target_page_indices)

        if pdf_strategy == "force_ocr":
            inspector_result = {"pages": [], "pdf_type": "scanned"}
            inspector_pages = {}
        else:
            inspector_result = inspect_and_extract_pdf(
                file_bytes,
                task_id,
                page_indices=target_page_indices,
            )
            inspector_pages = {
                int(page.get("page_index")): page
                for page in inspector_result.get("pages", [])
                if page.get("page_index") is not None
            }
        DOCUMENT_TASKS.check_cancelled(task_id)

        ocr_results = [None] * total_target_pages
        pages_requiring_ocr = []
        native_page_count = 0
        for local_idx, page_num in enumerate(target_page_indices):
            page_info = inspector_pages.get(page_num)
            native_text = str((page_info or {}).get("markdown") or "").strip()
            if page_info and not page_info.get("needs_ocr") and native_text:
                ocr_results[local_idx] = (
                    f"<!-- MATHBANK_PDF_PAGE:{page_num + 1} -->\n{native_text}"
                )
                native_page_count += 1
            else:
                pages_requiring_ocr.append(local_idx)

        if native_page_count:
            print(
                f"[PDF Inspector Flow] 原生直提 {native_page_count} 页，"
                f"视觉 OCR {len(pages_requiring_ocr)} 页 "
                f"(Type: {inspector_result.get('pdf_type')})",
                flush=True,
            )

        if not pages_requiring_ocr:
            DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=60,
                log=(
                    f"pdf-inspector 已按所选范围可靠提取 {native_page_count} 页原生文本，"
                    "正在连续拆题..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )
        else:
            DOCUMENT_TASKS.update(
                task_id,
                status="ocr_extraction",
                progress=30,
                log=(
                    f"所选 {total_target_pages} 页中，{native_page_count} 页已原生提取，"
                    f"仅对其余 {len(pages_requiring_ocr)} 页进行视觉转译..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )

            def ocr_worker(local_idx, image_path):
                acquired = False
                try:
                    while not acquired:
                        DOCUMENT_TASKS.check_cancelled(task_id)
                        acquired = PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    raw_text = ocr_pdf_page_image(image_path)
                    real_page_num = target_page_indices[local_idx] + 1
                    print(
                        f"[PDF OCR] 第 {real_page_num} 页识别完成 "
                        f"(characters={len(raw_text)})."
                    )
                    return local_idx, raw_text, None
                except TaskCancelled:
                    raise
                except Exception as ocr_error:
                    return local_idx, "", str(ocr_error)
                finally:
                    if acquired:
                        PDF_OCR_SEMAPHORE.release()

            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(pages_requiring_ocr), 4),
                thread_name_prefix="mathbank-pdf-ocr",
            )
            futures = []
            try:
                for local_idx in pages_requiring_ocr:
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    futures.append(
                        executor.submit(ocr_worker, local_idx, page_images[local_idx])
                    )

                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    local_idx, text, error = future.result()
                    if error:
                        real_page_num = target_page_indices[local_idx] + 1
                        raise RuntimeError(f"解析第 {real_page_num} 页出错: {error}")
                    processed_text = process_ocr_illustrations(text)
                    real_page_num = target_page_indices[local_idx] + 1
                    ocr_results[local_idx] = (
                        f"<!-- MATHBANK_PDF_PAGE:{real_page_num} -->\n"
                        f"{processed_text.strip()}"
                    )
                    completed += 1
                    progress = 30 + int(
                        (completed / len(pages_requiring_ocr)) * 40
                    )
                    DOCUMENT_TASKS.update(
                        task_id,
                        progress=progress,
                        log=(
                            f"视觉转译进度: {completed} / "
                            f"{len(pages_requiring_ocr)} 页已完成..."
                        ),
                    )
            finally:
                cancelled = DOCUMENT_TASKS.is_cancelled(task_id)
                if cancelled:
                    for future in futures:
                        future.cancel()
                executor.shutdown(wait=not cancelled, cancel_futures=True)

        DOCUMENT_TASKS.check_cancelled(task_id)
        full_latex_content = merge_pdf_page_texts(ocr_results)
        if not full_latex_content.strip():
            raise ValueError("所选 PDF 页面未能提取出可解析的文字内容。")

        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=80,
            log="文本与公式准备就绪！正在调用大模型拆解题目与标注属性...",
        )
        DOCUMENT_TASKS.check_cancelled(task_id)

        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_latex_content)
        if auto_title:
            paper_title = auto_title
        parsed_questions = parse_paper_text_internal(
            full_latex_content,
            generate_answers,
        )
        DOCUMENT_TASKS.check_cancelled(task_id)
        final_questions = post_process_pdf_parsed_questions(
            parsed_questions,
            paper_title,
            task_id,
            ocr_results,
        )
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.complete(
            task_id,
            log="完成！已为您提取并拆分全部题目卡片。",
            data=final_questions,
            page_images=list(page_urls),
            temp_assets=list(temp_assets),
            document_type="pdf",
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        DOCUMENT_TASKS.fail(
            task_id,
            f"PDF 智能拆解解析失败: {str(ex)}",
            document_type="pdf",
        )
    finally:
        tmp_pdf_path.unlink(missing_ok=True)


def parse_page_range(range_str: str, total_pages: int) -> list:
    """
    解析用户输入的页码范围字符串（1-indexed），转换为包含 0-indexed 页面索引的列表。
    支持格式如 "1-5", "1,3,5", "1-3,5,7-9"。
    """
    if total_pages <= 0:
        raise ValueError("PDF 没有有效页面。")
    if not range_str or not range_str.strip():
        return list(range(total_pages))

    pages = set()
    parts = str(range_str).replace(" ", "").split(",")
    for part in parts:
        if not part:
            raise ValueError("页码范围格式无效。")
        if "-" in part:
            sub_parts = part.split("-")
            if len(sub_parts) != 2:
                raise ValueError("页码范围格式无效。")
            try:
                start = int(sub_parts[0])
                end = int(sub_parts[1])
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if start < 1 or end < start or end > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.update(range(start - 1, end))
        else:
            try:
                page_number = int(part)
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.add(page_number - 1)

    if not pages:
        raise ValueError("页码范围不能为空。")
    return sorted(pages)


# ----------------- PDF Upload & Task Routing Endpoints -----------------

@app.post("/api/upload/pdf-task")
def upload_pdf_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    page_range: Optional[str] = Form(None),
    pdf_strategy: str = Form("native_preferred")
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .pdf 格式！"},
                status_code=400
            )
            
        # Incremental cap avoids loading an arbitrarily large multipart file.
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "PDF 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if not content.lstrip().startswith(b"%PDF-"):
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 PDF 文档！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备运行异步切片分析...",
            document_type="pdf",
            temp_assets=[],
        )
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_pdf_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                page_range,
                pdf_strategy,
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
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


def _delete_task_temp_assets(paths: list) -> int:
    """Delete only explicit files below this instance's upload tmp directory."""
    removed = 0
    tmp_root = Path(TMP_UPLOAD_DIR).resolve()
    for url in paths or []:
        try:
            full_path = resolve_upload_asset(
                str(url),
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
        except AssetSecurityError:
            continue
        if full_path.parent != tmp_root:
            continue
        if full_path.is_file():
            try:
                full_path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def run_docx_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False
):
    temp_assets = []
    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.update(
            task_id,
            status="extracting_docx",
            progress=25,
            log="已接收 Word 试卷，正在安全提取 OMML 公式、文字与配图...",
            document_type="docx",
            temp_assets=[],
        )

        # 2. 安全提取 Word Markdown；资产先放入 tmp，入库时再晋升。
        docx_res = extract_docx_markdown(
            file_bytes,
            output_dir=TMP_UPLOAD_DIR,
            url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
            asset_prefix=f"word_{task_id}",
        )
        temp_assets = docx_res.get("image_paths", [])
        if not docx_res.get("success") or not docx_res.get("markdown"):
            raise ValueError(docx_res.get("error") or "未能从 Word 文档中提取出有效试题内容！")

        full_markdown_content = docx_res["markdown"]
        img_count = docx_res.get("image_count", 0)
        diagnostics = docx_res.get("diagnostics", {})
        converted_count = diagnostics.get("omml_converted", 0) + diagnostics.get("mtef_converted", 0)
        review_count = diagnostics.get("review_required", 0)
        extraction_log = (
            f"Word 提取完成：{converted_count} 个公式已转换，{img_count} 张图片已保留"
            + (f"，{review_count} 处需人工核对。" if review_count else "，未发现需人工核对的内容。")
        )

        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=70,
            log=extraction_log + " 正在调用教研模型拆题...",
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )

        # 3. 智能提取标题与题目切片
        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_markdown_content)
        if auto_title:
            paper_title = auto_title

        # Keep every formula visible in-place for the model's mathematical
        # understanding, while assigning an immutable ID. The model returns
        # the ID and the server restores the exact Word-extracted source.
        locked_markdown_content, math_locks = lock_visible_math(
            full_markdown_content,
            task_id.replace("-", "")[:16],
        )
        diagnostics["math_locks_created"] = len(math_locks)
        DOCUMENT_TASKS.check_cancelled(task_id)
        parsed_questions = parse_paper_text_internal(locked_markdown_content, generate_answers)
        lock_report = restore_visible_math(parsed_questions, math_locks)
        diagnostics.update(lock_report)

        DOCUMENT_TASKS.check_cancelled(task_id)
        final_questions = post_process_pdf_parsed_questions(parsed_questions, paper_title, task_id, [full_markdown_content])
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.complete(
            task_id,
            log="完成！已提取并拆分 Word 题目，请优先检查带“公式待核对”标记的内容。" if review_count else "完成！已提取并拆分全部 Word 题目卡片。",
            data=final_questions,
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        DOCUMENT_TASKS.fail(
            task_id,
            f"Word 试卷拆解失败: {str(ex)}",
            document_type="docx",
        )


@app.post("/api/upload/docx-task")
def upload_docx_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false")
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".docx"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .docx 格式！"},
                status_code=400
            )
            
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "Word 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 Word DOCX 压缩包！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="Word 任务已排队，正在准备安全提取公式与配图...",
            document_type="docx",
            temp_assets=[],
        )
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_docx_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 Word 解析任务失败: {str(e)}"},
            status_code=500
        )


@app.get("/api/tasks/{task_id}/status")
def get_pdf_task_status(task_id: str):
    task = DOCUMENT_TASKS.snapshot(task_id)
    if not task:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_pdf_task(task_id: str):
    current = DOCUMENT_TASKS.snapshot(task_id)
    if current is None:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )

    current_status = current.get("status")
    if current_status in {"completed", "error"}:
        # Never delete assets belonging to a task that already produced a
        # result.  The previous behavior reported success and could remove
        # completed PDF crop files after a late ESC/click.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": current_status,
            },
            status_code=409,
        )
    if current_status == "cancelled":
        return {
            "status": "success",
            "message": "任务已中止。",
            "task_status": "cancelled",
        }

    task = DOCUMENT_TASKS.cancel(task_id)
    if task is None:  # Defensive race guard; records are not normally removed here.
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404,
        )
    if task.get("status") != "cancelled":
        # The worker may have completed between the snapshot above and the
        # atomic cancel call.  Never delete assets from that completed result.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": task.get("status"),
            },
            status_code=409,
        )
    removed = _delete_task_temp_assets(list(task.get("temp_assets", [])))
    return {
        "status": "success",
        "message": f"任务已成功中止，已清理 {removed} 个临时资产",
        "task_status": "cancelled",
    }


@app.post("/api/ai/clear-temp-crops")
def clear_temp_crops(payload: dict):
    """物理删除传递来的未入库临时裁剪图片路径"""
    try:
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("paths 必须是数组。")
        removed_count = _delete_task_temp_assets(paths)
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
        if not isinstance(payload, dict):
            raise ValueError("试卷数据格式不正确。")
        title = str(payload.get("title", "未命名试卷")).strip()
        subtitle = str(payload.get("subtitle", "")).strip()
        paper_type = payload.get("paper_type", "exam")
        questions_payload = payload.get("questions", [])

        if not title or len(title) > 200 or len(subtitle) > 200:
            raise ValueError("试卷标题不能为空，且标题与副标题均不能超过 200 字。")
        if paper_type not in {"exam", "quiz", "exam_19"}:
            raise ValueError("不支持的试卷模板。")
        if not isinstance(questions_payload, list) or not questions_payload:
            raise ValueError("试卷中至少需要包含一道题目。")
        if len(questions_payload) > 200:
            raise ValueError("单份试卷不能超过 200 道题。")

        normalized_items = []
        seen_question_ids = set()
        for item in questions_payload:
            if not isinstance(item, dict):
                raise ValueError("试卷题目数据格式不正确。")
            question_id = int(item.get("id"))
            score = int(item.get("score", 5))
            if question_id <= 0 or score < 0 or score > 100:
                raise ValueError("题目 ID 或分值不在有效范围内。")
            if question_id in seen_question_ids:
                raise ValueError("同一道题不能在一份试卷中重复出现。")
            seen_question_ids.add(question_id)
            normalized_items.append((question_id, score))

        questions = db.query(Question).filter(
            Question.id.in_(seen_question_ids)
        ).all()
        question_map = {question.id: question for question in questions}
        missing_ids = sorted(seen_question_ids - set(question_map))
        if missing_ids:
            raise ValueError("试卷中包含已删除或不存在的题目。")

        total_score = sum(score for _question_id, score in normalized_items)
        
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
        
        for idx, (qid, score) in enumerate(normalized_items):
            pq = PaperQuestion(
                paper_id=paper.id,
                question_id=qid,
                order_index=idx + 1,
                score=score
            )
            db.add(pq)
            question = question_map[qid]
            question.usage_count = (question.usage_count or 0) + 1
                
        db.commit()
        schedule_database_export(background_tasks, operation="save_paper")
        return {"status": "success", "message": "试卷保存成功！", "paper_id": paper.id}
    except (TypeError, ValueError) as e:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存试卷失败: {str(e)}"}, status_code=500)

@app.get("/api/papers")
def list_papers(db: Session = Depends(get_db)):
    """获取所有历史试卷列表"""
    try:
        from sqlalchemy import func

        rows = (
            db.query(Paper, func.count(PaperQuestion.id))
            .outerjoin(PaperQuestion, PaperQuestion.paper_id == Paper.id)
            .group_by(Paper.id)
            .order_by(Paper.created_at.desc())
            .all()
        )
        result = []
        for p, q_count in rows:
            d = p.to_dict()
            d["question_count"] = int(q_count)
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
        
        rows = (
            db.query(PaperQuestion, Question)
            .join(Question, Question.id == PaperQuestion.question_id)
            .filter(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_index.asc())
            .all()
        )

        questions_list = [
            {
                "id": question.id,
                "score": paper_question.score,
                "question": question.to_dict(),
            }
            for paper_question, question in rows
        ]
                
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
        reference_counts = {}
        for paper_question in pqs:
            reference_counts[paper_question.question_id] = (
                reference_counts.get(paper_question.question_id, 0) + 1
            )
        questions = db.query(Question).filter(
            Question.id.in_(reference_counts)
        ).all()
        for question in questions:
            if question.usage_count:
                question.usage_count = max(
                    0,
                    question.usage_count - reference_counts.get(question.id, 0),
                )
                
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
        db.delete(paper)
        db.commit()
        schedule_database_export(background_tasks, operation="delete_paper")
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

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
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

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)

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

def explain_latex_compile_error(log_text: str, tex_content: str) -> dict:
    """Explain one compile failure locally, then enrich it with the parse model."""
    diagnostic = build_local_latex_diagnostic(log_text, tex_content)
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv(
        "DEEPSEEK_PARSE_MODEL", "deepseek-v4-flash"
    )
    provider = resolve_text_provider(parse_model)
    if not provider.api_key:
        diagnostic["ai_note"] = "试卷拆解模型未配置，当前显示本地诊断结果。"
        return diagnostic

    system_prompt, user_prompt = build_latex_error_explanation_prompts(diagnostic)
    payload = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    is_deepseek = (
        "deepseek" in provider.model_name.lower()
        or "deepseek" in (provider.api_base or "").lower()
    ) and provider.model_name not in {"deepseek-chat", "deepseek-reasoner"}
    if is_deepseek and provider.reasoning_effort in {None, "default"}:
        payload["thinking"] = {"type": "disabled"}
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="latex_diagnostic",
    )

    try:
        response = post_chat_completion(
            provider,
            payload,
            timeout=45,
            provider_name=provider.provider_label,
        )
        raw_text = response.json()["choices"][0]["message"]["content"].strip()
        ai_value = parse_ai_json(raw_text)
        return merge_ai_latex_diagnostic(diagnostic, ai_value)
    except Exception:
        diagnostic["ai_note"] = "AI 暂时无法解释该错误，当前显示本地诊断结果。"
        return diagnostic


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

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
        pdf_bytes, log_or_err = compile_tex_to_pdf(tex_content, image_paths)
        
        if pdf_bytes:
            filename = f"sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf" if target == "sheet" else f"paper_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return Response(content=pdf_bytes, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            })
        else:
            diagnostic = explain_latex_compile_error(log_or_err, tex_content)
            return JSONResponse(
                content={
                    "status": "error",
                    "message": diagnostic.get("summary", "PDF 编译失败"),
                    "diagnostic": diagnostic,
                },
                status_code=400,
            )
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"编译 PDF 异常: {str(e)}"}, status_code=500)


@app.get("/api/runtime/pandoc/status")
def get_pandoc_runtime_status():
    """Report whether Word-native formula conversion is currently available."""
    install_state = PANDOC_INSTALL_MANAGER.snapshot()
    if install_state.get("status") in {"queued", "downloading", "verifying"}:
        return {"status": "success", "pandoc": install_state}
    return {"status": "success", "pandoc": pandoc_status()}


@app.post("/api/runtime/pandoc/install")
def install_pandoc_runtime():
    """Start or join the single verified app-local Pandoc installation task."""
    state = PANDOC_INSTALL_MANAGER.ensure()
    status_code = 200 if state.get("status") == "ready" else 202
    return JSONResponse(
        status_code=status_code,
        content={"status": "success", "pandoc": state},
    )


@app.get("/api/runtime/pandoc/install/{task_id}")
def get_pandoc_install_status(task_id: str):
    state = PANDOC_INSTALL_MANAGER.snapshot()
    if not state.get("task_id") or state.get("task_id") != task_id:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pandoc 安装任务不存在或已过期。"},
        )
    return {"status": "success", "pandoc": state}


@app.post("/api/paper/export/word")
def export_paper_word(payload: dict, db: Session = Depends(get_db)):
    """导出包含试卷正文与含答案解析两个 Word 文件的 ZIP 压缩包。"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        as_single_docx = bool(payload.get("as_single_docx", False))
        include_answers = bool(payload.get("include_answers", False))

        q_ids = [int(q.get("id")) for q in questions_input if q.get("id")]
        questions_db = db.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q.to_dict() for q in questions_db}

        questions_data = []
        for item in questions_input:
            qid = int(item.get("id"))
            if qid not in q_map:
                continue
            q_dict = dict(q_map[qid])
            if item.get("figure_align"):
                q_dict["figure_align"] = item.get("figure_align")
            q_item = {
                "question": q_dict,
                "score": int(item.get("score", 5)),
            }
            if item.get("solution_space") is not None:
                q_item["solution_space"] = item.get("solution_space")
            questions_data.append(q_item)

        if not questions_data:
            return JSONResponse(
                content={"status": "error", "message": "卷面为空，无法导出 Word。"},
                status_code=400,
            )

        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', "_", title.strip()) or "试卷"

        if as_single_docx:
            docx_bytes, diagnostics = build_word_document(
                title,
                subtitle,
                paper_type,
                questions_data,
                include_answers=include_answers,
                show_secret=show_secret,
                show_notice=show_notice,
                uploads_dir=UPLOAD_DIR,
            )
            suffix = "_含答案与解析" if include_answers else ""
            filename = f"{safe_title}{suffix}.docx"
            encoded_filename = quote(filename)
            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": f"attachment; filename=\"paper.docx\"; filename*=utf-8''{encoded_filename}",
                    "X-Word-Native-Formulas": str(diagnostics.get("native_formulas", 0)),
                    "X-Word-Fallback-Formulas": str(diagnostics.get("fallback_formulas", 0)),
                    "X-Word-Failed-Formulas": str(diagnostics.get("failed_formulas", 0)),
                    "X-Word-Missing-Images": str(diagnostics.get("missing_images", 0)),
                    "X-Word-Answer-Card-Omitted": "1" if diagnostics.get("answer_card_omitted") else "0",
                },
            )

        # Default: build clean student docx and full teacher docx with answers into a ZIP bundle
        main_docx, main_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=False,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
        )
        ans_docx, ans_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=True,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
        )

        zip_bytes = create_word_bundle_zip(title, main_docx, ans_docx)
        filename = f"{safe_title}_Word打包.zip"
        encoded_filename = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=\"paper_word_bundle.zip\"; filename*=utf-8''{encoded_filename}",
                "X-Word-Native-Formulas": str(main_diag.get("native_formulas", 0) + ans_diag.get("native_formulas", 0)),
                "X-Word-Fallback-Formulas": str(main_diag.get("fallback_formulas", 0) + ans_diag.get("fallback_formulas", 0)),
                "X-Word-Failed-Formulas": str(main_diag.get("failed_formulas", 0) + ans_diag.get("failed_formulas", 0)),
                "X-Word-Missing-Images": str(main_diag.get("missing_images", 0) + ans_diag.get("missing_images", 0)),
                "X-Word-Answer-Card-Omitted": "1" if main_diag.get("answer_card_omitted") else "0",
            },
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"生成 Word 试卷包失败: {str(e)}"},
            status_code=500,
        )

# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
