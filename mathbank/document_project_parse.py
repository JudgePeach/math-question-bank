"""Durable outer cache for the existing PDF splitting algorithm.

The executor owns prompts, transport, retries, source checks and scheduling.
This module never changes question text, metadata or source-review evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import threading

from .document_projects import DocumentProjectError, ProjectConflict, ProjectCorrupt, _CREDENTIALS
from .source_review_results import parse_review_response
from .task_manager import TaskCancelled

SCHEMA = "mathbank.document-project-legacy-parse.v1"
RAW_SCHEMA = "mathbank.document-project-legacy-raw.v1"
DEFAULT_ALGORITHM_VERSION = "legacy-pdf-parse.v1"
DEFAULT_PROMPT_VERSION = "legacy-prompts.v1"
DEFAULT_VALIDATION_VERSION = "legacy-source-validation.v1"


def _json(value, *, ordered=False):
    return json.dumps(value, ensure_ascii=False, sort_keys=not ordered, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode(value):
    """An unambiguous JSON tree preserves tuple/set indices without collisions."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("legacy cache dictionary keys must be strings")
        if any(key.lower().replace("-", "_") in _CREDENTIALS for key in value):
            raise ValueError("legacy cache cannot contain credential fields")
        return {"kind": "dict", "items": [[key, _encode(child)] for key, child in value.items()]}
    if isinstance(value, (list, tuple, set, frozenset)):
        kind = "list" if isinstance(value, list) else "tuple" if isinstance(value, tuple) else "frozenset" if isinstance(value, frozenset) else "set"
        items = [_encode(child) for child in value]
        if kind in {"set", "frozenset"}:
            items.sort(key=_json)
        return {"kind": kind, "items": items}
    if value is None or isinstance(value, (str, bool, int)) or isinstance(value, float) and math.isfinite(value):
        return {"kind": "scalar", "value": value}
    raise ValueError("legacy cache result is not serializable")


def _decode(value):
    if not isinstance(value, dict):
        raise ProjectCorrupt("原拆卷缓存类型记录无效。")
    kind = value.get("kind")
    if kind == "scalar" and set(value) == {"kind", "value"}:
        if value["value"] is None or isinstance(value["value"], (str, bool, int)) or isinstance(value["value"], float) and math.isfinite(value["value"]):
            return value["value"]
    elif set(value) == {"kind", "items"} and isinstance(value["items"], list):
        if kind == "dict":
            result = {}
            for pair in value["items"]:
                if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str) or pair[0] in result:
                    raise ProjectCorrupt("原拆卷缓存字典记录无效。")
                result[pair[0]] = _decode(pair[1])
            return result
        children = [_decode(child) for child in value["items"]]
        if kind == "list":
            return children
        if kind == "tuple":
            return tuple(children)
        if kind == "set":
            return set(children)
        if kind == "frozenset":
            return frozenset(children)
    raise ProjectCorrupt("原拆卷缓存类型记录无效。")


def _result(value):
    if (not isinstance(value, dict) or not isinstance(value.get("questions"), list)
            or any(not isinstance(question, dict) for question in value["questions"])):
        raise ValueError("原拆卷执行器未返回完整结果对象。")
    return value


