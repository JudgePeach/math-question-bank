"""The original PDF split algorithm, with optional outer-cache hooks.

This module preserves the original metadata/hybrid/full-text routing and source
checks. Cached responses are inputs to those same parsers, never source-block
instructions for constructing a different question body.
"""
from copy import deepcopy
import hashlib
import json
import threading

from mathbank.content_locks import lock_visible_math, reconcile_visible_math
from mathbank.paper_parse import finalize_source_answers
from mathbank.pdf_hybrid_request import prepare_pdf_task_plan, request_pdf_hybrid
from mathbank.pdf_hybrid_plan import pdf_hybrid_plan_diagnostics
from mathbank.pdf_split_parallel import request_pdf_parallel_split
from mathbank.task_manager import TaskCancelled


def completion_request_binding(provider, payload):
    value = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",", ":"),allow_nan=False)
    return {"provider":getattr(provider,"provider_code",None),"model":provider.model_name,
            "payload_sha256":hashlib.sha256(value.encode()).hexdigest(),
            "endpoint_sha256":hashlib.sha256(str(getattr(provider,"chat_completions_url","")).encode()).hexdigest()}


class _ReplayResponse:
    status_code = 200
    headers = {}
    def __init__(self, body):
        self.body = body
    def json(self):
        return deepcopy(self.body)


class LegacyResponseRecorder:
    """Record/replay complete non-stream responses without changing HTTP rules."""
    def __init__(self, post, cache, check_cancelled):
        self.post, self.cache, self.check_cancelled = post, cache, check_cancelled
        self.calls = 0
        self.replayed = 0
        self._lock = threading.RLock()
        self._read_bindings = set()

    @staticmethod
    def _binding_key(binding):
        return json.dumps(binding,sort_keys=True,separators=(",", ":"),allow_nan=False)

    def skip_cached_binding(self, provider, payload):
        """A caller rejected this exact historical body; keep it for audit."""
        binding = completion_request_binding(provider,payload)
        with self._lock:
            self._read_bindings.add(self._binding_key(binding))

    def __call__(self, provider, payload, **kwargs):
        self.check_cancelled()
        binding = completion_request_binding(provider,payload)
        key = self._binding_key(binding)
        with self._lock:
            may_read = key not in self._read_bindings
            # Mark before either reading history or making a new request. A
            # complete but rejected new body must not become its own retry.
            self._read_bindings.add(key)
        if self.cache is not None and not kwargs.get("stream") and may_read:
            body = self.cache.load_raw_response("legacy-http", input_binding=binding)
            if body is not None:
                with self._lock:
                    self.replayed += 1
                return _ReplayResponse(body)
        with self._lock:
            self.calls += 1
        response = self.post(provider,payload,**kwargs)
        self.check_cancelled()
        if self.cache is not None and not kwargs.get("stream") and response.status_code == 200:
            try:
                self.cache.on_response("legacy-http",response.json(),input_binding=binding)
            except TaskCancelled:
                raise
            except Exception:
                # Cache failures never cause another provider POST.
                self.check_cancelled()
        return response


