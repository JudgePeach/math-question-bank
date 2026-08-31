from dataclasses import replace
import re

import pytest

import mathbank.question_duplicates as duplicate_module
from mathbank.question_duplicates import (
    DuplicateComparison,
    EMPTY_TEXT_BAND,
    FINGERPRINT_VERSION,
    QuestionDuplicateInput,
    QuestionFingerprint,
    build_question_fingerprint,
    compare_question_fingerprints,
    compare_questions,
    compute_text_fragment_bands,
    extract_choices,
    extract_critical_math_tokens,
    find_batch_duplicate_groups,
    normalize_math_text,
    simhash_bands,
)


def fingerprint(content: str, **kwargs) -> QuestionFingerprint:
    return build_question_fingerprint(QuestionDuplicateInput(content, **kwargs))


def test_conservative_normalization_removes_only_presentation_noise():
    left = fingerprint(
        "已知\u200b$x \\leqslant 1$，求值。\r\n"
        r"\begin{choices}\item A. $\dfrac{1}{2}$\item B. $1$\end{choices}"
        "\n![原图](/static/uploads/random-a.png)"
    )
    right = fingerprint(
        "已知 \\( \\left x \\le 1 \\right \\)，求值。\n"
        r"\begin{choices}\item $\frac{1}{2}$\item $1$\end{choices}"
        "\n![](</static/uploads/another-random-name.jpg>)"
    )

    assert left.exact_hash == right.exact_hash
    assert left.critical_math_hash == right.critical_math_hash
    assert left.content_revision_hash == right.content_revision_hash
    comparison = compare_question_fingerprints(left, right)
    # The figure path is intentionally ignored, but no pixel signature exists.
    assert comparison.classification == "probable"
    assert comparison.needs_visual_review is True
    assert "SAFE_NORMALIZATION_ONLY" in comparison.reasons
    assert "VISUAL_SIGNATURE_PENDING" in comparison.reasons


def test_numbers_variables_relations_quantifiers_and_interval_brackets_survive():
    base = fingerprint(r"对任意 $x\in[0,1)$，都有 $x<2$。")
    cases = (
        (r"对任意 $x\in[0,1)$，都有 $x<3$。", "NUMBER_CHANGED"),
        (r"对任意 $y\in[0,1)$，都有 $y<2$。", "VARIABLE_CHANGED"),
        (r"对任意 $x\in[0,1)$，都有 $x\le 2$。", "RELATION_CHANGED"),
        (r"存在 $x\in[0,1)$，都有 $x<2$。", "QUANTIFIER_CHANGED"),
        (r"对任意 $x\in(0,1)$，都有 $x<2$。", "MATH_STRUCTURE_CHANGED"),
    )

    for content, expected_reason in cases:
        candidate = fingerprint(content)
        assert candidate.exact_hash != base.exact_hash
        assert candidate.critical_math_hash != base.critical_math_hash
        comparison = compare_question_fingerprints(base, candidate)
        assert comparison.classification == "possible_variant"
        assert expected_reason in comparison.reasons
        assert comparison.auto_merge_allowed is False


