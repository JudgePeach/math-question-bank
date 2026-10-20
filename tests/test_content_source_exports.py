"""Content credits accompany artifacts once without changing question records."""

import ast
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mathbank import content_sources, paper_helper as paper, sync_helper as sync, word_export_helper as word
from mathbank.database import Base, Question
from mathbank.paths import PROJECT_ROOT


QUESTION = {"id": 77, "question_type": "single_choice", "source": "教师自编",
            "content": r"求 $x^2$。\begin{choices}\item $1$\item $2$\item $3$\item $4$\end{choices}",
            "answer_markdown": "答案 $1$。"}


def source_files_in(archive):
    expected = content_sources.content_source_export_files()
    for filename, data in expected.items():
        assert archive.namelist().count(filename) == 1
        assert archive.read(filename) == data
    return expected


def test_tex_has_one_commented_catalog_scope_and_keeps_question_copy():
    question = deepcopy(QUESTION)
    tex = paper.build_latex_document("验证", "", "exam", [{"question": question, "score": 5}])
    assert question == QUESTION
    header, body = tex.split(paper._CONTENT_SOURCE_TEX_END, 1)
    assert tex.startswith(paper._CONTENT_SOURCE_TEX_BEGIN)
    assert tex.count(paper._CONTENT_SOURCE_TEX_BEGIN) == 1
    assert all(line.startswith("%") for line in header.splitlines())
    assert content_sources.load_content_sources()["scope_notice"] in header
    assert "CC BY-SA 4.0" not in body
    assert "求 $x^2$。" in body
    assert paper._with_content_source_tex_notice(tex) == tex
    revised = paper._with_content_source_tex_notice(tex, "新声明\n\\input{not-executed}%\n")
    assert revised.count(paper._CONTENT_SOURCE_TEX_BEGIN) == 1
    assert "% \\input{not-executed}%" in revised
    assert revised.split(paper._CONTENT_SOURCE_TEX_END, 1)[1] == body


@pytest.mark.parametrize("full", [False, True])
def test_tex_and_combined_zip_include_one_snapshot_and_keep_payloads(tmp_path, full):
    image = tmp_path / "figure.png"
    image.write_bytes(b"fixture-image-content")
    tex = paper.build_latex_document("验证", "", "exam", [{"question": QUESTION, "score": 5}])
    args = ["验证", tex, r"\documentclass{article}\begin{document}Answer\end{document}", [str(image)]]
    if full:
        result = paper.create_full_bundle_zip_package(*args, answer_sheet_tex="sheet", main_pdf_bytes=b"main-pdf", ans_pdf_bytes=b"answers-pdf", answer_sheet_pdf_bytes=b"sheet-pdf")
    else:
        result = paper.create_tex_zip_package(*args, answer_sheet_tex="sheet")
    with zipfile.ZipFile(BytesIO(result)) as archive:
        source_files_in(archive)
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for name in ("试卷正文.tex", "参考答案与解析.tex", "答题卡.tex"):
            text = archive.read(name).decode("utf-8")
            assert text.count(paper._CONTENT_SOURCE_TEX_BEGIN) == 1
        assert archive.read("images/figure.png") == image.read_bytes()
        if full:
            assert archive.read("试卷正文.pdf") == b"main-pdf"
            assert archive.read("参考答案与解析.pdf") == b"answers-pdf"


def test_word_zip_has_one_catalog_and_does_not_rewrite_docx_bytes():
    result = word.create_word_bundle_zip("验证", b"student-docx", b"teacher-docx")
    with zipfile.ZipFile(BytesIO(result)) as archive:
        source_files_in(archive)
        assert archive.read("验证.docx") == b"student-docx"
        assert archive.read("验证_含答案与解析.docx") == b"teacher-docx"
        assert len(archive.namelist()) == 4


