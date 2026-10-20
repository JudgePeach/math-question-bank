"""Pure first-pass fragment contracts: exact text, local doubt, safe figures."""
from copy import deepcopy
import hashlib
import json

import pytest
import requests

from pdf_feature_contract import experimental_pdf_only
from mathbank.pdf_page_fragments import (
    MAX_BLOCKS, SCHEMA, number_prefix, validate_pdf_page_fragments,
)
from mathbank.prompts import (
    COMMON_OCR_PROMPT, PDF_ORIGINAL_COMMON_OCR_PROMPT,
    build_pdf_page_fragments_prompt, build_pdf_page_vision_prompt,
    build_pdf_region_vision_prompt,
)
from mathbank.source_review_results import parse_review_response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
        lambda *_a, **_kw: pytest.fail("Fragment adaptation must make zero API calls"))


def block(text="16. 原题 $x=1$。\n", *, role="stem", number=16, edge="none"):
    return {"role": role, "number": number, "edge": edge, "text": text}


def packet(*blocks):
    return {"schema": SCHEMA, "blocks": list(blocks or (block(),)), "figures": [],
            "ignored_candidates": [], "page_complete": True, "warnings": []}


def info(**kwargs):
    return {"page_index": 2, "candidates": [], **kwargs}


def test_only_first_text_is_source_and_all_offsets_hashes_roundtrip_without_strip():
    values = [block("\n  13. 原题 $f(x+a)=x^2+2x+3$。\n\n", number=13),
              block("14. 下一题。  \n", number=14),
              block("数学  第3页 共4页\n", role="furniture", number=None)]
    original = packet(*values); before = deepcopy(original)
    result = validate_pdf_page_fragments(original, info())
    expected = "".join(value["text"] for value in values)
    assert result["markdown"] == expected
    fragments = result["fragments"]
    assert fragments["source_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert fragments["schema"] == SCHEMA and fragments["page_index"] == 2
    assert fragments["slot_blocks"] == {}
    for source, saved in zip(values, fragments["blocks"]):
        left, right = saved["range"]
        assert expected[left:right] == source["text"]
        assert saved["text_sha256"] == hashlib.sha256(source["text"].encode()).hexdigest()
        assert set(saved) == {"id", "role", "number", "edge", "range", "text_sha256", "pending_reasons"}
        assert "text" not in saved and saved["id"].startswith("p3-b")
    assert result == validate_pdf_page_fragments(original, info())
    assert original == before


@pytest.mark.parametrize("text,expected,rest", [
    ("16. 原题", 16, "原题"), ("15.（本小题满分13分）原题", 15, "（本小题满分13分）原题"),
    (" \n18． 原题", 18, "原题"), ("19、【答案】原答", 19, "【答案】原答"),
    ("**15.** 原题", 15, "原题"), ("**15.**\n原题", 15, "原题"),
    ("1.已知条件", 1, "已知条件"),
])
def test_visible_number_prefix_removes_only_original_label(text, expected, rest):
    number, end = number_prefix(text)
    assert number == expected and text[end:] == rest


@pytest.mark.parametrize("text", ["(1) 小问", "① 小问", "$16. x=1$", "【答案】1", "3.14是常数",
    "1.py", "第16题", "**16. 原题**", "```\n16. 示例\n```", "10001. 超限", "0. 编号", None])
def test_number_prefix_does_not_search_inside_math_subquestions_or_answer_values(text):
    assert number_prefix(text) == (None, None)


@pytest.mark.parametrize("metadata,text,code", [
    (16, "17. 实际原号不符。", "number_mismatch"),
    (16, "未打印顶层题号的原文。", "number_prefix_missing"),
    (None, "16. 明确原题号但元数据缺失。", "number_missing"),
])
def test_number_contradiction_is_local_and_never_creates_or_changes_a_number(metadata, text, code):
    result = validate_pdf_page_fragments(packet(block(text, number=metadata), block("18. 后一题。", number=18)), info())
    first, second = result["fragments"]["blocks"]
    assert first["number"] is metadata and code in first["pending_reasons"]
    assert second["number"] == 18 and second["pending_reasons"] == []
    assert text in result["markdown"]


@pytest.mark.parametrize("role", ["stem", "answer"])
@pytest.mark.parametrize("edge", ["from_previous", "both"])
def test_unnumbered_continuation_needs_no_fabricated_original_number(role, edge):
    result = validate_pdf_page_fragments(packet(block("故选项B错误；其余原解。", role=role, number=None, edge=edge)), info())
    saved = result["fragments"]["blocks"][0]
    assert saved["number"] is None and saved["edge"] == edge
    assert not any(reason.startswith("number_") for reason in saved["pending_reasons"])


def test_answer_key_full_table_and_unknown_roles_remain_available_without_guessing():
    table = "\\begin{tabular}{cc}题号&1\\\\答案&D\\\\\\end{tabular}\n"
    p = packet(block(table, role="answer_key", number=None),
        block("无法确认角色的原文。", role="unknown", number=None, edge="unknown"))
    result = validate_pdf_page_fragments(p, info())
    assert result["markdown"] == table + p["blocks"][1]["text"]
    assert result["fragments"]["blocks"][0]["pending_reasons"] == []
    assert result["fragments"]["blocks"][1]["pending_reasons"] == ["unknown_role", "unknown_edge"]


@pytest.mark.parametrize("bad,code", [
    ("13. 原题 $f(x)=1$ \\fillin $$ .\n", "fragment_math_review"),
    ("13. 原答 $\\begin{cases}x=1\\\\y=2\n", "fragment_math_review"),
    ("13. 原答 \x0crac{1}{2}与\x08egin{cases}。\n", "control_characters"),
    ("13. 原题\\begin{choices}\\item 1\\item\\item 3\\item 4\\end{choices}\n", "fragment_choices_review"),
])
def test_broken_formula_or_choices_is_an_advisory_on_one_block_without_page_retry(bad, code, monkeypatch):
    import mathbank.pdf_page_vision as v1
    original = v1._validated_page
    def metadata_only(value, page_info):
        assert value["markdown"] == "page diagnostics", "Never put source through v1 whole-page retry gate"
        return original(value, page_info)
    monkeypatch.setattr(v1, "_validated_page", metadata_only)
    result = validate_pdf_page_fragments(packet(block(bad, number=13), block("14. 后一题完整。", number=14)), info())
    assert result["markdown"] == bad + "14. 后一题完整。"
    assert code in result["fragments"]["blocks"][0]["pending_reasons"]
    assert result["fragments"]["blocks"][1]["pending_reasons"] == []


def test_literal_math_examples_do_not_become_fragment_formula_doubts():
    p = packet(block("16. 正文。\n```latex\n$\\begin{cases}\n```\n与转义金额\\$5。"))
    assert validate_pdf_page_fragments(p, info())["fragments"]["blocks"][0]["pending_reasons"] == []


def test_slots_are_owned_by_their_exact_block_and_use_existing_page_slot_ids():
    p = packet(block("10. 题干。\n[插图待补: 图1]\n", number=10),
        block("14. 原解。\n[插图待补: 图2]\n", role="answer", number=14))
    p["figures"] = [{"slot":label,"bbox":{"left":100,"top":200,"right":300,"bottom":400},
        "candidate_ids":[],"review_required":False,"review_reason":""} for label in ("图1","图2")]
    r = validate_pdf_page_fragments(p, info())
    ids = [b["id"] for b in r["fragments"]["blocks"]]
    assert r["fragments"]["slot_blocks"] == {"p3-s1":ids[0],"p3-s2":ids[1]}
    assert [f["slot_id"] for f in r["layout"]["figures"]] == ["p3-s1","p3-s2"]
    assert all("image_path" not in f and "crop_review" not in f for f in r["layout"]["figures"])


@pytest.mark.parametrize("bbox", [[900,900,100,100], [True,200,300,400], None])
def test_bad_geometry_keeps_text_and_pending_slots_without_attaching_an_asset(bbox):
    p=packet(block("16. 原题。\n[插图待补: 图1]"))
    p["figures"]=[{"slot":"图1","bbox":bbox,"candidate_ids":[],"review_required":True,"review_reason":""}]
    r=validate_pdf_page_fragments(p,info())
    assert r["markdown"]==p["blocks"][0]["text"] and r["layout"]["figures"]==[]
    assert r["layout"]["page_complete"] is False
    assert "figure_layout_review" in r["fragments"]["blocks"][0]["pending_reasons"]


def test_native_single_image_geometry_still_uses_existing_exact_asset_bounds():
    p=packet(block("16. 原题。\n[插图待补: 图1]"))
    p["figures"]=[{"slot":"图1","bbox":None,"candidate_ids":["native"],"review_required":False,"review_reason":""}]
    r=validate_pdf_page_fragments(p,info(candidates=[{"id":"native","type":"raster","bbox":[100,200,300,400]}]))
    assert r["layout"]["figures"][0]["bbox"]==[100,200,300,400]
    assert r["layout"]["figures"][0]["native_box"] is True


@pytest.mark.parametrize("kind", ["cross_block", "dangling", "duplicate", "unknown_candidate", "unknown_slot", "path", "bbox_url"])
def test_ambiguous_slots_and_model_asset_identity_are_rejected(kind):
    p=packet(block("16. 原题。[插图待补: 图1]"))
    figure={"slot":"图1","bbox":[100,200,300,400],"candidate_ids":[],"review_required":False,"review_reason":""}
    p["figures"]=[figure]
    if kind=="cross_block":p["blocks"]=[block("16. [插图"),block("待补: 图1]",role="unknown",number=None)]
    elif kind=="dangling":p["blocks"][0]["text"]="16. [插图待补: 图1"
    elif kind=="duplicate":p["blocks"].append(block("17. [插图待补: 图1]",number=17))
    elif kind=="unknown_candidate":figure["candidate_ids"]=["not-this-page"]
    elif kind=="unknown_slot":figure["slot"]="图2"
    elif kind=="path":figure["image_path"]="/static/uploads/untrusted.png"
    elif kind=="bbox_url":figure["bbox"]={"url":"https://example.invalid/image.png"}
    with pytest.raises(ValueError):validate_pdf_page_fragments(p,info())


@pytest.mark.parametrize("text", ["16. ![模型图](https://example.invalid/a.png)",
    "16. /static/uploads/arbitrary.png", "16. <img src='x'>", "16. \\includegraphics{x}",
    "16. <!-- MATHBANK_PDF_PAGE:3 -->", "16. [[MBM_fake_0001]]"])
def test_model_text_never_injects_assets_or_server_source_markers(text):
    with pytest.raises(ValueError):validate_pdf_page_fragments(packet(block(text)),info())


def test_server_marker_cannot_be_hidden_across_two_blocks():
    p=packet(block("16. <!-- MATH"),block("BANK_PDF_PAGE:3 -->",role="unknown",number=None))
    with pytest.raises(ValueError):validate_pdf_page_fragments(p,info())


@pytest.mark.parametrize("field,value", [("schema","v1"),("blocks",[]),("blocks",[block()]*(MAX_BLOCKS+1)),
    ("page_complete","false"),("warnings","warning"),("warnings",["x"*501]),("markdown","duplicate source")])
def test_strict_envelope_and_resource_limits(field,value):
    p=packet();p[field]=value
    with pytest.raises(ValueError):validate_pdf_page_fragments(p,info())


@pytest.mark.parametrize("field,value", [("role","problem"),("edge","continue"),("number","16"),
    ("number",True),("number",0),("number",10001),("text"," "),("text","x"*50001),("uuid","model-id")])
def test_block_schema_types_and_limits(field,value):
    p=packet();p["blocks"][0][field]=value
    with pytest.raises(ValueError):validate_pdf_page_fragments(p,info())


@pytest.mark.parametrize("diagnostics", [{"WARN":"具体疑点"},{"review_required":True},
    {"review_required":False},{"review_required":"false"},{"WARN":"具体疑点","review_required":"true"}])
@experimental_pdf_only
def test_existing_typed_diagnostic_compatibility_preserves_incomplete_page(diagnostics):
    p=packet();p.update(diagnostics);p["page_complete"]=False;p["warnings"]=["原警告"]
    before=deepcopy(p);r=validate_pdf_page_fragments(p,info())
    assert r["layout"]["page_complete"] is False
    assert all("page_incomplete" in block["pending_reasons"] for block in r["fragments"]["blocks"])
    assert "待核对：原警告" in r["layout"]["notes"] and p==before


def test_v2_prompt_has_one_body_protocol_no_discarded_native_transcript_or_candidate_text():
    evidence=info(markdown="discarded-private-native",text_blocks=[{"text":"private-text-block"}],
        candidates=[{"id":"native","type":"raster","bbox":[100,200,300,400],
            "native_box_eligible":True,"text":"private-candidate-text"}])
    p=build_pdf_page_fragments_prompt(evidence)
    assert SCHEMA in p and '"blocks"' in p and '"markdown":' not in p
    assert all(value not in p for value in ("discarded-private-native","private-text-block","private-candidate-text"))
    assert "from_previous" in p and "to_next" in p and "不纠正原卷印刷错误" in p
    assert "不额外补换行" in p and "反斜杠" in p and "原点为左上" in p
    assert '"native_box_eligible": true' in p
    assert '"markdown":' in build_pdf_page_vision_prompt(evidence),"Existing v1 builder stays available"


def test_v2_prompt_prioritizes_unnumbered_top_text_answer_domain_and_bottom_coverage():
    prompt = build_pdf_page_fragments_prompt(info())
    priority = prompt[:prompt.index(COMMON_OCR_PROMPT)]
    assert prompt.startswith("【本页完整性优先】")
    assert "页首到第一个新题号之间" in priority
    assert "无号的半句、公式和原解续文也须独立保留" in priority
    assert "number=null" in priority and "from_previous或unknown" in priority
    assert "题号本身不代表stem" in priority and "评分过程仍是answer" in priority
    assert "题干中要求证明仍是stem" in priority and "角色不确定用unknown保留" in priority
    assert "顶部、中段、底部是否漏抄" in priority and "to_next/both" in priority
    assert "不猜补后文" in priority


def test_v2_protocol_example_has_no_fixed_question_number_or_asserted_question_role():
    prompt = build_pdf_page_fragments_prompt(info())
    example, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"schema":'):])
    assert example["schema"] == SCHEMA and len(example["blocks"]) == 1
    assert example["blocks"][0]["role"] == "unknown"
    assert example["blocks"][0]["number"] is None
    assert example["blocks"][0]["edge"] == "unknown"
    assert number_prefix(example["blocks"][0]["text"]) == (None, None)
    assert "不能复制示例内容、角色、题号或坐标" in prompt


def test_v2_completeness_checklist_does_not_enter_v1_or_region_protocols():
    old_page = build_pdf_page_vision_prompt(info())
    region = build_pdf_region_vision_prompt([{"id": "r1", "page_info": info()}])
    for prompt in (old_page, region):
        assert prompt.startswith(PDF_ORIGINAL_COMMON_OCR_PROMPT)
        assert "【本页完整性优先】" not in prompt
        assert SCHEMA not in prompt and '"blocks":' not in prompt


def test_strict_raw_json_boundary_rejects_duplicate_keys_and_truncated_fragment_text():
    raw=json.dumps(packet(),ensure_ascii=False)
    assert validate_pdf_page_fragments(parse_review_response(raw),info())["markdown"]==block()["text"]
    with pytest.raises(ValueError):parse_review_response(raw[:-1]+',"blocks":[]}')
    with pytest.raises(ValueError):parse_review_response(raw[:-4])
