"""Portable original-PDF geometry/resource tests; no application or AI calls."""
from io import BytesIO
from copy import deepcopy
import base64
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pymupdf as fitz
import pytest
import requests

from mathbank.task_manager import TaskCancelled


from mathbank import pdf_vision_details as details


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No model/network calls allowed")))


def render(page, tmp_path, *, register=None, cancel=lambda: None, **kwargs):
    return details.render_pdf_detail_views(
        page, page_index=getattr(page, "number", 3), output_dir=tmp_path.resolve(), asset_prefix="detail_test",
        url_prefix="/static/test_uploads/tmp", register_asset=register or (lambda _url: None),
        check_cancelled=cancel, **kwargs,
    )


def coloured_pdf(rotation):
    with fitz.open() as document:
        for _index in range(3):
            document.new_page()
        page = document.new_page(width=300, height=400)
        for rect, colour in [
            (fitz.Rect(30, 40, 150, 200), (1, 0, 0)),
            (fitz.Rect(150, 40, 270, 200), (0, 1, 0)),
            (fitz.Rect(30, 200, 150, 360), (0, 0, 1)),
            (fitz.Rect(150, 200, 270, 360), (1, 1, 0)),
        ]:
            page.draw_rect(rect, color=colour, fill=colour)
        page.set_cropbox(fitz.Rect(30, 40, 270, 360))
        page.set_rotation(rotation)
        return document.tobytes()


@pytest.mark.parametrize("rotation,colours", [
    (0, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]),
    (90, [(0, 0, 255), (255, 0, 0), (255, 255, 0), (0, 255, 0)]),
    (180, [(255, 255, 0), (0, 0, 255), (0, 255, 0), (255, 0, 0)]),
    (270, [(0, 255, 0), (255, 255, 0), (255, 0, 0), (0, 0, 255)]),
])
def test_true_pdf_rotation_cropbox_pixels_and_overlap(tmp_path, rotation, colours):
    registered = []
    with fitz.open(stream=coloured_pdf(rotation), filetype="pdf") as document:
        result = render(document[3], tmp_path, register=registered.append)
    assert result["status"] == "prepared" and len(result["views"]) == len(registered) == 2
    expected_boxes = ([[0, 0, 1000, 560], [0, 440, 1000, 1000]] if rotation in (0, 180)
                      else [[0, 0, 560, 1000], [440, 0, 1000, 1000]])
    assert [view["bbox"] for view in result["views"]] == expected_boxes
    assert [view["id"] for view in result["views"]] == ["p4_d1", "p4_d2"]
    for view in result["views"]:
        assert view["page_index"] == 3 and view["actualdpi"] > 150
        assert registered[result["views"].index(view)] == view["url"]
        path = Path(view["path"])
        assert path.is_absolute() and path.parent == tmp_path.resolve()
        assert view["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            assert image.format == "PNG" and image.size == (view["width"], view["height"])
            assert max(image.size) <= 2400 and image.width * image.height <= 4_000_000
            for local_x, local_y in [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)]:
                x = view["bbox"][0] + local_x * (view["bbox"][2] - view["bbox"][0])
                y = view["bbox"][1] + local_y * (view["bbox"][3] - view["bbox"][1])
                expected = colours[(2 if y > 500 else 0) + (1 if x > 500 else 0)]
                actual = image.convert("RGB").getpixel((int(local_x * image.width), int(local_y * image.height)))
                assert all(abs(a - b) <= 3 for a, b in zip(actual, expected))


@pytest.mark.parametrize("width,height", [(595, 842), (842, 595), (800, 800)])
def test_actual_resolution_and_batch_budgets(tmp_path, width, height):
    with fitz.open() as document:
        page = document.new_page(width=width, height=height)
        result = render(page, tmp_path)
    assert result["status"] == "prepared"
    views = result["views"]
    assert all(150 < view["actualdpi"] < 302 for view in views)
    assert sum(view["width"] * view["height"] for view in views) <= 8_000_000
    assert sum(Path(view["path"]).stat().st_size for view in views) <= 20 * 1024 * 1024
    assert result["total_png_bytes"] == sum(Path(view["path"]).stat().st_size for view in views)
    assert all(Path(view["path"]).stat().st_size <= 10 * 1024 * 1024 for view in views)


