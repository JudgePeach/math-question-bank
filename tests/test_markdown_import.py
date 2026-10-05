"""Markdown paper-import regressions.

API tests must run in an isolated source checkout without the user's database,
uploads, retained assets, or credentials. Importing ``main`` initializes local
runtime storage, so an in-memory database fixture alone does not isolate it.
"""

import io
import json
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image
import pytest


@pytest.fixture(autouse=True)
def deny_real_ai_requests(monkeypatch):
    """A missing mock must fail locally instead of using a configured provider."""
    from mathbank import ai_http

    def denied(*_args, **_kwargs):
        raise AssertionError("Markdown import tests must not make real AI requests")

    monkeypatch.setattr(ai_http, "robust_request_post", denied)
    monkeypatch.setenv("PREFER_PARSE_MODEL", "BAILIAN/qwen3.7-max")
    monkeypatch.setenv("ALI_BAILIAN_API_KEY", "fake-markdown-test-key")
    monkeypatch.setenv("ALI_BAILIAN_API_BASE", "https://markdown-test.invalid/v1/")


@pytest.fixture
def headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _question(content, answer="", **extra):
    return {
        "content": content,
        "answer_markdown": answer,
        "question_type": "detailed_answer",
        "category_compulsory": "必修一",
        "category_chapter": "1. 集合与常用逻辑用语",
        "difficulty": "easy_error",
        "source": None,
        "referenced_images": [],
        **extra,
    }


def _parse(client, headers, monkeypatch, source, questions, *, mapping=None,
           source_format="markdown", inspect_request=None, generate_answers=False):
    """Mock only the provider boundary; exercise actual parsing and reconciliation."""
    from mathbank import ai_http

    calls = []

    def fake_post(_url, **kwargs):
        request = kwargs["json"]
        calls.append(request)
        if inspect_request:
            inspect_request(request)
        payload = questions(request) if callable(questions) else questions
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps({"questions": payload}, ensure_ascii=False)},
            }]
        }
        return response

    monkeypatch.setattr(ai_http, "robust_request_post", fake_post)
    form = {
        "latex_content": source,
        "paper_title": "",
        "image_mapping_json": json.dumps(mapping or {}),
        "generate_answers": "true" if generate_answers else "false",
    }
    if source_format is not None:
        form["source_format"] = source_format
    response = client.post("/api/ai/parse-paper", data=form, headers=headers)
    assert response.status_code == 200, response.text
    assert len(calls) == 1, "Splitting extracts original answers in a single request"
    return response.json()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_markdown_decoder_preserves_percent_math_and_body(encoding):
    from mathbank.markdown_helper import decode_and_prepare_markdown

    source = "# 高二数学试卷\n\n1. 增长率 20%，求 $x+1$。\n![图](figures/curve.png)\n"
    result = decode_and_prepare_markdown(source.encode(encoding))
    assert result["source"] == source
    assert result["model_source"] == source
    assert result["title"] == "高二数学试卷"
    assert result["diagnostics"]["encoding"] == ("utf-8-sig" if encoding == "utf-8" else encoding)
    assert result["diagnostics"]["encoding_fallback"] is (encoding == "gb18030")


@pytest.mark.parametrize("source,title", [
    ("说明\n\n# 第一份试卷\n\n# 第二份试卷\n1. 题目", "第一份试卷"),
    ("高一阶段检测\n===============\n\n1. 题目", "高一阶段检测"),
    ("```md\n# 示例标题\n```\n\n# 正式试卷\n1. 题目", "正式试卷"),
])
def test_markdown_title_uses_first_visible_h1(source, title):
    from mathbank.markdown_helper import prepare_markdown_source

    result = prepare_markdown_source(source)
    assert result["title"] == title
    assert result["source"] == source
    assert result["model_source"] == source