class LegacyParseCache:
    """Executor hooks: complete responses, structured stages and reliable groups."""

    def __init__(self, store, project_id, binding, fingerprint, revision, report, check_cancelled):
        self.store, self.project_id = store, project_id
        self.input_binding, self.fingerprint = deepcopy(binding), fingerprint
        self.revision, self.report, self.check_cancelled = revision, report, check_cancelled
        # A validation/assembly update can replay the paid model packet. The
        # caller's request binding must include the effective request-payload
        # digest and actual provider/model, never credentials.
        self.raw_binding = {key: deepcopy(value) for key, value in binding.items()
                            if key not in {"schema", "algorithm_version", "validation_version"}}
        self.raw_binding["schema"] = RAW_SCHEMA
        self._lock = threading.RLock()

    def check(self):
        with self._lock:
            return self._check()

    def _check(self):
        self.check_cancelled()
        try:
            metadata = self.store.get(self.project_id)
        except DocumentProjectError as exc:
            exc.retryable = False
            raise
        if (metadata.get("source_sha256") != self.input_binding["source_document_sha256"]
                or self.revision is not None and metadata.get("revision") != self.revision):
            error = ProjectConflict("拆卷期间工程或校对稿已变化，原结果与缓存均已保留，请重新载入。")
            error.retryable = False
            raise error

    def _identity(self, kind, stage, input_binding):
        if not isinstance(stage, str) or not stage or len(stage) > 512:
            raise ValueError("原拆卷缓存阶段标识无效。")
        local = {"kind": kind, "stage": stage, "scope": input_binding}
        root = self.raw_binding if kind in {"raw", "raw-index"} else self.fingerprint
        return "legacy-" + kind + "-" + _sha(_json([root, local]))[:32], _sha(_json([root, local]))

    def _packet_binding(self, kind):
        return self.raw_binding if kind in {"raw", "raw-index"} else self.input_binding

    def _packet_schema(self, kind):
        return RAW_SCHEMA if kind in {"raw", "raw-index"} else SCHEMA

    def _load(self, kind, stage, input_binding=None):
        with self._lock:
            return self._load_locked(kind, stage, input_binding)

    def _load_locked(self, kind, stage, input_binding=None):
        self.check()
        name, signature = self._identity(kind, stage, input_binding)
        packet = self.store.load_stage(self.project_id, name, signature)
        self.check()
        if packet is None:
            return None
        if (not isinstance(packet, dict) or packet.get("schema") != self._packet_schema(kind)
                or packet.get("binding") != self._packet_binding(kind) or packet.get("signature") != signature
                or packet.get("kind") != kind or packet.get("stage") != stage or packet.get("scope") != input_binding):
            raise ProjectCorrupt("原拆卷缓存与当前工作稿不一致，未重新请求模型。")
        return _decode(packet["payload"])

    def _save(self, kind, stage, value, input_binding=None):
        with self._lock:
            return self._save_locked(kind, stage, value, input_binding)

    def _save_locked(self, kind, stage, value, input_binding=None):
        self.check()
        try:
            name, signature = self._identity(kind, stage, input_binding)
            packet = {"schema": self._packet_schema(kind), "kind": kind, "stage": stage, "scope": deepcopy(input_binding),
                      "binding": deepcopy(self._packet_binding(kind)), "signature": signature, "payload": _encode(value)}
            self.store.save_stage(self.project_id, name, packet, signature, complete=True)
        except TaskCancelled:
            raise
        except Exception as exc:
            # Saving is never part of a provider retry decision. The executor
            # keeps the response it already paid for and can still return it.
            self.report["cache_errors"].append({"kind": kind, "stage": stage, "error_type": type(exc).__name__})
            return False
        self.check()
        return True

    def on_response(self, stage, value, input_binding=None):
        """Record only a complete stop/JSON model packet, without credentials.

        Rejected recording does not reject the executor's existing parser or
        create another model call. It only prevents incomplete raw caching.
        """
        self.check()
        try:
            choices = value.get("choices") if isinstance(value, dict) else None
            if not isinstance(choices, list) or not choices or choices[0].get("finish_reason") != "stop":
                raise ValueError("incomplete completion")
            parse_review_response(choices[0]["message"]["content"])
            digest = _sha(_json(_encode(value)))
        except (ValueError, TypeError, KeyError, AttributeError):
            self.report["cache_errors"].append({"kind": "raw", "stage": stage, "error_type": "IncompleteResponse"})
            return False
        history_stage = stage + "." + digest[:24]
        with self._lock:
            if not self._save("raw", history_stage, value, input_binding):
                return False
            return self._save("raw-index", stage, {"history_stage": history_stage, "response_sha256": digest}, input_binding)

    store_raw_response = on_response

    def load_raw_response(self, stage, input_binding=None):
        """Load the latest full reply for this exact effective model request.

        The explicit binding is a caller-owned payload/model digest or mapping.
        Unbound historical packets remain inspectable but are never replayed.
        Algorithm/validation versions intentionally do not enter raw identity.
        """
        self.check()
        if input_binding is None:
            return None
        with self._lock:
            index = self._load("raw-index", stage, input_binding)
            if index is None:
                return None
            if (not isinstance(index, dict) or not isinstance(index.get("history_stage"), str)
                    or not isinstance(index.get("response_sha256"), str)):
                raise ProjectCorrupt("原模型回包索引无效。")
            value = self._load("raw", index["history_stage"], input_binding)
            if value is None or _sha(_json(_encode(value))) != index["response_sha256"]:
                raise ProjectCorrupt("原模型回包与完整响应索引不一致。")
            choices = value.get("choices") if isinstance(value, dict) else None
            if not isinstance(choices, list) or not choices or choices[0].get("finish_reason") != "stop":
                raise ProjectCorrupt("原模型回包未完整结束。")
            try:
                parse_review_response(choices[0]["message"]["content"])
            except (ValueError, TypeError, KeyError):
                raise ProjectCorrupt("原模型回包不是完整JSON。")
            self.report["reused_raw_stages"].append(stage)
            return value

    load_latest_response = load_raw_response

    def save_structured(self, stage, value, input_binding=None):
        return self._save("structured", stage, value, input_binding)

    def load_structured(self, stage, input_binding=None):
        return self._load("structured", stage, input_binding)

    def save_partial(self, scope_id, result, input_binding=None):
        return self._save("partial", scope_id, result, input_binding)

    def load_partial(self, scope_id, input_binding=None):
        result = self._load("partial", scope_id, input_binding)
        if result is not None:
            self.report["reused_partial_scopes"].append(scope_id)
        return result