def execute_legacy_pdf_parse(source_content, *, source_namespace, curriculum,
        source_pages, layout_result, task_id, source_document_sha256,
        diagnostics, provider, post, parse_full, normalize_fillin,
        native_evidence=None, witnesses=None, transcript_normalizer=lambda value:value,
        figure_assets=None, cache=None, check_cancelled=lambda:None,
        report_attempt=lambda _event:None, parallel=True, finalize=None,
        full_body_only=False):
    """Use the pre-project algorithm; the caller owns persistence and UI state.

    ``full_body_only`` requires complete question bodies from the existing API
    parser. It excludes local source adoption and metadata-only hybrid routes;
    source reconciliation and original-answer protection still run afterward.
    """
    check_cancelled()
    locked_source, math_locks = (lock_visible_math(source_content,source_namespace)
                                  if layout_result is not None else (source_content, []))
    diagnostics["math_locks_created"] = len(math_locks)
    recorder = LegacyResponseRecorder(post,cache,check_cancelled)

    def whole(*, retry=True):
        return parse_full(locked_source,pdf_retry=retry,connection_retry=retry,
                          request_post=recorder,completion_cache=cache)

    preserved = frozenset()
    partial = False
    plan = None
    if full_body_only:
        # A resumed task can carry historical hybrid diagnostics. They cannot
        # describe this run, whose only accepted bodies come from API replies.
        diagnostics.pop("pdf_hybrid", None)
    if layout_result is not None and not full_body_only:
        plan = prepare_pdf_task_plan(source_content,diagnostics,source_pages=source_pages,
            layout_result=layout_result,task_id=task_id,generation=0,
            source_document_sha256=source_document_sha256,native_evidence=native_evidence,
            witnesses=witnesses or {},transcript_normalizer=transcript_normalizer,
            figure_assets=figure_assets or {})
        diagnostics["pdf_hybrid"] = pdf_hybrid_plan_diagnostics(plan)

    if plan is not None and plan["mode"] in {"hybrid","whole_metadata"}:
        def original_hybrid():
            return request_pdf_hybrid(plan,curriculum,provider=provider,post=recorder,
                diagnostics=diagnostics,normalize_fillin=normalize_fillin,
                task_id=task_id,generation=0,check_cancelled=check_cancelled,
                full_source_fallback=lambda:whole(retry=False))
        if parallel:
            result = request_pdf_parallel_split(plan,curriculum,provider=provider,post=recorder,
                diagnostics=diagnostics,task_id=task_id,generation=0,
                single_legacy_fallback=original_hybrid,normalize_fillin=normalize_fillin,
                check_cancelled=check_cancelled,report_attempt=report_attempt,
                on_group_result=cache.save_partial if cache is not None else None,
                load_group_result=cache.load_partial if cache is not None else None)
        else:
            from mathbank.pdf_split_parallel import pdf_split_request_slot
            with pdf_split_request_slot(provider,check_cancelled=check_cancelled):
                result = original_hybrid()
        questions = result.questions
        preserved = result.preserved_indices
        partial = result.partial
        diagnostics.setdefault("pdf_hybrid",{})["partial"] = partial
    else:
        # When an unambiguous original heading census exists, each output
        # scope can keep the *entire* context. No source range or cross-page
        # question is cut, and both replies use the original full-body parser.
        from mathbank.pdf_full_context_split import request_full_context_pdf_split
        diagnostics.pop("pdf_splitting", None)
        batch = request_full_context_pdf_split(source_content,locked_source=locked_source,
            provider=provider,parse_full=parse_full,request_post=recorder,cache=cache,
            diagnostics=diagnostics,check_cancelled=check_cancelled,report_attempt=report_attempt) if parallel else None
        if batch is not None:
            questions, partial = batch.questions, batch.partial
        else:
            from mathbank.pdf_split_parallel import pdf_split_request_slot
            with pdf_split_request_slot(provider,check_cancelled=check_cancelled):
                questions = whole()
            diagnostics["pdf_parallel_split"] = {"used_parallel":False,"status":"original",
                "reason":"unproven_original_number_census","calls":0,"max_concurrency":2}
    check_cancelled()
    if cache is not None:
        cache.save_structured("legacy-before-source-reconciliation",{
            "questions":questions,"preserved_indices":preserved,"partial":partial})
    if layout_result is not None:
        staged = deepcopy(questions) if preserved else questions
        diagnostics.update(reconcile_visible_math(staged,math_locks,source_content))
        if preserved:
            for index,candidate in enumerate(staged):
                if index not in preserved:
                    questions[index] = candidate
                elif candidate.get("source_review"):
                    questions[index]["source_review"] = candidate["source_review"]
        finalize_source_answers(questions,source_content)
    elif full_body_only:
        finalize_source_answers(questions, source_content)
    # Keep source identities when the old source matcher can establish them;
    # an optional model-supplied number never rewrites an established identity.
    origins = {}
    for row in diagnostics.get("source_matches",[]):
        if row.get("field") == "content" and type(row.get("source_number")) is int:
            origins.setdefault(row["question_index"],set()).add(row["source_number"])
    for index,question in enumerate(questions):
        numbers = origins.get(index,set())
        if len(numbers) == 1:
            question["source_number"] = next(iter(numbers))
    if finalize is not None:
        questions = finalize(questions,preserved)
    diagnostics["pdf_legacy_requests"] = {"new_calls":recorder.calls,"replayed_responses":recorder.replayed}
    packet = {"questions":questions,"preserved_indices":preserved,"partial":partial,
              "diagnostics":deepcopy({key:value for key,value in diagnostics.items()
                                      if key != "pdf_project_legacy_parse"})}
    if full_body_only:
        packet["workflow"] = "cloud-api-full-body.v1"
    return packet
