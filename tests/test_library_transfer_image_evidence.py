"""Only grounded image quotations follow the import's exact asset mapping."""
from copy import deepcopy
import pytest

from mathbank.library_transfer import _import_image_evidence
from mathbank.question_assets import rewrite_question_asset_paths

OLD='/static/uploads/answer.png'
NEW='/static/uploads/shared_new.png'
QUOTE=f'![原解]({OLD})'


@pytest.mark.parametrize('field,source_field',[('answer_markdown','answer_markdown'),('review','review_markdown')])
def test_grounded_image_quote_tracks_rewritten_field_without_mutating_source(field,source_field):
    record={field:'原解如下：\n'+QUOTE+'\n正文保留。'}
    frozen=deepcopy(record)
    values={field:rewrite_question_asset_paths(record[field],{OLD:NEW})}
    point={'evidence_source':source_field,'evidence':QUOTE}
    expected=QUOTE.replace(OLD,NEW)
    assert _import_image_evidence(point,record,values,{OLD:NEW})==expected
    assert expected in values[field] and record==frozen and point['evidence']==QUOTE


@pytest.mark.parametrize('point,source',[
    ({'evidence_source':'answer_markdown','evidence':OLD},OLD),
    ({'evidence_source':'answer_markdown','evidence':QUOTE},'```\n'+QUOTE+'\n```'),
    ({'evidence_source':'answer_markdown','evidence':'未曾出现'+QUOTE},QUOTE),
    ({'evidence_source':'content','evidence':QUOTE},QUOTE),
    ({'evidence_source':['legacy'],'evidence':{'retained':True}},QUOTE),
    ({'evidence_source':{'legacy':True},'evidence':['old']},QUOTE),
    ({'evidence_source':'answer_markdown','evidence':None},QUOTE),
])
def test_legacy_literals_bare_paths_and_ungrounded_metadata_are_not_reinterpreted(point,source):
    record={'answer_markdown':source,'content':source}
    values={key:rewrite_question_asset_paths(value,{OLD:NEW}) for key,value in record.items()}
    assert _import_image_evidence(point,record,values,{OLD:NEW})==point['evidence']


def test_unproven_rewrite_keeps_original_evidence():
    point={'evidence_source':'answer_markdown','evidence':QUOTE}
    assert _import_image_evidence(point,{'answer_markdown':QUOTE},{'answer_markdown':'未映射新图'},
                                 {OLD:NEW})==QUOTE
