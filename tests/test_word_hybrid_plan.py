"""Hybrid source groups require complete native evidence and private ownership."""

from copy import deepcopy
from dataclasses import asdict, replace
from io import BytesIO
import hashlib
import json
from xml.sax.saxutils import escape
import zipfile
from PIL import Image

import pytest

from mathbank.docx_helper import extract_docx_markdown, _new_diagnostics
from mathbank.word_hybrid_plan import (
    MAX_GROUPS, WordHybridPlanError, build_word_hybrid_plan,
    require_word_hybrid_plan, word_hybrid_plan_diagnostics,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
BAD = '<w:p><w:r><w:t>2. 求未知式的值：</w:t></w:r><m:oMath><m:unsupportedHybridFixture><m:r><m:t>x+1</m:t></m:r></m:unsupportedHybridFixture></m:oMath></w:p>'


def paragraph(value):
    return '<w:p><w:r><w:t>' + escape(value) + '</w:t></w:r></w:p>'


def package(body, *, relationships="", image=None):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>{body}</w:body></w:document>')
        if relationships:
            archive.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+relationships+'</Relationships>')
        if image is not None:
            archive.writestr("word/media/figure.png", image)
    return stream.getvalue()


def native(tmp_path, body=None):
    if body is None:
        body = paragraph('1. 已知二次函数$f(x)=x^2$，求$f(3)$。' + '甲题的独立说明。' * 30) + BAD + paragraph(r'3. 设集合$A=\{1,2,3\}$，写出元素个数。' + '丙题的独立说明。' * 30)
    blob = package(body)
    result = extract_docx_markdown(blob, output_dir=tmp_path, include_source_review_evidence=True)
    assert result["success"]
    return blob, result


def planned(blob, result, **kwargs):
    return build_word_hybrid_plan(result["markdown"], result["diagnostics"], task_id="hybrid-test", generation=7,
        source_document_sha256=hashlib.sha256(blob).hexdigest(), source_review_evidence=result["_source_review_evidence"], **kwargs)


def test_low_native_fraction_returns_original_without_metadata_dispatch(tmp_path):
    body = (paragraph('1. 独立甲题。' + '甲题独立说明。' * 40)
            + BAD.replace('2. 求未知式的值：', '2. 求未知式的值：' + '本题独立正文。' * 200)
            + paragraph('3. 独立丙题。' + '丙题独立说明。' * 40))
    blob, result = native(tmp_path, body)
    before = planned(blob, result)
    assert before['mode'] == 'hybrid'
    plan = planned(blob, result, min_metadata_source_fraction=0.5)
    assert plan['mode'] == 'original' and plan['groups'] == [] and plan['request_plan'] == []
    assert 'metadata_reuse_below_mixed_character_fraction_policy' in plan['global_reasons']
    assert 0 < plan['reuse_policy']['metadata_source_fraction'] < 0.5
    assert plan['reuse_policy']['is_token_measurement'] is False
    assert result['markdown'] == before['source'] == plan['source']
    require_word_hybrid_plan(plan, task_id='hybrid-test', generation=7)


def test_native_fraction_threshold_is_inclusive_and_signed(tmp_path):
    import math
    blob, result = native(tmp_path)
    first = planned(blob, result)
    ratio = first['reuse_policy']['metadata_source_fraction']
    plan = planned(blob, result, min_metadata_source_fraction=ratio)
    assert plan['mode'] == 'hybrid'
    assert planned(blob, result, min_metadata_source_fraction=math.nextafter(ratio, 1.0))['mode'] == 'original'
    plan['reuse_policy']['metadata_source_fraction'] = 1.0
    with pytest.raises(WordHybridPlanError):
        require_word_hybrid_plan(plan, task_id='hybrid-test', generation=7)


@pytest.mark.parametrize('fraction', [True, -0.1, 1.1, float('nan'), float('inf')])
def test_native_fraction_threshold_rejects_invalid_controls(tmp_path, fraction):
    blob, result = native(tmp_path)
    with pytest.raises(WordHybridPlanError):
        planned(blob, result, min_metadata_source_fraction=fraction)


