import json
from typing import get_type_hints

import pytest

from mathbank.ai_json import parse_ai_json
from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    build_ai_solve_prompts,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_pdf_parse_system_prompt,
)


def test_formula_producing_prompts_share_context_aware_fraction_rule():
    curriculum = {"必修一": {"1. 集合": []}}
    prompts = [
        COMMON_OCR_PROMPT,
        build_pdf_parse_system_prompt(curriculum, False),
        build_pdf_parse_system_prompt(curriculum, True),
        build_import_parse_system_prompt(curriculum),
    ]
    for question_type in (
        "single_choice", "multi_choice", "fill_in_blank", "detailed_answer"
    ):
        system_prompt, user_prompt = build_ai_solve_prompts(
            question_type, r"求 $\frac{x+1}{x-1}$"
        )
        prompts.append(system_prompt)
        assert user_prompt.endswith(r"求 $\frac{x+1}{x-1}$")

    for prompt in prompts:
        assert prompt.count("【分式】") == 1
        assert r"主体分式用 `\dfrac`" in prompt
        assert r"上标（含指数）、下标和嵌套内层分式用 `\frac`" in prompt
        assert r"$2^{\frac{n+1}{2}}$" in prompt
        assert r"$\dfrac{1+\frac{1}{x}}{2}$" in prompt
        assert r"原文显式 `\tfrac`/`\cfrac` 保留" in prompt
        assert "锁定公式及其 ID 优先原样保留，不受本规则改写" in prompt


def test_parse_ai_json_type_hints_resolve():
    hints = get_type_hints(parse_ai_json)

    assert "raw_markdown" in hints


def test_parse_ai_json_keeps_valid_json_unchanged():
    original = {
        "questions": [
            {
                "content": "第一行\n第二行 $\\frac{1}{2}$",
                "answer_markdown": "",
            }
        ]
    }

    assert parse_ai_json(json.dumps(original, ensure_ascii=False)) == original


def test_parse_ai_json_accepts_json_null():
    assert parse_ai_json("null") is None


def test_parse_ai_json_removes_markdown_fence_and_prose():
    raw = """```json
以下是结果：
{"questions": []}
```"""

    assert parse_ai_json(raw) == {"questions": []}


def test_parse_ai_json_repairs_literal_controls_and_latex_backslashes():
    raw = r"""{
  "questions": [
    {
      "content": "第一行
第二行 $\frac{1}{2}$ 与 \textbf{重点}<TAB>完成",
      "answer_markdown": ""
    }
  ]
}""".replace("<TAB>", "\t")

    parsed = parse_ai_json(raw)

    content = parsed["questions"][0]["content"]
    assert content == "第一行\n第二行 $\\frac{1}{2}$ 与 \\textbf{重点}\t完成"


def test_parse_ai_json_preserves_latex_that_looks_like_valid_json_escape():
    raw = r'{"content":"\\textbf{重点} 与 \\nabla f"}'

    parsed = parse_ai_json(raw)

    assert parsed["content"] == "\\textbf{重点} 与 \\nabla f"


def test_valid_json_cannot_decode_the_complete_fillin_macro_as_form_feed():
    raw = r'{"markdown":"14. 则 $k=$\fillin.","content":"下一空\fillin{2}"}'
    assert "\x0cillin" in json.loads(raw)["markdown"]  # Valid JSON, damaged LaTeX.
    parsed = parse_ai_json(raw)
    assert parsed == {"markdown": r"14. 则 $k=$\fillin.", "content": r"下一空\fillin{2}"}
    assert "\x0c" not in parsed["markdown"]


def test_fillin_repair_preserves_real_json_controls_and_already_escaped_commands():
    raw = r'{"content":"\fillin\nnext\ntext\ttext\rreturn\fpage\bback","other":"\\fillin \\frac{1}{2} \\textbf{字} \\nabla f"}'
    expected = json.loads(raw)
    expected["content"] = expected["content"].replace("\x0cillin", r"\fillin")
    assert parse_ai_json(raw) == expected


@pytest.mark.parametrize("raw", [
    r'{"content":"\n"}', r'{"content":"\t"}', r'{"content":"\f"}',
    r'{"content":"\\"}', r'{"content":"\filli"}',
    r'{"content":"\fillinois \fillinfoo"}',
    r'{"content":"normal \nnext \ntext \ttext \rreturn \fpage \bback"}',
    r'{"content":"quoted \\\"x\\\" and \\fillin; slash / and unicode \u0066"}',
])
def test_fillin_fix_does_not_reinterpret_other_valid_json_strings(raw):
    assert parse_ai_json(raw) == json.loads(raw)


def test_parse_ai_json_rejects_structurally_invalid_output():
    with pytest.raises(json.JSONDecodeError):
        parse_ai_json('{"questions": nope}')


def test_paper_prompts_require_valid_json_escaping():
    curriculum = {"必修一": {"1. 集合": []}}
    prompts = (
        build_pdf_parse_system_prompt(curriculum, False),
        build_import_parse_system_prompt(curriculum),
    )

    for prompt in prompts:
        assert "JSON 转义序列 `\\n`" in prompt
        assert "LaTeX 命令的反斜杠必须按 JSON 规范转义为双反斜杠" in prompt
        assert "字符串内部换行直接输出真实回车" not in prompt


def test_classification_prompts_prefer_later_curriculum_module():
    curriculum = {
        "必修一": {"5. 三角函数": []},
        "必修二": {"6. 平面向量及其应用": []},
    }
    prompts = (
        build_classification_system_prompt(curriculum),
        build_pdf_parse_system_prompt(curriculum, False),
        build_import_parse_system_prompt(curriculum),
    )

    for prompt in prompts:
        assert "选择位置最靠后的模块作为最终分类" in prompt
        assert "先比较学段从上到下的顺序" in prompt
        assert "若属于同一学段，再比较章节从前到后的顺序" in prompt
        assert "必修二的“平面向量及其应用”" in prompt
        assert "仅作为背景条件被提及" in prompt


def test_single_question_classification_prompt_requests_only_coarse_question_form():
    prompt = build_classification_system_prompt({"必修一": {"1. 集合": []}})

    assert '"compulsory"' in prompt
    assert '"chapter"' in prompt
    assert '"question_form"' in prompt
    assert "question_type" not in prompt
    assert "包含且仅包含以下三个 key" in prompt
    assert "任何选择题一律为 `choice`" in prompt
    assert "严禁输出或猜测 `single_choice`、`multi_choice`" in prompt
