"""A linked tiny footer cache is never permission to follow an external URL."""

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

from defusedxml import ElementTree as ET
from PIL import Image
import pymupdf as fitz
import pytest

from mathbank import docx_source_evidence as evidence
from mathbank.task_manager import TaskCancelled

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"


def png():
    out = BytesIO()
    Image.new("RGB", (2, 3), "white").save(out, format="PNG")
    return out.getvalue()


def package(*, placement="footer", cache=True, href=True, extra_use=False, mode="file",
            size="0.05pt", position="absolute", extra_style="", extra_child="", dynamic=False):
    part = {"footer": "footer1.xml", "header": "header1.xml", "body": "document.xml", "footnote": "footnotes.xml"}[placement]
    picture = (f'<w:p><w:r><w:pict><v:shape id="cache" style="position:{position};left:0pt;margin-top:-20pt;'
               f'width:{size};height:{size};z-index:1;{extra_style}" filled="f" stroked="f">'
               f'<v:path/><v:imagedata r:id="cache"' + (' r:href="external"' if href else '')
               + ' o:title=""/><o:lock aspectratio="t"/>' + extra_child + '</v:shape></w:pict></w:r></w:p>')
    if extra_use:
        picture += '<w:p><w:r><w:t r:id="external">Unexpected other use</w:t></w:r></w:p>'
    ns = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:v="{V}" xmlns:o="{O}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w14="urn:w14" mc:Ignorable="w14"'
    body = '<w:p><w:r><w:t>1. Exact original question with answer.</w:t></w:r></w:p>'
    if dynamic:
        body += '<w:p><w:r><w:instrText>INCLUDETEXT "file:///secret"</w:instrText></w:r></w:p>'
    if placement == "body":
        body += picture
    else:
        body += f'<w:sectPr><w:{"header" if placement == "header" else "footer"}Reference w:type="default" r:id="part"/></w:sectPr>'
    parts = {"word/document.xml": (f'<?xml version="1.0" encoding="UTF-8"?><w:document {ns}><w:body>{body}</w:body></w:document>').encode(),
             "[Content_Types].xml": f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'.encode(),
             "word/styles.xml": f'<w:styles xmlns:w="{W}"><w:style w:styleId="unchanged"/></w:styles>'.encode(),
             "word/embeddings/equation.bin": b"original equation bytes remain identical"}
    if placement != "body":
        root_name = {"footer": "ftr", "header": "hdr", "footnote": "footnotes"}[placement]
        parts["word/" + part] = (f'<?xml version="1.0" encoding="UTF-8"?>\r\n<w:{root_name} {ns}>' + picture + f'</w:{root_name}>').encode()
        parts["word/_rels/document.xml.rels"] = (f'<Relationships xmlns="{P}"><Relationship Id="part" Type="{R}/{"header" if placement == "header" else "footer"}" Target="{part}"/></Relationships>').encode()
    target = "file:///not/read/a/private-image.png" if mode == "file" else "https://not-read.invalid/a.png"
    internal = f'<Relationship Id="cache" Type="{R}/image" Target="media/cache.png"/>' if cache else ""
    parts["word/_rels/" + part + ".rels"] = (f'<Relationships xmlns="{P}"><Relationship Id="external" Type="{R}/image" Target="{target}" TargetMode="External"/>' + internal + '</Relationships>').encode()
    if cache:
        parts["word/media/cache.png"] = png()
    return zip_parts(parts)


def zip_parts(parts):
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()


def parts(data):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "SYSTEM_GENERATED_DIR", tmp_path / "system")
    monkeypatch.setattr(evidence, "find_docx_renderer", lambda explicit=None: "/test/native-soffice")
    seen, assets = [], []
    def native(renderer, source, work, cancel):
        cancel()
        seen.append(source.read_bytes())
        path = work / "original.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=200, height=300)
            page.insert_text((10, 30), "Exact original question")
            doc.save(path)
        return path
    monkeypatch.setattr(evidence, "_native_pdf", native)
    return {"seen": seen, "assets": assets, "output": tmp_path / "uploads", "data": package()}


def prepare(setup, **kwargs):
    return evidence.prepare_docx_source_evidence(setup["data"], output_dir=setup["output"], url_prefix="/static/uploads/tmp",
                                                task_id="footer_test", register_asset=setup["assets"].append, **kwargs)


