import ast
import asyncio

from mathbank.curriculums import get_curriculum_preset, load_curriculum
from mathbank.paths import (
    CURRICULUMS_DIR,
    DATABASE_FILE,
    PROJECT_ROOT,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from mathbank.prompts import build_ai_solve_prompts


STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"


def test_python_sources_do_not_use_deprecated_fitz_import():
    source_paths = [PROJECT_ROOT / "main.py"]
    for directory_name in ("mathbank", "scripts", "tests"):
        source_paths.extend((PROJECT_ROOT / directory_name).rglob("*.py"))

    violations = []
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "fitz" for alias in node.names
            ):
                violations.append(f"{source_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module == "fitz":
                violations.append(f"{source_path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert violations == []


def test_shared_paths_are_absolute_and_project_anchored():
    for path in (DATABASE_FILE, STATIC_DIR, TEMPLATES_DIR, CURRICULUMS_DIR):
        assert path.is_absolute()
    assert DATABASE_FILE.parent == PROJECT_ROOT
    assert STATIC_DIR.parent == PROJECT_ROOT


def test_all_curriculum_presets_load_from_resources():
    for version in ("A", "B", "S", "H"):
        preset = get_curriculum_preset(version)
        assert preset["version"] == version
        assert preset["metadata"]["curriculum"] == load_curriculum(version)
        assert preset["metadata"]["question_types"]
        assert preset["metadata"]["difficulties"]


def test_solve_prompt_builder_preserves_required_structure():
    system_prompt, user_prompt = build_ai_solve_prompts(
        "single_choice",
        "若 $x=1$，求函数值。",
        ocr_result="草稿",
        custom_prompt="简化步骤",
    )
    assert "【参考答案】" in system_prompt
    assert "严禁超纲" in system_prompt
    assert "草稿" in user_prompt
    assert "简化步骤" in user_prompt
    assert "若 $x=1$" in user_prompt


def test_solve_prompt_allows_high_school_derivatives_but_bans_university_calculus():
    system_prompt, _ = build_ai_solve_prompts("detailed_answer", "求导数")

    assert "允许使用普通高中课程中的导数及其应用" in system_prompt
    assert "洛必达法则" in system_prompt
    assert "泰勒展开" in system_prompt
    assert "积分等" in system_prompt
    assert "微积分、洛必达法则、泰勒展开等大学高等数学方法" not in system_prompt


def test_solve_prompt_emits_single_latex_backslash_in_headings():
    system_prompt, _ = build_ai_solve_prompts("single_choice", "求 1+1")

    assert r"\textbf{【参考答案】}" in system_prompt
    assert r"\\textbf{【参考答案】}" not in system_prompt


def test_editor_identity_and_meta_preview_have_single_sources():
    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")
    editor_source = (STATIC_JS_DIR / "editor.js").read_text(encoding="utf-8")
    import_source = (STATIC_JS_DIR / "import.js").read_text(encoding="utf-8")
    combined_source = "\n".join((api_source, editor_source, import_source))

    assert "const EditorState =" in api_source
    assert "window.EditorState = EditorState" in api_source
    for legacy_name in (
        "currentQuestionId",
        "currentSeqNum",
        "currentCreatedAt",
        "currentDraftId",
    ):
        assert legacy_name not in combined_source

    assert combined_source.count("getElementById('paperBadges')") == 1
    assert "window.renderEditorPaperMeta = renderEditorPaperMeta" in editor_source
    assert "renderEditorPaperMeta();" in import_source
    select_start = import_source.index("function selectQuestion(")
    select_end = import_source.index("window.reloadCurrentQuestionSilently", select_start)
    select_source = import_source[select_start:select_end]
    assert "EditorState.useQuestion(item)" not in select_source
    assert select_source.index(".then(async fullItem =>") < select_source.index("EditorState.useQuestion(fullItem)")
    assert "loadSequence !== questionDetailLoadSequence" in select_source


def test_multimodal_ai_routes_use_shared_provider_resolvers():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    for legacy_helper in (
        "def ocr_via_siliconflow",
        "def ocr_via_ali_bailian",
        "def ocr_via_zhongzhan",
    ):
        assert legacy_helper not in main_source

    assert "def ocr_via_provider" in main_source
    assert "resolve_ocr_provider(engine)" in main_source
    assert "resolve_ocr_fallbacks(prefer_engine)" in main_source
    assert main_source.count("resolve_draw_provider(prefer_draw)") == 2


def test_all_text_ai_routes_parse_models_with_the_shared_effort_rules():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    assert "parse_effort=False" not in main_source
    assert "robust_request_post" not in main_source
    assert "apply_model_thinking_policy" in main_source


def test_bailian_model_presets_are_current_and_task_specific():
    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")

    assert "const BAILIAN_MODEL_PRESETS_BY_TASK" in api_source
    assert "const BAILIAN_MODEL_DEFAULTS_BY_TASK" in api_source
    for task_key in ("solve", "parse", "classify", "ocr", "draw"):
        assert f"{task_key}:" in api_source
    for current_model in ("qwen3.7-flash", "qwen3.7-plus", "qwen3.8-max"):
        assert current_model in api_source
    for removed_preset in (
        '"qwen3-vl-flash"',
        '"qwen-vl-plus"',
        '"qwen-vl-max"',
        '"qwen-max"',
        '"qwen-plus"',
        '"qwen3.5-ocr"',
    ):
        assert removed_preset not in api_source
    assert "models = [selectedValue, ...models]" in api_source


def test_blocking_upload_and_ai_handlers_run_in_fastapi_worker_threads():
    import inspect
    import main

    for handler_name in (
        "upload_image",
        "upload_tex_source",
        "upload_batch_images",
        "upload_pdf_task",
        "upload_docx_task",
        "ai_solve",
        "ai_classify",
        "ai_parse_paper",
    ):
        assert not inspect.iscoroutinefunction(getattr(main, handler_name))


def test_paper_parsers_use_defensive_ai_json_parser():
    def called_functions(path, name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        return {node.func.id for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

    for handler in ("parse_paper_text_internal", "ai_parse_paper"):
        assert "parse_paper_completion" in called_functions(PROJECT_ROOT / "main.py", handler)
    assert "parse_ai_json" in called_functions(
        PROJECT_ROOT / "mathbank" / "paper_parse.py", "parse_paper_completion"
    )


def test_backend_modules_and_cli_tools_live_in_packages():
    for module_name in (
        "asset_security.py",
        "backup.py",
        "database.py",
        "db_migrations.py",
        "health.py",
        "paper_helper.py",
        "sync_helper.py",
        "task_manager.py",
    ):
        assert not (PROJECT_ROOT / module_name).exists()
        assert (PROJECT_ROOT / "mathbank" / module_name).is_file()

    for script_name in (
        "search_questions.py",
        "backup.py",
        "restore.py",
        "migrate_fillin.py",
        "migrate_choice_parentheses.py",
        "build_release.py",
    ):
        assert not (PROJECT_ROOT / script_name).exists()
        assert (PROJECT_ROOT / "scripts" / script_name).is_file()


def test_server_holds_restore_runtime_lock_before_database_initialization():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    lock_call = main_source.index(
        "_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()"
    )
    database_init = main_source.index("\ninit_db()", lock_call)
    assert lock_call < database_init
    assert "atexit.register(_RUNTIME_LOCK.close)" in main_source[lock_call:database_init]


def test_lifespan_owns_post_startup_maintenance(monkeypatch):
    import main

    calls = []

    class FakeTasks:
        def start_maintenance(self, *, interval_seconds):
            calls.append(("task_maintenance", interval_seconds))

        def shutdown(self, *, wait):
            calls.append(("task_shutdown", wait))

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            calls.append(("thread_created", target.__name__, name, daemon))

        def start(self):
            calls.append(("thread_started",))

    monkeypatch.setattr(main, "IS_TESTING", False)
    monkeypatch.setattr(main, "DOCUMENT_TASKS", FakeTasks())
    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    async def exercise_lifespan():
        async with main.app_lifespan(main.app):
            calls.append(("app_ready",))

    asyncio.run(exercise_lifespan())

    assert calls == [
        ("task_maintenance", 60.0),
        (
            "thread_created",
            "start_startup_cleanup",
            "mathbank-post-startup-maintenance",
            True,
        ),
        ("thread_started",),
        ("app_ready",),
        ("task_shutdown", False),
    ]


def test_post_startup_maintenance_backs_up_before_mutating(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: calls.append("sleep"))
    monkeypatch.setattr(
        main, "create_full_backup_if_due", lambda: calls.append("backup")
    )
    monkeypatch.setattr(
        main, "heal_database_curriculum_names", lambda: calls.append("heal")
    )
    monkeypatch.setattr(main, "clean_orphaned_images", lambda: calls.append("clean"))
    monkeypatch.setattr(
        main, "recalibrate_usage_counts", lambda: calls.append("recalibrate")
    )
    monkeypatch.setattr(
        main,
        "rebuild_all_missing_fingerprints",
        lambda *_args, **_kwargs: calls.append("fingerprints") or {"backfilled": 0},
    )
    monkeypatch.setattr(
        main, "print_optional_tool_diagnostics", lambda: calls.append("tools")
    )

    main.start_startup_cleanup()

    assert calls == [
        "sleep",
        "backup",
        "heal",
        "clean",
        "recalibrate",
        "fingerprints",
        "tools",
    ]


def test_post_startup_maintenance_stops_mutation_when_backup_fails(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: calls.append("sleep"))

    def fail_backup():
        calls.append("backup")
        raise OSError("backup unavailable")

    monkeypatch.setattr(main, "create_full_backup_if_due", fail_backup)
    monkeypatch.setattr(
        main, "heal_database_curriculum_names", lambda: calls.append("heal")
    )
    monkeypatch.setattr(main, "clean_orphaned_images", lambda: calls.append("clean"))
    monkeypatch.setattr(
        main, "recalibrate_usage_counts", lambda: calls.append("recalibrate")
    )
    monkeypatch.setattr(
        main, "print_optional_tool_diagnostics", lambda: calls.append("tools")
    )

    main.start_startup_cleanup()

    assert calls == ["sleep", "backup", "tools"]


def test_metadata_load_does_not_run_full_database_heal_before_ready():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    metadata_start = main_source.index("def load_or_init_metadata()")
    metadata_end = main_source.index("def get_active_version_code()", metadata_start)
    assert "heal_database_curriculum_names()" not in main_source[
        metadata_start:metadata_end
    ]
