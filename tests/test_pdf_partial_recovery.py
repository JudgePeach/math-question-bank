"""Cloud-only PDF partial delivery: source-only copies, no data/credentials/API."""
from copy import deepcopy
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from mathbank.pdf_partial import complete_boundary_drafts, merge_partial_drafts, merge_recovered_questions


PAGE = "1. 第一题的内容，计算$1+1$。\n2. 已知函数$f(x)=x^2$，求$f(2)$。\n3. 第三题起点，计算$3+3$。"


def drafts(text=PAGE, page=1):
    return complete_boundary_drafts([{"page_number": page, "markdown": text}],
        source_sha256="a" * 64, paper_title="隔离测试")


def test_only_closed_interior_region_keeps_exact_source_and_pending_metadata():
    cards, isolated = drafts()
    assert len(cards) == 1
    card = cards[0]
    assert card["content"] == PAGE[PAGE.index("2."):PAGE.index("3.")]
    assert card["answer_markdown"] == "" and card["knowledge_points"] == []
    assert card["question_type"] == "" and card["difficulty"] is None
    assert card["source_review"]["required"] is True
    assert card["source_review"]["blocking"] is False
    assert card["source_review"]["disposition"] == "advisory"
    assert card["source_review"].get("verified_by") != "vision"
    assert len(isolated) == 2
    assert merge_partial_drafts(cards, cards) == cards


@pytest.mark.parametrize("replacement", [
    "2. 已知$x=1，求$x$。\n",  # unmatched math
    "2. 求值。\\begin{choices}\\item 甲\\item 乙\n",  # open choices
    "2. 求值。\\begin{tabular}{cc}a&b\\\\\n",  # open table
    "2. 依据上题求$f(2)$。\n",
    "2. 如图，求$f(2)$。\n",
    "2. [插图待补: 图1]，求值。\n",
])
def test_ambiguous_math_choices_tables_references_and_figures_are_isolated(replacement):
    text = PAGE[:PAGE.index("2.")] + replacement + PAGE[PAGE.index("3."):]
    cards, isolated = drafts(text)
    assert cards == [] and isolated


def test_never_join_across_failed_or_unselected_pages_and_skip_first_last_fragments():
    pages = [{"page_number": 1, "markdown": "1. 完整起点。\n2. 只有开头$"},
             {"page_number": 2, "markdown": ""},
             {"page_number": 3, "markdown": "x$的续文。\n3. 新题开头。"}]
    cards, isolated = complete_boundary_drafts(pages, source_sha256="b" * 64, paper_title="测试")
    assert cards == []
    assert any(row["pages"] == [2] and row["reason"] == "page_not_transcribed" for row in isolated)


def test_shared_material_and_nonconsecutive_numbers_are_isolated():
    assert drafts("以下题目共用条件。\n" + PAGE)[0] == []
    assert drafts(PAGE.replace("3.", "4."))[0] == []


def test_original_answer_is_preserved_and_not_guessed():
    text = PAGE.replace("\n3.", "\n【答案】$4$\n3.")
    card = drafts(text)[0][0]
    assert card["answer_markdown"] == "\n【答案】$4$\n"
    assert card["content"] + card["answer_markdown"] == text[text.index("2."):text.index("3.")]


def test_recovery_keeps_delivered_cards_and_only_adds_unique_source_identities():
    old = drafts()[0]
    incoming = [{"content": old[0]["content"], "answer_markdown": "模型不能覆盖旧原文"},
                {"content": "第三题起点，计算$3+3$。", "answer_markdown": ""}]
    result, isolated = merge_recovered_questions(old, incoming, PAGE, source_sha256="a" * 64)
    assert result[0] == old[0] and len(result) == 2
    assert len({q["pdf_source_card_id"] for q in result}) == 2
    assert isolated == []


def test_repeated_question_numbers_cannot_silently_absorb_a_new_section():
    old = drafts()[0]
    full = PAGE + "\n二、另一节\n2. 另一道不同题，求$9+9$。\n"
    incoming = [{"content": "另一道不同题，求$9+9$。", "answer_markdown": ""}]
    result, isolated = merge_recovered_questions(old, incoming, full, source_sha256="a" * 64)
    assert result == old
    assert isolated and isolated[0]["reason"] in {
        "recovered_source_number_not_unique", "recovered_card_identity_uncertain"}