def test_only_external_attribute_and_relationship_change_all_other_parts_exact(setup):
    original = setup["data"]
    result = prepare(setup)
    assert result["status"] == "ready" and result["evidence_kind"] == "docx_embedded_footer_cache_render"
    assert result["evidence_version"] == 1 and result["evidence_scope"] == "body_only"
    assert result["body_original_same"] is True and evidence.validate_docx_body_render_evidence(result)
    assert setup["data"] == original and setup["seen"][0] != original
    before, after = parts(original), parts(setup["seen"][0])
    assert before.keys() == after.keys()
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"word/footer1.xml", "word/_rels/footer1.xml.rels"}
    assert after["word/footer1.xml"] == before["word/footer1.xml"].replace(b' r:href="external"', b'')
    assert b'mc:Ignorable="w14"' in after["word/footer1.xml"]
    assert b'r:id="cache"' in after["word/footer1.xml"] and b'width:0.05pt;height:0.05pt' in after["word/footer1.xml"]
    rels = ET.fromstring(after["word/_rels/footer1.xml.rels"])
    assert [r.attrib["Id"] for r in rels] == ["cache"]
    receipt = result["footer_cache_receipt"]
    assert set(receipt["changed_parts"]) == changed
    assert receipt["unchanged_parts_sha256"] == {name: hashlib.sha256(before[name]).hexdigest() for name in before if name not in changed}
    assert result["source_sha256"] == result["original_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["derivative_sha256"] == result["rendering_copy_sha256"] == hashlib.sha256(setup["seen"][0]).hexdigest()
    serialized = json.dumps(result)
    assert "private-image" not in serialized and "file:///" not in serialized and str(setup["output"]) not in serialized
    assert "不确认页脚" in " ".join(result["notes"])


@pytest.mark.parametrize("settings", [
    {"placement": "body"}, {"placement": "header"}, {"placement": "footnote"},
    {"cache": False}, {"href": False}, {"extra_use": True}, {"mode": "https"},
    {"size": "15pt"}, {"size": "0pt"}, {"size": "5%"}, {"position": "relative"},
    {"extra_style": "rotation:1;"}, {"extra_child": '<v:wrap type="square"/>'}, {"dynamic": True},
])
def test_unproven_external_layout_or_nonfooter_resource_still_blocks_before_converter(setup, settings):
    setup["data"] = package(**settings)
    result = prepare(setup)
    assert result["status"] == "failed" and result["pages"] == []
    assert setup["seen"] == [] and setup["assets"] == []
    assert "private-image" not in json.dumps(result)


@pytest.mark.parametrize("change", ["corrupt_png", "wrong_format", "external_cache", "template", "duplicate_id", "unknown_owner", "extra_image_attr", "macro"])
def test_cache_pair_cannot_hide_other_untrusted_resource_or_active_content(setup, change):
    package_parts = parts(setup["data"])
    if change == "corrupt_png": package_parts["word/media/cache.png"] = b"broken PNG"
    elif change == "wrong_format":
        out = BytesIO(); Image.new("RGB", (2, 2)).save(out, format="JPEG"); package_parts["word/media/cache.png"] = out.getvalue()
    elif change == "external_cache":
        name = "word/_rels/footer1.xml.rels"
        package_parts[name] = package_parts[name].replace(b'Target="media/cache.png"', b'Target="file:///cache.png" TargetMode="External"')
    elif change == "template":
        package_parts["word/_rels/settings.xml.rels"] = f'<Relationships xmlns="{P}"><Relationship Id="t" Type="{R}/attachedTemplate" TargetMode="External" Target="file:///private.dotx"/></Relationships>'.encode()
    elif change == "duplicate_id":
        name = "word/_rels/footer1.xml.rels"
        package_parts[name] = package_parts[name].replace(b'Id="cache"', b'Id="external"')
    elif change == "unknown_owner":
        package_parts["word/document.xml"] = package_parts["word/document.xml"].replace(b'r:id="part"', b'r:id="missing"')
    elif change == "extra_image_attr":
        package_parts["word/footer1.xml"] = package_parts["word/footer1.xml"].replace(b'o:title=""', b'o:title="" src="file:///private.png"')
    elif change == "macro": package_parts["word/vbaProject.bin"] = b"macro"
    setup["data"] = zip_parts(package_parts)
    assert prepare(setup)["status"] == "failed" and not setup["seen"]


@pytest.mark.parametrize("change", ["scope", "body_same", "source", "derivative", "receipt", "unchanged_body", "page_text", "page_url", "manifest", "signature"])
def test_public_flags_or_recomputed_hashes_cannot_forge_derivative_evidence(setup, change):
    result = prepare(setup)
    bad = deepcopy(result)
    if change == "scope": bad["evidence_scope"] = "original_entire_document"
    elif change == "body_same": bad["body_original_same"] = False
    elif change == "source": bad["source_sha256"] = bad["original_sha256"] = "1" * 64
    elif change == "derivative": bad["rendering_copy_sha256"] = bad["derivative_sha256"] = "2" * 64
    elif change == "receipt": bad["footer_cache_receipt"]["changed_parts"]["word/document.xml"] = {"original_sha256": "0" * 64, "derivative_sha256": "1" * 64}
    elif change == "unchanged_body": bad["footer_cache_receipt"]["unchanged_parts_sha256"]["word/document.xml"] = "3" * 64
    elif change == "page_text": bad["pages"][0]["text"] = "different question"
    elif change == "page_url": bad["pages"][0]["image_path"] = "/static/uploads/tmp/some-other-page.png"
    elif change == "manifest": bad["manifest_sha256"] = "4" * 64
    elif change == "signature": bad["evidence_signature"] = "5" * 64
    if change in {"page_text", "page_url"}:
        bad["manifest_sha256"] = evidence._manifest_hash(bad["pages"])
        bad["page_images"] = [page["image_path"] for page in bad["pages"]]
    assert not evidence.validate_docx_body_render_evidence(bad)
    assert evidence.validate_docx_body_render_evidence(json.loads(json.dumps(result)))


def test_same_task_cache_is_resigned_and_image_bytes_still_checked(setup, monkeypatch):
    first = prepare(setup)
    second = prepare(setup, cached_evidence=first)
    assert len(setup["seen"]) == 1 and second["cache_reused"] is True
    assert second["evidence_signature"] != first["evidence_signature"]
    assert evidence.validate_docx_body_render_evidence(second)
    assert evidence.validate_docx_body_render_evidence(first) and first["cache_reused"] is False
    image = setup["output"] / Path(second["pages"][0]["image_path"]).name
    image.write_bytes(b"changed image")
    third = prepare(setup, cached_evidence=second)
    assert len(setup["seen"]) == 2 and third["cache_reused"] is False
    assert evidence.validate_docx_body_render_evidence(third)
    monkeypatch.setattr(evidence, "_EVIDENCE_SIGNING_KEY", b"new process key")
    assert not evidence.validate_docx_body_render_evidence(third)


def test_tampered_receipt_cannot_be_reused_even_after_updating_page_manifest(setup):
    cached = prepare(setup)
    cached["footer_cache_receipt"]["body_parts_unchanged"] = False
    cached["manifest_sha256"] = evidence._manifest_hash(cached["pages"])
    fresh = prepare(setup, cached_evidence=cached)
    assert len(setup["seen"]) == 2 and fresh["cache_reused"] is False
    assert evidence.validate_docx_body_render_evidence(fresh)


@pytest.mark.parametrize("drop_fields", [False, True])
def test_cached_derivative_cannot_downgrade_to_public_original_kind(setup, drop_fields):
    cached = prepare(setup)
    cached.update(evidence_kind="original_docx_render", evidence_version=2)
    if drop_fields:
        for key in ("evidence_signature", "footer_cache_receipt", "derivative_sha256", "rendering_copy_sha256",
                    "evidence_scope", "body_original_same", "original_sha256"):
            cached.pop(key)
    else:
        assert not evidence.validate_docx_body_render_evidence(cached)
    fresh = prepare(setup, cached_evidence=cached)
    assert len(setup["seen"]) == 2 and fresh["evidence_kind"] == "docx_embedded_footer_cache_render"
    assert fresh["cache_reused"] is False and evidence.validate_docx_body_render_evidence(fresh)


def test_cancellation_after_new_kind_image_removes_only_attempt_assets(setup):
    setup["output"].mkdir()
    prior = setup["output"] / "previous.png"; prior.write_bytes(b"previous")
    def cancel():
        if setup["assets"]: raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled): prepare(setup, check_cancelled=cancel)
    assert list(setup["output"].iterdir()) == [prior]


