"""Legacy headers get an explicit review reason, never a v5 success claim.

These synthetic headers exercise the dispatch boundary; they do not establish
support for real legacy Equation Editor record grammars.
"""
from io import BytesIO
import struct
import zipfile

import pytest
from PIL import Image

from mathbank.docx_helper import extract_docx_markdown
from mathbank.mtef_helper import decode_mtef_formula, extract_mtef_from_ole


def native(payload):
    return struct.pack("<HIHI4I", 28, 0x00020000, 0, len(payload), 0, 0, 0, 0) + payload


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("wrapped", [False, True])
def test_legacy_version_is_identified_but_never_fed_to_v5(version, wrapped):
    # A forged text annotation cannot bypass the legacy-format boundary.
    payload = bytes((version, 1, int(version == 3), 3, 0)) + b"LaTeX: x+1\0"
    data = native(payload) if wrapped else payload
    assert extract_mtef_from_ole(data) == payload
    result = decode_mtef_formula(data)
    assert not result.success and not result.latex and result.confidence == "none"
    assert f"MTEF {version}" in result.warning and "原公式预览" in result.warning


@pytest.mark.parametrize("data", [b"\x03", b"\x04\x01\x00\x03\x00",
    b"\x03\x02\x00\x03\x00\0", b"\x04\x01\x02\x03\x00\0",
    b"\x04\x01\x00\x07\x00\0", b"\x03\x01\x01\x03\xff\0",
    b"prefix\x03\x01\x01\x03\x00LaTeX: x+1\0"])
def test_bad_or_embedded_legacy_headers_are_not_located_in_arbitrary_bytes(data):
    assert not extract_mtef_from_ole(data)
    result = decode_mtef_formula(data)
    assert not result.success and "未找到" in result.warning


@pytest.mark.parametrize("version", [3, 4])
def test_legacy_equation_native_inside_ole_retains_version_dispatch(monkeypatch, version):
    payload = bytes((version, 1, int(version == 3), 3, 0, 0))

    class Compound:
        def __init__(self, *_args): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def listdir(self, **_kwargs): return [["Equation Native"]]
        def openstream(self, _path): return BytesIO(native(payload))

    monkeypatch.setattr("mathbank.mtef_helper.olefile.OleFileIO", Compound)
    result = decode_mtef_formula(b"simulated compound container")
    assert not result.success and f"MTEF {version}" in result.warning


@pytest.mark.parametrize("version", [3, 4])
def test_docx_legacy_formula_keeps_preview_in_its_original_position(tmp_path, version):
    image = Image.new("RGB", (12, 8), "white")
    image.putpixel((4, 3), (0, 0, 0))
    png = BytesIO(); image.save(png, format="PNG")
    blob = BytesIO()
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{w}" xmlns:r="{r}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"><w:body><w:p><w:r><w:t>1. 原式</w:t></w:r><w:r><w:object><v:shape><v:imagedata r:id="picture"/></v:shape><o:OLEObject ProgID="Equation.3" r:id="formula"/></w:object></w:r><w:r><w:t>之后</w:t></w:r></w:p></w:body></w:document>')
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="formula" Type="{r}/oleObject" Target="embeddings/equation.bin"/><Relationship Id="picture" Type="{r}/image" Target="media/equation.png"/></Relationships>')
        archive.writestr("word/embeddings/equation.bin", native(bytes((version, 1, int(version == 3), 3, 0, 0))))
        archive.writestr("word/media/equation.png", png.getvalue())
    result = extract_docx_markdown(blob.getvalue(), output_dir=tmp_path, url_prefix="/static/uploads/tmp")
    assert result["success"] and len(result["image_paths"]) == 1
    assert result["markdown"].startswith("1. 原式\n![](")
    assert result["markdown"].endswith("之后")
    assert result["diagnostics"]["mtef_converted"] == 0
    assert result["diagnostics"]["mtef_fallback_images"] == 1
    assert result["diagnostics"]["review_required"] >= 1
    assert any(f"MTEF {version}" in warning for warning in result["diagnostics"]["warnings"])
    assert (tmp_path / result["image_paths"][0].rsplit("/", 1)[1]).read_bytes() == png.getvalue()


@pytest.mark.parametrize("typeface", [3, 4, 5, 6, 22, 24, -1])
@pytest.mark.parametrize("width", [8, 16])
def test_a_redefinable_font_position_without_mtcode_cannot_be_guessed(typeface, width):
    option = 0x20 | (0x04 if width == 8 else 0x10)
    position = b"a" if width == 8 else b"a\0"
    record = bytes((2, option, typeface + 128)) + position
    payload = b"\x05\x01\x00\x07\x00DSMT7\x00\x00\x01\x00" + record + b"\0\0"
    result = decode_mtef_formula(payload)
    assert not result.success and not result.latex
    assert "缺少 MTCode" in result.warning and "字体编码" in result.warning