def test_located_native_risk_has_one_mixed_request_and_two_certified_independent_groups(tmp_path):
    blob, result = native(tmp_path)
    original_diag = deepcopy(result["diagnostics"])
    plan = planned(blob, result)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    assert [(group["source_numbers"], group["route"]) for group in plan["groups"]] == [([1], "metadata"), ([2], "split"), ([3], "metadata")]
    assert len(plan["request_plan"]) == 1 and plan["request_plan"][0]["max_posts"] == 1
    assert plan["fallback_policy"]["max_additional_posts"] == 1
    assert not plan["fallback_policy"]["repeat_completed_groups"]
    assert result["diagnostics"] == original_diag and result["diagnostics"]["review_required"] == 1
    require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)
    for group in plan["groups"]:
        if group["route"] == "metadata":
            private = group["_source_metadata_plan"]
            assert [q["id"] for q in private["questions"]] == group["question_ids"]
            assert private["original_source_sha256"] == plan["source_sha256"]
        else:
            assert "_source_metadata_plan" not in group
    with pytest.raises(TypeError): json.dumps(plan)


def test_positive_global_risk_without_private_scopes_never_gets_local_clean_certificates(tmp_path):
    _, result = native(tmp_path)
    diag = deepcopy(result["diagnostics"])
    plan = build_word_hybrid_plan(result["markdown"], diag, task_id="hybrid-test", generation=7)
    assert plan["mode"] == "original" and plan["groups"] == []
    assert "positive_global_diagnostics_unlocated" in plan["global_reasons"]
    assert diag == result["diagnostics"] and diag["review_required"] == 1
    require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


@pytest.mark.parametrize("fake", [{"status": "ready", "blocks": []}, True])
def test_public_scopes_or_flag_cannot_authorize_metadata_groups(tmp_path, fake):
    blob, result = native(tmp_path)
    result["_source_review_evidence"] = fake
    plan = planned(blob, result)
    assert plan["mode"] == "original" and "diagnostic_origin_unavailable" in plan["global_reasons"]


def test_tampered_evidence_cannot_sign_a_clean_subset(tmp_path):
    blob, result = native(tmp_path)
    evidence = result["_source_review_evidence"]
    payload = json.loads(evidence.payload)
    payload["blocks"][1]["has_risk"] = False
    result["_source_review_evidence"] = replace(evidence, payload=json.dumps(payload))
    assert planned(blob, result)["mode"] == "original"


def test_wrong_document_hash_and_changed_native_counter_invalidate_scopes(tmp_path):
    blob, result = native(tmp_path)
    other = build_word_hybrid_plan(result["markdown"], result["diagnostics"], task_id="hybrid-test", generation=7,
        source_document_sha256="0" * 64, source_review_evidence=result["_source_review_evidence"])
    assert other["mode"] == "original"
    result["diagnostics"]["review_required"] = 0
    assert planned(blob, result)["mode"] == "original"


def test_explicit_dependency_closure_is_one_risky_group_not_two_native_cards(tmp_path):
    body = paragraph('1. 设$f(x)=x^2$，求$f(3)$。') + paragraph('2. 根据第1题中的函数，求$f(4)$。') + paragraph(r'3. 设集合$A=\{1,2\}$，求元素个数。' + '独立集合说明。' * 40)
    blob, result = native(tmp_path, body)
    plan = planned(blob, result)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    assert [(g["source_numbers"], g["route"]) for g in plan["groups"]] == [([1, 2], "split"), ([3], "metadata")]


def test_unknown_previous_reference_does_not_use_nonexhaustive_report_as_partial_proof(tmp_path):
    blob, result = native(tmp_path, paragraph('1. 第一题。') + paragraph('2. 根据前面的图求值。') + paragraph('3. 独立第三题。'))
    plan = planned(blob, result)
    assert plan["mode"] == "original"
    assert "dependency_scope_not_exhaustive" in plan["global_reasons"] or "global_source_boundary_uncertain" in plan["global_reasons"]


@pytest.mark.parametrize("body", [
    paragraph('1. 第一题。') + paragraph('1. 第二题。'),
    paragraph('各题均使用下图。') + paragraph('1. 求值。') + paragraph('2. 求值。'),
    paragraph('1. 第一题文字2. 第二题文字。'),
])
def test_global_duplicate_unowned_context_or_heading_gaps_keep_whole_original(tmp_path, body):
    blob, result = native(tmp_path, body)
    assert planned(blob, result)["mode"] == "original"


def test_source_scope_risk_window_is_not_split_into_clean_origins(tmp_path):
    body = paragraph('1. 独立甲题。') + '<w:sdt><w:sdtContent>' + paragraph('2. 独立乙题。') + BAD.replace('2. 求未知式', '3. 求未知式') + '</w:sdtContent></w:sdt>' + paragraph('4. 独立丁题。' + '独立说明。' * 60)
    blob, result = native(tmp_path, body)
    plan = planned(blob, result)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    assert any(group["source_numbers"] == [2, 3] and group["route"] == "split" for group in plan["groups"])


