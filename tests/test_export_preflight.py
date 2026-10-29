"""Isolated export snapshots must fail closed on every consumed image."""
import ast
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import zipfile

import pytest
from starlette.responses import JSONResponse, Response
from PIL import Image

from mathbank import export_preflight as preflight, paper_helper as paper


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    reports = tmp_path / "reports"
    monkeypatch.setattr(preflight, "EXPORT_PREFLIGHTS_DIR", reports)
    return uploads, reports


def question(number, content, answer="", **extra):
    return {"question": {"id": number, "content": content, "answer_markdown": answer,
                         "question_type": "detailed_answer", **extra}, "score": 5}


def inspect(items, sources, sandbox, **kwargs):
    return preflight.preflight_paper_export(items, sources, target="tex", uploads_dir=sandbox[0], **kwargs)


def test_missing_images_have_question_field_and_durable_hashes_without_question_text(sandbox):
    items = [question(11, "PRIVATE QUESTION ![](/static/uploads/stem.png)",
                      "PRIVATE ANSWER ![](/static/uploads/answer.png)")]
    frozen = deepcopy(items)
    source = paper.build_latex_document("验证", "", "exam", items, include_answers=True)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"answers": source}, sandbox)
    report = error.value.report
    assert report["status"] == "blocked"
    assert {(entry["question_id"], entry["field"], entry["reference"]) for entry in report["errors"]} == {
        (11, "content", "/static/uploads/stem.png"), (11, "answer_markdown", "/static/uploads/answer.png")}
    assert preflight.read_export_preflight(report["id"]) == report
    serialized = json.dumps(report)
    assert "PRIVATE QUESTION" not in serialized and "PRIVATE ANSWER" not in serialized
    assert len(report["question_sha256"]["11"]) == 64
    assert items == frozen and not list(sandbox[0].iterdir())


def test_successful_snapshot_matches_the_exact_files_in_source_zip(sandbox):
    image = sandbox[0] / "stem.png"
    image.write_bytes(b"isolated image fixture")
    items = [question(7, "题目 ![](/static/uploads/stem.png)")]
    source = paper.build_latex_document("测试", "", "exam", items)
    report, paths = inspect(items, {"paper": source}, sandbox)
    result = paper.create_tex_zip_package("测试", source, "", paths, preflight_report=report)
    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert archive.read("images/stem.png") == image.read_bytes()
        assert json.loads(archive.read("export-preflight.json")) == report
    assert report["status"] == "passed" and report["counts"]["errors"] == 0
    assert report["resources"][0]["bytes"] == image.stat().st_size
    assert len(report["resources"][0]["sha256"]) == 64