@pytest.mark.parametrize(
    ("left_term", "right_term", "expected_reason"),
    (
        ("八月完成", "九月完成", "NUMBER_CHANGED"),
        ("一元函数", "二元函数", "NUMBER_CHANGED"),
        ("三本资料", "四本资料", "NUMBER_CHANGED"),
        ("不存在实数解", "存在实数解", "QUANTIFIER_CHANGED"),
        ("至少有一个解", "至多有一个解", "QUANTIFIER_CHANGED"),
        ("不超过给定值", "不少于给定值", "RELATION_CHANGED"),
        ("不大于零", "不小于零", "RELATION_CHANGED"),
        ("大于等于零", "小于等于零", "RELATION_CHANGED"),
        ("等于零", "不等于零", "RELATION_CHANGED"),
        ("大于零", "小于零", "RELATION_CHANGED"),
        ("正数", "负数", "RELATION_CHANGED"),
        ("非正", "非负", "RELATION_CHANGED"),
        ("为正", "为负", "RELATION_CHANGED"),
        ("单调递增", "单调递减", "OPERATOR_CHANGED"),
        ("递增", "递减", "OPERATOR_CHANGED"),
        ("增加", "减少", "OPERATOR_CHANGED"),
        ("上升", "下降", "OPERATOR_CHANGED"),
        ("最大", "最小", "OPERATOR_CHANGED"),
        ("奇数", "偶数", "OPERATOR_CHANGED"),
    ),
)
def test_chinese_semantic_changes_are_variants_under_long_common_context(
    left_term, right_term, expected_reason
):
    prefix = (
        "在一次数学建模活动中，老师要求学生根据给出的完整背景材料和已知条件，"
        "判断目标对象满足的核心性质为"
    )
    suffix = "，并结合定义写出严谨、完整且可以逐步核对的证明理由。"

    comparison = compare_questions(
        {"content": prefix + left_term + suffix},
        {"content": prefix + right_term + suffix},
    )

    assert comparison.classification == "possible_variant"
    assert expected_reason in comparison.reasons
    assert comparison.auto_merge_allowed is False


def test_chinese_semantic_matching_prefers_longest_words_without_bare_signs():
    tokens = extract_critical_math_tokens(
        normalize_math_text("命题不存在实数解，但这份正确结论由负责的学生给出。")
    )

    assert "不存在" in tokens
    assert "存在" not in tokens
    assert "正" not in tokens
    assert "负" not in tokens

    relation_tokens = extract_critical_math_tokens(
        normalize_math_text("函数值不等于零，并且大于等于给定常数。")
    )
    assert "不等于" in relation_tokens
    assert "大于等于" in relation_tokens
    assert "等于" not in relation_tokens
    assert "大于" not in relation_tokens


def test_answer_change_invalidates_snapshot_without_changing_duplicate_identity():
    left = fingerprint("同一题干 $x=1$。", answer_markdown="解析甲")
    right = fingerprint("同一题干 $x=1$。", answer_markdown="解析乙")

    assert left.exact_hash == right.exact_hash
    assert left.content_revision_hash != right.content_revision_hash
    comparison = compare_question_fingerprints(left, right)
    assert comparison.classification == "exact"
    assert "ANSWER_DIFF" in comparison.reasons


def test_answer_asset_change_invalidates_snapshot_without_affecting_prompt_identity():
    left = fingerprint(
        "同一题干 $x=1$。",
        answer_markdown="![答案图](/static/uploads/a.png)",
        answer_asset_signatures=("pixel:red",),
    )
    right = fingerprint(
        "同一题干 $x=1$。",
        answer_markdown="![答案图](/static/uploads/b.png)",
        answer_asset_signatures=("pixel:blue",),
    )

    assert left.exact_hash == right.exact_hash
    assert left.content_revision_hash != right.content_revision_hash
    assert "ANSWER_DIFF" in compare_question_fingerprints(left, right).reasons


def test_explicit_latex_quantifiers_are_not_collapsed():
    every = fingerprint(r"$\forall x\in R, x^2\ge 0$")
    exists = fingerprint(r"$\exists x\in R, x^2\ge 0$")

    result = compare_question_fingerprints(every, exists)

    assert result.classification == "possible_variant"
    assert "QUANTIFIER_CHANGED" in result.reasons


def test_short_formula_with_changed_number_is_a_variant_not_a_duplicate():
    result = compare_questions({"content": r"求 $x=1$"}, {"content": r"求 $x=2$"})

    assert result.classification == "possible_variant"
    assert "NUMBER_CHANGED" in result.reasons
    assert result.auto_merge_allowed is False


def test_raw_percent_is_question_content_not_a_tex_comment():
    new_count = fingerprint("甲班人数增加50%，求新人数。")
    growth_rate = fingerprint("甲班人数增加50%，求增长率。")
    escaped_percent = fingerprint(r"甲班人数增加50\%，求新人数。")

    assert new_count.exact_hash != growth_rate.exact_hash
    assert new_count.exact_hash == escaped_percent.exact_hash


