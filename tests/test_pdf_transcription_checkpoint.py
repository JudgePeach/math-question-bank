"""Restart-safe original OCR integrity, using isolated files and no requests."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from mathbank.document_projects import DocumentProjectStore
from mathbank.pdf_task_resume import prepare_pdf_checkpoint, seal_pdf_checkpoint
from mathbank.pdf_transcription_checkpoint import restore_project_pdf_transcription_witnesses
from mathbank.pdf_transcription_scopes import finalize_pdf_transcription_snapshot, verify_pdf_transcription_snapshot
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("No model API permitted"))


def png(path, size=(40, 60), color="white"):
    image = BytesIO()
    Image.new("RGB", size, color).save(image, format="PNG")
    path.write_bytes(image.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    import pymupdf as fitz
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        source = document.tobytes()
    root = tmp_path / "projects"
    files = {}
    def asset(name, size=(40, 60), color="white"):
        path = tmp_path / name
        sha = png(path, size, color)
        url = "/static/uploads/tmp/" + name
        files[url] = path
        return url, sha
    images = [asset(f"pdf_page_original-task_{i}.png")[0] for i in range(2)]
    detail_url, detail_sha = asset("pdf_detail_original-task_0_p1_d1_" + "a" * 32 + ".png", (80, 100))
    detail_view = {"id": "p1_d1", "page_index": 0, "bbox": [0, 0, 1000, 560],
                   "actualdpi": 300, "width": 80, "height": 100, "sha256": detail_sha}
    texts = ["1. 原题$x=1$。", "2. 原题$y=2$。"]
    joints = []
    for index, text in enumerate(texts):
        joints.append({"markdown": text, "layout": {"page_complete": True, "figures": [], "notes": []},
            "attempts": [{"stage": "joint_page", "page_index": index, "attempt": 1, "status": "succeeded",
                "http_status": 200, "finish_reason": "stop", "response_sha256": "f" * 64,
                "response_characters": 100, "model": "offline-model"}], "visual_calls": 1, "model": "offline-model",
            "detail_input": {"status": "none", "detail_count": 0, "detail_views": [], "notes": []}})
    joints[0]["detail_input"] = {"status": "included", "detail_count": 1, "detail_views": [detail_view],
        "detail_total_pixels": 8000, "detail_total_png_bytes": files[detail_url].stat().st_size, "notes": []}
    store = DocumentProjectStore(root)
    project = store.create(source, "原卷.pdf", {"pdf_strategy": "layout_aware"}, {})["project_id"]
    state = prepare_pdf_checkpoint(None, task_id="original-task", document_sha256=hashlib.sha256(source).hexdigest(),
        page_indices=[0, 1], strategy="layout_aware", verify_suspicions=False, resolve_asset=files.__getitem__)
    state["pages"] = {str(i): {"markdown": f"<!-- MATHBANK_PDF_PAGE:{i+1} -->\n" + texts[i],
                               "joint": deepcopy(joints[i])} for i in range(2)}
    state["prepared"] = {"page_texts": ["working prepared content must never be signed", "other prepared content"]}
    def save(value=state):
        sealed = seal_pdf_checkpoint(value, assets=list(files), resolve_asset=files.__getitem__)
        store.sync_task(project, {"task_id": "original-task", "status": "error", "pdf_resume_state": sealed,
                                "page_images": images, "page_numbers": [1, 2]})
        return sealed
    original = save()
    return SimpleNamespace(store=store, project=project, source=source, files=files, images=images,
        detail_url=detail_url, state=state, original=original, texts=texts, save=save, asset=asset)


def restore(setup, **kwargs):
    diagnostics = kwargs.pop("diagnostics", {})
    value = restore_project_pdf_transcription_witnesses(setup.store, setup.project, kwargs.pop("source_bytes", setup.source),
        resolve_asset=kwargs.pop("resolve_asset", setup.files.__getitem__),
        transcript_normalizer=kwargs.pop("transcript_normalizer", lambda s: s), diagnostics=diagnostics, **kwargs)
    return value, diagnostics["pdf_cached_transcription_witness"]


def test_original_joint_cache_reissues_private_integrity_but_never_native_truth(setup):
    before = setup.store.load_task(setup.project)
    witnesses, report = restore(setup)
    assert set(witnesses) == {0, 1} and report["restored_pages"] == [1, 2]
    pages = [{"page_number": i+1, "origin": "joint_vision", "markdown": setup.state["pages"][str(i)]["markdown"]}
             for i in range(2)]
    layout = {"pages": [{"page_index": i, "status": "checked", "figures": [], "warnings": []} for i in range(2)]}
    source = "\n\n".join(page["markdown"].split("-->", 1)[1] for page in pages)
    evidence = finalize_pdf_transcription_snapshot(source, {}, source_document_sha256=hashlib.sha256(setup.source).hexdigest(),
        source_pages=pages, layout_result=layout, witnesses=witnesses, transcript_normalizer=lambda x: x, figure_assets={})
    proof = verify_pdf_transcription_snapshot(source, {}, evidence, source_document_sha256=hashlib.sha256(setup.source).hexdigest(),
        source_pages=pages, layout_result=layout)
    assert proof["status"] == "ready" and all(p["snapshot_eligible"] for p in proof["pages"])
    assert proof["native_reliable"] is False and all(p["reliable"] is False for p in proof["pages"])
    assert report["original_verified"] is False and report["ai_calls"] == 0
    assert setup.store.load_task(setup.project) == before
    assert "working prepared" not in witnesses[0].payload


def test_persisted_reload_without_any_old_private_objects_still_restores(setup):
    setup.store = DocumentProjectStore(setup.store.root)
    witnesses, _ = restore(setup)
    assert set(witnesses) == {0, 1}
    assert all(type(value).__name__ == "_TranscriptWitness" for value in witnesses.values())


@pytest.mark.parametrize("field,value", [("status", "failed"), ("http_status", 429),
    ("finish_reason", "length"), ("finish_reason", None), ("stage", "regional_page"),
    ("page_index", 1), ("response_sha256", None), ("response_characters", 0)])
def test_missing_original_success_evidence_skips_only_its_page(setup, field, value):
    state = deepcopy(setup.state)
    state["pages"]["0"]["joint"]["attempts"][-1][field] = value
    setup.save(state)
    witnesses, report = restore(setup)
    assert set(witnesses) == {1}
    assert report["skipped_pages"] == [{"page_number": 1, "reason": "successful_request_evidence_missing"}]


@pytest.mark.parametrize("change", ["quality_flag", "quality_body", "missing_joint", "missing_detail_input", "page_text"])
def test_no_repair_of_bad_or_missing_original_payload(setup, change):
    state = deepcopy(setup.state)
    page = state["pages"]["0"]
    if change == "quality_flag":
        page["joint"]["ocr_succeeded"] = False
    elif change == "quality_body":
        page["joint"]["markdown"] = "1. 原题\n\\begin{choices}\\item \\item 2\\end{choices}"
        page["markdown"] = "<!-- MATHBANK_PDF_PAGE:1 -->\n" + page["joint"]["markdown"]
    elif change == "missing_joint":
        page["joint"] = None
    elif change == "missing_detail_input":
        page["joint"].pop("detail_input")
    else:
        page["markdown"] += " manual changed"
    setup.save(state)
    witnesses, _ = restore(setup)
    assert set(witnesses) == {1}


def test_manual_and_recovered_working_pages_never_get_new_witnesses(setup):
    witnesses, report = restore(setup, edited_page_numbers={1}, recovered_page_numbers={2})
    assert witnesses == {} and report["restored_pages"] == []
    assert [row["reason"] for row in report["skipped_pages"]] == ["working_page_excluded"] * 2


def test_normalizer_must_exactly_reproduce_original_page_record(setup):
    state = deepcopy(setup.state)
    state["pages"]["0"]["joint"]["markdown"] += "\nremove me"
    setup.save(state)
    witnesses, _ = restore(setup, transcript_normalizer=lambda text: text.removesuffix("\nremove me"))
    assert set(witnesses) == {0, 1}
    witnesses, _ = restore(setup)
    assert set(witnesses) == {1}


@pytest.mark.parametrize("asset", ["navigation", "detail"])
@pytest.mark.parametrize("change", ["missing", "changed", "symlink"])
def test_sealed_original_assets_must_all_still_exist_and_match(setup, asset, change):
    url = setup.images[0] if asset == "navigation" else setup.detail_url
    path = setup.files[url]
    if change == "missing":
        path.unlink()
    elif change == "changed":
        path.write_bytes(b"changed source pixels")
    else:
        other = path.with_suffix(".copy.png")
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    witnesses, report = restore(setup)
    assert witnesses == {} and report["reason"] == "sealed_checkpoint_or_original_asset_unavailable"


def test_does_not_borrow_current_same_hash_thumbnail_when_original_is_missing(setup):
    current_url, _ = setup.asset("pdf_page_current-task_0.png")
    setup.files[setup.images[0]].unlink()
    def replacement(url):
        return setup.files[current_url] if url == setup.images[0] else setup.files[url]
    witnesses, report = restore(setup, resolve_asset=replacement)
    assert witnesses == {} and report["restored_pages"] == []


def test_detail_namespace_selects_original_nav_not_extra_current_preview(setup):
    setup.asset("pdf_page_current-task_0.png")
    setup.save()
    witnesses, _ = restore(setup)
    assert set(witnesses) == {0, 1}
    assert "pdf_page_original-task_0.png" in witnesses[0].payload
    assert "pdf_page_current-task_0.png" not in witnesses[0].payload


def test_no_detail_and_two_possible_original_navigation_images_is_ambiguous(setup):
    setup.asset("pdf_page_current-task_1.png")
    setup.save()
    witnesses, report = restore(setup)
    assert set(witnesses) == {0}
    assert report["skipped_pages"] == [{"page_number": 2, "reason": "original_navigation_missing_or_ambiguous"}]


def test_same_detail_sha_with_multiple_sealed_paths_is_ambiguous(setup):
    setup.asset("pdf_detail_original-task_0_p1_d1_" + "b" * 32 + ".png", (80, 100))
    setup.save()
    witnesses, report = restore(setup)
    assert set(witnesses) == {1}
    assert report["skipped_pages"] == [{"page_number": 1, "reason": "original_detail_missing_or_ambiguous"}]


def test_true_pdf_bytes_hash_is_required_even_if_checkpoint_self_hash_is_valid(setup):
    witnesses, report = restore(setup, source_bytes=setup.source + b"modified")
    assert not witnesses and report["reason"] == "original_pdf_changed"


def test_unsealed_or_changed_checkpoint_is_never_authority(setup):
    task = setup.store.load_task(setup.project)
    task["pdf_resume_state"]["pages"]["0"]["joint"]["markdown"] = "fake original"
    setup.store.sync_task(setup.project, task)
    witnesses, report = restore(setup)
    assert witnesses == {} and report["reason"] == "sealed_checkpoint_or_original_asset_unavailable"


def test_json_verified_flag_cannot_replace_server_owned_store(setup):
    result = restore_project_pdf_transcription_witnesses({"verified": True, "checkpoint": setup.original},
        setup.project, setup.source, resolve_asset=setup.files.__getitem__, transcript_normalizer=lambda x: x)
    assert result == {}


def test_cancellation_is_not_reported_as_a_success_or_silent_cache_miss(setup):
    diagnostics = {}
    def cancelled():
        raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        restore(setup, check_cancelled=cancelled, diagnostics=diagnostics)
    assert diagnostics["pdf_cached_transcription_witness"]["status"] == "cancelled"


def test_card_only_draft_save_does_not_invalidate_original_ocr_evidence(setup):
    changed = False
    def normalizer(text):
        nonlocal changed
        if not changed:
            setup.store.save_draft(setup.project, {"questions": [], "page_edits": []}, 0)
            changed = True
        return text
    witnesses, report = restore(setup, transcript_normalizer=normalizer)
    assert set(witnesses) == {0, 1} and report["status"] == "restored"


def test_original_checkpoint_changed_during_restore_discards_all_new_witnesses(setup):
    changed = False
    def normalizer(text):
        nonlocal changed
        if not changed:
            state = deepcopy(setup.state)
            state["run"] = 9
            setup.save(state)
            changed = True
        return text
    witnesses, report = restore(setup, transcript_normalizer=normalizer)
    assert witnesses == {} and report["reason"] == "project_changed"


def test_fake_physical_page_outside_original_pdf_is_ineligible(setup):
    state = deepcopy(setup.state)
    state["page_indices"].append(999)
    setup.save(state)
    witnesses, report = restore(setup)
    assert witnesses == {} and report["reason"] == "sealed_checkpoint_missing"