def test_answers_in_reverse_section_order_have_disjoint_owned_spans_not_envelope_ownership(tmp_path):
    body = paragraph('一、解答题') + paragraph('1. 已知二次函数$f(x)=x^2$，求$f(3)$。') + BAD + paragraph(r'3. 设集合$A=\{1,2\}$，求元素个数。') + paragraph('参考答案：') + paragraph('3. $2$。') + paragraph('1. $9$。')
    blob, result = native(tmp_path, body)
    plan = planned(blob, result, min_metadata_source_characters=0)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    all_owned = [bounds for group in plan["groups"] for bounds in group["owned_ranges"]]
    assert len(all_owned) == len({tuple(bounds) for bounds in all_owned})
    first = plan["groups"][0]
    assert len(first["owned_ranges"]) == 2
    assert first["source_range"][1] > plan["groups"][1]["source_range"][0]
    require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


def test_no_certified_group_keeps_original_without_per_question_requests(tmp_path):
    blob, result = native(tmp_path, BAD + BAD.replace('2.', '3.'))
    plan = planned(blob, result)
    assert plan["mode"] == "original" and not plan["request_plan"]


@pytest.mark.parametrize("mutation", ["source", "route", "owned", "context", "qid", "budget", "generation"])
def test_hybrid_hmac_binds_source_routes_ranges_identity_and_request_budget(tmp_path, mutation):
    blob, result = native(tmp_path)
    plan = planned(blob, result)
    assert plan["mode"] == "hybrid"
    if mutation == "source": plan["source"] += "changed"
    elif mutation == "route": plan["groups"][0]["route"] = "split"
    elif mutation == "owned": plan["groups"][0]["owned_ranges"][0][0] += 1
    elif mutation == "context": plan["groups"][0]["context_ranges"] = [[0, 1]]
    elif mutation == "qid": plan["groups"][0]["question_ids"][0] = "known-but-wrong"
    elif mutation == "budget": plan["fallback_policy"]["max_additional_posts"] = 5
    else: plan["generation"] = 8
    with pytest.raises(WordHybridPlanError): require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


def test_json_hybrid_certificate_and_wrong_task_cannot_replay_plan(tmp_path):
    blob, result = native(tmp_path)
    plan = planned(blob, result)
    with pytest.raises(WordHybridPlanError): require_word_hybrid_plan(plan, task_id="other-task", generation=7)
    plan["_hybrid_plan_certificate"] = asdict(plan["_hybrid_plan_certificate"])
    with pytest.raises(WordHybridPlanError): require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


def test_metadata_private_projection_is_checked_after_the_hybrid_snapshot(tmp_path):
    blob, result = native(tmp_path)
    plan = planned(blob, result)
    plan["groups"][0]["_source_metadata_plan"]["questions"][0]["content"] += "altered"
    with pytest.raises(ValueError): require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


def test_hybrid_group_limit_and_low_native_reuse_policy_do_not_change_source(tmp_path):
    body = ''.join(BAD.replace('2. 求未知式', f'{i}. 求未知式') if i % 2 == 0
                   else paragraph(f'{i}. 独立问题{i}。') for i in range(1, MAX_GROUPS + 4))
    blob, result = native(tmp_path, body)
    assert planned(blob, result)["mode"] == "original"
    blob, result = native(tmp_path / 'small', paragraph('1. 独立甲题。') + BAD + paragraph('3. 独立丙题。'))
    plan = planned(blob, result)
    assert plan["mode"] == "original" and "native_reuse_below_character_policy" in plan["global_reasons"]


def test_adjacent_clean_questions_are_one_metadata_group_without_crossing_bad_question(tmp_path):
    body = paragraph('1. 求甲变量$x=2$。') + paragraph('2. 求乙变量$y=3$。') + BAD.replace('2. 求未知式', '3. 求未知式') + paragraph('4. 求丙变量$z=4$。') + paragraph('5. 求丁变量$t=5$。')
    blob, result = native(tmp_path, body)
    plan = planned(blob, result, min_metadata_source_characters=0)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    assert [(g["source_numbers"], g["route"]) for g in plan["groups"]] == [([1, 2], "metadata"), ([3], "split"), ([4, 5], "metadata")]
    require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)