def run_project_legacy_parse(
    store, project_id, source, source_pages, curriculum, *, source_document_sha256, executor,
    asset_receipts=None, expected_revision=None, algorithm_version=DEFAULT_ALGORITHM_VERSION,
    prompt_version=DEFAULT_PROMPT_VERSION, validation_version=DEFAULT_VALIDATION_VERSION,
    check_cancelled=lambda: None, diagnostics=None, force=False, resume_partial=False,
    accept_cached_result=None,
):
    """Run the unchanged legacy executor once, or return its exact cached result.

    ``executor(cache)`` owns every paid-call limit. The wrapper never retries it.
    A cached partial stays manual unless a reliable group scheduler explicitly
    opts into ``resume_partial`` and reuses the cache's scoped results.
    ``accept_cached_result(packet)`` may reject an assembled result after a
    local validation change without discarding its raw/scoped model cache.
    It receives an independent copy and must return a bool. An exception or
    non-bool result stops locally, before invoking the executor or paid work.
    """
    if (not isinstance(source, str) or not isinstance(source_pages, list) or not isinstance(curriculum, dict)
            or not isinstance(source_document_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_document_sha256)
            or not callable(executor) or type(force) is not bool or type(resume_partial) is not bool
            or accept_cached_result is not None and not callable(accept_cached_result)
            or any(not isinstance(v, str) or not v for v in (algorithm_version, prompt_version, validation_version))):
        raise ValueError("原拆卷工程输入无效。")
    check_cancelled()
    diagnostics = diagnostics if diagnostics is not None else {}
    with store.operation(project_id):
        metadata = store.get(project_id)
        revision = metadata.get("revision")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != revision):
            raise ProjectConflict("工程修订已变化，请重新载入本地工作稿。", current_revision=revision)
        binding = {"schema": SCHEMA, "source_document_sha256": source_document_sha256,
                   "source_sha256": _sha(source), "source_pages_sha256": _sha(_json(source_pages)),
                   "curriculum_sha256": _sha(_json(curriculum, ordered=True)),
                   "asset_receipts_sha256": _sha(_json(asset_receipts or {})),
                   "algorithm_version": algorithm_version, "prompt_version": prompt_version,
                   "validation_version": validation_version}
        fingerprint = _sha(_json(binding))
        report = {"schema": SCHEMA, "mode": "legacy_executor_cache", "fingerprint": fingerprint,
                  "reused": False, "executor_calls": 0, "new_calls": None, "cache_errors": [],
                  "reused_partial_scopes": [], "reused_raw_stages": []}
        diagnostics["pdf_project_legacy_parse"] = report
        cache = LegacyParseCache(store, project_id, binding, fingerprint, expected_revision, report, check_cancelled)
        cache.check()
        accepted = None if force else cache._load("accepted", "final")
        if accepted is not None:
            _result(accepted)
            acceptable = True
            if accept_cached_result is not None:
                report["accepted_cache_checked"] = True
                try:
                    acceptable = accept_cached_result(deepcopy(accepted))
                    if type(acceptable) is not bool:
                        raise ValueError("原拆卷缓存接受检查必须返回布尔值。")
                    # A validator must not bypass cancellation or the caller's
                    # optional revision binding by taking time or changing it.
                    cache.check()
                except TaskCancelled:
                    report.update(status="cancelled", new_calls=0,
                                  accepted_cache_error="TaskCancelled")
                    raise
                except Exception as exc:
                    report.update(status="cached_acceptance_error", new_calls=0,
                                  accepted_cache_error=type(exc).__name__)
                    exc.retryable = False
                    raise
                report["accepted_cache_rejected"] = not acceptable
            if acceptable and (not accepted.get("partial") or not resume_partial):
                report.update(reused=True, new_calls=0,
                              partial=bool(accepted.get("partial")),
                              status="partial_manual_review" if accepted.get("partial") else "reused")
                return deepcopy(accepted)
        # Immutable caller snapshots belong to the executor closure. It must
        # not be invoked a second time because the result could not be saved.
        report.update(status="executing", executor_calls=1)
        value = _result(executor(cache))
        cache.check()
        cache._save("accepted", "final", value)
        report.update(status="partial" if value.get("partial") else "completed", partial=bool(value.get("partial")))
        cache.check()
        return deepcopy(value)


parse_project_legacy_pdf = run_project_legacy_parse