SCENARIO = r'''
import json, time, threading, uuid
from copy import deepcopy
from pathlib import Path
import requests
requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError("No live API"))
import pymupdf as fitz
from fastapi.testclient import TestClient
import main
from mathbank.database import SessionLocal, Question, engine
from mathbank.document_task_store import DocumentTaskStore, DurableDocumentTasks

assert not Path('.env').exists()
assert Path(engine.url.database).resolve().parent == Path.cwd()
assert main.PDF_CLOUD_ONLY is True
main.DOCUMENT_TASKS.shutdown()
store = DocumentTaskStore(Path('.system_generated/tasks'))
main.DOCUMENT_TASK_STORE = store
main.DOCUMENT_TASKS = DurableDocumentTasks(store=store, transient_document_types={'pdf'}, max_workers=1, max_queue=1)
main.DOCUMENT_TASK_INPUTS = {}
main.schedule_database_export = lambda *a, **k: None
main.get_current_curriculum = lambda: {'必修一': {'函数': ['函数值']}}
source = {
    0: '1. 首题，计算$1+1$。\n2. 已知函数$f(x)=x^2$，求$f(2)$。\n3. 跨页题起点，求值。',
    1: '跨页题续文，补充条件。\n4. 计算$4+4$。\n5. 计算$5+5$。',
    2: '6. 第一题，计算$6+6$。\n7. 已知$y=7$，求$y+1$。\n8. 末题，计算$8+8$。',
}
ocr_calls, split_calls = [], []
block_page = False
entered = threading.Event()
release = threading.Event()
split_unknown = False
failed = {1}
def ocr(path, **kwargs):
    index = kwargs['page_index']; ocr_calls.append(index)
    if block_page and index == 1:
        entered.set()
        assert release.wait(3), 'Mock OCR barrier was not released'
    if index in failed:
        raise RuntimeError('mock page read result unknown')
    return source[index]
main.ocr_pdf_page_image = ocr
def split(text, *args, **kwargs):
    split_calls.append(text)
    if split_unknown:
        raise RuntimeError('mock split read result unknown')
    assert all(f'MATHBANK_PDF_PAGE:{n}' in text for n in [1, 2, 3])
    return [{'content': '首题，计算$1+1$。', 'answer_markdown': ''},
            {'content': '已知函数$f(x)=x^2$，求$f(2)$。', 'answer_markdown': ''},
            {'content': '跨页题起点，求值。\n跨页题续文，补充条件。', 'answer_markdown': ''},
            {'content': '计算$4+4$。', 'answer_markdown': ''},
            {'content': '计算$5+5$。', 'answer_markdown': ''},
            {'content': '第一题，计算$6+6$。', 'answer_markdown': ''},
            {'content': '已知$y=7$，求$y+1$。', 'answer_markdown': ''},
            {'content': '末题，计算$8+8$。', 'answer_markdown': ''}]
main.parse_paper_text_internal = split
main.post_process_pdf_parsed_questions = lambda qs, *a, **k: qs
with fitz.open() as pdf:
    for index in range(3):
        page = pdf.new_page(width=300, height=400)
        page.insert_text((30, 40), f'Fixture page {index + 1}')
    raw = pdf.tobytes()

def settle(client, task_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get(f'/api/tasks/{task_id}/status').json()
        with main.DOCUMENT_TASKS._lock:
            future = main.DOCUMENT_TASKS._records[task_id].future
        if state['status'] in {'completed', 'error', 'cancelled'} and future is None:
            return state
        time.sleep(.01)
    raise AssertionError('Task did not settle')

with TestClient(main.app) as client:
    client.headers.update({'X-Local-Token': main.LOCAL_TOKEN})
    reply = client.post('/api/upload/pdf-task', files={'file': ('partial.pdf', raw, 'application/pdf')},
        data={'client_request_id': str(uuid.uuid4()), 'pdf_strategy': 'force_ocr',
              'pdf_verify_suspicions': 'false', 'paper_title': 'mock-partial'})
    assert reply.status_code == 200, reply.text
    task_id = reply.json()['task_id']
    first = settle(client, task_id)
    assert first['status'] == 'completed' and first['partial'] and first['can_resume'], first
    assert sorted(ocr_calls) == [0, 1, 2] and split_calls == []
    assert len(first['data']) == 2, first
    assert [q['pdf_source_scope']['source_number'] for q in first['data']] == [2, 7]
    assert first['diagnostics']['pdf_partial_checkpoint']['failed_pages'] == [2]
    assert 'pdf_resume_state' not in first and 'document_input' not in first
    old_cards = deepcopy(first['data'])
    # A second failure requests only missing page; no duplicate cards/split.
    block_page = True
    assert client.post(f'/api/tasks/{task_id}/resume').status_code == 200
    assert entered.wait(3)
    assert client.post(f'/api/tasks/{task_id}/resume').status_code == 409
    release.set()
    repeated = settle(client, task_id)
    block_page = False
    assert repeated['data'] == old_cards and ocr_calls.count(1) == 2
    assert ocr_calls.count(0) == ocr_calls.count(2) == 1 and split_calls == []
    # Explicit retry can recover the gap, then the original splitter sees all
    # pages exactly once. Delivered machine cards remain byte-for-byte stable.
    failed.clear()
    assert client.post(f'/api/tasks/{task_id}/resume').status_code == 200
    final = settle(client, task_id)
    assert final['status'] == 'completed', final
    assert final['can_resume'] is False
    assert final['diagnostics']['pdf_partial_checkpoint']['failed_pages'] == []
    assert final['data'][:2] == old_cards
    assert len({q['pdf_source_card_id'] for q in final['data']}) == len(final['data'])
    assert len(split_calls) == 1 and ocr_calls.count(1) == 3
    assert ocr_calls.count(0) == ocr_calls.count(2) == 1
    assert client.post(f'/api/tasks/{task_id}/resume').status_code == 409
    assert len(final['data']) == 8, final
    # Cancelled recovery must not publish fresh cards or call the splitter.
    failed.add(1)
    another = client.post('/api/upload/pdf-task', files={'file': ('cancel.pdf', raw, 'application/pdf')},
        data={'client_request_id': str(uuid.uuid4()), 'pdf_strategy': 'force_ocr', 'pdf_verify_suspicions': 'false'})
    cancel_id = another.json()['task_id']
    cancel_first = settle(client, cancel_id)
    failed.clear(); block_page = True; entered.clear(); release.clear()
    assert client.post(f'/api/tasks/{cancel_id}/resume').status_code == 200
    assert entered.wait(3)
    assert client.post(f'/api/tasks/{cancel_id}/cancel').status_code == 200
    release.set()
    cancelled = settle(client, cancel_id)
    assert cancelled['status'] == 'cancelled' and not cancelled['can_resume']
    assert cancelled['data'] == cancel_first['data'] and len(split_calls) == 1
    # A split read outcome is unknown: the page receipt cannot authorize an
    # automatic or same-task repeat of the paid split request.
    block_page = False; failed.add(1)
    last = client.post('/api/upload/pdf-task', files={'file': ('unknown.pdf', raw, 'application/pdf')},
        data={'client_request_id': str(uuid.uuid4()), 'pdf_strategy': 'force_ocr', 'pdf_verify_suspicions': 'false'})
    unknown_id = last.json()['task_id']
    unknown_first = settle(client, unknown_id)
    failed.clear(); split_unknown = True
    assert client.post(f'/api/tasks/{unknown_id}/resume').status_code == 200
    unknown = settle(client, unknown_id)
    assert unknown['status'] == 'error' and not unknown['can_resume'], unknown
    assert unknown['data'] == unknown_first['data']
    assert client.post(f'/api/tasks/{unknown_id}/resume').status_code == 409
    assert len(split_calls) == 2
    assert list(store.root.iterdir()) == [], 'cloud PDF must remain process-local'
    with SessionLocal() as db:
        assert db.query(Question).count() == 0
    print('PDF_PARTIAL_EVIDENCE=' + json.dumps({'partial_cards':len(old_cards),
        'final_cards':len(final['data']), 'ocr_calls':ocr_calls, 'split_calls':1,
        'cancelled_resume':True, 'unknown_split_not_repeated':True}))
main.DOCUMENT_TASKS.shutdown()
'''


def test_cloud_partial_failure_explicit_retry_reuses_success_and_preserves_card_identity(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "source-only-pdf-partial"
    workspace.mkdir()
    shutil.copy2(root / "main.py", workspace / "main.py")
    shutil.copytree(root / "mathbank", workspace / "mathbank", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (root / "templates").is_dir():
        shutil.copytree(root / "templates", workspace / "templates")
    (workspace / "static").mkdir()
    runner = workspace / "isolated_pdf_partial.py"
    runner.write_text(SCENARIO, encoding="utf-8")
    # Strip inherited provider secrets as well as never copying the source .env.
    environment = {key: value for key, value in os.environ.items()
                   if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET"))}
    environment["PYTHONPATH"] = str(workspace)
    result = subprocess.run([sys.executable, "-B", str(runner)], cwd=workspace, env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout[-7000:] + "\n" + result.stderr[-7000:]
    assert '"partial_cards": 2' in result.stdout and '"split_calls": 1' in result.stdout