def test_byte_scanner_does_not_break_explicit_end_tags_namespace_prefixes_or_quoted_brackets(setup):
    changed = parts(setup["data"])
    name = "word/_rels/footer1.xml.rels"
    changed[name] = changed[name].replace(b'TargetMode="External"/>', b'TargetMode="External"></Relationship>')
    name = "word/footer1.xml"
    changed[name] = changed[name].replace(b'r:href="external"', b"r:href = 'external'").replace(b'o:title=""', b'o:title="harmless &gt; title"')
    setup["data"] = zip_parts(changed)
    result = prepare(setup)
    assert result["status"] == "ready" and evidence.validate_docx_body_render_evidence(result)
    assert b'r:href' not in parts(setup["seen"][0])["word/footer1.xml"]


@pytest.mark.parametrize("input_value", [None, [], {}, {"status": "ready", "evidence_kind": "docx_embedded_footer_cache_render", "evidence_version": 1}])
def test_validator_rejects_unsigned_or_noncontract_input(input_value):
    assert not evidence.validate_docx_body_render_evidence(input_value)


def test_ordinary_v2_contract_remains_independent_of_derivative_signature():
    assert evidence.validate_docx_body_render_evidence({"status": "ready", "evidence_kind": "original_docx_render", "evidence_version": 2})
