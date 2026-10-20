"""Frozen upstream PDF processing contracts; no Git refs or live API required.

The historical cloud baseline is JudgePeach/math-question-bank 1a6e50a4.
The upstream routing is frozen.  Explicit additions for knowledge labels,
teacher-entered titles and cooperative cancellation are removed from a copy
before comparing its AST with that baseline.  Every other statement remains
covered; uploads and transient storage also have real isolated HTTP tests.
"""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CLOUD_REVISION = "1a6e50a4"
# Portable AST digests generated once from the fixed upstream source at
# 1a6e50a4fcd9ce27c39088ba2d08dc231d23f448 using _ast_key below; confirmed
# identical with Python 3.10 and 3.14. Runtime tests never read a Git revision.
FUNCTION_HASHES = {
    "run_pdf_parsing_task": (
        "835ed56f7377c68dab29e906a05bee4456dadab5905d984e9a01f23c0cc6f5a6",
        "ac6699a2e315dcced3edd2eb795b8fc49d51cdcec6fc7016bafa016b7907e458"),
    "parse_paper_text_internal": (
        "8d616efbfc715ab5d7744b6628b9f25bb26eb203d2a78be3111acc2a1529ab67",
        "2f53f5e24d8fbe1d33488a9611cd71de1e4bfbc62f1c96ad0ae8460e46b39052"),
    "post_process_pdf_parsed_questions": (
        "9ee15bb7fdf05ac8bc2cbd444f2b0698d04d3cc70c42bb3a70353f81327b7ce6",
        "bcd76d30669e036bfd3313c906cbccc182698455b293e297e8b5a471d59cb82a"),
    "parse_page_range": (
        "1874f5e6463fad1ae7f0c7e685e1af182103219a7b39f6a1c662ecfb455f3f30",
        "a6af08897a22cd9107c84dbbc872baebf0c41e697f4fd963f2e58b8a4d6f20e1"),
}


