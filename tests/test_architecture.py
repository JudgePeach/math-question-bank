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
    assert "EditorState.questionId !== requestedQuestionId" in import_source


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


def test_paper_parsers_use_defensive_ai_json_parser():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    assert main_source.count("parse_ai_json(raw_ai_text, raw_markdown=latex_content)") == 1
    assert main_source.count("parse_ai_json(raw_ai_text, raw_markdown=model_source)") == 1


def test_backend_modules_and_cli_tools_live_in_packages():
    for module_name in ("database.py", "paper_helper.py", "sync_helper.py"):
        assert not (PROJECT_ROOT / module_name).exists()
        assert (PROJECT_ROOT / "mathbank" / module_name).is_file()

    for script_name in (
        "search_questions.py",
        "migrate_fillin.py",
        "migrate_choice_parentheses.py",
        "build_release.py",
    ):
        assert not (PROJECT_ROOT / script_name).exists()
        assert (PROJECT_ROOT / "scripts" / script_name).is_file()
