"""Prove that configured limits reach the ordinary, non-project PDF route."""
from pathlib import Path
import os
import shutil
import subprocess
import sys


SCENARIO = r'''
import json,time,threading,uuid
from pathlib import Path
import requests
requests.sessions.Session.request=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No external model'))
import pymupdf as fitz
from fastapi.testclient import TestClient
import main
from mathbank.asset_security import MAX_PDF_BYTES,MAX_SINGLE_IMAGE_BYTES,MAX_IMAGE_PIXELS
from mathbank.tex_helper import MAX_TEX_BYTES
from mathbank.database import SessionLocal,Question,engine
assert Path(engine.url.database).resolve().parent==Path.cwd()
assert not Path('.env').exists()
assert (MAX_PDF_BYTES,MAX_SINGLE_IMAGE_BYTES,MAX_IMAGE_PIXELS,MAX_TEX_BYTES)==(64*1048576,12*1048576,60000000,8*1048576)
assert main.MAX_PDF_TASK_PAGES==120 and main.PDF_OCR_CAPACITY.total==6
assert main.PDF_OCR_SEMAPHORE is main.PDF_OCR_CAPACITY.auxiliary
assert main.PDF_CLOUD_ONLY is True
main.get_current_curriculum=lambda: {'必修一':{'函数':['函数值']}}
main.schedule_database_export=lambda *a,**k:None
lock=threading.Lock();full=threading.Event();release=threading.Event()
active=peak=0;seen=[]
def ocr(path,**kwargs):
    global active,peak
    with lock:
        active+=1;peak=max(peak,active);seen.append(kwargs['page_index'])
        if active==6:full.set()
    try:
        assert release.wait(10)
        i=kwargs['page_index']+1
        return f'{i}. 计算${i}+1$。'
    finally:
        with lock:active-=1
main.ocr_pdf_page_image=ocr
main.parse_paper_text_internal=lambda *a,**k:[{'content':f'计算${i}+1$。','answer_markdown':'',
    'question_type':'detailed_answer','difficulty':'normal'} for i in range(1,7)]
main.post_process_pdf_parsed_questions=lambda questions,*a,**k:questions
with fitz.open() as pdf:
    for n in range(6):pdf.new_page(width=200,height=300).insert_text((20,30),str(n))
    raw=pdf.tobytes()
with TestClient(main.app) as client:
    client.headers.update({'X-Local-Token':main.LOCAL_TOKEN})
    response=client.post('/api/upload/pdf-task',files={'file':('six.pdf',raw,'application/pdf')},
        data={'client_request_id':str(uuid.uuid4()),'pdf_strategy':'force_ocr','pdf_verify_suspicions':'false'})
    assert response.status_code==200,response.text
    task_id=response.json()['task_id']
    try:
        assert full.wait(10),{'peak':peak,'seen':seen}
        snapshot=main.PDF_OCR_CAPACITY.snapshot()
        assert snapshot['page_active']==snapshot['active']==6 and peak==6
        assert 0<snapshot['page_input_bytes']<=snapshot['page_input_budget_bytes']
        assert not main.PDF_OCR_SEMAPHORE.acquire(blocking=False)
    finally:release.set()
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        task=client.get(f'/api/tasks/{task_id}/status').json()
        if task['status'] in {'completed','error'}:break
        time.sleep(.01)
    assert task['status']=='completed',task
    assert sorted(seen)==list(range(6)) and len(task['data'])==6
    settings=client.get('/api/settings').json()
    assert settings['processing_limits']['document_upload_bytes']==64*1048576
    assert settings['processing_limits']['document_workers']==3
    assert settings['processing_limits']['document_queue']==8
    with SessionLocal() as db:assert db.query(Question).count()==0
main.DOCUMENT_TASKS.shutdown()
assert main.PDF_OCR_CAPACITY.snapshot()['active']==0
print('ORDINARY_PDF_CAPACITY='+json.dumps({'peak':peak,'formal_rows':0,'external_models':0}))
'''


def test_configured_capacity_reaches_real_source_only_upload_worker(tmp_path):
    root=Path(__file__).resolve().parents[1]
    target=tmp_path/'source-only-runtime'
    target.mkdir()
    shutil.copy2(root/'main.py',target/'main.py')
    shutil.copytree(root/'mathbank',target/'mathbank',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if (root/'templates').exists():shutil.copytree(root/'templates',target/'templates')
    (target/'static').mkdir()
    script=target/'capacity_scenario.py';script.write_text(SCENARIO)
    env={key:value for key,value in os.environ.items()
         if not any(word in key.upper() for word in ('API_KEY','TOKEN','SECRET'))}
    env.update(PYTHONPATH=str(target),MATHBANK_MAX_DOCUMENT_MIB='64',MATHBANK_MAX_IMAGE_MIB='12',
        MATHBANK_MAX_IMAGE_MEGAPIXELS='60',MATHBANK_MAX_TEXT_SOURCE_MIB='8',MATHBANK_MAX_PDF_PAGES='120',
        MATHBANK_DOCUMENT_WORKERS='3',MATHBANK_DOCUMENT_QUEUE='8',PDF_OCR_CONCURRENCY='6')
    result=subprocess.run([sys.executable,'-B',str(script)],cwd=target,env=env,
        capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stdout[-6000:]+'\n'+result.stderr[-6000:]
    assert '"peak": 6' in result.stdout and '"formal_rows": 0' in result.stdout
