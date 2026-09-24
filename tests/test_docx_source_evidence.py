"""Original DOCX evidence lifecycle, with no model or real converter in unit tests."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pymupdf as fitz
import pytest

from mathbank import docx_source_evidence as evidence
from mathbank.task_manager import TaskCancelled


def docx_bytes(*, relationship=None, field=None, macro=False):
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        paragraph = '<w:p><w:r><w:t>Original Word source, never extracted Markdown</w:t></w:r></w:p>'
        if field:
            paragraph += f'<w:p><w:r><w:instrText>{field}</w:instrText></w:r></w:p>'
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraph + '</w:body></w:document>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        if relationship:
            archive.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + relationship + '</Relationships>')
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")
    return output.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "SYSTEM_GENERATED_DIR", tmp_path / "system")
    monkeypatch.setattr(evidence, "find_docx_renderer", lambda explicit=None: "/test/native-soffice")
    calls = []
    def native(renderer, original, work, check_cancelled):
        check_cancelled()
        calls.append({"renderer": renderer, "source": original.read_bytes(), "work": work})
        path = work / "original.pdf"
        with fitz.open() as document:
            for number in (1, 2):
                page = document.new_page(width=400, height=500)
                page.insert_text((30, 40), f"ORIGINAL WORD PAGE {number}")
                page.draw_rect(fitz.Rect(30, 80, 170, 150))
            document.save(path)
        return path
    monkeypatch.setattr(evidence, "_native_pdf", native)
    return {"data": docx_bytes(), "output": tmp_path / "uploads", "registered": [], "calls": calls, "root": tmp_path}


def call(setup, **kwargs):
    return evidence.prepare_docx_source_evidence(setup["data"], output_dir=setup["output"], url_prefix="/static/uploads/tmp",
        task_id="word_test", register_asset=setup["registered"].append, **kwargs)


def test_original_bytes_are_passed_to_native_renderer_and_only_bounded_pngs_survive(setup):
    source = setup["data"]
    result = call(setup)
    assert result["status"] == "ready" and result["evidence_kind"] == "original_docx_render"
    assert result["source_sha256"] == evidence.hashlib.sha256(source).hexdigest()
    assert setup["calls"][0]["source"] == source and setup["data"] == source
    assert result["page_numbers"] == [1, 2]
    assert result["page_images"] == setup["registered"]
    assert [p["text"].strip() for p in result["pages"]] == ["ORIGINAL WORD PAGE 1", "ORIGINAL WORD PAGE 2"]
    for page in result["pages"]:
        assert page["image_path"].startswith("/static/uploads/tmp/docx_page_word_test_" + result["source_sha256"][:16])
        assert not any(key.startswith("local_") for key in page)
        path = setup["output"] / Path(page["image_path"]).name
        assert evidence.hashlib.sha256(path.read_bytes()).hexdigest() == page["image_sha256"]
        assert (page["width"], page["height"]) == (400, 500)
    assert not setup["calls"][0]["work"].exists()
    assert not list(setup["root"].rglob("*.pdf")) and not list(setup["root"].rglob("*.docx"))
    assert str(setup["root"]) not in json.dumps(result)


def test_verified_same_task_cache_skips_converter_and_does_not_reregister(setup, monkeypatch):
    first = call(setup)
    registered = list(setup["registered"])
    monkeypatch.setattr(evidence, "find_docx_renderer", lambda explicit=None: pytest.fail("No renderer probe for valid cached pages"))
    second = call(setup, cached_evidence=first)
    assert second["status"] == "ready" and second["cache_reused"] is True
    assert len(setup["calls"]) == 1 and setup["registered"] == registered
    assert first["cache_reused"] is False


@pytest.mark.parametrize("change", ["oversized_text", "oversized_pixels", "invalid_page_number", "missing_font_check"])
def test_cache_resource_and_visibility_bounds_are_rechecked_even_with_updated_manifest(setup, change):
    cached = call(setup)
    page = cached["pages"][0]
    if change == "oversized_text": page["text"] = "x" * (evidence.MAX_PAGE_TEXT + 1)
    elif change == "oversized_pixels": page["pixel_width"] = evidence.MAX_PAGE_PIXELS
    elif change == "invalid_page_number": page["page_number"] = True
    elif change == "missing_font_check": page.pop("font_check")
    cached["manifest_sha256"] = evidence._manifest_hash(cached["pages"])
    result = call(setup, cached_evidence=cached)
    assert result["status"] == "ready" and result["cache_reused"] is False and len(setup["calls"]) == 2


@pytest.mark.parametrize("change", ["task", "source", "text", "file", "missing", "url", "kind", "old_version"])
def test_changed_cache_cannot_be_reused_as_unchanged_evidence(setup, change):
    cached = call(setup)
    if change == "task": cached["task_id"] = "another_task"
    elif change == "source": cached["source_sha256"] = "0" * 64
    elif change == "text": cached["pages"][0]["text"] = "edited transcript"
    elif change == "file": (setup["output"] / Path(cached["pages"][0]["image_path"]).name).write_bytes(b"changed")
    elif change == "missing": (setup["output"] / Path(cached["pages"][0]["image_path"]).name).unlink()
    elif change == "url": cached["pages"][0]["image_path"] = "/private/secret.png"
    elif change == "kind": cached["evidence_kind"] = "markdown_reconstruction"
    elif change == "old_version": cached.pop("evidence_version")
    result = call(setup, cached_evidence=cached)
    assert result["status"] == "ready" and result["cache_reused"] is False and len(setup["calls"]) == 2


def test_missing_renderer_is_explicitly_unavailable_without_fake_visual_evidence(setup, monkeypatch):
    monkeypatch.setattr(evidence, "find_docx_renderer", lambda explicit=None: None)
    result = call(setup)
    assert result["status"] == "unavailable" and result["pages"] == result["page_images"] == []
    assert not setup["calls"] and not setup["registered"] and not setup["output"].exists()


@pytest.mark.parametrize("data", [b"", b"not a Word archive", b"PK broken package"])
def test_invalid_docx_never_reaches_renderer(setup, data):
    setup["data"] = data
    result = call(setup)
    assert result["status"] == "failed" and not setup["calls"] and not setup["registered"]


@pytest.mark.parametrize("setting", ["MAX_DOCX_BYTES", "MAX_PAGES", "MAX_PAGE_PIXELS", "MAX_TOTAL_PIXELS", "MAX_PAGE_BYTES", "MAX_TOTAL_IMAGE_BYTES", "MAX_PAGE_TEXT", "MAX_TOTAL_TEXT"])
def test_resource_limits_fail_without_publishing_partial_page_evidence(setup, monkeypatch, setting):
    monkeypatch.setattr(evidence, setting, 1)
    result = call(setup)
    assert result["status"] == "failed" and result["pages"] == result["page_images"] == []
    assert not list(setup["output"].glob("*.png"))
    assert not list((setup["root"] / "system").rglob("*.docx"))


@pytest.mark.parametrize("stage", ["before", "after_first_image"])
def test_cancellation_propagates_and_removes_only_this_attempts_assets(setup, stage):
    setup["output"].mkdir()
    previous = setup["output"] / "existing.png"
    previous.write_bytes(b"existing asset")
    def cancel():
        if stage == "before" or setup["registered"]:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled): call(setup, check_cancelled=cancel)
    assert previous.read_bytes() == b"existing asset"
    assert list(setup["output"].iterdir()) == [previous]
    assert not list((setup["root"] / "system").rglob("*.docx"))


def test_conversion_failure_never_exposes_converter_output_or_original_text(setup, monkeypatch):
    def fail(*args):
        raise RuntimeError("secret original text or private filename")
    monkeypatch.setattr(evidence, "_native_pdf", fail)
    result = call(setup)
    assert result["status"] == "failed" and "secret" not in json.dumps(result)
    assert result["pages"] == []


@pytest.mark.parametrize("content", ["macro", "external_image", "external_template", "dynamic_field"])
def test_active_or_external_content_is_not_evaluated_during_native_render(setup, content):
    if content == "macro": setup["data"] = docx_bytes(macro=True)
    elif content == "dynamic_field": setup["data"] = docx_bytes(field='INCLUDETEXT "https://example.test/private"')
    else:
        kind = "image" if content == "external_image" else "attachedTemplate"
        setup["data"] = docx_bytes(relationship=f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" TargetMode="External" Target="https://example.test/private"/>')
    result = call(setup)
    assert result["status"] == "failed" and not setup["calls"] and not setup["registered"]
    assert "https://" not in json.dumps(result)


def test_ordinary_hyperlinks_are_not_external_resource_updates(setup):
    setup["data"] = docx_bytes(relationship='<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" TargetMode="External" Target="https://example.test/"/>')
    assert call(setup)["status"] == "ready"


def test_native_converter_command_is_isolated_and_uses_original_docx(tmp_path, monkeypatch):
    original = tmp_path / "original.docx"
    original.write_bytes(docx_bytes())
    seen = []
    class Process:
        returncode = 0
        pid = 123
        def poll(self): return 0
    def popen(command, **kwargs):
        seen.append((command, kwargs))
        target = Path(command[command.index("--outdir") + 1]) / "original.pdf"
        with fitz.open() as doc:
            doc.new_page(); doc.save(target)
        return Process()
    monkeypatch.setattr(evidence.subprocess, "Popen", popen)
    output = evidence._native_pdf("/native/soffice", original, tmp_path, lambda: None)
    command, options = seen[0]
    assert command[0] == "/native/soffice" and command[-1] == str(original)
    assert "--headless" in command and "pdf:writer_pdf_Export" in command
    assert any(arg.startswith("-env:UserInstallation=file:") for arg in command)
    assert options["env"].get("HOME") == evidence.os.environ.get("HOME")
    assert output.is_file() and (tmp_path / "profile/user/registrymodifications.xcu").is_file()


@pytest.mark.parametrize("cancel", [False, True])
def test_native_converter_timeout_and_cancel_stop_own_child(tmp_path, monkeypatch, cancel):
    original = tmp_path / "original.docx"; original.write_bytes(docx_bytes())
    stopped = []
    class Process:
        returncode = None
        pid = 123
        def poll(self): return None
    process = Process()
    monkeypatch.setattr(evidence.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(evidence, "_stop_own_process", lambda item: stopped.append(item))
    if not cancel:
        monkeypatch.setattr(evidence, "RENDER_TIMEOUT_SECONDS", -1)
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if cancel and calls >= 2: raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled if cancel else evidence._EvidenceError):
        evidence._native_pdf("/native/soffice", original, tmp_path, check)
    assert stopped == [process]


def test_pdf_text_mapping_does_not_certify_an_invisible_chinese_glyph():
    class Page:
        def get_texttrace(self):
            return [{"type": 0, "opacity": 1, "font": "WrongLatinFont",
                     "chars": [(ord("学"), 4, (20, 30), (20, 20, 20, 30))]}]
    with pytest.raises(evidence._EvidenceError, match="不可见中文"):
        evidence._font_visibility(Page())


def test_visible_chinese_glyphs_are_counted_but_deliberately_hidden_text_is_not():
    class Page:
        def get_texttrace(self):
            return [{"type": 0, "opacity": 1, "font": "SongtiSC",
                     "chars": [(ord("学"), 4, (20, 30), (20, 20, 30, 30))]},
                    {"type": 3, "font": "HiddenText", "chars": [(ord("隐"), 4, (20, 30), (20, 20, 20, 30))]}]
    assert evidence._font_visibility(Page()) == {"cjk_characters": 1, "zero_width_cjk": 0, "cjk_fonts": ["SongtiSC"]}


def test_bad_font_evidence_is_never_published_ready(setup, monkeypatch):
    def reject(_page):
        raise evidence._EvidenceError("原Word渲染出现不可见中文字符")
    monkeypatch.setattr(evidence, "_font_visibility", reject)
    result = call(setup)
    assert result["status"] == "failed" and result["pages"] == [] and not setup["registered"]


def test_local_font_configuration_does_not_change_home_or_installed_fonts(tmp_path):
    environment = dict(evidence.os.environ)
    original_home = environment.get("HOME")
    evidence._configure_native_fonts(tmp_path, environment)
    assert environment.get("HOME") == original_home
    if "FONTCONFIG_FILE" in environment and Path(environment["FONTCONFIG_FILE"]).parent == tmp_path:
        config = Path(environment["FONTCONFIG_FILE"]).read_text()
        assert "<cachedir>" + str(tmp_path / "font-cache") + "</cachedir>" in config
        assert "http://" not in config and "https://" not in config