def _ast_key(node):
    """Serialize semantic fields consistently on Python 3.10 through 3.14.

    ast.dump changed its default handling of empty lists/optional fields.
    Render all those fields ourselves; omit only the empty type_params field
    added to these node types in Python 3.12.  Nonempty parameters stay hashed.
    """
    if isinstance(node, ast.AST):
        fields = list(ast.iter_fields(node))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Preserve a nonempty field even on an older AST host used by the
            # portability regression below, where it is absent from _fields.
            if not any(name == "type_params" for name, _ in fields) and hasattr(node, "type_params"):
                fields.append(("type_params", node.type_params))
            fields = [(name, value) for name, value in fields
                      if not (name == "type_params" and value == [])]
        return type(node).__name__ + "(" + ", ".join(
            name + "=" + _ast_key(value) for name, value in fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(_ast_key(value) for value in node) + "]"
    return repr(node)


def test_portable_ast_omits_only_empty_version_added_type_parameters():
    ordinary = ast.parse("def f(x):\n    return x\n").body[0]
    empty = deepcopy(ordinary)
    empty.type_params = []
    assert _ast_key(empty) == _ast_key(ordinary)
    generic = deepcopy(ordinary)
    generic.type_params = [ast.Name(id="T", ctx=ast.Load())]
    assert _ast_key(generic) != _ast_key(ordinary)
    assert "type_params=[Name(id='T'" in _ast_key(generic)
    changed = ast.parse("def f(x):\n    return y\n").body[0]
    assert _ast_key(changed) != _ast_key(ordinary)


def _replace_statement_block(node, current, original, *, count=1):
    """Allow one exact reviewed increment, never a broad function rewrite."""
    current = ast.parse(current).body
    original = ast.parse(original).body
    signature = [_ast_key(item) for item in current]
    replaced = 0

    def visit(parent):
        nonlocal replaced
        for _name, value in ast.iter_fields(parent):
            if isinstance(value, list):
                index = 0
                while index < len(value):
                    candidate = value[index:index + len(current)]
                    if (len(candidate) == len(current)
                            and all(isinstance(item, ast.AST) for item in candidate)
                            and [_ast_key(item) for item in candidate] == signature):
                        value[index:index + len(current)] = deepcopy(original)
                        replaced += 1
                        index += len(original)
                    else:
                        if isinstance(value[index], ast.AST):
                            visit(value[index])
                        index += 1
            elif isinstance(value, ast.AST):
                visit(value)
    visit(node)
    assert replaced == count, "Reviewed PDF increment changed; inspect it before updating the contract"


def _upstream_function_copy(node):
    node = deepcopy(node)
    if node.name == "parse_paper_text_internal":
        _replace_statement_block(node, '''
curriculum = copy.deepcopy(get_current_curriculum())
system_instructions = build_pdf_parse_system_prompt(curriculum, generate_answers_bool, load_ai_knowledge_catalog())
''', '''
system_instructions = build_pdf_parse_system_prompt(get_current_curriculum(), generate_answers_bool)
''')
        _replace_statement_block(node,
            "validate_import_knowledge(parsed_questions, curriculum, diagnostics=diagnostics)",
            "validate_import_knowledge(parsed_questions, get_current_curriculum())", count=2)
    elif node.name == "post_process_pdf_parsed_questions":
        _replace_statement_block(node, '''
knowledge_catalog = load_ai_knowledge_catalog()
for question in parsed_questions:
    if "knowledge_points" in question:
        annotate_ai_knowledge_points(question, knowledge_catalog)
''', "")
    elif node.name == "run_pdf_parsing_task":
        _replace_statement_block(node, '''
task = DOCUMENT_TASKS.snapshot(task_id) or {}
options = (task.get("document_input") or {}).get("options") or {}
paper_title = str(options.get("paper_title") or "").strip()
if not paper_title:
    paper_title = extract_title_from_latex(full_latex_content) or os.path.splitext(filename)[0]
''', '''
paper_title = os.path.splitext(filename)[0]
auto_title = extract_title_from_latex(full_latex_content)
if auto_title:
    paper_title = auto_title
''')
        loops = [item for item in ast.walk(node) if isinstance(item, ast.For)
                 and isinstance(item.iter, ast.Call) and isinstance(item.iter.func, ast.Name)
                 and item.iter.func.id == "iter_completed_futures"]
        assert len(loops) == 1
        assert _ast_key(loops[0].iter) == _ast_key(ast.parse(
            "iter_completed_futures(futures, check_pdf_cancelled)", mode="eval").body)
        loops[0].iter = ast.parse("concurrent.futures.as_completed(futures)", mode="eval").body
        _replace_statement_block(node,
            "cancelled = DOCUMENT_TASKS.is_cancelled(task_id) or not DOCUMENT_TASKS.exists(task_id)",
            "cancelled = DOCUMENT_TASKS.is_cancelled(task_id)")
        log = " 道完整题目；部分疑点题组仍需校对，未处理范围已保留，可展开原文与未匹配题段核对。"
        old = " 道完整题目；部分疑点题组未完成，原文与未匹配题段可展开核对。"
        literals = [item for item in ast.walk(node) if isinstance(item, ast.Constant) and item.value == log]
        assert len(literals) == 1
        literals[0].value = old
    return node


@pytest.mark.parametrize("function", FUNCTION_HASHES)
def test_cloud_pdf_functions_allow_only_reviewed_increments_over_original_ast(function):
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    node = functions[function]
    original_source, original_ast = FUNCTION_HASHES[function]
    if function == "parse_page_range":
        assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == original_source
    canonical = _upstream_function_copy(node)
    assert hashlib.sha256(_ast_key(canonical).encode()).hexdigest() == original_ast


def test_original_processor_has_no_new_local_draft_or_parallel_rules():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "run_pdf_parsing_task")
    body = ast.get_source_segment(source, function)
    for name in ("execute_legacy_pdf_parse", "execute_pdf_local_draft", "structured_fragments",
                 "PdfFigurePipeline", "run_pdf_pipeline", "run_project_legacy_parse",
                 "request_pdf_parallel_split", "request_pdf_full_context_split"):
        assert name not in body
    assert "prepare_pdf_task_plan" in body
    assert "request_pdf_hybrid" in body
    assert "verify_pdf_source_suspicions" in body
    # The upstream source retained proven native/transcript metadata routing;
    # forcing every plan to full-body API would be another behavioral change.
    assert 'pdf_plan["mode"] in {"hybrid", "whole_metadata"}' in body
    assert 'max_workers=min(len(pages_requiring_ocr), 4)' in body


MODULE_HASHES = {
    "pdf_figures.py": "ac4da7da10dfcf9630e234019e9a8030f4d2765481572ddec296d795b5112b16",
    "pdf_inspector_helper.py": "c4e17353afa406daf9990ba8d533f2dca16ed10908bb82c534fab0c8a4dd3fbf",
    "pdf_page_vision.py": "c2362c7f64f4397f07e488dcc4aca5d970347d305cc9b528ee61e3db7bdd86ef",
    "pdf_region_vision.py": "a6a7c95882f12a8aab05da4fc061b865d037399e2bc2754be5bd1a3cecee43bd",
    "pdf_source_verify.py": "c1bfb8096abdf289044bf2a54d5c3abc1822b56835f9ba7459a7ec4bc3d249c6",
    "pdf_vision_request.py": "43a13a271ebe5ef67cb67ed2625fdcd293bf591dbee265fd94a6aacb75f0fa10",
}