def test_located_nonstandard_choices_do_not_taint_adjacent_certified_native_groups(tmp_path):
    body = paragraph('1. 已知甲变量$x=2$，求$x$。') + paragraph('2. 比较乙变量$y$。\nA. $1$ B. $2$ C. $3$') + paragraph('3. 已知丙变量$z=3$，求$z$。')
    blob, result = native(tmp_path, body)
    plan = planned(blob, result, min_metadata_source_characters=0)
    assert plan["mode"] == "hybrid", plan["global_reasons"]
    assert [(g["source_numbers"], g["route"]) for g in plan["groups"]] == [([1], "metadata"), ([2], "split"), ([3], "metadata")]


def test_whole_clean_source_keeps_existing_fast_path_and_content_free_diagnostics(tmp_path):
    blob, result = native(tmp_path, paragraph('1. 已知二次函数$f(x)=x^2$，求$f(3)$。'))
    plan = planned(blob, result)
    assert plan["mode"] == "whole_metadata"
    require_word_hybrid_plan(plan, task_id="hybrid-test", generation=7)
    diagnostic = word_hybrid_plan_diagnostics(plan)
    assert result["markdown"] not in json.dumps(diagnostic, ensure_ascii=False)
    assert diagnostic["primary_posts"] == 1


def figure_source(tmp_path, *, incomplete_anchor=False):
    vertical = '' if incomplete_anchor else '<wp:positionV relativeFrom="paragraph"><wp:posOffset>400</wp:posOffset></wp:positionV>'
    drawing = ('<w:r><w:drawing><wp:anchor simplePos="0" behindDoc="0">'
        '<wp:positionH relativeFrom="column"><wp:posOffset>700</wp:posOffset></wp:positionH>' + vertical +
        '<wp:extent cx="1600" cy="1600"/><wp:docPr id="1" name="Picture 1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:blipFill><a:blip r:embed="img"/></pic:blipFill></pic:pic>'
        '</a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>')
    body = paragraph('12. 已知$x=2$，求值。' + '独立完整说明。' * 50) + '<w:p>'+drawing+'<w:r><w:t>13. 在数列中求值。</w:t></w:r></w:p>' + paragraph('14. 如图，求长度。') + paragraph('15. 给定$y=3$，求值。')
    png = BytesIO(); Image.new('RGB', (8, 8), 'red').save(png, format='PNG')
    blob = package(body, relationships=f'<Relationship Id="img" Type="{R}/image" Target="media/figure.png"/>', image=png.getvalue())
    result = extract_docx_markdown(blob, output_dir=tmp_path, include_source_review_evidence=True)
    return blob, result


def test_proven_neighbor_figure_veto_closes_both_13_and_14_into_one_risky_group(tmp_path):
    blob, result = figure_source(tmp_path)
    plan = planned(blob, result, min_metadata_source_characters=0)
    assert plan['mode'] == 'hybrid', plan['global_reasons']
    assert any({13, 14}.issubset(g['source_numbers']) and g['route'] == 'split' for g in plan['groups'])
    # The retained unplaced image falls inside the previous raw stem's range.
    # That stem must also remain risky; never remove its URL to certify it.
    assert any(g['source_numbers'] == [12, 13, 14] and g['route'] == 'split' for g in plan['groups'])
    assert any(g['source_numbers'] == [15] and g['route'] == 'metadata' for g in plan['groups'])


def test_incomplete_anchor_is_not_localized_merely_because_related_numbers_exist(tmp_path):
    blob, result = figure_source(tmp_path, incomplete_anchor=True)
    assert result['diagnostics']['heading_image_ownership'][0]['reason'] not in {'following_question_refers_unplaced_figure','rendered_heading_not_safely_separable'}
    plan = planned(blob, result)
    assert plan['mode'] == 'original'
    assert 'native_image_ownership_unknown' in plan['global_reasons']


def test_scope_only_floating_table_does_not_take_clean_global_counter_fast_path(tmp_path):
    table = '<w:tbl><w:tblPr><w:tblpPr w:vertAnchor="page" w:tblpY="400"/></w:tblPr><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc>'+paragraph('数据')+'</w:tc></w:tr></w:tbl>'
    blob, result = native(tmp_path, paragraph('1. 根据数据表完成本题。') + table + paragraph('2. 给定$x=2$，求值。'))
    assert result['diagnostics']['review_required'] == 0
    plan = planned(blob, result)
    assert plan['mode'] == 'original'
    assert 'native_structural_ownership_unknown' in plan['global_reasons']


def test_request_context_cannot_be_changed_through_unsigned_inspection_cache(tmp_path):
    blob, result = native(tmp_path)
    plan = planned(blob, result)
    plan['_inspection']['document_metadata'].append({'text':'changed source-grade context'})
    with pytest.raises(WordHybridPlanError): require_word_hybrid_plan(plan, task_id='hybrid-test', generation=7)
