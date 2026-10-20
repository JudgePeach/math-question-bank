"""PDF empty-page, cancellation and stage-resume contracts; never live AI."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from uuid import uuid4

import pymupdf as fitz
import pytest
import requests

from pdf_feature_contract import experimental_pdf_only
from mathbank.pdf_blank_pages import is_confirmed_blank_page
from mathbank.pdf_task_resume import prepare_pdf_checkpoint, seal_pdf_checkpoint


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *_a, **_k: pytest.fail("No live model calls"))


def pdf_bytes(*, count=2, blank_indices=()):
    with fitz.open() as doc:
        for index in range(count):
            page = doc.new_page(width=595, height=842)
            if index not in blank_indices:
                page.insert_text((30, 40), f"PDF fixture page {index + 1}")
        return doc.tobytes()


def reset_task(main, task_id):
    main.DOCUMENT_TASKS.remove(task_id)
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])


def test_empty_sheet_requires_exact_local_white_render_and_no_text():
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        assert is_confirmed_blank_page(page)
        page.draw_line((20, 20), (21, 21), color=(0.99, 0.99, 0.99))
        assert not is_confirmed_blank_page(page)
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.insert_text((20, 20), "x", color=(1, 1, 1))
        assert not is_confirmed_blank_page(page)


@experimental_pdf_only
def test_native_inspector_recognizes_real_blank_page():
    from mathbank.pdf_inspector_helper import inspect_and_extract_pdf
    result = inspect_and_extract_pdf(pdf_bytes(count=1, blank_indices=(0,)))
    assert result["pages"][0]["confirmed_blank"] is True
    assert result["pages"][0]["markdown"] == ""


def test_original_cloud_ocr_strategy_keeps_blank_page_numbers(monkeypatch):
    import main
    task_id = "blank-flow-" + uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    calls = []
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda path, **_k: calls.append(path) or "1. 计算 $1+1$。")
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: [
        {"content": "计算 $1+1$。", "answer_markdown": ""}])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(blank_indices=(1,)), "blank.pdf", pdf_strategy="force_ocr")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        # force_ocr retains the upstream all-page request contract.  The
        # separately tested local blank detector is not installed in this flow.
        assert len(calls) == 2 and task["page_numbers"] == [1, 2]
        assert "blank_page_numbers" not in task["diagnostics"].get("pdf_extraction", {})
        assert len(task["data"]) == 1
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_pdf_user_title_takes_precedence_over_detected_heading(monkeypatch):
    import main
    task_id = "pdf-title-" + uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[],
                              document_input={"options": {"paper_title": "教师指定标题"}})
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *_a, **_k: "1. 计算 $1+1$。")
    monkeypatch.setattr(main, "extract_title_from_latex", lambda _source: "原卷识别标题")
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: [
        {"content": "计算 $1+1$。", "answer_markdown": ""}])
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(count=1), "filename.pdf", pdf_strategy="force_ocr")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        assert task["data"][0]["source"] == "教师指定标题"
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@experimental_pdf_only
def test_entirely_blank_pdf_never_requests_a_model(monkeypatch):
    import main
    task_id = "all-blank-" + uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    monkeypatch.setattr(main, "ocr_pdf_page_image", lambda *_a, **_k: pytest.fail("Blank page cannot cost an OCR call"))
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: pytest.fail("Blank PDF has no questions"))
    try:
        main.run_pdf_parsing_task(task_id, pdf_bytes(count=1, blank_indices=(0,)), "blank.pdf", pdf_strategy="force_ocr")
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "error" and "可解析" in task["error"]
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@experimental_pdf_only
def test_resume_reuses_successful_page_and_retries_only_failed_page(monkeypatch):
    import main
    task_id = "resume-pages-" + uuid4().hex
    raw = pdf_bytes()
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    calls = [0, 0]
    def ocr(path, **_kwargs):
        page = int(Path(path).stem.rsplit("_", 1)[1])
        calls[page] += 1
        if page == 1 and calls[page] == 1:
            raise ValueError("Injected page failure")
        return f"{page + 1}. 第{page + 1}题 $x={page + 1}$。"
    monkeypatch.setattr(main, "ocr_pdf_page_image", ocr)
    split_calls = []
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: split_calls.append(True) or [
        {"content": "第1题 $x=1$。", "answer_markdown": ""},
        {"content": "第2题 $x=2$。", "answer_markdown": ""}])
    try:
        main.run_pdf_parsing_task(task_id, raw, "resume.pdf", pdf_strategy="force_ocr")
        failed = main.DOCUMENT_TASKS.snapshot(task_id)
        assert failed["status"] == "error" and len(failed["pdf_resume_state"]["pages"]) == 1
        checkpoint = json.loads(json.dumps(failed["pdf_resume_state"]))
        reset_task(main, task_id)
        main.run_pdf_parsing_task(task_id, raw, "resume.pdf", pdf_strategy="force_ocr", resume_state=checkpoint)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        assert calls == [1, 2] and split_calls == [True]
        assert task["diagnostics"]["pdf_resume"]["reused_visual_pages"] == 1
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


@experimental_pdf_only
def test_resume_reuses_accepted_split_after_postprocessing_failure(monkeypatch):
    import main
    task_id = "resume-split-" + uuid4().hex
    raw = pdf_bytes(count=1)
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    monkeypatch.setattr(main, "inspect_and_extract_pdf", lambda *_a, **_k: {"pages": [
        {"page_index": 0, "markdown": "1. 计算 $1+1$。", "needs_ocr": False}]})
    split_calls = []
    monkeypatch.setattr(main, "parse_paper_text_internal", lambda *_a, **_k: split_calls.append(True) or [
        {"content": "计算 $1+1$。", "answer_markdown": ""}])
    original = main.post_process_pdf_parsed_questions
    monkeypatch.setattr(main, "post_process_pdf_parsed_questions", lambda *_a, **_k: (_ for _ in ()).throw(
        ValueError("Injected local postprocess failure")))
    try:
        main.run_pdf_parsing_task(task_id, raw, "resume.pdf", pdf_strategy="layout_aware", pdf_verify_suspicions=False)
        failed = main.DOCUMENT_TASKS.snapshot(task_id)
        assert failed["status"] == "error" and failed["pdf_resume_state"]["accepted_split"]["questions"]
        checkpoint = json.loads(json.dumps(failed["pdf_resume_state"]))
        reset_task(main, task_id)
        monkeypatch.setattr(main, "post_process_pdf_parsed_questions", original)
        main.run_pdf_parsing_task(task_id, raw, "resume.pdf", pdf_strategy="layout_aware",
                                 pdf_verify_suspicions=False, resume_state=checkpoint)
        task = main.DOCUMENT_TASKS.snapshot(task_id)
        assert task["status"] == "completed", task.get("error")
        assert split_calls == [True]
        assert task["diagnostics"]["pdf_resume"]["reused_split"] is True
        assert task["diagnostics"]["pdf_resume"]["reused_layout"] is True
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_resume_rejects_changed_file_options_and_asset(tmp_path):
    path = tmp_path / "page.png"
    path.write_bytes(b"original")
    arguments = dict(task_id="same-task", document_sha256="a" * 64, page_indices=[0],
                     strategy="force_ocr", verify_suspicions=True, resolve_asset=lambda _url: path)
    state = prepare_pdf_checkpoint(None, **arguments)
    snapshot = seal_pdf_checkpoint(state, assets=["original-page"], resolve_asset=arguments["resolve_asset"])
    assert prepare_pdf_checkpoint(snapshot, **arguments)["run"] == 1
    for changed in ({"document_sha256": "b" * 64}, {"page_indices": [1]},
                    {"strategy": "layout_aware"}, {"task_id": "other-task"}):
        with pytest.raises(ValueError, match="不一致"):
            prepare_pdf_checkpoint(snapshot, **{**arguments, **changed})
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="变化"):
        prepare_pdf_checkpoint(snapshot, **arguments)


def test_cancellation_stops_pdf_collector_while_http_worker_is_blocked(monkeypatch):
    import main
    task_id = "cancel-collector-" + uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])
    entered, release = threading.Event(), threading.Event()
    def ocr(*_a, **_k):
        entered.set()
        assert release.wait(10)
        return "1. Late response"
    monkeypatch.setattr(main, "ocr_pdf_page_image", ocr)
    worker = threading.Thread(target=main.run_pdf_parsing_task,
        args=(task_id, pdf_bytes(count=1), "cancel.pdf"), kwargs={"pdf_strategy": "force_ocr"})
    worker.start()
    try:
        assert entered.wait(5)
        main.DOCUMENT_TASKS.cancel(task_id)
        worker.join(2)
        assert not worker.is_alive(), "Collector must check cancellation before HTTP returns"
        assert main.DOCUMENT_TASKS.snapshot(task_id)["status"] == "cancelled"
    finally:
        release.set()
        worker.join(5)
        main.DOCUMENT_TASKS.remove(task_id)
