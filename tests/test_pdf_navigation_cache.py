"""Navigation reuse retains full checkpoint validation and physical-page identity."""
from copy import deepcopy
import hashlib

import pytest

from mathbank.pdf_navigation_cache import bind_pdf_navigation, cached_pdf_navigation
from mathbank.pdf_task_resume import prepare_pdf_checkpoint, seal_pdf_checkpoint


@pytest.fixture
def saved(tmp_path):
    paths = {}
    for task in ("old-task", "other-task"):
        for number in (0, 1):
            url = f"/static/uploads/tmp/pdf_page_{task}_{number}.png"
            path = tmp_path / url.rsplit("/", 1)[-1]
            path.write_bytes(f"original-pdf-page-{number}".encode())
            paths[url] = path
    resolve = paths.__getitem__
    settings = dict(task_id="old-task", document_sha256=hashlib.sha256(b"pdf").hexdigest(),
                    page_indices=[0, 1], strategy="layout_aware", verify_suspicions=False,
                    resolve_asset=resolve)
    state = prepare_pdf_checkpoint(None, **settings)
    urls = [url for url in paths if "old-task" in url]
    state = seal_pdf_checkpoint(state, assets=urls, resolve_asset=resolve)
    return state, settings, paths


def test_unique_legacy_navigation_is_reused_after_full_receipt_validation(saved):
    state, settings, paths = saved
    checked = prepare_pdf_checkpoint(state, **settings)
    for number in (0, 1):
        url = f"/static/uploads/tmp/pdf_page_old-task_{number}.png"
        assert cached_pdf_navigation(checked, number, resolve_asset=paths.__getitem__) == (url, paths[url])
    assert cached_pdf_navigation(checked, 2, resolve_asset=paths.__getitem__) is None


def test_explicit_binding_survives_new_task_id_without_duplicate_png(saved):
    state, settings, paths = saved
    checked = prepare_pdf_checkpoint(state, **settings)
    url = "/static/uploads/tmp/pdf_page_old-task_0.png"
    bind_pdf_navigation(checked, 0, url)
    checked["task_id"] = "new-task"
    sealed = seal_pdf_checkpoint(checked, assets=[], resolve_asset=paths.__getitem__)
    checked = prepare_pdf_checkpoint(sealed, **{**settings, "task_id": "new-task"})
    assert cached_pdf_navigation(checked, 0, resolve_asset=paths.__getitem__) == (url, paths[url])
    assert len(checked["assets"]) == 2


def test_changed_pixels_still_fail_before_cache_selection(saved):
    state, settings, paths = saved
    next(iter(paths.values())).write_bytes(b"changed")
    with pytest.raises(ValueError, match="已变化"):
        prepare_pdf_checkpoint(state, **settings)


@pytest.mark.parametrize("change", ["source", "page", "dpi", "unregistered"])
def test_invalid_explicit_binding_is_not_used(saved, change):
    state, settings, paths = saved
    checked = prepare_pdf_checkpoint(state, **settings)
    url = "/static/uploads/tmp/pdf_page_old-task_0.png"
    bind_pdf_navigation(checked, 0, url)
    binding = checked["navigation_pages"]["0"]
    if change == "source": binding["document_sha256"] = "wrong"
    if change == "page": binding["page_index"] = 1
    if change == "dpi": binding["dpi"] = 300
    if change == "unregistered": binding["url"] = "/static/uploads/tmp/pdf_page_other-task_0.png"
    assert cached_pdf_navigation(checked, 0, resolve_asset=paths.__getitem__) is None


def test_ambiguous_legacy_pages_are_rendered_again_but_exact_layout_selects_one(saved):
    state, settings, paths = saved
    another = "/static/uploads/tmp/pdf_page_other-task_0.png"
    checked = prepare_pdf_checkpoint(state, **settings)
    checked = seal_pdf_checkpoint(checked, assets=[another], resolve_asset=paths.__getitem__)
    checked = prepare_pdf_checkpoint(checked, **settings)
    assert cached_pdf_navigation(checked, 0, resolve_asset=paths.__getitem__) is None
    checked["prepared"] = {"layout_result": {"pages": [{"page_index": 0, "page_image": another}]}}
    assert cached_pdf_navigation(checked, 0, resolve_asset=paths.__getitem__) == (another, paths[another])
    wrong = deepcopy(checked)
    wrong["prepared"]["layout_result"]["pages"][0]["page_image"] = "/static/uploads/tmp/pdf_page_old-task_1.png"
    assert cached_pdf_navigation(wrong, 0, resolve_asset=paths.__getitem__) is None
