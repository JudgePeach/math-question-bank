"""Pure local-history recovery with sealed temporary project/checkpoint assets."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from mathbank.document_projects import DocumentProjectStore
from mathbank.pdf_task_resume import prepare_pdf_checkpoint, seal_pdf_checkpoint
from mathbank.pdf_transcript_recovery import recover_pdf_transcript_pages, STAGE
from mathbank.task_manager import TaskCancelled


GOOD = '1. 完整原题\n\\begin{choices}\n\\item 0\n\\item 1\n\\item 2\n\\item 3\n\\end{choices}'
BAD = '\n'.join(f'{i}. 坏页\n$$\\begin{{choices}}\n\\item\n\\item\n\\item\n\\item$$' for i in range(1,7))
GOOD2 = '7. 当前完整的第二页 $x=1$。'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *_a, **_kw: pytest.fail('Recovery must not call a model'))


@pytest.fixture
def setup(tmp_path):
    files = {}
    def asset(name, body):
        url = '/static/uploads/tmp/' + name
        path = tmp_path/name; path.write_bytes(body); files[url] = path
        return url
    current_images = [asset('current-page-1.png', b'identical-page-one-pixels'), asset('current-page-2.png', b'page-two-pixels')]
    old_images = [asset('old-page-1.png', b'identical-page-one-pixels'), asset('old-page-2.png', b'page-two-pixels')]
    figure = asset('old-figure.png', b'unchanged-old-figure')
    source = b'%PDF-identical-source'
    document_sha = hashlib.sha256(source).hexdigest()
    store = DocumentProjectStore(tmp_path/'projects')
    old = store.create(source, '旧工程.pdf', {}, {})['project_id']
    current = store.create(source, '当前工程.pdf', {}, {})['project_id']
    current_texts = [BAD, GOOD2]
    old_texts = [GOOD+'\n![]('+figure+')', '7. 历史第二页不应覆盖当前好页。']
    def checkpoint(task_id, texts, images, *, indices=(0,1), layout=None, extra_assets=()):
        state = prepare_pdf_checkpoint(None, task_id=task_id, document_sha256=document_sha,
            page_indices=list(indices), strategy='layout_aware', verify_suspicions=False, resolve_asset=files.__getitem__)
        state['pages'] = {str(index): {'markdown':text,'joint':None} for index,text in zip(indices,texts)}
        state['prepared'] = {'page_texts':list(texts),'layout_result':deepcopy(layout)}
        return seal_pdf_checkpoint(state, assets=[*images,*extra_assets], resolve_asset=files.__getitem__)
    old_state = checkpoint('old-task',old_texts,old_images,extra_assets=[figure])
    current_state = checkpoint('current-task',current_texts,current_images)
    def save_old(state=old_state, images=old_images):
        store.sync_task(old, {'task_id':'old-task','status':'error','page_images':list(images),'pdf_resume_state':state})
    save_old()
    store.sync_task(current, {'task_id':'current-task','status':'error','page_images':current_images,'pdf_resume_state':current_state})
    return SimpleNamespace(store=store,old=old,current=current,source=source,document_sha=document_sha,
        old_images=old_images,current_images=current_images,figure=figure,files=files,
        current_texts=current_texts,old_texts=old_texts,current_state=current_state,old_state=old_state,
        checkpoint=checkpoint,save_old=save_old)


def recover(setup, *, texts=None, edited=None, layout=None, checkpoint=None, check_cancelled=lambda:None):
    return recover_pdf_transcript_pages(setup.store,setup.current,checkpoint or setup.current_state,
        setup.current_images,[1,2],setup.current_texts if texts is None else texts,layout,
        edited_pages=edited or {},resolve_asset=setup.files.__getitem__,check_cancelled=check_cancelled)


def test_same_pdf_page_and_sealed_pixels_recover_only_bad_page_with_zero_models(setup):
    original=deepcopy(setup.current_state)
    saved_before=setup.store.load_task(setup.current)['pdf_resume_state']
    texts,layout,report=recover(setup)
    assert texts==[setup.old_texts[0],GOOD2]
    assert layout is None and report['ai_calls']==0 and report['original_verified'] is False
    assert report['pending_pages']==[]
    receipt=report['recovered_pages'][0]
    assert receipt['page_number']==1 and receipt['source_project_id']==setup.old
    assert receipt['checkpoint_sha256']==setup.old_state['state_sha256']
    assert receipt['previous_sha256']==hashlib.sha256(BAD.encode()).hexdigest()
    assert receipt['working_sha256']==hashlib.sha256(texts[0].encode()).hexdigest()
    assert setup.figure in setup.store.asset_urls(setup.current)
    assert setup.current_state==original
    assert setup.store.load_task(setup.current)['pdf_resume_state']==saved_before
    assert setup.store.load_task(setup.current)['pdf_resume_state']['prepared']['page_texts'][0]==BAD
    manifest=json.loads((setup.store.root/setup.current/'manifest.json').read_text())
    assert STAGE in manifest['stages'] and manifest['stages'][STAGE]['complete'] is True


def test_current_good_page_wins_even_when_history_has_a_different_complete_transcript(setup):
    current=['1. 当前好题不能被覆盖。',GOOD2]
    texts,_,report=recover(setup,texts=current)
    assert texts==current and not report['recovered_pages'] and report['ai_calls']==0


def test_user_page_edit_has_priority_and_original_checkpoint_is_not_rewritten(setup):
    before=deepcopy(setup.current_state)
    manual='1. 教师人工已改正文，须保留。'
    texts,_,report=recover(setup,edited={1:manual})
    # The application applies the actual edit after recovery; the helper must
    # neither overwrite it with history nor mutate the immutable OCR baseline.
    assert texts==setup.current_texts and not report['recovered_pages']
    working=list(texts);working[0]=manual
    assert working[0]==manual and setup.current_state==before


@pytest.mark.parametrize('mismatch',['document_sha','physical_page','pixels','checkpoint','asset','unsealed_figure'])
def test_history_with_wrong_identity_or_tampered_evidence_is_not_reused(setup,mismatch):
    if mismatch=='document_sha':
        state=deepcopy(setup.old_state);state['document_sha256']='0'*64;setup.save_old(state)
    elif mismatch=='physical_page':
        state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,indices=(1,2),extra_assets=[setup.figure]);setup.save_old(state)
    elif mismatch=='pixels':
        setup.files[setup.old_images[0]].write_bytes(b'different-physical-pixels')
        state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,extra_assets=[setup.figure]);setup.save_old(state)
    elif mismatch=='checkpoint':
        state=deepcopy(setup.old_state);state['prepared']['page_texts'][0]='1. sealed hash does not cover this change';setup.save_old(state)
    elif mismatch=='asset':
        setup.files[setup.figure].write_bytes(b'figure changed after sealing')
    else:
        state=setup.checkpoint('old-task',setup.old_texts,setup.old_images);setup.save_old(state)
    texts,_,report=recover(setup)
    assert texts==setup.current_texts and not report['recovered_pages']
    assert [row['page_number'] for row in report['pending_pages']]==[1]
    assert report['ai_calls']==0


def test_actual_current_pixel_file_must_still_match_its_receipt(setup):
    setup.files[setup.current_images[0]].write_bytes(b'current image changed')
    texts,_,report=recover(setup)
    assert texts==setup.current_texts and not report['recovered_pages']


def test_recovered_layout_replaces_only_affected_page_and_preserves_source_layouts(setup):
    old_layout={'pages':[{'page_index':0,'figures':[{'id':'old-figure','image_path':setup.figure}]},
                         {'page_index':1,'figures':[]}], 'issues':[{'page_index':0,'reason':'old warning'}]}
    current_layout={'pages':[{'page_index':0,'figures':[]},{'page_index':1,'figures':[{'id':'keep-current'}]}],
                    'issues':[{'page_index':0,'reason':'broken'}, {'page_index':1,'reason':'keep'}]}
    state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,layout=old_layout,extra_assets=[setup.figure])
    setup.save_old(state);before=deepcopy(current_layout)
    texts,layout,report=recover(setup,layout=current_layout)
    assert report['recovered_pages'] and layout['pages'][0]=={**old_layout['pages'][0], 'page_image':setup.current_images[0]}
    assert layout['pages'][1]==current_layout['pages'][1]
    assert layout['page_texts']==texts and current_layout==before
    assert layout['issues']==[{'page_index':1,'reason':'keep'},{'page_index':0,'reason':'old warning'}]


def test_deleted_history_navigation_is_replaced_by_identical_current_page_image(setup):
    old_layout={'pages':[{'page_index':0,'page_number':1,'page_image':setup.old_images[0],
                          'figures':[{'image_path':setup.figure}]},
                         {'page_index':1,'page_number':2,'page_image':setup.old_images[1],'figures':[]}], 'issues':[]}
    current_layout={'pages':[{'page_index':0,'page_number':1,'page_image':setup.current_images[0],'figures':[]},
                             {'page_index':1,'page_number':2,'page_image':setup.current_images[1],'figures':[]}], 'issues':[]}
    state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,layout=old_layout,extra_assets=[setup.figure])
    setup.save_old(state)
    original=deepcopy(state)
    setup.files[setup.old_images[0]].unlink()
    texts,layout,report=recover(setup,layout=current_layout)
    assert texts[0]==setup.old_texts[0] and report['recovered_pages']
    assert layout['pages'][0]['page_image']==setup.current_images[0]
    assert layout['pages'][1]==current_layout['pages'][1]
    assert report['recovered_assets']==[setup.figure]
    assert not set(report['recovered_assets']).intersection([*setup.old_images,*setup.current_images])
    assert setup.store.load_task(setup.old)['pdf_resume_state']==original
    assert original['prepared']['layout_result']['pages'][0]['page_image']==setup.old_images[0]


def test_cached_recovery_rebinds_navigation_to_latest_task_url_with_identical_pixels(setup,monkeypatch):
    old_layout={'pages':[{'page_index':0,'page_image':setup.old_images[0],'figures':[{'image_path':setup.figure}]},
                         {'page_index':1,'page_image':setup.old_images[1],'figures':[]}], 'issues':[]}
    current_layout={'pages':[{'page_index':0,'page_image':setup.current_images[0],'figures':[]},
                             {'page_index':1,'page_image':setup.current_images[1],'figures':[]}], 'issues':[]}
    state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,layout=old_layout,extra_assets=[setup.figure])
    setup.save_old(state)
    first_texts,first_layout,first_report=recover(setup,layout=current_layout)
    old_current_url=setup.current_images[0]
    rebound_url='/static/uploads/tmp/rebound-task-page-1.png'
    rebound_path=setup.files[old_current_url].with_name('rebound-task-page-1.png')
    rebound_path.write_bytes(setup.files[old_current_url].read_bytes())
    setup.files[rebound_url]=rebound_path
    setup.current_images[0]=rebound_url
    rebound_checkpoint=setup.checkpoint('rebound-task',setup.current_texts,setup.current_images)
    rebound_layout=deepcopy(current_layout);rebound_layout['pages'][0]['page_image']=rebound_url
    setup.files[old_current_url].unlink()
    setup.store.delete(setup.old)
    monkeypatch.setattr(setup.store,'find_existing',lambda *_a,**_kw:pytest.fail('Equal pixel receipts must reuse cached recovery'))
    texts,layout,report=recover(setup,layout=rebound_layout,checkpoint=rebound_checkpoint)
    assert report['reused'] is True and texts==first_texts
    assert first_layout['pages'][0]['page_image']==old_current_url
    assert layout['pages'][0]['page_image']==rebound_url
    assert report['recovered_assets']==first_report['recovered_assets']==[setup.figure]
    assert not set(report['recovered_assets']).intersection([*setup.old_images,old_current_url,rebound_url])


def test_cached_recovery_never_rolls_back_updated_unaffected_page_layout_or_issues(setup,monkeypatch):
    old_layout={'pages':[{'page_index':0,'page_image':setup.old_images[0],'figures':[{'image_path':setup.figure}]},
                         {'page_index':1,'page_image':setup.old_images[1],'figures':[]}],
                'issues':[{'page_index':0,'reason':'restored page issue'}]}
    first_layout={'pages':[{'page_index':0,'page_image':setup.current_images[0],'figures':[]},
                           {'page_index':1,'page_image':setup.current_images[1],
                            'figures':[{'id':'old-unaffected-position','bbox':[1,2,3,4]}],'notes':['old notes']}],
                  'issues':[{'page_index':1,'reason':'old unaffected issue'}], 'revision_tag':'old-layout'}
    state=setup.checkpoint('old-task',setup.old_texts,setup.old_images,layout=old_layout,extra_assets=[setup.figure])
    setup.save_old(state)
    first_texts,_,first_report=recover(setup,layout=first_layout)
    assert first_report['recovered_pages']
    updated=deepcopy(first_layout)
    updated['pages'][1].update(figures=[{'id':'current-corrected-position','bbox':[10,20,30,40]}],
                               notes=['current teacher-reviewed position'], current_flag={'keep':True})
    updated['issues']=[{'page_index':1,'reason':'current unaffected issue','evidence':'retained'}]
    updated['revision_tag']='current-layout'
    before=deepcopy(updated)
    monkeypatch.setattr(setup.store,'find_existing',lambda *_a,**_kw:pytest.fail('Same text/pixels must use the recovery cache'))
    texts,layout,report=recover(setup,layout=updated)
    assert report['reused'] is True and texts==first_texts
    assert layout['pages'][1]==updated['pages'][1]
    assert layout['revision_tag']=='current-layout'
    assert layout['pages'][0]['page_image']==setup.current_images[0]
    assert layout['pages'][0]['figures']==old_layout['pages'][0]['figures']
    assert [row for row in layout['issues'] if row['page_index']==1]==updated['issues']
    assert [row for row in layout['issues'] if row['page_index']==0]==old_layout['issues']
    assert layout['page_texts']==texts and updated==before


def test_cached_recovery_survives_history_project_deletion_and_keeps_assets_registered(setup,monkeypatch):
    first_texts,first_layout,first_report=recover(setup)
    assert first_report['recovered_pages']
    setup.store.delete(setup.old)
    assert setup.figure in setup.store.referenced_assets()
    monkeypatch.setattr(setup.store,'find_existing',lambda *_a,**_kw:pytest.fail('Cached recovery must not need its deleted history project'))
    texts,layout,report=recover(setup)
    assert texts==first_texts and layout==first_layout
    assert report['reused'] is True and report['ai_calls']==0
    assert report['recovered_pages']==first_report['recovered_pages']


def test_cached_recovery_still_rejects_changed_registered_figure(setup):
    recover(setup);setup.store.delete(setup.old)
    setup.files[setup.figure].write_bytes(b'tampered even after history deletion')
    with pytest.raises(ValueError,match='changed recovery asset'):
        recover(setup)


def test_cancellation_propagates_without_touching_first_pass_or_starting_models(setup):
    original=deepcopy(setup.current_state)
    def stop():raise TaskCancelled('cancel locally')
    with pytest.raises(TaskCancelled):recover(setup,check_cancelled=stop)
    assert setup.current_state==original