def test_choices_extraction_preserves_order_and_ignores_nested_items():
    stem, choices = extract_choices(
        r"题干\begin{choices}"
        r"\item[A] A. $x=1$"
        r"\item B. \begin{enumerate}\item 内层一\item 内层二\end{enumerate} $x=2$"
        r"\end{choices}结尾"
    )

    assert "题干" in stem and "结尾" in stem
    assert choices == (
        "$x=1$",
        r"\begin{enumerate}\item 内层一\item 内层二\end{enumerate} $x=2$",
    )


def test_option_order_change_is_probable_not_exact():
    left = fingerprint(
        r"方程的解是\begin{choices}\item $1$\item $2$\item $3$\end{choices}"
    )
    right = fingerprint(
        r"方程的解是\begin{choices}\item $2$\item $1$\item $3$\end{choices}"
    )

    result = compare_question_fingerprints(left, right)

    assert left.ordered_choices_hash != right.ordered_choices_hash
    assert left.unordered_choices_hash == right.unordered_choices_hash
    assert result.classification == "probable"
    assert "OPTION_ORDER_CHANGED" in result.reasons
    assert result.auto_merge_allowed is False


def test_answer_difference_is_explanation_only():
    left = fingerprint("求 $x+1$ 的值。", answer_markdown="答案：$2$\n\n解析甲")
    right = fingerprint("求 $x+1$ 的值。", answer_markdown="答案：$2$  解析乙")

    result = compare_question_fingerprints(left, right)

    assert left.exact_hash == right.exact_hash
    assert left.answer_hash != right.answer_hash
    assert result.classification == "exact"
    assert "ANSWER_DIFF" in result.reasons


def test_answer_whitespace_does_not_change_answer_hash():
    left = fingerprint("同题", answer_markdown="第一行\n第二行")
    right = fingerprint("同题", answer_markdown=" 第一行   第二行 ")

    assert left.answer_hash == right.answer_hash


def test_question_type_difference_is_not_a_hard_exclusion():
    left = fingerprint("同一道题 $x=1$", question_type="single_choice")
    right = fingerprint("同一道题 $x=1$", question_type="multi_choice")

    result = compare_question_fingerprints(left, right)

    assert result.classification == "exact"
    assert "TYPE_DIFF" in result.reasons


def test_visible_assets_gate_exact_classification():
    exact_left = fingerprint(
        "如图求解。![](a.png)", visible_image_signatures=("sha256:aaa",)
    )
    exact_right = fingerprint(
        "如图求解。![](b.jpg)", visible_image_signatures=("sha256:aaa",)
    )
    changed = fingerprint(
        "如图求解。![](c.webp)", visible_image_signatures=("sha256:bbb",)
    )

    exact_result = compare_question_fingerprints(exact_left, exact_right)
    changed_result = compare_question_fingerprints(exact_left, changed)

    assert exact_result.classification == "exact"
    assert exact_result.needs_visual_review is False
    assert "FIGURE_EXACT" in exact_result.reasons
    assert changed_result.classification == "probable"
    assert changed_result.needs_visual_review is True
    assert "FIGURE_DIFF" in changed_result.reasons


def test_opaque_asset_signatures_remain_case_sensitive():
    upper = fingerprint(
        "如图求解。![](a.png)", tikz_signatures=("tikz:PointA",)
    )
    lower = fingerprint(
        "如图求解。![](b.png)", tikz_signatures=("tikz:pointa",)
    )

    result = compare_question_fingerprints(upper, lower)

    assert result.classification == "probable"
    assert result.needs_visual_review is True
    assert "TIKZ_DIFF" in result.reasons


def test_missing_figure_never_becomes_exact():
    with_figure = fingerprint("如图求解。![](a.png)")
    without_figure = fingerprint("如图求解。")

    result = compare_question_fingerprints(with_figure, without_figure)

    assert result.classification != "exact"
    assert result.auto_merge_allowed is False


