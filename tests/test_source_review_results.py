import pytest

from mathbank.source_review_results import parse_review_response


@pytest.mark.parametrize('raw', ['{"items":[]}', '```json\n{"items":[]}\n```',
                                '```JSON\r\n{"items":[]}\r\n```', '```\n{"items":[]}\n```'])
def test_complete_verification_json_and_fences(raw):
    assert parse_review_response(raw) == {'items': []}


def test_complete_root_array_is_preserved_for_verifier_compatibility():
    assert parse_review_response('[{"id":"word_001"}]') == [{'id': 'word_001'}]


@pytest.mark.parametrize('raw', [
    '', None, '{"items": [', '{"items": [],}', 'prefix {"items":[]}',
    '{"items":[]} trailing', '```json\n{"items":[]}',
    '{"items":[],"items":[]}', '{"items":[{"id":"first","id":"second"}]}',
    '{"items":NaN}', '{"items":Infinity}', '{"items":-Infinity}',
])
def test_partial_or_ambiguous_json_is_never_repaired(raw):
    with pytest.raises(ValueError):
        parse_review_response(raw)