def test_huge_page_skips_before_pixmap_allocation(tmp_path):
    class Page:
        number = 3
        rect = fitz.Rect(0, 0, 12000, 16000)
        def get_pixmap(self, **_kwargs):
            raise AssertionError("The insufficient-resolution view must not render")
    result = render(Page(), tmp_path)
    assert result["status"] == "skipped" and result["views"] == []
    assert result["total_png_bytes"] == 0
    assert "150 DPI" in result["notes"][0] and not list(tmp_path.iterdir())


def test_second_registration_failure_rolls_back_only_new_files(tmp_path):
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep existing")
    registered = []
    def register(url):
        registered.append(url)
        if len(registered) == 2:
            raise RuntimeError("do not expose this private callback text")
    with fitz.open() as document:
        result = render(document.new_page(width=240, height=320), tmp_path, register=register)
    assert result["status"] == "skipped" and result["views"] == []
    assert existing.read_bytes() == b"keep existing" and list(tmp_path.iterdir()) == [existing]
    assert "private callback" not in str(result)


@pytest.mark.parametrize("stage", ["initial", "registration", "after_registration"])
def test_cancellation_propagates_and_removes_only_own_files(tmp_path, stage):
    existing = tmp_path / "keep.png"
    existing.write_bytes(b"existing")
    registered = []
    def register(url):
        registered.append(url)
        if stage == "registration":
            raise TaskCancelled("cancelled")
    def cancel():
        if stage == "initial" or stage == "after_registration" and registered:
            raise TaskCancelled("cancelled")
    with fitz.open() as document, pytest.raises(TaskCancelled):
        render(document.new_page(width=240, height=320), tmp_path, register=register, cancel=cancel)
    assert list(tmp_path.iterdir()) == [existing] and existing.read_bytes() == b"existing"


@pytest.mark.parametrize("unsafe", ["relative", "directory_link", "parent_link", "parent_file", "dotdot"])
def test_invalid_output_path_rejected_without_outside_writes(tmp_path, unsafe):
    base = tmp_path.resolve()
    target = base / "target"
    target.mkdir()
    link = base / "link"
    link.symlink_to(target, target_is_directory=True)
    file = base / "file"
    file.write_bytes(b"keep")
    output = {"relative": Path("relative"), "directory_link": link, "parent_link": link / "child",
              "parent_file": file / "child", "dotdot": base / "target/../escape"}[unsafe]
    with fitz.open() as document, pytest.raises(ValueError):
        details.render_pdf_detail_views(document.new_page(), page_index=0, output_dir=output,
            asset_prefix="safe", url_prefix="/static/uploads/tmp", register_asset=lambda _url: None)
    assert not list(target.iterdir()) and file.read_bytes() == b"keep"


@pytest.mark.parametrize("index,prefix,url", [
    (True, "safe", "/static/uploads/tmp"), (-1, "safe", "/static/uploads/tmp"),
    ("1", "safe", "/static/uploads/tmp"), (0, "../bad", "/static/uploads/tmp"),
    (0, "safe.png", "/static/uploads/tmp"), (0, "safe", "https://outside.example"),
    (0, "safe", "/static/uploads/../tmp"), (0, "safe", "/static/uploads/tmp?x=y"),
])
def test_invalid_configuration_does_not_create_files(tmp_path, index, prefix, url):
    with fitz.open() as document, pytest.raises(ValueError):
        details.render_pdf_detail_views(document.new_page(), page_index=index, output_dir=tmp_path.resolve(),
            asset_prefix=prefix, url_prefix=url, register_asset=lambda _url: None)
    assert not list(tmp_path.iterdir())