def test_fingerprint_is_deterministic_serializable_and_has_eight_bands():
    original = fingerprint(
        r"已知 $x^2=1$，求 $x$。",
        answer_markdown=r"解：$x=\pm1$",
        visible_image_signatures=("sha256:abc",),
        tikz_signatures=("tikz:def",),
    )
    rebuilt = QuestionFingerprint.from_mapping(original.to_dict())

    assert rebuilt == original
    assert rebuilt.snapshot_hash == rebuilt.content_revision_hash
    assert rebuilt.critical_math_tokens == rebuilt.critical_tokens
    assert original.fingerprint_version == FINGERPRINT_VERSION == 3
    assert len(original.simhash_hex) == 32
    assert original.bands == simhash_bands(original.simhash_hex)
    assert all(0 <= band <= 65535 for band in original.bands)
    assert len(original.text_bands) == 8
    assert all(re.fullmatch(r"[0-9a-f]{16}", band) for band in original.text_bands)
    assert build_question_fingerprint(
        QuestionDuplicateInput(
            r"已知 $x^2=1$，求 $x$。",
            answer_markdown=r"解：$x=\pm1$",
            visible_image_signatures=("sha256:abc",),
            tikz_signatures=("tikz:def",),
        )
    ) == original


def test_text_fragment_bands_are_stable_and_validated():
    expected = (
        "ffffa532292effff",
        "ffffaec6ffffffff",
        "bc81ffffffffffff",
        "ffffae0cffffffff",
        "ffff5e39ffff8a29",
        "ffffffffffffffff",
        "fbe8ffffffffffff",
        "fffff46e5ae3ffff",
    )

    assert compute_text_fragment_bands("甲乙丙丁戊己庚辛") == expected
    assert compute_text_fragment_bands("甲乙丙丁戊己庚辛") == expected
    with pytest.raises(ValueError, match="16 lowercase hex"):
        replace(fingerprint("稳定性测试题"), text_bands=("INVALID",) * 8)


def test_text_fragment_oph_hashes_each_unique_feature_once(monkeypatch):
    real_sha256 = duplicate_module.hashlib.sha256
    calls = {"count": 0}

    def counted_sha256(*args, **kwargs):
        calls["count"] += 1
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(duplicate_module.hashlib, "sha256", counted_sha256)

    # Six unique tokens produce three literal and three skeleton 4-grams.
    bands = compute_text_fragment_bands("甲乙丙丁戊己")

    assert len(bands) == 8
    assert calls["count"] == 6


def test_text_skeleton_recalls_month_and_number_variant_without_weakening_math():
    august = fingerprint(
        "八月计划第一天记忆 $50$ 个单词，随后每天复习并增加新单词。"
    )
    september = fingerprint(
        "九月计划第一天记忆 $60$ 个单词，随后每天复习并增加新单词。"
    )

    shared = (set(august.text_bands) & set(september.text_bands)) - {
        EMPTY_TEXT_BAND
    }
    comparison = compare_question_fingerprints(august, september)
    scan = find_batch_duplicate_groups((august, september))

    assert shared
    assert august.exact_hash != september.exact_hash
    assert comparison.classification == "possible_variant"
    assert "NUMBER_CHANGED" in comparison.reasons
    assert comparison.auto_merge_allowed is False
    assert scan.candidate_pair_count == 1
    assert scan.groups[0].strongest_classification == "possible_variant"


def test_real_52_53_style_suffix_variant_is_recalled_by_text_band():
    longer = fingerprint(
        "某位同学暑假期间要在八月上旬完成一定量的英语单词的记忆，"
        "计划是：第一天记忆 $300$ 个单词，第一天后的每一天，在复习"
        "前面记忆过的单词的基础上增加 $50$ 个新单词的记忆量．则该"
        "同学记忆的单词总个数 $y$ 与记忆天数 $x$ 的函数关系式为\n\n"
        r"\fillin．"
    )
    suffix = fingerprint(
        "第一天后的每一天，在复习前面记忆过的单词的基础上增加 $50$ "
        "个新单词的记忆量．则该同学记忆的单词总个数 $y$ 与记忆天数 "
        "$x$ 的函数关系式为\n\n"
        r"\fillin．"
    )

    shared = (set(longer.text_bands) & set(suffix.text_bands)) - {
        EMPTY_TEXT_BAND
    }
    scan = find_batch_duplicate_groups((longer, suffix))

    assert set(longer.bands).isdisjoint(suffix.bands)
    assert shared
    assert scan.candidate_pair_count == 1
    assert scan.compared_pair_count == 1
    assert scan.groups[0].member_indexes == (0, 1)
    assert scan.groups[0].strongest_classification == "possible_variant"
    reasons = scan.groups[0].comparisons[0].comparison.reasons
    assert "NUMBER_CHANGED" in reasons


