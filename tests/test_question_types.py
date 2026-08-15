import pytest

from mathbank.question_types import (
    detect_structured_question_form,
    normalize_ai_question_form,
)


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (r"答案为\fillin", "fill_in_blank"),
        (r"答案为\FILLIN", "fill_in_blank"),
        (r"\begin{choices}\item A\end{choices}", "choice"),
        (r"\begin { choices }\item A\end{choices}", "choice"),
        ("请证明该结论", None),
    ),
)
def test_structured_question_form_detection(content, expected):
    assert detect_structured_question_form(content) == expected


def test_choices_environment_takes_precedence_over_blank_inside_an_option():
    content = r"\begin{choices}\item \fillin\item 2\end{choices}"

    assert detect_structured_question_form(content) == "choice"


@pytest.mark.parametrize(
    ("model_value", "expected"),
    (
        ("single_choice", "choice"),
        ("multi_choice", "choice"),
        ("单选题", "choice"),
        ("多选题", "choice"),
        ("fill_in_blank", "fill_in_blank"),
        ("detailed_answer", "detailed_answer"),
        ("unexpected", "unknown"),
        (None, "unknown"),
    ),
)
def test_ai_question_form_never_preserves_single_or_multi_choice(model_value, expected):
    assert normalize_ai_question_form(model_value) == expected
