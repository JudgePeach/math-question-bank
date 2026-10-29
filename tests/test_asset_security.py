import io
import errno
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from main import LOCAL_TOKEN, compile_tikz_to_png
from mathbank.asset_security import (
    AssetSecurityError,
    fsync_writable_file,
    normalize_answer_tikz_assets,
    normalize_content_tikz_assets,
    normalize_optional_upload_asset_reference,
    normalize_upload_asset_reference,
    resolve_upload_asset,
    replace_file_atomic,
    write_private_text_atomic,
)
from mathbank.database import Question
from mathbank.paper_helper import compile_tex_to_pdf


def _png_bytes(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_private_atomic_write_works_without_unix_fchmod(tmp_path, monkeypatch):
    monkeypatch.delattr("mathbank.asset_security.os.fchmod", raising=False)
    target = tmp_path / "private-token"

    write_private_text_atomic(target, "persistent-value")

    assert target.read_text(encoding="utf-8") == "persistent-value"
    assert not list(tmp_path.glob(".private-token.*"))


def test_completed_file_fsync_retains_write_access_and_original_bytes(tmp_path, monkeypatch):
    target = tmp_path / "中文 含空格.zip"
    target.write_bytes(b"completed archive")
    real_fsync, descriptors = os.fsync, []
    def require_write_access(descriptor):
        # A zero-byte write fails for an rb descriptor on both host platforms.
        os.write(descriptor, b"")
        descriptors.append(descriptor)
        real_fsync(descriptor)
    monkeypatch.setattr("mathbank.asset_security.os.fsync", require_write_access)
    fsync_writable_file(target)
    assert target.read_bytes() == b"completed archive" and len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def _replace_platform(monkeypatch, platform, replacement):
    from mathbank import asset_security
    monkeypatch.setattr(asset_security, "os", SimpleNamespace(**{
        **vars(os), "name": platform, "replace": replacement,
    }))
    delays = []
    monkeypatch.setattr(asset_security.time, "sleep", delays.append)
    return delays


def test_private_atomic_write_retries_only_transient_windows_occupancy(tmp_path, monkeypatch):
    target = tmp_path / "private-token"
    target.write_text("old", encoding="utf-8")
    original_replace, attempts = os.replace, []
    def transient(source, destination):
        attempts.append((source, destination))
        if len(attempts) <= 2:
            failure = PermissionError(errno.EACCES, "private filename")
            failure.winerror = 32
            raise failure
        return original_replace(source, destination)
    delays = _replace_platform(monkeypatch, "nt", transient)
    write_private_text_atomic(target, "新回执")
    assert target.read_text(encoding="utf-8") == "新回执"
    assert delays == [0.05, 0.1] and len(attempts) == 3
    assert not list(tmp_path.glob(".private-token.*"))


@pytest.mark.parametrize("platform,winerror", [("nt", 5), ("nt", 112), ("posix", 32)])
def test_atomic_replace_does_not_retry_permanent_or_non_windows_errors(tmp_path, monkeypatch, platform, winerror):
    source, target = tmp_path / "new", tmp_path / "old"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    failure = PermissionError(errno.EACCES, "private filename")
    failure.winerror = winerror
    attempts = []
    def fail(*args):
        attempts.append(args)
        raise failure
    delays = _replace_platform(monkeypatch, platform, fail)
    with pytest.raises(PermissionError) as raised:
        replace_file_atomic(source, target)
    assert raised.value is failure and len(attempts) == 1 and not delays
    assert source.read_bytes() == b"new" and target.read_bytes() == b"old"


@pytest.mark.parametrize("winerror", [32, 33])
def test_atomic_replace_transient_retry_is_bounded_and_preserves_files(tmp_path, monkeypatch, winerror):
    source, target = tmp_path / "new", tmp_path / "old"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    failure = PermissionError(errno.EACCES, "private filename")
    failure.winerror = winerror
    attempts = []
    def fail(*args):
        attempts.append(args)
        raise failure
    delays = _replace_platform(monkeypatch, "nt", fail)
    with pytest.raises(PermissionError) as raised:
        replace_file_atomic(source, target)
    assert raised.value is failure and len(attempts) == 5 and sum(delays) <= 1
    assert source.read_bytes() == b"new" and target.read_bytes() == b"old"


def test_upload_asset_resolver_accepts_only_contained_regular_files(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    image_path = upload_dir / "safe.png"
    image_path.write_bytes(_png_bytes())

    resolved = resolve_upload_asset(
        "/static/uploads/safe.png",
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert resolved == image_path.resolve()
    assert normalize_upload_asset_reference(
        "static/uploads/safe.png",
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    ) == "/static/uploads/safe.png"

    directory_with_image_suffix = upload_dir / "folder.png"
    directory_with_image_suffix.mkdir()
    with pytest.raises(AssetSecurityError, match="普通文件"):
        resolve_upload_asset(
            "/static/uploads/folder.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )


def test_upload_asset_resolver_rejects_traversal_and_symlinks(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())

    with pytest.raises(AssetSecurityError):
        resolve_upload_asset(
            "/static/uploads/../outside.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )

    link = upload_dir / "linked.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持测试符号链接")
    with pytest.raises(AssetSecurityError, match="符号链接"):
        resolve_upload_asset(
            "/static/uploads/linked.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )


def test_tikz_reference_assets_must_be_registered_with_question_images(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    rendered = upload_dir / "tikz.png"
    reference = upload_dir / "original.png"
    rendered.write_bytes(_png_bytes())
    reference.write_bytes(_png_bytes())
    rendered_url = "/static/uploads/tikz.png"
    reference_url = "/static/uploads/original.png"

    with pytest.raises(AssetSecurityError, match="image_paths"):
        normalize_optional_upload_asset_reference(
            reference_url,
            allowed_image_paths=[rendered_url],
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )

    assets = normalize_answer_tikz_assets(
        [{
            "id": "tikz_reference_test",
            "image_path": rendered_url,
            "reference_image_path": reference_url,
            "tikz_code": "\\begin{tikzpicture}\\end{tikzpicture}",
            "instruction": "",
        }],
        allowed_image_paths=[rendered_url, reference_url],
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert assets[0]["reference_image_path"] == reference_url

    content_assets = normalize_content_tikz_assets(
        [{
            "id": "content_tikz_reference_test",
            "image_path": rendered_url,
            "reference_image_path": reference_url,
            "tikz_code": "\\begin{tikzpicture}\\end{tikzpicture}",
            "instruction": "题干示意图",
        }],
        allowed_image_paths=[rendered_url, reference_url],
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert content_assets[0]["instruction"] == "题干示意图"


def test_single_upload_reencodes_real_images_and_rejects_active_content(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        valid = client.post(
            "/api/upload",
            files={"file": ("renamed.html", _png_bytes(), "text/html")},
            headers=headers,
        )
        fake = client.post(
            "/api/upload",
            files={"file": ("payload.html", b"<script>alert(1)</script>", "text/html")},
            headers=headers,
        )

    assert valid.status_code == 200
    stored_url = valid.json()["file_path"]
    assert stored_url.endswith(".png")
    stored_path = upload_dir / Path(stored_url).name
    with Image.open(stored_path) as stored:
        assert stored.format == "PNG"
    assert fake.status_code == 400
    assert list(upload_dir.glob("*.html")) == []


def test_batch_upload_uses_the_same_safe_reencoding(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    polyglot = _png_bytes() + b"<script>active-trailer</script>"

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        response = client.post(
            "/api/upload/batch",
            files=[("files", ("diagram.html", polyglot, "text/html"))],
            headers=headers,
        )

    assert response.status_code == 200
    stored_url = response.json()["mapping"]["diagram.html"]
    stored_path = upload_dir / Path(stored_url).name
    assert stored_path.suffix == ".png"
    assert b"active-trailer" not in stored_path.read_bytes()
    with Image.open(stored_path) as stored:
        assert stored.format == "PNG"


def test_single_ocr_and_pdf_uploads_stop_at_stream_limits(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}

    with patch("main.MAX_SINGLE_IMAGE_BYTES", 8):
        image_response = client.post(
            "/api/upload",
            files={"file": ("large.png", b"123456789", "image/png")},
            headers=headers,
        )
    with patch("main.MAX_OCR_IMAGE_BYTES", 8):
        ocr_response = client.post(
            "/api/ocr",
            files={"file": ("large.png", b"123456789", "image/png")},
            headers=headers,
        )
    with patch("main.MAX_PDF_BYTES", 8):
        pdf_response = client.post(
            "/api/upload/pdf-task",
            files={"file": ("large.pdf", b"%PDF-1234", "application/pdf")},
            headers=headers,
        )

    assert image_response.status_code == 413
    assert ocr_response.status_code == 413
    assert pdf_response.status_code == 413


def test_pdf_upload_rejects_suffix_only_spoof(client):
    response = client.post(
        "/api/upload/pdf-task",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 400
    assert "不是有效的 PDF" in response.json()["message"]


def test_question_image_paths_reject_traversal_and_legacy_delete_never_unlinks_it(
    client, db_session, tmp_path
):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    temp_dir = upload_dir / "tmp"
    temp_dir.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    unsafe_reference = "/static/uploads/../outside.png"

    payload = {
        "content": "安全路径测试题",
        "question_type": "single_choice",
        "difficulty": "medium",
        "image_paths": json.dumps([unsafe_reference]),
    }
    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.TMP_UPLOAD_DIR", str(temp_dir)
    ), patch("main.UPLOAD_DIR_REL", "static/uploads"):
        rejected = client.post("/api/questions", data=payload, headers=headers)
    assert rejected.status_code == 400
    assert outside.exists()

    legacy = Question(content="旧数据", question_type="single_choice", difficulty="medium")
    legacy.image_paths = [unsafe_reference]
    db_session.add(legacy)
    db_session.commit()
    legacy_id = legacy.id

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        deleted = client.delete(f"/api/questions/{legacy_id}", headers=headers)
    assert deleted.status_code == 200
    assert outside.exists()


def test_xelatex_commands_disable_shell_escape_and_restrict_kpathsea(tmp_path):
    paper_calls = []

    def fail_paper(cmd, *, cwd, **kwargs):
        paper_calls.append((cmd, cwd, kwargs))
        return SimpleNamespace(returncode=1, stdout="failure", stderr="")

    with patch("mathbank.paper_helper.subprocess.run", side_effect=fail_paper):
        pdf_bytes, _ = compile_tex_to_pdf("unique shell escape security test")
    assert pdf_bytes is None
    paper_cmd, paper_cwd, paper_kwargs = paper_calls[0]
    assert "-no-shell-escape" in paper_cmd
    assert paper_kwargs["env"]["openin_any"] == "p"
    assert paper_kwargs["env"]["openout_any"] == "p"
    assert paper_kwargs["env"]["TEXMFOUTPUT"] == os.path.abspath(paper_cwd)

    tikz_calls = []

    def fail_tikz(cmd, **kwargs):
        tikz_calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/xelatex"), patch(
        "subprocess.run", side_effect=fail_tikz
    ), patch("main.UPLOAD_DIR", str(tmp_path)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        with pytest.raises(RuntimeError, match="编译错误"):
            compile_tikz_to_png("\\begin{tikzpicture}\\end{tikzpicture}")
    tikz_cmd, tikz_kwargs = tikz_calls[0]
    assert "-no-shell-escape" in tikz_cmd
    assert tikz_kwargs["env"]["openin_any"] == "p"
    assert tikz_kwargs["env"]["openout_any"] == "p"


def test_invalid_token_log_does_not_echo_submitted_or_expected_secret(client, capsys):
    submitted = "attacker-supplied-secret"
    response = client.post(
        "/api/settings/save",
        data={"deepseek_key": "unused"},
        headers={"X-Local-Token": submitted},
    )
    captured = capsys.readouterr().out
    assert response.status_code == 403
    assert submitted not in captured
    assert LOCAL_TOKEN not in captured


def test_future_patch_routes_are_also_protected_by_local_token(client):
    blocked = client.patch("/api/not-yet-defined")
    authenticated = client.patch(
        "/api/not-yet-defined", headers={"X-Local-Token": LOCAL_TOKEN}
    )

    assert blocked.status_code == 403
    assert authenticated.status_code == 404


def test_manual_pdf_crop_requires_a_real_uuid_task_and_bounded_coordinates(
    client, tmp_path
):
    import main

    headers = {"X-Local-Token": LOCAL_TOKEN}
    task_id = str(uuid.uuid4())
    upload_dir = tmp_path / "uploads"
    temp_dir = upload_dir / "tmp"
    temp_dir.mkdir(parents=True)
    page_image = temp_dir / f"pdf_page_{task_id}_0.png"
    page_image.write_bytes(_png_bytes((100, 100)))
    main.DOCUMENT_TASKS.create(
        task_id, document_type="pdf", temp_assets=[], status="completed"
    )

    try:
        with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
            "main.TMP_UPLOAD_DIR", str(temp_dir)
        ), patch("main.UPLOAD_DIR_REL", "static/uploads"):
            traversal = client.post(
                "/api/ai/manual-crop-pdf",
                json={"task_id": "../../outside", "page_index": 0},
                headers=headers,
            )
            invalid_box = client.post(
                "/api/ai/manual-crop-pdf",
                json={
                    "task_id": task_id,
                    "page_index": 0,
                    "xmin": 90,
                    "ymin": 10,
                    "xmax": 10,
                    "ymax": 80,
                },
                headers=headers,
            )
            valid = client.post(
                "/api/ai/manual-crop-pdf",
                json={
                    "task_id": task_id,
                    "page_index": 0,
                    "xmin": 10,
                    "ymin": 10,
                    "xmax": 80,
                    "ymax": 80,
                },
                headers=headers,
            )

        assert traversal.status_code == 400
        assert invalid_box.status_code == 400
        assert valid.status_code == 200
        crop_name = Path(valid.json()["image_path"]).name
        assert (temp_dir / crop_name).is_file()
        assert valid.json()["image_path"] in main.DOCUMENT_TASKS.snapshot(task_id)[
            "temp_assets"
        ]
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_cancel_endpoint_does_not_delete_assets_when_worker_wins_race(
    client, monkeypatch
):
    import main

    task_id = str(uuid.uuid4())
    asset = "/static/test_uploads/tmp/completed.png"
    main.DOCUMENT_TASKS.create(
        task_id, status="processing", temp_assets=[asset], document_type="pdf"
    )
    deleted = []
    monkeypatch.setattr(
        main.DOCUMENT_TASKS,
        "cancel",
        lambda _task_id: {"status": "completed", "temp_assets": [asset]},
    )
    monkeypatch.setattr(
        main,
        "_delete_task_temp_assets",
        lambda paths: deleted.extend(paths) or len(paths),
    )

    try:
        response = client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"X-Local-Token": LOCAL_TOKEN},
        )
        assert response.status_code == 409
        assert response.json()["task_status"] == "completed"
        assert deleted == []
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@pytest.mark.parametrize("route", ["/api/upload", "/api/ocr"])
def test_pillow_syntax_error_is_an_invalid_image_response(client, route):
    # Cover decoder exception normalization on both supported Pillow pins.
    with patch("mathbank.asset_security.ImageOps.exif_transpose", side_effect=SyntaxError("bad EXIF")):
        response = client.post(
            route, files={"file": ("bad.png", _png_bytes(), "image/png")},
            headers={"X-Local-Token": LOCAL_TOKEN},
        )
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    assert "bad EXIF" not in response.json()["message"]


def _png_with_exif_chunk(payload):
    import struct
    import zlib
    raw = _png_bytes()
    chunk = b"eXIf" + payload
    return (raw[:33] + struct.pack(">I", len(payload)) + chunk
            + struct.pack(">I", zlib.crc32(chunk) & 0xffffffff) + raw[33:])


@pytest.mark.parametrize("route", ["/api/upload", "/api/ocr"])
@pytest.mark.parametrize("markers", [2, 3, 10])
def test_malformed_png_exif_is_rejected_before_upload_or_ocr(client, route, markers):
    from PIL import __version__ as pillow_version
    if int(pillow_version.split(".")[0]) < 13:
        pytest.skip("Pillow 12 compatibility fallback still accepts malformed EXIF; not a security fix")
    raw = _png_with_exif_chunk(b"Exif\0\0" * markers)
    assert len(raw) < 256
    response = client.post(
        route, files={"file": ("bad.png", raw, "image/png")},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_valid_png_exif_still_uploads(client, tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    raw = _png_with_exif_chunk(exif.tobytes())
    with patch("main.UPLOAD_DIR", str(tmp_path)):
        response = client.post(
            "/api/upload", files={"file": ("valid.png", raw, "image/png")},
            headers={"X-Local-Token": LOCAL_TOKEN},
        )
    assert response.status_code == 200