def test_input_mapping_and_comparison_are_api_friendly():
    payload = {
        "content": "同题 $x=1$",
        "answer_markdown": "答案",
        "question_type": "detailed_answer",
        "visible_image_signatures": [],
        "tikz_signatures": [],
        "answer_asset_signatures": [],
    }

    question = QuestionDuplicateInput.from_mapping(payload)
    result = compare_questions(payload, question)

    assert question.to_dict() == payload
    assert result.to_dict()["classification"] == "exact"
    assert result.to_dict()["level"] == "exact"
    assert result.level == "exact"
    assert result.to_dict()["auto_merge_allowed"] is False
    assert result.to_dict()["requires_human_review"] is True


def test_duplicate_comparison_rejects_any_auto_merge_flag():
    with pytest.raises(ValueError, match="never auto-merge"):
        DuplicateComparison("exact", 1.0, ("EXACT_TEXT",), False, True)


def test_batch_exact_groups_need_only_linear_chain_pairs():
    duplicate = fingerprint("完全相同的题目 $x=1$")
    fingerprints = [duplicate, duplicate, duplicate, fingerprint("另一道题 $y=9$")]

    scan = find_batch_duplicate_groups(fingerprints)

    assert scan.exact_group_count == 1
    assert scan.candidate_pair_count == 2
    assert scan.compared_pair_count == 2
    assert len(scan.groups) == 1
    assert scan.groups[0].member_indexes == (0, 1, 2)
    assert scan.groups[0].strongest_classification == "exact"


def test_batch_text_fallback_skips_items_with_primary_review(monkeypatch):
    prefix = "在一个足够长的共同背景中，需要依据给定条件完成计算并写出完整理由，已知 "
    contents = (
        prefix + "$x=1$。",
        prefix + "$x=2$。",
        prefix + "$y=3$。",
        prefix + "$y=4$。",
    )
    fingerprints = []
    for index, content in enumerate(contents):
        item = fingerprint(content)
        primary_group = 100 if index < 2 else 200
        controlled_simhash = (primary_group,) + tuple(
            1000 + index * 10 + band for band in range(1, 8)
        )
        fingerprints.append(
            replace(
                item,
                bands=controlled_simhash,
                text_bands=("1111111111111111",)
                + (EMPTY_TEXT_BAND,) * 7,
            )
        )

    real_compare = duplicate_module.compare_question_fingerprints
    calls = {"count": 0}

    def counted_compare(*args, **kwargs):
        calls["count"] += 1
        return real_compare(*args, **kwargs)

    monkeypatch.setattr(
        duplicate_module, "compare_question_fingerprints", counted_compare
    )

    scan = find_batch_duplicate_groups(fingerprints)

    assert calls["count"] == 2
    assert scan.candidate_pair_count == 2
    assert scan.compared_pair_count == 2
    assert [group.member_indexes for group in scan.groups] == [(0, 1), (2, 3)]


