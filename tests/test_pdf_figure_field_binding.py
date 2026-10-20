"""Pending PDF slots bind to exact stem/answer ranges, including repeated labels."""
from copy import deepcopy

import pytest

from mathbank.pdf_figure_pipeline import bind_figure_review_questions


def bundle():
    stem = '16. 题干前文[插图待补: 图1]题干后文'
    answer = '16. 解：第一步。\n[插图待补: 图1]\n第二步和最后结论。'
    pages = [{'page_number':3,'markdown':'<!-- MATHBANK_PDF_PAGE:3 -->\n'+stem},
             {'page_number':7,'markdown':'<!-- MATHBANK_PDF_PAGE:7 -->\n'+answer}]
    # Main merges strip()'d pages and then removes page markers.
    source = '\n'+stem+'\n\n\n'+answer
    qs = [{'source_number':16,'content':stem[4:], 'answer_markdown':answer[4:]}]
    matches=[]
    for field,raw in [('content',stem),('answer_markdown',answer)]:
        start=source.index(raw)+4
        matches.append({'question_index':0,'field':field,'source_start':start,'source_end':start+len(raw)-4,
            'source_ranges':[[start,start+len(raw)-4]],'source_excerpt':raw[4:]})
    report={'items':[{'page_number':3,'slot_id':'p3-s1','status':'pending'},
                     {'page_number':7,'slot_id':'p7-s1','status':'pending'}]}
    return qs,matches,pages,report


def test_pending_same_named_figures_bind_to_distinct_fields_and_middle_position():
    qs,matches,pages,report=bundle();before=deepcopy(qs)
    bind_figure_review_questions(report,qs,source_matches=matches,source_pages=pages)
    first,second=report['items']
    assert first['question_index']==second['question_index']==0
    assert first['source_number']==second['source_number']==16
    assert first['target_field']=='content'and second['target_field']=='answer_markdown'
    assert first['slot_label']==second['slot_label']=='[插图待补: 图1]'
    assert qs[0]['answer_markdown'][second['slot_offset']:].startswith(second['slot_label'])
    assert qs==before and all(item['status']=='pending'for item in report['items'])


@pytest.mark.parametrize('change',['edited_answer','bad_excerpt','bad_range','different_page','duplicate_slot','competing_card'])
def test_unproved_or_ambiguous_slot_does_not_guess_a_target(change):
    qs,matches,pages,report=bundle()
    if change=='edited_answer':qs[0]['answer_markdown']+='教师新改。'
    elif change=='bad_excerpt':matches[1]['source_excerpt']+='坏证据'
    elif change=='bad_range':matches[1]['source_ranges']=[[0,9999]]
    elif change=='different_page':report['items'][1]['page_number']=8
    elif change=='duplicate_slot':pages.append(deepcopy(pages[1]))
    else:
        qs.append(deepcopy(qs[0]));matches.append({**matches[1],'question_index':1})
    bind_figure_review_questions(report,qs,source_matches=matches,source_pages=pages)
    assert 'target_field'not in report['items'][1]
    assert 'slot_label'not in report['items'][1]


def test_canonical_verified_slot_does_not_renumber_later_pending_slots():
    import hashlib
    from mathbank.pdf_page_fragments import SCHEMA
    def sha(text):return hashlib.sha256(text.encode()).hexdigest()
    image='![插图](/static/uploads/tmp/one.png)'
    text='16. '+image+'\n中间[插图待补: 图2]，之后[插图待补: 图3]。'
    block={'id':'block-one','role':'stem','number':16,'edge':'none','range':[0,len(text)],
        'text_sha256':sha(text),'pending_reasons':[]}
    rows=[]
    for index,label in enumerate([image,'[插图待补: 图2]','[插图待补: 图3]'],1):
        start=text.index(label)
        rows.append({'slot_id':f'p3-s{index}','label':f'[插图待补: 图{index}]',
            'range':[start,start+len(label)],'replaced':index==1})
    page={'page_number':3,'markdown':text,'fragments':{'schema':SCHEMA,'page_index':2,
        'source_sha256':sha(text),'blocks':[block],
        'slot_blocks':{f'p3-s{i}':'block-one'for i in range(1,4)}},'field_sources':[{
        'block_id':'block-one','slots':rows,'replacements':[{
            'slot_id':'p3-s1','range':rows[0]['range'],'image_path':'/static/uploads/tmp/one.png'}]}]}
    q={'source_number':16,'content':text[4:],'answer_markdown':''}
    matches=[{'question_index':0,'field':'content','source_ranges':[[4,len(text)]],'source_excerpt':text[4:]}]
    report={'items':[{'page_number':3,'slot_id':'p3-s2','status':'pending'},
                     {'page_number':3,'slot_id':'p3-s3','status':'pending'}]}
    bind_figure_review_questions(report,[q],source_matches=matches,source_pages=[page])
    assert [item['slot_label']for item in report['items']]==['[插图待补: 图2]','[插图待补: 图3]']
    assert all(item['target_field']=='content'for item in report['items'])


def test_opening_existing_project_derives_field_targets_without_writing_saved_evidence(tmp_path):
    import threading
    from types import SimpleNamespace
    from mathbank.document_projects import DocumentProjectStore
    from mathbank.document_project_service import DocumentProjectService
    qs,matches,pages,review=bundle()
    store=DocumentProjectStore(tmp_path/'projects')
    pid=store.create(b'%PDF-test','test.pdf',{}, {})['project_id']
    store.sync_task(pid,{'task_id':'previous','status':'completed','data':qs,
        'pdf_source_pages':pages,'diagnostics':{'pdf_figure_review':review,'source_matches':matches}})
    before=deepcopy(store.load_task(pid));manifest=store.get(pid)
    tasks=SimpleNamespace(_lock=threading.RLock(),_records={},snapshot=lambda _task:None)
    detail=DocumentProjectService(store,tasks).detail(pid)
    items=detail['diagnostics']['pdf_figure_review']['items']
    assert [item['target_field']for item in items]==['content','answer_markdown']
    assert items[1]['source_number']==16
    assert store.load_task(pid)==before and store.get(pid)==manifest
    assert 'target_field'not in before['diagnostics']['pdf_figure_review']['items'][0]