def test_only_generated_images_are_required_and_tikz_replacements_need_no_preview_png(sandbox):
    items = [question(2, "![](/static/uploads/missing-preview.png)",
                      image_paths=["/static/uploads/unrelated-missing.png"])]
    report, paths = inspect(items, {"paper": r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"}, sandbox)
    assert paths == [] and report["resources"] == [] and report["status"] == "passed"


def test_tex_scanner_ignores_comments_and_verbatim_but_checks_executable_tikz():
    source = r"""% MathBank-Question-ID: 8
% \includegraphics{comment.png}
\verb|\includegraphics{verbatim.png}|
\begin{verbatim}\includegraphics{block.png}\end{verbatim}
\begin{tikzpicture}\node{\includegraphics{real.png}};\end{tikzpicture}
\\includegraphics{escaped.png}
\includegraphics* [width=2cm] {other.png}
"""
    assert list(preflight.tex_image_references(source)) == [(8, "real.png"), (8, "other.png")]


def test_source_basename_collisions_are_rejected_instead_of_overwriting(sandbox):
    for directory in ("first", "second"):
        (sandbox[0] / directory).mkdir()
        (sandbox[0] / directory / "same.png").write_bytes(directory.encode())
    items = [question(1, "![](/static/uploads/first/same.png)"),
             question(2, "![](/static/uploads/second/same.png)")]
    source = paper.build_latex_document("测试", "", "exam", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert any(entry["reason"] == "basename_collision" for entry in error.value.report["errors"])


def test_symlinks_and_unsafe_paths_block_without_reading_outside_uploads(sandbox, tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"private bytes must never be hashed")
    (sandbox[0] / "link.png").symlink_to(outside)
    items = [question(9, "![](/static/uploads/link.png)")]
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": "% MathBank-Question-ID: 9\n" + r"\includegraphics{link.png}"}, sandbox)
    assert error.value.report["errors"][0]["reason"] == "unreadable_or_unsafe"
    assert "sha256" not in error.value.report["resources"][0]
    with pytest.raises(FileNotFoundError):
        preflight.read_export_preflight("../secret")


def test_report_retention_is_bounded_and_previous_export_snapshot_stays_immutable(sandbox, monkeypatch):
    monkeypatch.setattr(preflight, "MAX_REPORTS", 2)
    ids = []
    for number in range(3):
        report, _ = inspect([question(number, "正文")], {"paper": "no images"}, sandbox)
        ids.append(report["id"])
        # Retention orders by filesystem mtime, not insertion order for ties.
        # Fast writes may share one timestamp on Windows; make chronology real.
        timestamp = 1_700_000_000 + number
        os.utime(sandbox[1] / (report["id"] + ".json"), (timestamp, timestamp))
    assert len(list(sandbox[1].glob("*.json"))) == 2
    with pytest.raises(FileNotFoundError):
        preflight.read_export_preflight(ids[0])
    assert preflight.read_export_preflight(ids[-1])["counts"]["questions"] == 1


def test_final_copy_and_zip_boundary_reject_lost_file_and_copy_errors(sandbox, monkeypatch):
    image = sandbox[0] / "figure.png"
    image.write_bytes(b"image")
    source = r"\includegraphics{figure.png}"
    monkeypatch.setattr(paper.shutil, "copy", lambda *args: (_ for _ in ()).throw(PermissionError("no")))
    monkeypatch.setattr(paper.subprocess, "run", lambda *args, **kwargs: pytest.fail("compiler must not run"))
    paper.clear_pdf_cache()
    assert paper.compile_tex_to_pdf(source, [str(image)])[0] is None
    image.unlink()
    assert paper.compile_tex_to_pdf(source, [str(image)])[0] is None
    with pytest.raises(ValueError, match="丢失"):
        paper.create_tex_zip_package("test", source, "", [str(image)])


def endpoint(name, items, sandbox, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    module = ast.parse((root / "main.py").read_text())
    node = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    namespace = {"Session": object, "Depends": lambda _: None, "get_db": lambda: None,
        "_prepare_paper_export_questions": lambda *_: deepcopy(items),
        "build_latex_document": paper.build_latex_document,
        "build_answer_sheet_latex": lambda *_: "sheet", "METADATA_CACHE": {},
        "UPLOAD_DIR": str(sandbox[0]), "UPLOAD_DIR_REL": "static/uploads",
        "preflight_paper_export": preflight.preflight_paper_export,
        "ExportPreflightError": preflight.ExportPreflightError,
        "PaperExportValidationError": type("PaperExportValidationError", (ValueError,), {}),
        "compile_tex_to_pdf": lambda *_: pytest.fail("compiler must not run on missing images"),
        "create_tex_zip_package": lambda *_ ,**kw: pytest.fail("packager must not run on missing images"),
        "Response": Response, "JSONResponse": JSONResponse}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("name", ["export_paper_pdf", "export_paper_tex", "export_paper_bundle"])
def test_export_routes_stop_before_compile_or_packaging_on_missing_image(name, sandbox, monkeypatch):
    run = endpoint(name, [question(12, "![](/static/uploads/required.png)")], sandbox, monkeypatch)
    result = run({"questions": [{"id": 12}], "paper_type": "exam"}, db=None)
    assert result.status_code == 400
    payload = json.loads(result.body)
    assert payload["preflight"]["status"] == "blocked"
    assert payload["preflight"]["errors"][0]["question_id"] == 12
    assert preflight.read_export_preflight(payload["preflight"]["id"])["status"] == "blocked"


@pytest.mark.parametrize("corrupt", [False, True])
def test_required_retained_image_uses_existing_verified_recovery_only(sandbox, tmp_path, corrupt):
    from mathbank import asset_lifecycle as lifecycle
    uploads = sandbox[0]
    recovery = tmp_path / "retained"
    lifecycle.register_asset_store(uploads, recovery)
    try:
        for filename in ("required.png", "unused.png"):
            (uploads / filename).write_bytes(("original " + filename).encode())
            lifecycle.quarantine_asset("/static/uploads/" + filename, uploads_dir=uploads,
                                       url_prefix="static/uploads", reason="test")
        if corrupt:
            (recovery / "files/required.png").write_bytes(b"changed bytes")
        items = [question(33, "![](/static/uploads/required.png)",
                          image_paths=["/static/uploads/required.png", "/static/uploads/unused.png"])]
        source = "% MathBank-Question-ID: 33\n" + r"\includegraphics{required.png}"
        if corrupt:
            with pytest.raises(preflight.ExportPreflightError) as error:
                inspect(items, {"paper": source}, sandbox)
            assert error.value.report["status"] == "blocked"
            assert not (uploads / "required.png").exists()
            assert (recovery / "files/required.png").read_bytes() == b"changed bytes"
        else:
            report, paths = inspect(items, {"paper": source}, sandbox)
            assert report["status"] == "passed" and paths == [str(uploads / "required.png")]
            assert report["resources"][0]["restored"] is True
            assert (uploads / "required.png").read_bytes() == b"original required.png"
        assert not (uploads / "unused.png").exists()
        assert (recovery / "files/unused.png").is_file()
    finally:
        lifecycle._STORES.pop(uploads.resolve(), None)


def test_pdf_success_exposes_the_durable_snapshot_id(sandbox, monkeypatch):
    import datetime
    run = endpoint("export_paper_pdf", [question(41, "No images. $x=1$.")], sandbox, monkeypatch)
    run.__globals__.update(compile_tex_to_pdf=lambda *_: (b"isolated-pdf", "ok"), datetime=datetime)
    result = run({"questions": [{"id": 41}], "paper_type": "exam"}, db=None)
    assert result.status_code == 200 and result.body == b"isolated-pdf"
    report = preflight.read_export_preflight(result.headers["X-Export-Preflight-ID"])
    assert report["status"] == "passed" and report["counts"]["questions"] == 1


def test_bundle_compile_failure_does_not_succeed_with_a_partial_archive(sandbox, monkeypatch):
    run = endpoint("export_paper_bundle", [question(41, "No images.")], sandbox, monkeypatch)
    run.__globals__.update(compile_tex_to_pdf=lambda *_: (None, "isolated compile failure"),
                           explain_latex_compile_error=lambda *_: {"summary": "隔离编译失败", "fixes": []},
                           create_full_bundle_zip_package=lambda *_, **kw: pytest.fail("no partial bundle"))
    result = run({"questions": [{"id": 41}], "paper_type": "exam"}, db=None)
    assert result.status_code == 400
    payload = json.loads(result.body)
    assert "未生成不完整归档" in payload["message"]
    assert payload["preflight"]["status"] == "passed"  # Image check and compilation are separate results.


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
@pytest.mark.parametrize("basename", ["diagram", "diagram.v1"])
def test_extensionless_tex_images_resolve_one_bound_raster_through_package(sandbox, suffix, basename):
    image = sandbox[0] / (basename + suffix)
    Image.new("RGB", (40, 20), "white").save(image)
    reference = "/static/uploads/" + image.name
    items = [question(17, "Figure: " + r"\includegraphics{" + basename + "}",
                      image_paths=[reference])]
    original = deepcopy(items)
    source = paper.build_latex_document("Image", "", "quiz", items)
    report, paths = inspect(items, {"paper": source}, sandbox)
    assert paths == [str(image)]
    assert report["status"] == "passed" and report["resources"][0]["reference"] == reference
    assert preflight.assert_tex_image_files([source], paths) == [image]
    package = paper.create_tex_zip_package("Image", source, "", paths, preflight_report=report)
    with zipfile.ZipFile(BytesIO(package)) as archive:
        assert archive.read("images/" + image.name) == image.read_bytes()
        assert (r"\includegraphics{" + basename + "}") in archive.read("试卷正文.tex").decode()
    assert items == original


@pytest.mark.parametrize("missing_alternative", [False, True])
def test_extensionless_reference_rejects_ambiguous_bound_suffixes_even_if_one_is_missing(sandbox, missing_alternative):
    for filename in ("diagram.png", "diagram.jpg"):
        if filename != "diagram.jpg" or not missing_alternative:
            Image.new("RGB", (40, 20), "white").save(sandbox[0] / filename)
    items = [question(18, r"Figure: \includegraphics{diagram}",
                      image_paths=["/static/uploads/diagram.png", "/static/uploads/diagram.jpg"])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert {item["reason"] for item in error.value.report["errors"]} == {"ambiguous_image_reference"}
    if not missing_alternative:
        with pytest.raises(ValueError, match="歧义"):
            preflight.assert_tex_image_files([source], [sandbox[0] / "diagram.png", sandbox[0] / "diagram.jpg"])


def test_extensionless_binding_is_per_question_and_final_package_must_stay_unambiguous(sandbox):
    for filename in ("diagram.png", "diagram.jpg"):
        Image.new("RGB", (40, 20), "white").save(sandbox[0] / filename)
    items = [question(19, r"Figure: \includegraphics{diagram}", image_paths=["/static/uploads/diagram.png"]),
             question(20, "Other question", image_paths=["/static/uploads/diagram.jpg"])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    _, paths = inspect(items, {"paper": source}, sandbox)
    assert paths == [str(sandbox[0] / "diagram.png")]  # An unused binding cannot create ambiguity.
    items[1]["question"]["content"] = r"Other: \includegraphics{diagram}"
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert {item["reason"] for item in error.value.report["errors"]} == {"ambiguous_image_reference"}


@pytest.mark.parametrize("suffix", [".png", ".gif", ".webp", ".pdf", ".tex"])
def test_extensionless_resolution_never_globs_uploads_or_infers_unapproved_types(sandbox, suffix):
    image = sandbox[0] / ("diagram" + suffix)
    image.write_bytes(b"unbound or unsupported image")
    items = [question(21, r"Figure: \includegraphics{diagram}",
                      image_paths=[] if suffix == ".png" else ["/static/uploads/" + image.name])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError):
        inspect(items, {"paper": source}, sandbox)
    if suffix != ".png":
        with pytest.raises(ValueError, match="未打包图片"):
            preflight.assert_tex_image_files([source], [image])


def test_extensionless_resolution_cannot_borrow_another_questions_binding(sandbox):
    image = sandbox[0] / "diagram.png"
    Image.new("RGB", (40, 20), "white").save(image)
    items = [question(22, r"Figure: \includegraphics{diagram}"),
             question(23, "Other question", image_paths=["/static/uploads/diagram.png"])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert error.value.report["errors"][0]["question_id"] == 22


def test_extensionless_resolution_still_rejects_symlinked_bound_image(sandbox, tmp_path):
    outside = tmp_path / "private.png"
    Image.new("RGB", (40, 20), "white").save(outside)
    (sandbox[0] / "diagram.png").symlink_to(outside)
    items = [question(26, r"Figure: \includegraphics{diagram}",
                      image_paths=["/static/uploads/diagram.png"])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert error.value.report["errors"][0]["reason"] == "unreadable_or_unsafe"
    assert "sha256" not in error.value.report["resources"][0]
    with pytest.raises(ValueError, match="丢失或不可读"):
        preflight.assert_tex_image_files([source], [sandbox[0] / "diagram.png"])


@pytest.mark.parametrize("reference", ["../diagram", "https://example.test/diagram", "diagram?x=1"])
def test_extensionless_resolution_preserves_path_rejections(sandbox, reference):
    image = sandbox[0] / "diagram.png"
    Image.new("RGB", (40, 20), "white").save(image)
    items = [question(24, "Figure: " + r"\includegraphics{" + reference + "}",
                      image_paths=["/static/uploads/diagram.png"])]
    source = paper.build_latex_document("Image", "", "quiz", items)
    with pytest.raises(preflight.ExportPreflightError) as error:
        inspect(items, {"paper": source}, sandbox)
    assert error.value.report["errors"][0]["reason"] == "unsupported_export_path"
    with pytest.raises(ValueError, match="未打包图片"):
        preflight.assert_tex_image_files([source], [image])


@pytest.mark.skipif(os.environ.get("MATHBANK_TEST_EXPORT_NATIVE") != "1" or not shutil.which("xelatex"),
                    reason="Opt-in native XeLaTeX extensionless export validation")
def test_real_xelatex_accepts_extensionless_preflight_copy_and_extracted_package(sandbox, tmp_path):
    image = sandbox[0] / "diagram.v1.png"
    Image.new("RGB", (60, 30), "black").save(image)
    items = [question(25, r"Figure: \includegraphics{diagram.v1}",
                      image_paths=["/static/uploads/diagram.v1.png"])]
    source = (r"\documentclass{article}\usepackage{graphicx}\graphicspath{{images/}{./}}"
              "\n% MathBank-Question-ID: 25\n" +
              r"\begin{document}Figure: \includegraphics{diagram.v1}\end{document}")
    report, paths = inspect(items, {"paper": source}, sandbox)
    pdf, log = paper.compile_tex_to_pdf(source, paths)
    assert pdf and pdf.startswith(b"%PDF"), log
    package = paper.create_tex_zip_package("Image", source, "", paths, preflight_report=report)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(BytesIO(package)) as archive:
        archive.extractall(extracted)
    result = subprocess.run([shutil.which("xelatex"), "-no-shell-escape", "-halt-on-error",
                             "-interaction=nonstopmode", "试卷正文.tex"], cwd=extracted,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout[-5000:]
    import pymupdf
    for payload in (pdf, (extracted / "试卷正文.pdf").read_bytes()):
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            assert len(document) == 1 and len(document[0].get_images()) == 1