@pytest.mark.parametrize("filename", MODULE_HASHES)
def test_cloud_pdf_modules_match_original_bytes(filename):
    content = (ROOT / "mathbank" / filename).read_bytes()
    if filename == "pdf_vision_request.py":
        # The single-image/new callers may request one attempt; old PDF callers
        # retain the same default two-attempt budget and transport behavior.
        source = content.decode("utf-8")
        increments = [
            ("    max_attempts: int = PDF_VISION_MAX_ATTEMPTS,\n", ""),
            ('    if type(max_attempts) is not int or not 1 <= max_attempts <= PDF_VISION_MAX_ATTEMPTS:\n'
             '        raise ValueError("PDF 请求最多允许1或2次尝试。")\n', ""),
            ("range(1, max_attempts + 1)", "range(1, PDF_VISION_MAX_ATTEMPTS + 1)"),
            ('"max_attempts": max_attempts', '"max_attempts": PDF_VISION_MAX_ATTEMPTS'),
            ("retrying=retryable and number < max_attempts", "retrying=retryable and number < PDF_VISION_MAX_ATTEMPTS"),
        ]
        for current, original in increments:
            assert source.count(current) == 1
            source = source.replace(current, original)
        content = source.encode("utf-8")
    assert hashlib.sha256(content).hexdigest() == MODULE_HASHES[filename]


PROMPT_PAGE_INFO = {
    "page_index": 2,
    "candidates": [
        {"id": "p3-r1", "bbox": [120, 160, 460, 580], "type": "raster",
         "native_box_eligible": True, "native_bbox_points": [32, 45, 132, 165],
         "private_diagnostic": "not-model-data"},
        {"id": "p3-v1", "bbox": [510, 610, 830, 880], "type": "vector",
         "native_box_eligible": False},
    ],
    "figure_slots": [{"id": "p3-s1", "label": "图1"}],
}
PROMPT_SOURCE = '16. 如图，$f(x)=\\dfrac{x+1}{x-1}$。\n[插图待补: 图1]\n求函数的定义域。'
PROMPT_CURRICULUM = {
    "必修一": {"3. 函数的概念与性质": ["3.1 函数的概念及其表示"]},
    "选修一": {"3. 圆锥曲线的方程": []},
}
PROMPT_HASHES = {
    "common_ocr": "e8fcc55b4ae71227aab446e985ef53ad6d13b370b48d92d1b14f8eea2e56d70e",
    "visual_fidelity": "48c66a4789adfe7317d41cae38a1f896c409fe138834d81e7adc990eadc05452",
    "structured_output": "0d6f1268827c7ddb8563e90ba9274a9bacdb2ac2cc3bfa3f3cdb20fc861258bd",
    "page_vision": "84a1fff5da490b94458e6989b8b8708fb48de72d946b67eebed1bcc01e567953",
    "layout": "badaa41235ac862f14fe72fa3f668e7af3769844379da6d2ad507bb793b82b35",
    "parse_extract": "8932113bdb616d093643c90113b5f9cbf873f4df04e968c3aa1c1c33442e82e6",
    "parse_generate": "502b481838209420ca7b671219dafeaa82761728887c294f537c4928f43df1c8",
    "region_figures": "08c5141d98b8649d066544ac22be5744a404dba1395bd316b74bcfa18c7f9d7b",
    "region_text": "3a81e78d607f18b2dc49a89fdb0c7ae024d3ce0063c4486d05b930b042fc0bff",
}


@pytest.mark.parametrize("name", PROMPT_HASHES)
def test_cloud_pdf_paid_prompt_outputs_match_original_golden(name):
    """Hash actual strings sent by original builders, including shared literals.

    Expected digests were generated once by executing prompts.py from the
    frozen upstream revision. Runtime tests neither fetch Git nor execute a
    historical fixture which could accidentally change with current code.
    """
    from mathbank import prompts

    regions = [{"id": "r1", "page_info": PROMPT_PAGE_INFO},
               {"id": "r2", "page_info": {"candidates": []}}]
    values = {
        "common_ocr": lambda: prompts.PDF_ORIGINAL_COMMON_OCR_PROMPT,
        "visual_fidelity": lambda: prompts.PDF_VISUAL_FIDELITY_RULE,
        "structured_output": lambda: prompts.PDF_STRUCTURED_OUTPUT_INSTRUCTIONS,
        "page_vision": lambda: prompts.build_pdf_page_vision_prompt(PROMPT_PAGE_INFO),
        "layout": lambda: prompts.build_pdf_layout_prompt(PROMPT_SOURCE, PROMPT_PAGE_INFO),
        "parse_extract": lambda: prompts.build_pdf_parse_system_prompt(PROMPT_CURRICULUM, False),
        "parse_generate": lambda: prompts.build_pdf_parse_system_prompt(PROMPT_CURRICULUM, True),
        "region_figures": lambda: prompts.build_pdf_region_vision_prompt(regions, include_figures=True),
        "region_text": lambda: prompts.build_pdf_region_vision_prompt(regions, include_figures=False),
    }
    value = values[name]()
    if name in {"parse_extract", "parse_generate"}:
        from mathbank.ai_knowledge_points import build_ai_knowledge_prompt
        addition = build_ai_knowledge_prompt()
        field = '      "knowledge_points": [],\n'
        assert value.count(addition) == value.count(field) == 1
        value = value.replace(addition, "").replace(field, "")
    assert hashlib.sha256(value.encode("utf-8")).hexdigest() == PROMPT_HASHES[name]
