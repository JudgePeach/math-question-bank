"""The v2 vision request preserves one text payload and has no v1 fallback."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from pdf_feature_contract import experimental_pdf_only

from test_pdf_page_vision import setup  # noqa: F401
from mathbank import pdf_page_vision as vision
from mathbank.pdf_page_fragments import SCHEMA


def packet():
    return {"schema":SCHEMA,"blocks":[
        {"role":"stem","number":7,"edge":"none","text":" 7. 原题$x=1$。\n"},
        {"role":"stem","number":8,"edge":"none","text":"8. 下一题保留。\n "}],
        "figures":[],"ignored_candidates":[],"page_complete":True,"warnings":[]}


@experimental_pdf_only
def test_structured_request_archives_one_text_and_never_creates_v1_witness(setup,monkeypatch):
    config=replace(setup.provider,model_name="Qwen/Qwen3.8-27B")
    monkeypatch.setattr(vision,"resolve_ocr_provider",lambda *_a:config)
    monkeypatch.setattr("mathbank.pdf_transcription_scopes.begin_pdf_transcription_witness",
        lambda *_a,**_k:pytest.fail("v2 must not obtain a v1 integrity witness"))
    data=packet();body=json.dumps(data,ensure_ascii=False)
    setup.response["choices"][0]["message"]["content"]=body
    archive=[]
    result=vision.request_pdf_page(setup.image,{**setup.info,"candidates":[]},structured_fragments=True,
        include_transcription_evidence=True,max_attempts=1,on_response=lambda b,m:archive.append((deepcopy(b),m)))
    assert len(setup.calls)==len(archive)==1
    request=setup.calls[0][1]
    assert request["enable_thinking"] is False
    user=next(message for message in request["messages"]if message["role"]=="user")
    assert SCHEMA in user["content"][0]["text"]
    assert result["markdown"]=="".join(row["text"]for row in data["blocks"])
    assert all("text"not in row for row in result["fragments"]["blocks"])
    assert archive[0][0]["choices"][0]["message"]["content"]==body
    assert "_transcription_witness"not in result


@pytest.mark.parametrize("broken",["duplicate_key","v1_packet","truncated"])
@experimental_pdf_only
def test_structured_page_rejects_invalid_whole_packet_without_another_protocol(setup,broken):
    body=json.dumps(packet(),ensure_ascii=False)
    if broken=="duplicate_key":body=body[:-1]+',"blocks":[]}'
    elif broken=="v1_packet":body=json.dumps({"markdown":"7. 旧页正文","figures":[],
        "ignored_candidates":[],"page_complete":True,"warnings":[]})
    else:setup.response["choices"][0]["finish_reason"]="length"
    setup.response["choices"][0]["message"]["content"]=body
    archive=[]
    with pytest.raises(ValueError):
        vision.request_pdf_page(setup.image,{**setup.info,"candidates":[]},structured_fragments=True,
            max_attempts=1,on_response=lambda b,m:archive.append(b))
    assert len(setup.calls)==len(archive)==1


@experimental_pdf_only
def test_fragment_format_risk_is_retained_in_its_own_block(setup):
    data=packet();data["blocks"][0]["text"]="7. 原始坏宏\frac{1}{2}和多余$。\n"
    setup.response["choices"][0]["message"]["content"]=json.dumps(data,ensure_ascii=False)
    result=vision.request_pdf_page(setup.image,{**setup.info,"candidates":[]},structured_fragments=True,max_attempts=1)
    blocks=result["fragments"]["blocks"]
    assert result["markdown"]=="".join(row["text"]for row in data["blocks"])
    assert "control_characters"in blocks[0]["pending_reasons"]
    assert "control_characters"not in blocks[1]["pending_reasons"]
    assert blocks[1]["number"]==8
    assert len(setup.calls)==1