def test_markdown_upload_accepts_uppercase_suffix_and_decodes(client, headers):
    source = "# 旧版中文试卷\n\n1. 比例为 20%，后面的正文不得丢失。"
    response = client.post(
        "/api/upload/markdown-source",
        files={"file": ("试卷.MD", source.encode("gb18030"), "text/markdown")},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["source"] == source
    assert data["title"] == "旧版中文试卷"
    assert data["diagnostics"]["encoding"] == "gb18030"


@pytest.mark.parametrize("filename", ["paper.tex", "paper.txt", "paper.markdown", "paper.md.exe"])
def test_markdown_upload_rejects_other_suffixes(client, headers, filename):
    response = client.post(
        "/api/upload/markdown-source",
        files={"file": (filename, b"# paper", "text/markdown")},
        headers=headers,
    )
    assert response.status_code == 400


def test_markdown_upload_rejects_empty_and_oversized_sources(client, headers):
    empty = client.post(
        "/api/upload/markdown-source", files={"file": ("paper.md", b"", "text/markdown")},
        headers=headers,
    )
    oversized = client.post(
        "/api/upload/markdown-source",
        files={"file": ("paper.md", b"a" * (5 * 1024 * 1024 + 1), "text/markdown")},
        headers=headers,
    )
    assert empty.status_code == 400
    assert oversized.status_code == 413


def test_parse_paper_rejects_unknown_source_format_before_ai(client, headers):
    response = client.post(
        "/api/ai/parse-paper",
        data={"latex_content": "1. 题目", "source_format": "html"},
        headers=headers,
    )
    assert response.status_code == 400


def test_markdown_parse_uses_own_prompt_and_restores_original_formulas(client, headers, monkeypatch):
    source = "# 公式保真试卷\n\n1. 利率 20%，已知 $x \\in \\mathbb{R}$，求 \\(x+1\\)。"

    def inspect(request):
        assert "Markdown" in request["messages"][0]["content"]
        sent_source = request["messages"][1]["content"]
        assert "利率 20%，已知" in sent_source
        assert r"$x \in \mathbb{R}$" in sent_source

    def questions(request):
        lock_ids = re.findall(r'id="(MBM_[^"]+)"', request["messages"][1]["content"])
        assert len(lock_ids) == 2
        return [_question(f"利率 20%，已知 [[{lock_ids[0]}]]，求 [[{lock_ids[1]}]]。")]

    data = _parse(client, headers, monkeypatch, source, questions, inspect_request=inspect)
    assert data["source_format"] == "markdown"
    question = data["questions"][0]
    assert question["content"] == r"利率 20\%，已知 $x \in \mathbb{R}$，求 \(x+1\)。"
    assert question["source"] == "公式保真试卷"
    assert data["tex_diagnostics"]["math_locks_restored"] == 2
    assert data["tex_diagnostics"]["question_count_actual"] == 1


def test_markdown_original_answer_is_retained_in_its_own_formula_slots(client, headers, monkeypatch):
    source = "# 原版答案卷\n\n1. 计算 $1+1$。\n\n参考答案：\n\n1. $2$。"

    def questions(request):
        lock_ids = re.findall(r'id="(MBM_[^"]+)"', request["messages"][1]["content"])
        assert len(lock_ids) == 2
        return [_question(f"计算 [[{lock_ids[0]}]]。", f"[EXTRACTED_ORIGINAL] [[{lock_ids[1]}]]。")]

    data = _parse(client, headers, monkeypatch, source, questions)
    question = data["questions"][0]
    assert question["content"] == "计算 $1+1$。"
    assert question["answer_markdown"] == "$2$。"
    assert not question.get("source_review", {}).get("required")
    assert data["tex_diagnostics"]["math_locks_restored"] == 2


def test_markdown_answer_without_origin_marker_keeps_source_review(client, headers, monkeypatch):
    source = "1. 计算 $1+1$。"
    data = _parse(client, headers, monkeypatch, source, [_question("计算 $1+1$。", "2")])
    question = data["questions"][0]
    assert question["answer_markdown"] == "2"
    assert question["source_review"]["required"]
    assert any("答案未注明原卷来源" in reason for reason in question["source_review"]["reasons"])


def test_markdown_formula_difference_retains_source_evidence(client, headers, monkeypatch):
    source = "1. 已知 $x=1$，求值。\n【答案】$2$。"
    data = _parse(client, headers, monkeypatch, source,
                  [_question("已知 $x=3$，求值。", "[EXTRACTED_ORIGINAL]$4$。")])
    review = data["questions"][0]["source_review"]
    assert review["required"]
    assert "$x=1$" in review["source_excerpt"]
    assert "$2$" in review["source_excerpt"]


def test_markdown_percent_followed_lock_shells_restore_single_math_delimiters(client, headers, monkeypatch):
    source = "1. 增长率为 20%，求 $x$。\n【答案】收益占 50%，结果为 $y$。"

    def questions(request):
        lock_ids = re.findall(r'id="(MBM_[^"]+)"', request["messages"][1]["content"])
        assert len(lock_ids) == 2
        return [_question(
            f"增长率为 20%，求 $[[{lock_ids[0]}]]$。",
            f"[EXTRACTED_ORIGINAL]收益占 50%，结果为 $[[{lock_ids[1]}]]$。",
        )]

    data = _parse(client, headers, monkeypatch, source, questions)
    question = data["questions"][0]
    assert question["content"] == r"增长率为 20\%，求 $x$。"
    assert question["answer_markdown"] == r"收益占 50\%，结果为 $y$。"
    assert "$$" not in question["content"] + question["answer_markdown"]
    assert "MBM_" not in question["content"] + question["answer_markdown"]
    assert data["tex_diagnostics"]["math_locks_restored"] == 2


def test_markdown_code_example_formulas_stay_literal_before_real_formula(client, headers, monkeypatch):
    examples = (
        "阅读以下源码示例：\n\n"
        "```tex\n$x+1$ 和 \\(y+1\\)\n```\n\n"
        "行内代码 `$a+1$` 与 `\\[b+1\\]`。\n\n"
        "<pre>$c+1$ 和 \\(d+1\\)</pre>\n\n"
        "<code>$e+1$</code>\n\n"
        "    $f+1$ 与 \\[g+1\\]\n\n"
    )
    source = "1. " + examples + "正式题公式为 $z+1$。"

    def questions(request):
        sent_source = request["messages"][1]["content"]
        lock_ids = re.findall(r'id="(MBM_[^"]+)"', sent_source)
        assert len(lock_ids) == 1, "Only the actual question formula may be locked"
        assert examples in sent_source, "Code examples must be sent byte-for-byte"
        return [_question(examples + f"正式题公式为 [[{lock_ids[0]}]]。")]

    data = _parse(client, headers, monkeypatch, source, questions)
    result = data["questions"][0]["content"]
    assert "<pre>" not in result and "<code>" not in result
    for body in (r"$c+1$ 和 \(d+1\)", "$e+1$", r"    $f+1$ 与 \[g+1\]"):
        assert body in result
    assert result.endswith("正式题公式为 $z+1$。")
    assert data["tex_diagnostics"]["math_locks_created"] == 1
    assert data["tex_diagnostics"]["math_locks_restored"] == 1


def test_markdown_uploaded_images_keep_anchor_alt_title_and_deduplicate(client, headers, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    uploaded = client.post(
        "/api/upload/batch",
        files=[("files", (r"figures\curve.PNG", buffer.getvalue(), "image/png"))],
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    mapping = uploaded.json()["mapping"]
    path = mapping["curve.PNG"]
    content = '如图 ![曲线](figures/curve.png "题干图")，读数 20%，再看 ![同图](curve.png)。'
    answer = '先看 ![答案图](<figures/curve.png> "解析图")，再说明结论。'
    source = "1. " + content + "\n【答案】" + answer
    data = _parse(client, headers, monkeypatch, source,
                  [_question(content, "[EXTRACTED_ORIGINAL]" + answer, referenced_images=["curve.png"])],
                  mapping=mapping)
    question = data["questions"][0]
    assert question["content"] == f'如图 ![曲线]({path} "题干图")，读数 20\\%，再看 ![同图]({path})。'
    assert question["answer_markdown"] == f'先看 ![答案图](<{path}> "解析图")，再说明结论。'
    assert question["image_paths"] == [path]
    assert "![插图]" not in question["content"]
    assert data["tex_diagnostics"]["unmapped_images"] == []
    assert data["tex_diagnostics"]["unassigned_source_images"] == []


def test_markdown_inline_images_map_when_model_omits_referenced_images(client, headers, monkeypatch):
    content = "增长 20%，如图 ![原图](figures/curve.png)，然后回答。"
    question = _question(content)
    question.pop("referenced_images")
    data = _parse(client, headers, monkeypatch, "1. " + content, [question],
                  mapping={"CURVE.PNG": "/static/uploads/curve-local.png"})
    result = data["questions"][0]
    assert result["content"] == r"增长 20\%，如图 ![原图](/static/uploads/curve-local.png)，然后回答。"
    assert result["image_paths"] == ["/static/uploads/curve-local.png"]
    assert result["content"].count("/static/uploads/curve-local.png") == 1
    assert data["tex_diagnostics"]["unassigned_source_images"] == []


def test_markdown_mapping_protects_code_links_and_remote_images(client, headers, monkeypatch):
    content = (
        "本地图 ![题图](curve.png)。\n\n"
        "```md\n![代码图](curve.png)\n```\n\n"
        "行内 `![代码](curve.png)` 与 [资源链接](curve.png)。\n\n"
        "远程图 ![远程](https://example.invalid/curve.png \"来源\")。"
    )
    data = _parse(client, headers, monkeypatch, "1. " + content,
                  [_question(content, referenced_images=["curve.png", "https://example.invalid/curve.png"])],
                  mapping={"curve.png": "/static/uploads/curve-local.png"})
    question = data["questions"][0]
    assert question["content"] == content.replace("![题图](curve.png)", "![题图](/static/uploads/curve-local.png)")
    assert question["image_paths"] == ["/static/uploads/curve-local.png"]
    assert question["content"].count("/static/uploads/curve-local.png") == 1


def test_markdown_missing_image_is_reported_without_erasing_original_anchor(client, headers, monkeypatch):
    content = "如图 ![待上传](figures/missing.png)，求结论。"
    data = _parse(client, headers, monkeypatch, "1. " + content,
                  [_question(content, referenced_images=["missing.png"])])
    question = data["questions"][0]
    assert question["content"] == content
    assert question["image_paths"] == []
    report = data["tex_diagnostics"]
    assert any("missing.png" in item for item in report["unmapped_images"])
    assert any("missing.png" in warning for warning in report["warnings"])


def test_parse_paper_defaults_to_tex_and_retains_tex_preprocessing(client, headers, monkeypatch):
    source = "\\title{TeX兼容卷}\n\\begin{document}\n% TeX注释\n\\question 计算 $1+1$。\n\\end{document}"

    def inspect(request):
        sent_source = request["messages"][1]["content"]
        assert "TeX注释" not in sent_source
        assert r"\begin{document}" not in sent_source

    data = _parse(client, headers, monkeypatch, source, [_question("计算 $1+1$。")],
                  source_format=None, inspect_request=inspect)
    assert data["source_format"] == "tex"
    assert data["questions"][0]["source"] == "TeX兼容卷"
    assert data["tex_diagnostics"]["comments_removed"] == 1


@pytest.mark.parametrize("fail_commit", [False, True])
def test_markdown_candidate_save_reload_and_rollback_keep_visible_and_literal_assets_separate(
    client, headers, monkeypatch, db_session, fail_commit,
):
    """The real save/promotion contract runs only in the source-only checkout."""
    import main
    from sqlalchemy.exc import SQLAlchemyError

    prefix = "markdown-storage-" + uuid.uuid4().hex
    temporary, permanent = [], []
    urls = []
    png = io.BytesIO()
    Image.new("RGB", (7, 5), "navy").save(png, format="PNG")
    original_bytes = png.getvalue()
    try:
        for name in ("body", "answer"):
            filename = f"{prefix}-{name}.png"
            source_file = Path(main.TMP_UPLOAD_DIR) / filename
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_bytes(original_bytes)
            temporary.append(source_file)
            permanent.append(Path(main.UPLOAD_DIR) / filename)
            urls.append(f"/{main.UPLOAD_DIR_REL}/tmp/{filename}")
        literal_path = f"/{main.UPLOAD_DIR_REL}/tmp/{prefix}-missing-example.png"
        content = (
            "比例50%，见 ![题图](body.png)。\n\n"
            f"<pre>![源码图]({literal_path}) 60% $示例$</pre>\n\n"
            f"    ![源码同图]({urls[0]}) 70%\n\n"
            "求 $x$。"
        )
        answer = "[EXTRACTED_ORIGINAL]收益20%，见 ![答案图](answer.png)，结果 $y$。"
        raw_source = "# 存储兼容卷\n\n1. " + content + "\n【答案】" + answer.replace("[EXTRACTED_ORIGINAL]", "")
        uploaded = client.post(
            "/api/upload/markdown-source",
            files={"file": ("paper.md", raw_source.encode("utf-8"), "text/markdown")}, headers=headers,
        )
        assert uploaded.json()["source"] == raw_source
        result = _parse(client, headers, monkeypatch, raw_source, [_question(content, answer)],
                        mapping={"body.png": urls[0], "answer.png": urls[1]})
        candidate = result["questions"][0]
        assert candidate["image_paths"] == urls
        assert "比例50\\%" in candidate["content"] and "收益20\\%" in candidate["answer_markdown"]
        assert f"![源码同图]({urls[0]}) 70%" in candidate["content"]
        assert f"![源码图]({literal_path}) 60% $示例$" in candidate["content"]
        assert "$x$" in candidate["content"] and "$y$" in candidate["answer_markdown"]
        assert result["tex_diagnostics"]["markdown_storage_compatibility"]["percent_escapes"] == 2
        form = {key: candidate[key] for key in (
            "content", "answer_markdown", "question_type", "difficulty", "source",
        )}
        form["image_paths"] = json.dumps(candidate["image_paths"])
        if fail_commit:
            def failing_commit():
                raise SQLAlchemyError("isolated Markdown commit failure")
            monkeypatch.setattr(db_session, "commit", failing_commit)
        saved = client.post("/api/questions", data=form, headers=headers)
        if fail_commit:
            assert saved.status_code == 400, saved.text
            assert not any(path.exists() for path in permanent)
        else:
            assert saved.status_code == 200, saved.text
            saved_data = saved.json()
            expected_urls = [f"/{main.UPLOAD_DIR_REL}/{path.name}" for path in permanent]
            assert saved_data["asset_path_map"] == dict(zip(urls, expected_urls))
            question_id = saved_data["question"]["id"]
            reloaded = client.get(f"/api/questions/{question_id}", headers=headers).json()
            assert reloaded["image_paths"] == expected_urls
            assert f"![题图]({expected_urls[0]})" in reloaded["content"]
            assert f"![答案图]({expected_urls[1]})" in reloaded["answer_markdown"]
            assert f"![源码同图]({urls[0]}) 70%" in reloaded["content"]
            assert literal_path in reloaded["content"]
            assert all(path.read_bytes() == original_bytes for path in permanent)
        assert all(path.read_bytes() == original_bytes for path in temporary)
        # The source endpoint and source evidence never acquire stored escapes.
        assert raw_source.encode("utf-8").decode("utf-8") == uploaded.json()["source"]
    finally:
        for path in temporary + permanent:
            path.unlink(missing_ok=True)
