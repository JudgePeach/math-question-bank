import pytest

from mathbank.question_assets import (
    embedded_question_assets,
    rewrite_question_asset_paths,
    rewrite_structured_asset_paths,
    structured_question_assets,
)


OLD = "/static/uploads/tmp/figure.png"
NEW = "/static/uploads/figure.png"


@pytest.mark.parametrize("source", [
    f'![图]({OLD})', f'![图](<{OLD}> "图注")', f'![完成50%]({OLD} "50%")',
    rf'\includegraphics[width=2cm]{{{OLD}}}',
    f'<img alt="图" src="{OLD}">', f'<img src={OLD}>',
])
def test_visible_image_is_registered_and_only_destination_changes(source):
    assert embedded_question_assets(source, source) == [OLD]
    assert rewrite_question_asset_paths(source, {OLD: NEW}) == source.replace(OLD, NEW)


@pytest.mark.parametrize("wrapper", [
    '`%s`', '```latex\n%s\n```', '~~~\n%s\n~~~', '<!-- %s -->',
    '````latex\n%s\n`````',
    r'\begin{verbatim}%s\end{verbatim}', r'\begin{tikzpicture}%s\end{tikzpicture}',
    r'\%s', r'\verb|%s|', r'\lstinline{%s}', r'\detokenize{%s}', '%% %s',
])
def test_literal_image_text_never_becomes_an_asset_or_gets_rewritten(wrapper):
    literal = wrapper % f'![例子]({OLD})'
    value = literal + f'\n![真图]({OLD})'
    assert embedded_question_assets(literal) == []
    assert rewrite_question_asset_paths(value, {OLD: NEW}) == literal + f'\n![真图]({NEW})'


def test_unclosed_fence_is_literal_and_escaped_percent_is_visible():
    unfinished = f'```latex\n![例子]({OLD})'
    assert embedded_question_assets(unfinished) == []
    assert rewrite_question_asset_paths(unfinished, {OLD: NEW}) == unfinished
    assert embedded_question_assets(r'\% ' + f'![真图]({OLD})') == [OLD]


def test_percent_in_first_image_does_not_hide_next_image_on_same_line():
    value = f'![50%]({OLD} "50%") ![第二图]({NEW})'
    assert embedded_question_assets(value) == [OLD, NEW]


def test_structured_references_are_complete_but_source_and_instruction_are_untouched():
    reference = "/static/uploads/tmp/original.png"
    values = [{"id": "drawing", "image_path": OLD, "reference_image_path": reference,
               "tikz_code": "literal " + OLD, "instruction": OLD}]
    assert structured_question_assets(values) == [OLD, reference]
    result = rewrite_structured_asset_paths(values, {OLD: NEW})
    assert result[0]["image_path"] == NEW
    assert result[0]["reference_image_path"] == reference
    assert result[0]["tikz_code"] == values[0]["tikz_code"]
    assert result[0]["instruction"] == OLD
    assert values[0]["image_path"] == OLD


def test_external_images_are_not_interpreted_as_local_assets():
    source = "![外链](https://example.invalid/static/uploads/figure.png)"
    assert embedded_question_assets(source) == []
