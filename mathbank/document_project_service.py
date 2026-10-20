"""Connect seven-day projects to bounded runtime jobs without owning AI calls."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter, defaultdict
import hashlib
import json
import re
import time
import uuid
from functools import wraps

from mathbank.document_projects import DocumentProjectError, ProjectConflict, ProjectNotFound

_CARD_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_INITIAL_REVISION_STAGE = "initial-machine-draft-revision"
_INITIAL_REVISION_SCHEMA = "mathbank.document-project-initial-revision.v1"


def _draft_sha256(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def project_question_ids(project_id, questions, *, previous_questions=()):
    """Keep receipts stable without equating two partial results by position.

    A previous *baseline*, never the teacher's edited text, may establish a
    unique source-number or exact source-content correspondence. Unknown or
    ambiguous rows get IDs scoped to this result, rather than ``question:0``.
    """
    rows = deepcopy(questions)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DocumentProjectError("工程题目格式无效。")
    namespace = uuid.UUID(project_id)

    def valid_id(row):
        value = row.get("project_card_id")
        return (value if isinstance(value, str) and _CARD_ID.fullmatch(value)
                and row.get("import_project_id", project_id) == project_id else None)

    def number(row):
        value = row.get("source_number")
        return value if type(value) is int and value > 0 else None

    def content_key(row, *, scope_only=False):
        # Classification, warnings, selections and generated review metadata
        # are not source identity. Empty cards provide no matching evidence.
        if not scope_only and (not isinstance(row.get("content"), str) or not row["content"].strip()):
            return None
        value = [row.get("content"), row.get("answer_markdown") or "", row.get("image_paths") or []]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()

    existing = [valid_id(row) for row in rows]
    if any(count > 1 for value, count in Counter(existing).items() if value):
        raise DocumentProjectError("工程题卡身份重复，请保留校对稿并重新核对。")
    numbers = Counter(number(row) for row in rows if number(row) is not None)
    keys = [content_key(row) for row in rows]
    key_counts = Counter(keys)
    prior_numbers, prior_keys, id_numbers = defaultdict(set), defaultdict(set), defaultdict(set)
    for row in previous_questions if isinstance(previous_questions, (list, tuple)) else ():
        if not isinstance(row, dict) or not (card_id := valid_id(row)):
            continue
        if (value := number(row)) is not None:
            prior_numbers[value].add(card_id)
            id_numbers[card_id].add(value)
        if (key := content_key(row)) is not None:
            prior_keys[key].add(card_id)
    result_scope = hashlib.sha256(json.dumps([[number(row), content_key(row, scope_only=True)] for row in rows],
        separators=(",", ":")).encode()).hexdigest()
    used = set(value for value in existing if value)

    def unique(candidates):
        return next(iter(candidates)) if len(candidates) == 1 and not candidates & used else None

    for index, row in enumerate(rows):
        value, key = number(row), keys[index]
        card_id = existing[index]
        if not card_id and value is not None and numbers[value] == 1:
            card_id = unique({candidate for candidate in prior_numbers[value]
                              if id_numbers[candidate] == {value}})
        if not card_id and key is not None and key_counts[key] == 1:
            card_id = unique({candidate for candidate in prior_keys[key]
                              if value is None or not id_numbers[candidate] or id_numbers[candidate] == {value}})
        if not card_id:
            identity = (f"number:{value}" if value is not None and numbers[value] == 1
                        else f"result:{result_scope}:{index}:{key}")
            card_id = "card-" + uuid.uuid5(namespace, identity).hex
            if card_id in used:
                card_id = "card-" + uuid.uuid5(namespace, f"result:{result_scope}:{index}:{key}").hex
            if card_id in used:
                raise DocumentProjectError("工程题卡身份冲突，请保留校对稿并重新核对。")
        row["project_card_id"] = card_id
        row["import_project_id"] = project_id
        used.add(card_id)
    return rows


def project_task_guard(tasks_getter, store_getter):
    def decorate(function):
        @wraps(function)
        def run(task_id, *args, **kwargs):
            snapshot = tasks_getter().snapshot(task_id) or {}
            store = store_getter()
            if not store or not snapshot.get("project_id"):
                return function(task_id, *args, **kwargs)
            with store.operation(snapshot["project_id"]):
                return function(task_id, *args, **kwargs)
        return run
    return decorate



def project_dispatch_guard(tasks_getter, store_getter):
    """Make the active-check/bind/submit sequence atomic across HTTP requests."""
    def decorate(function):
        @wraps(function)
        def run(project_id, *args, **kwargs):
            store = store_getter()
            if store is None:
                return function(project_id, *args, **kwargs)
            with tasks_getter()._request_lock:
                with store.operation(project_id):
                    return function(project_id, *args, **kwargs)
        return run
    return decorate


def project_draft_from_task(project_id, task):
    return {"questions": project_question_ids(project_id, task.get("data") or []),
            "baselineQuestions": project_question_ids(project_id, task.get("data") or []),
            "paperTitle": ((task.get("document_input") or {}).get("options") or {}).get("paper_title") or "",
            "pageImages": list(task.get("page_images") or []),
            "pageNumbers": list(task.get("page_numbers") or []),
            "diagnostics": deepcopy(task.get("diagnostics") or {}),
            "page_edits": [], "taskId": task.get("task_id"), "documentType": "pdf"}


class DocumentProjectService:
    def __init__(self, store, tasks, *, source_reader=None, asset_resolver=None):
        self.store, self.tasks = store, tasks
        self.source_reader, self.asset_resolver = source_reader, asset_resolver

    def active_task_ids(self):
        with self.tasks._lock:
            # A terminal status is published before the worker's final project
            # writes/lease release. Protect that flush just like a running task.
            active = set()
            for key, record in self.tasks._records.items():
                future = record.future
                if future is None:
                    continue
                done = getattr(future, "done", None)
                if not callable(done) or not done():
                    active.add(key)
            return active

    def metadata(self, metadata):
        result = dict(metadata)
        result["last_used_at"] = result.get("last_used_at", result.get("last_used"))
        result["updated_at"] = result["last_used_at"]
        # IO is bounded to the one project's asset headers, never file contents.
        extra = 0
        if self.asset_resolver:
            try:
                for url in self.store.asset_urls(result["project_id"]):
                    try:
                        path = self.asset_resolver(url)
                        if path.is_file() and not path.is_symlink():
                            extra += path.stat().st_size
                    except (OSError, ValueError):
                        pass
            except DocumentProjectError:
                pass
        result["total_bytes"] = int(result.get("total_bytes") or 0) + extra
        result["retention_days"] = 7
        return result

    def question_ids(self, project_id, questions, *, draft=None):
        """Reconcile with saved machine baselines, without reading edited text."""
        original = self.store.get_baseline(project_id) or {}
        if draft is None:
            draft = self.store.get_draft(project_id).get("payload") or {}
        previous = list(original.get("questions") or [])
        if isinstance(draft.get("baselineQuestions"), list):
            previous.extend(draft["baselineQuestions"])
        return project_question_ids(project_id, questions, previous_questions=previous)

    def validated_initial_revision_receipt(self, project_id):
        """Prove the only automatic 0-to-1 draft write, never a teacher revision.

        The stage itself is verified by the store's manifest hash. Its payload
        must still describe the current whole draft and bound task. Any ordinary
        save advances the monotonically increasing revision, invalidating this
        receipt even if the teacher happens to save identical text.
        """
        try:
            metadata = self.store.get(project_id)
            if metadata.get("revision") != 1 or not metadata.get("has_draft"):
                return None
            saved = self.store.get_draft(project_id)
            if saved["revision"] != 1 or not saved["metadata"].get("has_draft"):
                return None
            receipt = self.store.load_stage(project_id, _INITIAL_REVISION_STAGE,
                _INITIAL_REVISION_SCHEMA + ":" + project_id)
            if (not isinstance(receipt, dict)
                    or set(receipt) != {"schema", "origin", "project_id", "task_id",
                                       "base_revision", "revision", "draft_sha256"}
                    or receipt.get("schema") != _INITIAL_REVISION_SCHEMA
                    or receipt.get("origin") != "first_machine_draft"
                    or receipt.get("project_id") != project_id
                    or type(receipt.get("base_revision")) is not int or receipt["base_revision"] != 0
                    or type(receipt.get("revision")) is not int or receipt["revision"] != 1
                    or not isinstance(receipt.get("task_id"), str)
                    or not _CARD_ID.fullmatch(receipt["task_id"])
                    or receipt["task_id"] != saved["metadata"].get("task_id")
                    or receipt["task_id"] != saved["payload"].get("taskId")
                    or receipt.get("draft_sha256") != _draft_sha256(saved["payload"])):
                return None
            # Do not join a receipt read before a competing CAS write with newer
            # metadata. A later competing write is still protected by the PUT CAS.
            current = self.store.get(project_id)
            if current.get("revision") != 1 or current.get("task_id") != receipt["task_id"]:
                return None
            return deepcopy(receipt)
        except DocumentProjectError:
            return None

    def sync(self, task_id, snapshot):
        project_id = snapshot.get("project_id")
        if not project_id or snapshot.get("document_type") != "pdf":
            return
        if snapshot.get("data"):
            snapshot = {**snapshot, "data": self.question_ids(project_id, snapshot["data"])}
        self.store.sync_task(project_id, {**snapshot, "task_id": task_id})
        if snapshot.get("data") and not self.store.get(project_id).get("has_baseline"):
            draft = project_draft_from_task(project_id, {**snapshot, "task_id": task_id})
            self.store.save_baseline(project_id, draft)
        # Keep the immutable early/old machine baseline as evidence. A complete
        # fragment result or a corrected local rule can materialize the latest
        # working draft only while no teacher draft exists; CAS closes the race.
        if (snapshot.get("status") in {"completed", "error", "interrupted"} and snapshot.get("data")
                and (snapshot.get("status") == "completed"
                     and snapshot.get("diagnostics", {}).get("pdf_local_cache_recheck", {}).get("status")
                     in {"structure_changed", "fragment_rules_changed"}
                     or snapshot.get("diagnostics", {}).get("pdf_local_draft", {}).get("assembly")
                     == "first_pass_blocks")):
            metadata = self.store.get(project_id)
            if not metadata.get("has_draft"):
                draft = project_draft_from_task(project_id, {**snapshot, "task_id": task_id})
                try:
                    saved = self.save_draft(project_id, draft, metadata["revision"])
                    if metadata["revision"] == 0 and saved["revision"] == 1:
                        receipt = {"schema": _INITIAL_REVISION_SCHEMA, "origin": "first_machine_draft",
                            "project_id": project_id, "task_id": task_id,
                            "base_revision": 0, "revision": 1, "draft_sha256": _draft_sha256(saved["draft"])}
                        self.store.save_stage(project_id, _INITIAL_REVISION_STAGE, receipt,
                            _INITIAL_REVISION_SCHEMA + ":" + project_id)
                except ProjectConflict:
                    # A real edit won; leave it intact and expose the rebuilt
                    # machine result through the existing candidate flow.
                    pass
        if snapshot.get("status") in {"completed","error","cancelled"}:
            report = {"task_id":task_id,"status":snapshot.get("status"),
                "error":snapshot.get("error"),"diagnostics":snapshot.get("diagnostics") or {}}
            self.store.save_stage(project_id,"history-"+task_id.replace("-", "")[:60],report,
                                  hashlib.sha256(task_id.encode()).hexdigest())

    def detail(self, project_id):
        metadata = self.metadata(self.store.get(project_id))
        saved = self.store.get_draft(project_id)
        task = self.store.load_task(project_id)
        live = self.tasks.snapshot(metadata.get("task_id")) if metadata.get("task_id") else None
        effective = live or task
        metadata.update(revision=saved["revision"], draft=saved.get("payload"),
            source_pages=deepcopy(task.get("pdf_source_pages") or []),
            page_images=deepcopy(task.get("page_images") or []),
            page_numbers=deepcopy(task.get("page_numbers") or []),
            diagnostics=deepcopy(task.get("diagnostics") or {}),
            partial=bool(task.get("partial")),
            can_resume=effective.get("status") in {"error", "interrupted", "created", "cancelled"},
            task={key: effective.get(key) for key in ("task_id", "status", "progress", "log", "error", "partial")})
        metadata["latest_result"] = self.question_ids(project_id, task.get("data") or [],
                                                       draft=saved.get("payload") or {})
        metadata["latest_result_id"] = hashlib.sha256(json.dumps(metadata["latest_result"],ensure_ascii=False,
            sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        metadata["task"]["project_id"] = project_id
        receipt = self.validated_initial_revision_receipt(project_id)
        if receipt is not None and receipt["revision"] == metadata["revision"]:
            metadata["project_revision_receipt"] = receipt
        if not metadata["source_pages"]:
            pages = ((task.get("pdf_resume_state") or {}).get("pages") or {})
            metadata["source_pages"] = [{"page_number":int(index)+1, "markdown":row["markdown"]}
                                        for index, row in sorted(pages.items(), key=lambda item:int(item[0]))]
        review = metadata["diagnostics"].get("pdf_figure_review")
        if isinstance(review, dict):
            from mathbank.pdf_figure_pipeline import bind_figure_review_questions
            bind_figure_review_questions(review, metadata["latest_result"],
                source_matches=metadata["diagnostics"].get("source_matches", []),
                source_pages=metadata["source_pages"])
        return metadata

    def save_draft(self, project_id, draft, expected_revision):
        draft = deepcopy(draft)
        questions = draft.get("questions", [])
        if not isinstance(questions, list):
            raise DocumentProjectError("工程题卡必须为列表。")
        draft["questions"] = self.question_ids(project_id, questions)
        result = self.store.save_draft(project_id, draft, expected_revision)
        return {**self.metadata(result["metadata"]), "revision":result["revision"], "draft":result["payload"]}

    def fork(self, project_id, draft=None):
        original = self.store.get(project_id)
        created = self.store.create(self.store.load_source(project_id), original["filename"],
                                    original["options"], self.store.load_curriculum(project_id))
        new_id = created["project_id"]
        task = self.store.load_task(project_id)
        saved_fields = ("saved", "saved_id", "savedSnapshot", "modifiedAfterSave", "savedQuestionId")

        def rebind(rows):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                old_id = row.pop("project_card_id", None)
                row.pop("import_project_id", None)
                if isinstance(old_id, str) and _CARD_ID.fullmatch(old_id):
                    row["project_card_id"] = "card-" + uuid.uuid5(uuid.UUID(new_id), "fork:"+old_id).hex
                row["import_project_id"] = new_id
                for key in saved_fields:
                    row.pop(key, None)
                if isinstance(row.get("project_incoming_candidate"), dict):
                    rebind([row["project_incoming_candidate"]])
            return project_question_ids(new_id, rows)

        if isinstance(task.get("data"), list):
            task["data"] = rebind(task["data"])
        # A copied checkpoint is rebound by dispatch; it does not own the old job.
        task.pop("task_id", None)
        task.update(project_id=new_id, status="interrupted")
        self.store.sync_task(new_id, task)
        payload = deepcopy(draft if draft is not None else self.store.get_draft(project_id).get("payload") or {})
        for field in ("questions", "baselineQuestions"):
            if isinstance(payload.get(field),list):
                payload[field] = rebind(payload[field])
        self.save_draft(new_id, payload, 0)
        return self.detail(new_id)

    def checkpoint(self, project_id, *, analyze=False):
        task = self.store.load_task(project_id)
        state = deepcopy(task.get("pdf_resume_state"))
        draft = self.store.get_draft(project_id).get("payload") or {}
        edits = draft.get("page_edits") or []
        if not isinstance(edits, list) or any(not isinstance(row, dict)
                or type(row.get("page_number")) is not int or row["page_number"] < 1
                or not isinstance(row.get("markdown"), str) or len(row["markdown"]) > 500000 for row in edits):
            raise DocumentProjectError("按页校对稿格式无效。")
        if analyze:
            if not state or not state.get("prepared"):
                raise ProjectConflict("工程尚未完成首次识图，无法只分析本地工作稿。")
        return state

    def migrate_tasks(self, curriculum):
        """Adopt existing local PDF checkpoints without starting any network call."""
        if not self.source_reader:
            return 0
        with self.tasks._lock:
            snapshots = [(key, deepcopy(record.state)) for key, record in self.tasks._records.items()
                         if record.state.get("document_type") == "pdf" and not record.state.get("project_id")]
        count = 0
        for task_id, task in snapshots:
            try:
                source = task.get("document_input") or {}
                content = self.source_reader(task_id, task)
                metadata = self.store.create(content, source["filename"], source["options"], curriculum, task_id)
                task.update(project_id=metadata["project_id"], task_id=task_id)
                self.store.sync_task(metadata["project_id"], task)
                if task.get("data"):
                    self.store.save_baseline(metadata["project_id"], project_draft_from_task(metadata["project_id"], task))
                self.tasks.update(task_id, project_id=metadata["project_id"])
                # Terminal task update intentionally refuses mutation; the new
                # project still needs its association persisted for old pages.
                with self.tasks._lock:
                    record = self.tasks._records.get(task_id)
                    if record:
                        record.state["project_id"] = metadata["project_id"]
                self.tasks._persist(task_id)
                count += 1
            except (DocumentProjectError, KeyError, ValueError, OSError):
                continue
        return count
