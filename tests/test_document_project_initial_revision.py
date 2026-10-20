"""A first machine working draft is distinct from a competing teacher save."""
from copy import deepcopy
import hashlib
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from mathbank.document_project_api import register_document_project_routes
from mathbank.document_project_service import DocumentProjectService
from mathbank.document_projects import DocumentProjectStore
from test_document_projects_frontend import project_script, run_node  # noqa: F401
from test_import_source_review import review_script  # noqa: F401
from test_pdf_layout_frontend import pdf_script  # noqa: F401


SCHEMA = "mathbank.document-project-initial-revision.v1"
STAGE = "initial-machine-draft-revision"


class Tasks:
    def __init__(self):
        self._lock = threading.RLock()
        self._records = {}

    def snapshot(self, _task_id):
        return None


@pytest.fixture
def initialized(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    pid = store.create(b"%PDF-isolated", "fixture.pdf", {}, {})["project_id"]
    service = DocumentProjectService(store, Tasks())
    rows = [{"source_number": 1, "content": "first body", "answer_markdown": "original answer"}]
    snapshot = {"project_id": pid, "status": "completed", "document_type": "pdf", "data": rows,
        "diagnostics": {"pdf_local_draft": {"assembly": "first_pass_blocks"}}}
    service.sync("first-machine-task", snapshot)
    return service, pid, snapshot


def test_initial_machine_revision_has_an_exact_current_whole_draft_receipt(initialized):
    service, pid, snapshot = initialized
    saved = service.store.get_draft(pid)
    receipt = service.validated_initial_revision_receipt(pid)
    assert saved["revision"] == 1
    assert receipt == {"schema": SCHEMA, "origin": "first_machine_draft", "project_id": pid,
        "task_id": "first-machine-task", "base_revision": 0, "revision": 1,
        "draft_sha256": hashlib.sha256(json.dumps(saved["payload"], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    assert service.detail(pid)["project_revision_receipt"] == receipt
    service.sync("first-machine-task", snapshot)
    assert service.validated_initial_revision_receipt(pid) == receipt
    assert service.store.get(pid)["revision"] == 1


@pytest.mark.parametrize("change", [False, True])
def test_any_teacher_save_invalidates_the_initial_receipt_and_old_cas_still_conflicts(initialized, change):
    service, pid, _ = initialized
    app = FastAPI()
    register_document_project_routes(app, get_service=lambda: service,
        dispatch_project=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("dispatch forbidden")),
        release_assets=lambda _: None)
    payload = service.store.get_draft(pid)["payload"]
    if change:
        payload["questions"][0]["answer_markdown"] = "teacher answer at exact image location"
    with TestClient(app) as client:
        saved = client.put(f"/api/document-projects/{pid}/draft", json={"expected_revision": 1, "draft": payload})
        assert saved.status_code == 200 and saved.json()["revision"] == 2
        assert service.validated_initial_revision_receipt(pid) is None
        assert "project_revision_receipt" not in client.get(f"/api/document-projects/{pid}").json()
        stale = client.put(f"/api/document-projects/{pid}/draft", json={"expected_revision": 1, "draft": payload})
        assert stale.status_code == 409 and stale.json()["current_revision"] == 2
        assert service.store.get_draft(pid)["payload"] == saved.json()["draft"]


@pytest.mark.parametrize("key,value", [
    ("schema", "other-schema"), ("origin", "teacher"), ("project_id", "wrong-project"),
    ("task_id", "other-task"), ("base_revision", True), ("revision", True),
    ("base_revision", 1), ("revision", 2), ("draft_sha256", "0" * 64), ("extra", "not allowed"),
])
def test_initial_receipt_with_mismatched_binding_is_not_authority(initialized, key, value):
    service, pid, _ = initialized
    receipt = service.validated_initial_revision_receipt(pid)
    receipt[key] = value
    service.store.save_stage(pid, STAGE, receipt, SCHEMA + ":" + pid)
    assert service.validated_initial_revision_receipt(pid) is None


def test_receipt_read_racing_a_teacher_save_is_not_authority(initialized, monkeypatch):
    service, pid, _ = initialized
    original = service.store.load_stage
    def racing_load(*args, **kwargs):
        result = original(*args, **kwargs)
        saved = service.store.get_draft(pid)
        payload = deepcopy(saved["payload"])
        payload["questions"][0]["content"] = "teacher wins after receipt load"
        service.save_draft(pid, payload, saved["revision"])
        return result
    monkeypatch.setattr(service.store, "load_stage", racing_load)
    assert service.validated_initial_revision_receipt(pid) is None
    assert service.store.get_draft(pid)["payload"]["questions"][0]["content"] == "teacher wins after receipt load"


def test_rebinding_a_different_task_invalidates_the_initial_receipt(initialized):
    service, pid, _ = initialized
    service.store.bind_task(pid, "different-task")
    assert service.validated_initial_revision_receipt(pid) is None


def test_receipt_file_must_pass_the_manifest_hash_check(initialized):
    service, pid, _ = initialized
    directory = service.store.root / pid
    manifest = json.loads((directory / "manifest.json").read_text())
    (directory / manifest["stages"][STAGE]["file"]).write_text("{}")
    assert service.validated_initial_revision_receipt(pid) is None


def test_teacher_first_revision_is_never_certified_as_machine_initialization(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    pid = store.create(b"%PDF-isolated", "fixture.pdf", {}, {})["project_id"]
    service = DocumentProjectService(store, Tasks())
    payload = {"questions": [{"content": "teacher first edit", "answer_markdown": "manual answer"}]}
    service.save_draft(pid, payload, 0)
    service.sync("machine-task", {"project_id": pid, "status": "completed", "document_type": "pdf",
        "data": [{"content": "machine body"}], "diagnostics": {"pdf_local_draft": {"assembly": "first_pass_blocks"}}})
    assert service.validated_initial_revision_receipt(pid) is None
    assert service.store.get_draft(pid)["payload"]["questions"][0]["content"] == "teacher first edit"


INITIAL_JS = r'''
function initialReceipt(changes={}){return {schema:'mathbank.document-project-initial-revision.v1',
 origin:'first_machine_draft',project_id:'initial-project',task_id:'initial-task',base_revision:0,revision:1,
 draft_sha256:'a'.repeat(64),...changes};}
function beginInitial(){session.begin({projectId:'initial-project',projectRevision:0,taskId:'initial-task',phase:'running'});
 const rows=[question('machine first body')];replaceParsedQuestions(rows,{saveDraft:false});renderParsedQuestionsList(rows);return rows;}
function terminal(rows,receipt){return {project_id:'initial-project',project_revision:1,task_id:'initial-task',
 document_type:'pdf',status:'completed',data:rows,project_revision_receipt:receipt,
 diagnostics:{pdf_local_draft:{assembly:'first_pass_blocks'}}};}
function finishProof(){session.clear({discardDeletedProject:true});stopCurrentDocumentPoll();}
'''


def test_frontend_initial_receipt_keeps_teacher_text_images_metadata_and_uses_revision_one(project_script):
    run_node(project_script + INITIAL_JS + r'''
async function proof(){
 const rows=beginInitial();
 const manual={...rows[0],content:'teacher body',answer_markdown:'before\n![answer diagram](/static/uploads/tmp/manual.png)\nafter',
   difficulty:'challenge',source:'teacher source'};
 replaceParsedQuestions([manual],{saveDraft:false});renderParsedQuestionsList([manual]);
 document.getElementById('parsed-card-0').querySelector('.card-difficulty').value='challenge';
 session.completed(terminal(rows,initialReceipt()),[manual]);session.flush();
 assert.equal(stored().projectRevision,1);assert.equal(stored().questions[0].content,'teacher body');
 assert.equal(stored().questions[0].answer_markdown,manual.answer_markdown);
 assert.equal(stored().questions[0].source,'teacher source');
 assert.equal(stored().questions[0].difficulty,'challenge');
 const saving=session.saveServerNow();await flush();
 const body=JSON.parse(requests[0].options.body);assert.equal(body.expected_revision,1);
 assert.equal(body.draft.questions[0].answer_markdown,manual.answer_markdown);
 assert.ok(!('projectRevisionReceipt' in body.draft),'server receipt is runtime metadata, not teacher content');
 next('/api/document-projects/initial-project/draft',{revision:2});assert.equal(await saving,true);
 assert.equal(stored().projectRevision,2);assert.ok(!stored().projectRevisionReceipt);
 finishProof();
}
proof().catch(error=>{console.error(error);process.exitCode=1;});
''')


@pytest.mark.parametrize("matching_task", [False, True])
def test_reopen_only_adopts_first_machine_receipt_for_the_unsaved_copy_of_that_task(project_script, matching_task):
    run_node(project_script + INITIAL_JS + r'''
async function proof(){
 const local={version:1,projectId:'initial-project',projectRevision:0,taskId:MATCHING?'initial-task':'old-task',
   projectLocalDirty:true,projectSaveStatus:'conflict',questions:[question('unsaved teacher answer crop')],
   baselineQuestions:[question('machine first body')],paperTitle:'retained teacher title'};
 local.questions[0].answer_markdown='before\n![answer diagram](/static/uploads/tmp/manual.png)\nafter';
 storage.set('mathbank_document_import_session_v1:initial-project',JSON.stringify(local));
 const data=details('initial-project',1,{questions:[question('machine first body')],baselineQuestions:[question('machine first body')]});
 data.task_id='initial-task';data.task={task_id:'initial-task',status:'completed'};
 data.project_revision_receipt=initialReceipt();
 const card=await open(data);
 assert.equal(card.querySelector('.card-content-textarea').value,'unsaved teacher answer crop');
 assert.equal(stored().questions[0].answer_markdown,local.questions[0].answer_markdown);
 assert.equal(stored().projectRevision,MATCHING?1:0);
 assert.equal(stored().projectSaveStatus,MATCHING?'local':'conflict');
 assert.equal(requests.length,0,'opening cannot perform a blind overwrite or start a model');finishProof();
}
proof().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("MATCHING", json.dumps(matching_task)))


@pytest.mark.parametrize("changes", [None, {"project_id": "other-project"}, {"task_id": "other-task"},
    {"base_revision": 1}, {"revision": 2}, {"draft_sha256": "invalid"}, {"origin": "teacher"}])
def test_frontend_does_not_adopt_unproved_remote_or_409_revisions(project_script, changes):
    receipt = "undefined" if changes is None else "initialReceipt(" + json.dumps(changes) + ")"
    run_node(project_script + INITIAL_JS + r'''
async function proof(){
 const rows=beginInitial();session.completed(terminal(rows,RECEIPT),rows);session.flush();
 assert.equal(stored().projectRevision,0);
 const saving=session.saveServerNow();await flush();assert.equal(JSON.parse(requests[0].options.body).expected_revision,0);
 next('/api/document-projects/initial-project/draft',{detail:'teacher saved elsewhere',current_revision:9},409);
 assert.equal(await saving,false);assert.equal(stored().projectSaveStatus,'conflict');
 assert.equal(stored().projectRevision,0);await new Promise(resolve=>setTimeout(resolve,550));
 assert.equal(requests.length,0,'unproved current_revision cannot trigger overwrite retry');finishProof();
}
proof().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("RECEIPT", receipt))


@pytest.mark.parametrize("competing_teacher", [False, True])
def test_receipt_arriving_during_stale_put_retries_once_without_bypassing_real_conflict(project_script, competing_teacher):
    run_node(project_script + INITIAL_JS + r'''
async function proof(){
 const rows=beginInitial();session.saveSoon();session.flush();const stale=session.saveServerNow();await flush();
 assert.equal(JSON.parse(requests[0].options.body).expected_revision,0);
 session.update({projectId:'initial-project',taskId:'initial-task',projectRevision:1,projectRevisionReceipt:initialReceipt()});
 next('/api/document-projects/initial-project/draft',{detail:'stale initial revision',current_revision:1},409);
 await stale;assert.equal(stored().projectRevision,1);assert.notEqual(stored().projectSaveStatus,'conflict');
 await new Promise(resolve=>setTimeout(resolve,550));assert.equal(requests.length,1);
 assert.equal(JSON.parse(requests[0].options.body).expected_revision,1);
 if(COMPETING){next('/api/document-projects/initial-project/draft',{detail:'real other window revision',current_revision:2},409);}
 else{next('/api/document-projects/initial-project/draft',{revision:2});}
 await flush();assert.equal(stored().projectSaveStatus,COMPETING?'conflict':'saved');
 assert.equal(stored().questions[0].content,'machine first body');
 await new Promise(resolve=>setTimeout(resolve,550));assert.equal(requests.length,0,'receipt retry must be bounded');finishProof();
}
proof().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("COMPETING", json.dumps(competing_teacher)))