@pytest.fixture
def export_session(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.connection().exec_driver_sql("PRAGMA user_version=13")
        session.add(Question(content="教师自编 $x=1$。", source="教师自编", question_type="fill_in_blank"))
        session.commit()
        monkeypatch.setattr(sync, "BACKUP_DIR", str(tmp_path))
        monkeypatch.setattr(sync, "JSON_BACKUP_PATH", str(tmp_path / "questions_backup.json"))
        monkeypatch.setattr(sync, "MD_BACKUP_PATH", str(tmp_path / "questions_library.md"))
        monkeypatch.setattr(sync, "resolve_active_version", lambda: "A")
        yield session
    engine.dispose()


def test_sync_keeps_top_level_array_with_once_only_sibling_notices(export_session, tmp_path):
    before = export_session.connection().exec_driver_sql("SELECT * FROM questions").all()
    result = sync.export_database_to_files(export_session, quiet=True)
    payload = json.loads(Path(result["json_path"]).read_text())
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["source"] == "教师自编"
    assert "content_sources" not in payload[0]
    for filename, expected in content_sources.content_source_export_files().items():
        assert (tmp_path / filename).read_bytes() == expected
    markdown = Path(result["markdown_path"]).read_text()
    assert markdown.count(content_sources.content_sources_notice()) == 1
    assert markdown.count("教师自编 $x=1$。") == 1
    assert export_session.connection().exec_driver_sql("SELECT * FROM questions").all() == before
    assert not list(tmp_path.glob(".mathbank-export-*"))


def test_failed_sidecar_preparation_keeps_all_last_good_exports(export_session, tmp_path, monkeypatch):
    names = ["questions_backup.json", "questions_library.md", content_sources.CONTENT_SOURCES_JSON_NAME, content_sources.CONTENT_SOURCES_NOTICE_NAME]
    for name in names:
        (tmp_path / name).write_text("last-good:" + name)
    original = sync._temporary_output_path
    def fail_notice(path):
        if Path(path).name == content_sources.CONTENT_SOURCES_NOTICE_NAME:
            raise OSError("simulated source-notice output failure")
        return original(path)
    monkeypatch.setattr(sync, "_temporary_output_path", fail_notice)
    with pytest.raises(RuntimeError, match="source-notice output failure"):
        sync.export_database_to_files(export_session, quiet=True)
    assert all((tmp_path / name).read_text() == "last-good:" + name for name in names)
    assert not list(tmp_path.glob(".mathbank-export-*"))


def sample_pdf():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((35, 45), "Unchanged visible content x = 1/2")
        document.set_metadata({"title": "Original title", "author": "Teacher", "subject": "Original subject"})
        return document.tobytes()


def test_pdf_metadata_keeps_page_pixels_text_geometry_and_existing_credits():
    before = sample_pdf()
    notice = content_sources.content_sources_notice()
    after = paper._with_content_source_pdf_metadata(before, notice)
    with pymupdf.open(stream=before, filetype="pdf") as left, pymupdf.open(stream=after, filetype="pdf") as right:
        assert right.metadata["title"] == left.metadata["title"]
        assert right.metadata["author"] == left.metadata["author"]
        assert right.metadata["subject"] == "Original subject\n\n" + notice.rstrip()
        assert len(left) == len(right)
        assert left[0].rect == right[0].rect
        assert left[0].get_text() == right[0].get_text()
        assert left[0].get_pixmap().samples == right[0].get_pixmap().samples
        assert notice not in right[0].get_text()
    assert paper._with_content_source_pdf_metadata(after, notice) == after


def test_compiler_adds_metadata_and_cache_tracks_the_notice(monkeypatch):
    raw = sample_pdf()
    calls = []
    def fake_run(command, *, cwd, **kwargs):
        calls.append(command)
        Path(cwd, "paper.pdf").write_bytes(raw)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(paper.subprocess, "run", fake_run)
    monkeypatch.setattr(paper, "content_sources_notice", lambda: "Scoped catalog A")
    paper.clear_pdf_cache()
    try:
        first, _ = paper.compile_tex_to_pdf("unique content-sources compiler cache test")
        with pymupdf.open(stream=first, filetype="pdf") as document:
            assert "Scoped catalog A" in document.metadata["subject"]
        assert len(calls) == 2
        cached, _ = paper.compile_tex_to_pdf("unique content-sources compiler cache test")
        assert cached == first and len(calls) == 2
        monkeypatch.setattr(paper, "content_sources_notice", lambda: "Scoped catalog B")
        second, _ = paper.compile_tex_to_pdf("unique content-sources compiler cache test")
        with pymupdf.open(stream=second, filetype="pdf") as document:
            assert "Scoped catalog B" in document.metadata["subject"]
            assert "Scoped catalog A" not in document.metadata["subject"]
        assert len(calls) == 4
    finally:
        paper.clear_pdf_cache()


def test_existing_bundle_api_uses_catalog_aware_packager_without_duplicate_files(tmp_path, monkeypatch):
    from mathbank import export_preflight
    monkeypatch.setattr(export_preflight, "EXPORT_PREFLIGHTS_DIR", tmp_path / "reports")
    module = ast.parse((PROJECT_ROOT / "main.py").read_text())
    node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "export_paper_bundle")
    node.decorator_list = []
    from starlette.responses import Response, JSONResponse
    namespace = {"Session": object, "Depends": lambda _: None, "get_db": lambda: None,
                 "_prepare_paper_export_questions": lambda data, db: [{"question": deepcopy(QUESTION), "score": 5}],
                 "build_latex_document": paper.build_latex_document, "build_answer_sheet_latex": lambda *args: "sheet",
                 "preflight_paper_export": export_preflight.preflight_paper_export,
                 "ExportPreflightError": export_preflight.ExportPreflightError,
                 "UPLOAD_DIR": "unused", "UPLOAD_DIR_REL": "unused",
                 "compile_tex_to_pdf": lambda *args: (b"pdf-result", ""), "METADATA_CACHE": {},
                 "create_full_bundle_zip_package": paper.create_full_bundle_zip_package,
                 "Response": Response, "JSONResponse": JSONResponse, "PaperExportValidationError": ValueError, "re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace)
    result = namespace["export_paper_bundle"]({"title": "验证", "paper_type": "exam_19", "questions": [{"id": 77}]}, db=None)
    assert result.status_code == 200
    with zipfile.ZipFile(BytesIO(result.body)) as archive:
        source_files_in(archive)
        assert archive.read("试卷正文.pdf") == b"pdf-result"
        assert archive.read("答题卡.pdf") == b"pdf-result"