def test_batch_text_fallback_runs_after_primary_candidates_refine_to_none(
    monkeypatch,
):
    prefix = "在一个足够长的共同背景中，需要依据给定条件完成计算并写出完整理由，已知 "
    first = fingerprint(prefix + "$x=1$。")
    unrelated = fingerprint("空间中有三个互相垂直的平面，判断直线的位置关系。")
    variant = fingerprint(prefix + "$x=2$。")
    fingerprints = (
        replace(
            first,
            bands=(100, 1001, 1002, 1003, 1004, 1005, 1006, 1007),
            text_bands=("2222222222222222",) + (EMPTY_TEXT_BAND,) * 7,
        ),
        replace(
            unrelated,
            bands=(100, 2001, 2002, 2003, 2004, 2005, 2006, 2007),
            text_bands=("3333333333333333",) + (EMPTY_TEXT_BAND,) * 7,
        ),
        replace(
            variant,
            bands=(300, 3001, 3002, 3003, 3004, 3005, 3006, 3007),
            text_bands=("2222222222222222",) + (EMPTY_TEXT_BAND,) * 7,
        ),
    )
    real_compare = duplicate_module.compare_question_fingerprints
    calls = {"count": 0}

    def counted_compare(*args, **kwargs):
        calls["count"] += 1
        return real_compare(*args, **kwargs)

    monkeypatch.setattr(
        duplicate_module, "compare_question_fingerprints", counted_compare
    )

    scan = find_batch_duplicate_groups(fingerprints)

    assert calls["count"] == 2
    assert scan.candidate_pair_count == 2
    assert scan.groups[0].member_indexes == (0, 2)
    assert scan.groups[0].strongest_classification == "possible_variant"
    assert "NUMBER_CHANGED" in scan.groups[0].comparisons[0].comparison.reasons


def test_batch_two_stages_share_one_per_item_budget_and_report_drops():
    prefix = "在一个足够长的共同背景中，需要依据给定条件完成计算并写出完整理由，已知 "
    first = fingerprint(prefix + "$x=1$。")
    unrelated = fingerprint("空间中有三个互相垂直的平面，判断直线的位置关系。")
    variant = fingerprint(prefix + "$x=2$。")
    fingerprints = (
        replace(
            first,
            bands=(100, 1001, 1002, 1003, 1004, 1005, 1006, 1007),
            text_bands=("2222222222222222",) + (EMPTY_TEXT_BAND,) * 7,
        ),
        replace(
            unrelated,
            bands=(100, 2001, 2002, 2003, 2004, 2005, 2006, 2007),
            text_bands=("3333333333333333",) + (EMPTY_TEXT_BAND,) * 7,
        ),
        replace(
            variant,
            bands=(300, 3001, 3002, 3003, 3004, 3005, 3006, 3007),
            text_bands=("2222222222222222",) + (EMPTY_TEXT_BAND,) * 7,
        ),
    )

    scan = find_batch_duplicate_groups(
        fingerprints,
        max_candidates_per_item=1,
    )

    assert scan.candidate_pair_count == 1
    assert scan.compared_pair_count == 1
    assert scan.dropped_candidate_pair_count == 1
    assert scan.index_complete is False


def test_unrelated_large_batch_is_not_compared_all_pairs():
    # Unique explicit band values make the no-all-pairs property deterministic;
    # a 400-item all-pairs implementation would compare 79,800 pairs.
    fingerprints = []
    for index in range(400):
        item = fingerprint(f"题目 {index}：计算 $x+{index}$。")
        unique_bands = tuple(index * 8 + band for band in range(8))
        unique_text_bands = tuple(
            f"{index * 8 + band:016x}" for band in range(8)
        )
        fingerprints.append(
            replace(
                item,
                bands=unique_bands,
                text_bands=unique_text_bands,
            )
        )

    scan = find_batch_duplicate_groups(fingerprints)

    assert scan.candidate_pair_count == 0
    assert scan.compared_pair_count == 0
    assert scan.groups == ()
    assert scan.index_complete is True


def test_broad_lsh_bucket_is_bounded_and_disclosed():
    fingerprints = []
    for index in range(10):
        item = fingerprint(f"题目 {index}：$x={index}$。")
        fingerprints.append(
            replace(
                item,
                bands=(0,) * 8,
                text_bands=(EMPTY_TEXT_BAND,) * 8,
            )
        )

    scan = find_batch_duplicate_groups(fingerprints, max_bucket_size=5)

    assert scan.candidate_pair_count == 0
    assert scan.truncated_bucket_count == 8
    assert scan.index_complete is False


def test_malformed_choices_environment_is_kept_conservatively():
    content = r"题干\begin{choices}\item $1$\item $2$"

    stem, choices = extract_choices(content)

    assert stem == content
    assert choices == ()


def test_math_normalizer_does_not_join_distinct_variables():
    assert normalize_math_text("$x y$") != normalize_math_text("$xy$")