def test_collision_never_overwrites_or_deletes_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(details.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    existing = tmp_path / "detail_test_p1_d2_fixed.png"
    existing.write_bytes(b"previous asset")
    with fitz.open() as document:
        result = render(document.new_page(width=240, height=320), tmp_path)
    assert result["status"] == "skipped" and not result["views"]
    assert list(tmp_path.iterdir()) == [existing] and existing.read_bytes() == b"previous asset"


@pytest.mark.parametrize("replacement", ["regular", "symlink"])
def test_registration_replacement_is_not_deleted_by_rollback(tmp_path, replacement):
    external = tmp_path / "external.bin"
    external.write_bytes(b"keep outside")
    changed = []
    def register(url):
        path = tmp_path / Path(url).name
        path.unlink()
        if replacement == "regular":
            path.write_bytes(b"replacement")
        else:
            path.symlink_to(external)
        changed.append(path)
        raise RuntimeError("registration failed")
    with fitz.open() as document:
        result = render(document.new_page(width=240, height=320), tmp_path, register=register)
    assert result["status"] == "skipped" and not result["views"]
    assert external.read_bytes() == b"keep outside"
    assert changed[0].is_symlink() if replacement == "symlink" else changed[0].read_bytes() == b"replacement"


def test_false_registration_is_not_reported_prepared(tmp_path):
    with fitz.open() as document:
        result = render(document.new_page(width=240, height=320), tmp_path, register=lambda _url: False)
    assert result["status"] == "skipped" and not result["views"] and not list(tmp_path.iterdir())

@pytest.mark.parametrize("register,cancel", [(None, lambda: None), (lambda _url: None, None)])
def test_noncallable_callback_rejected_before_writes(tmp_path, register, cancel):
    with fitz.open() as document, pytest.raises(ValueError):
        details.render_pdf_detail_views(document.new_page(), page_index=0, output_dir=tmp_path.resolve(),
            asset_prefix="safe", url_prefix="/static/uploads/tmp", register_asset=register, check_cancelled=cancel)
    assert not list(tmp_path.iterdir())


def test_inplace_mutation_of_earlier_registered_view_is_not_accepted(tmp_path):
    registered = []
    def register(url):
        registered.append(tmp_path / Path(url).name)
        if len(registered) == 2:
            with registered[0].open("r+b") as handle:
                handle.write(b"changed")
    with fitz.open() as document:
        result = render(document.new_page(width=240, height=320), tmp_path, register=register)
    assert result["status"] == "skipped" and not result["views"]
    assert not list(tmp_path.iterdir())


class SmallPage:
    number = 3
    rect = fitz.Rect(0, 0, 24, 36)
    def get_pixmap(self, *, matrix, clip, **_kwargs):
        width, height = math.ceil(clip.width * matrix.a), math.ceil(clip.height * matrix.d)
        with BytesIO() as buffer:
            Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
            png = buffer.getvalue()
        return SimpleNamespace(width=width, height=height, tobytes=lambda _kind: png)


@pytest.mark.parametrize("budget", ["per_png", "total_png", "total_pixels"])
def test_resource_budget_failures_return_no_partial_files(tmp_path, monkeypatch, budget):
    if budget == "per_png":
        monkeypatch.setattr(details, "MAX_DETAIL_PNG_BYTES", 1)
    elif budget == "total_png":
        monkeypatch.setattr(details, "MAX_TOTAL_DETAIL_PNG_BYTES", 200)
    else:
        monkeypatch.setattr(details, "MAX_TOTAL_DETAIL_PIXELS", 10_000)
    registered = []
    result = render(SmallPage(), tmp_path, register=registered.append)
    assert result["status"] == "skipped" and not result["views"] and not registered
    assert not list(tmp_path.iterdir())


def test_real_page_identity_mismatch_rejected_before_rendering(tmp_path):
    with fitz.open() as document, pytest.raises(ValueError):
        details.render_pdf_detail_views(document.new_page(), page_index=3, output_dir=tmp_path.resolve(),
            asset_prefix="safe", url_prefix="/static/uploads/tmp", register_asset=lambda _url: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("budget", [-1, True, 1.5, None])
def test_invalid_remaining_byte_budget_rejected(tmp_path, budget):
    with pytest.raises(ValueError):
        render(SmallPage(), tmp_path, byte_budget=budget)
    assert not list(tmp_path.iterdir())


def test_zero_remaining_budget_does_not_render_or_register(tmp_path):
    class Page(SmallPage):
        def get_pixmap(self, **_kwargs):
            raise AssertionError("Zero budget must not render")
    registered = []
    result = render(Page(), tmp_path, byte_budget=0, register=registered.append)
    assert result["status"] == "skipped" and not result["views"] and not registered
    assert not list(tmp_path.iterdir())


def test_remaining_budget_is_atomic_and_accepts_exact_bytes(tmp_path):
    prepared = render(SmallPage(), tmp_path / "first")
    byte_count = sum(Path(view["path"]).stat().st_size for view in prepared["views"])
    registered = []
    skipped = render(SmallPage(), tmp_path / "second", byte_budget=byte_count - 1, register=registered.append)
    assert skipped["status"] == "skipped" and not skipped["views"] and not registered
    accepted = render(SmallPage(), tmp_path / "third", byte_budget=byte_count)
    assert accepted["status"] == "prepared" and len(accepted["views"]) == 2


@pytest.fixture
def prepared_views(tmp_path):
    result = render(SmallPage(), tmp_path)
    assert result["status"] == "prepared"
    return result["views"]


def test_prepared_messages_bind_exact_png_and_safe_frozen_metadata(prepared_views):
    original = deepcopy(prepared_views)
    messages, metadata = details.prepare_detail_messages(prepared_views, page_index=3)
    assert len(messages) == 4 and metadata["detail_count"] == 2
    assert prepared_views == original
    for index, view in enumerate(prepared_views):
        assert view["id"] in messages[index * 2]["text"]
        assert "原PDF页号=4" in messages[index * 2]["text"] and "不另抄" in messages[index * 2]["text"]
        assert base64.b64decode(messages[index * 2 + 1]["image_url"]["url"].split(",", 1)[1]) == Path(view["path"]).read_bytes()
    assert metadata["detail_total_pixels"] == sum(view["width"] * view["height"] for view in prepared_views)
    assert metadata["detail_total_png_bytes"] == sum(Path(view["path"]).stat().st_size for view in prepared_views)
    assert not any(key in str(metadata) for key in ("path", "url", "base64", "data:image", str(Path(prepared_views[0]["path"]).parent)))
    prepared_views[0]["bbox"][0] = 123
    assert metadata["detail_views"][0]["bbox"][0] == 0


def test_empty_views_prepare_no_extra_messages():
    messages, metadata = details.prepare_detail_messages([], page_index=0)
    assert messages == [] and metadata == {"detail_count": 0, "detail_total_pixels": 0,
                                         "detail_total_png_bytes": 0, "detail_views": []}


@pytest.mark.parametrize("change", [
    "too_many", "duplicate_id", "wrong_page", "unknown_id", "missing_field", "extra_field", "not_dict",
    "bad_bbox", "mixed_axis", "bbox_bool", "bbox_nan", "bbox_huge", "dpi_low", "dpi_huge",
    "width_bool", "width_changed", "hash_changed", "url_external", "url_name", "relative_path",
])
def test_malicious_descriptors_rejected_before_any_messages(prepared_views, change):
    views = deepcopy(prepared_views)
    first = views[0]
    if change == "too_many": views.append(deepcopy(first))
    elif change == "duplicate_id": views[1]["id"] = first["id"]
    elif change == "wrong_page": first["page_index"] = 4
    elif change == "unknown_id": first["id"] = "p4_d3"
    elif change == "missing_field": first.pop("sha256")
    elif change == "extra_field": first["verified"] = True
    elif change == "not_dict": views[1] = "bad"
    elif change == "bad_bbox": first["bbox"][2] = 999
    elif change == "mixed_axis": views[1]["bbox"] = [440, 0, 1000, 1000]
    elif change == "bbox_bool": first["bbox"][0] = False
    elif change == "bbox_nan": first["bbox"][0] = float("nan")
    elif change == "bbox_huge": first["bbox"][0] = 10 ** 1000
    elif change == "dpi_low": first["actualdpi"] = 150
    elif change == "dpi_huge": first["actualdpi"] = 10 ** 1000
    elif change == "width_bool": first["width"] = True
    elif change == "width_changed": first["width"] += 1
    elif change == "hash_changed": first["sha256"] = "0" * 64
    elif change == "url_external": first["url"] = "https://outside.example/" + Path(first["path"]).name
    elif change == "url_name": first["url"] = first["url"].replace(".png", "_other.png")
    elif change == "relative_path": first["path"] = Path(first["path"]).name
    with pytest.raises(ValueError):
        details.prepare_detail_messages(views, page_index=3)


@pytest.mark.parametrize("change", ["symlink", "parent_symlink", "inplace_hash", "bad_png", "jpeg", "dimensions", "animated"])
def test_changed_or_spoofed_image_files_are_rejected(prepared_views, tmp_path, change):
    views = deepcopy(prepared_views)
    view = views[1]
    path = Path(view["path"])
    png = path.read_bytes()
    if change == "symlink":
        external = tmp_path / "outside.png"
        external.write_bytes(png)
        path.unlink()
        path.symlink_to(external)
    elif change == "parent_symlink":
        link = tmp_path / "alias"
        link.symlink_to(path.parent, target_is_directory=True)
        view["path"] = str(link / path.name)
    elif change == "inplace_hash":
        path.write_bytes(png + b"changed")
    else:
        with BytesIO() as buffer:
            if change == "bad_png":
                buffer.write(b"\x89PNG\r\n\x1a\nnot a complete PNG")
            elif change == "jpeg":
                Image.new("RGB", (view["width"], view["height"]), "white").save(buffer, format="JPEG")
            elif change == "dimensions":
                Image.new("RGB", (view["width"] + 1, view["height"]), "white").save(buffer, format="PNG")
            else:
                one = Image.new("RGB", (view["width"], view["height"]), "white")
                two = Image.new("RGB", (view["width"], view["height"]), "red")
                one.save(buffer, format="PNG", save_all=True, append_images=[two], duration=100)
            png = buffer.getvalue()
        path.write_bytes(png)
        view["sha256"] = hashlib.sha256(png).hexdigest()
    with pytest.raises(ValueError):
        details.prepare_detail_messages(views, page_index=3)


@pytest.mark.parametrize("budget", ["single_bytes", "total_bytes", "total_pixels"])
def test_message_preparation_budgets_reject_whole_batch(prepared_views, monkeypatch, budget):
    byte_count = sum(Path(view["path"]).stat().st_size for view in prepared_views)
    pixel_count = sum(view["width"] * view["height"] for view in prepared_views)
    if budget == "single_bytes":
        monkeypatch.setattr(details, "MAX_DETAIL_PNG_BYTES", 1)
    elif budget == "total_bytes":
        monkeypatch.setattr(details, "MAX_TOTAL_DETAIL_PNG_BYTES", byte_count - 1)
    else:
        monkeypatch.setattr(details, "MAX_TOTAL_DETAIL_PIXELS", pixel_count - 1)
    with pytest.raises(ValueError):
        details.prepare_detail_messages(prepared_views, page_index=3)


def test_message_preparation_cancellation_propagates_without_deleting_views(prepared_views):
    calls = []
    def cancel():
        calls.append(1)
        if len(calls) == 3:
            raise TaskCancelled("cancelled")
    with pytest.raises(TaskCancelled):
        details.prepare_detail_messages(prepared_views, page_index=3, check_cancelled=cancel)
    assert all(Path(view["path"]).is_file() for view in prepared_views)


def test_cancel_during_last_base64_encode_cannot_return_messages(prepared_views, monkeypatch):
    calls = []
    cancelled = []
    original = base64.b64encode
    def encode(value):
        result = original(value)
        calls.append(1)
        if len(calls) == 2:
            cancelled.append(True)
        return result
    def cancel():
        if cancelled:
            raise TaskCancelled("cancelled")
    monkeypatch.setattr(base64, "b64encode", encode)
    with pytest.raises(TaskCancelled):
        details.prepare_detail_messages(prepared_views, page_index=3, check_cancelled=cancel)
    assert len(calls) == 2 and all(Path(view["path"]).is_file() for view in prepared_views)
