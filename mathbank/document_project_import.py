"""Card revision binding and same-transaction import receipts."""
from __future__ import annotations

from functools import wraps
import hashlib
import json

from fastapi import HTTPException
from mathbank.database import DocumentImportReceipt
from mathbank.document_projects import DocumentProjectError

_TEXT = ("content", "answer_markdown", "question_type", "difficulty", "source", "review",
         "category_compulsory", "category_chapter", "category_knowledge", "tikz_code",
         "tikz_reference_image_path", "tags", "related_question_id")
_JSON = {"image_paths":[], "content_tikz_assets":[], "answer_tikz_assets":[],
         "image_layouts":{}, "sub_knowledge":[], "knowledge_points":[]}


def import_payload_hash(values):
    normalized = {key:str(values.get(key) or "").strip() for key in _TEXT}
    for key, default in _JSON.items():
        value = values.get(key)
        if value is None or value == "":
            value = default
        if isinstance(value,str):
            value = json.loads(value)
        normalized[key] = value
    images = normalized["image_paths"]
    if not isinstance(images,list) or any(not isinstance(value,str) for value in images):
        raise ValueError("工程图片清单无效。")
    normalized["image_paths"] = list(dict.fromkeys(images))
    normalized["figure_align"] = str(values.get("figure_align") or "right")
    normalized["figure_size"] = str(values.get("figure_size") or "auto")
    custom = values.get("figure_align_custom", False)
    normalized["figure_align_custom"] = custom is True or custom in ("true","1",1)
    assets = normalized["content_tikz_assets"]
    if isinstance(assets,list) and assets and isinstance(assets[0],dict):
        normalized["tikz_code"] = str(assets[0].get("tikz_code") or "")
        normalized["tikz_reference_image_path"] = str(assets[0].get("reference_image_path") or "")
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def project_import_guard(store_getter):
    """Asset and curriculum locks are acquired before this per-project lease."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            project_id = kwargs.get("import_project_id")
            if not isinstance(project_id, str) or not project_id:
                return function(*args, **kwargs)
            store = store_getter()
            if store is None:
                raise HTTPException(409, "本地工程服务不可用，请保留校对稿。")
            try:
                with store.operation(project_id):
                    return function(*args, **kwargs)
            except DocumentProjectError as exc:
                raise HTTPException(409, str(exc)) from exc
        return run
    return decorate


def prepare_project_import(db, store, project_id, card_id, revision, payload):
    if not isinstance(project_id, str) or not project_id:
        return None, None
    if not isinstance(card_id, str) or not card_id or len(card_id)>160 or type(revision) is not int or revision<0:
        raise ValueError("工程题卡或修订号无效。")
    digest = import_payload_hash(payload)
    receipt = db.query(DocumentImportReceipt).filter_by(project_id=project_id,card_id=card_id).first()
    if receipt:
        if receipt.payload_sha256 != digest:
            raise HTTPException(409, "该工程题卡已经入库；修改后请编辑原题，或另存为新的工程副本。")
        if receipt.question_id is None:
            raise HTTPException(410, "这张工程题卡对应的题目已被删除，未重复新建。")
        return digest, receipt.question_id
    saved = store.get_draft(project_id)
    if revision > saved["revision"]:
        raise HTTPException(409, "工程修订尚未保存，请等待本地保存完成。")
    cards = (saved.get("payload") or {}).get("questions") or []
    matches = [row for row in cards if isinstance(row,dict) and row.get("project_card_id")==card_id]
    if len(matches)!=1 or import_payload_hash(matches[0])!=digest:
        raise HTTPException(409, "工程题卡内容已变化，请先保存并重新核对后入库。")
    return digest, None


def record_project_import(db, *, project_id, card_id, revision, digest, question_id):
    if digest:
        db.add(DocumentImportReceipt(project_id=project_id,card_id=card_id,
            draft_revision=revision,payload_sha256=digest,question_id=question_id))
