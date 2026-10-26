"""Profile settings and classification requests in the isolated test app."""
import json
import os
from types import SimpleNamespace

import pytest
from mathbank.database import Question


def test_settings_quality_round_trip_preserves_legacy_client_mode(client, monkeypatch, tmp_path, db_session):
    import main
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    monkeypatch.setenv('MATHBANK_CLASSIFY_MODE', 'quick')
    path = tmp_path / '.env'
    path.write_text('UNRELATED_SETTING=keep\nMATHBANK_CLASSIFY_MODE=quick\n')
    monkeypatch.setattr(main, 'ENV_FILE', path)
    monkeypatch.setattr(main, 'post_chat_completion', lambda *a, **k: pytest.fail('Settings must not call AI'))
    headers = {'X-Local-Token':main.LOCAL_TOKEN}
    reply = client.post('/api/settings/save', data={'classify_quality':'precise'}, headers=headers)
    assert reply.status_code == 200
    assert client.get('/api/settings').json()['classification_policy']['mode'] == 'precise'
    assert 'MATHBANK_CLASSIFY_MODE=precise\n' in path.read_text()
    assert 'UNRELATED_SETTING=keep\n' in path.read_text()
    # A client without the new control does not reset an existing preference.
    assert client.post('/api/settings/save', data={}, headers=headers).status_code == 200
    assert client.get('/api/settings').json()['classification_policy']['mode'] == 'precise'
    before = path.read_bytes()
    assert client.post('/api/settings/save', data={'classify_quality':'invalid'}, headers=headers).status_code == 400
    assert path.read_bytes() == before
    assert db_session.query(Question).count() == 0


@pytest.mark.parametrize('quality,expected', [(None,4096),('quick',4096),('precise',8192)])
def test_single_question_profile_reaches_real_request_without_database_writes(client, monkeypatch, db_session, quality, expected):
    import main
    monkeypatch.setenv('MATHBANK_CLASSIFY_MODE', 'quick')
    monkeypatch.setenv('PREFER_CLASSIFY_MODEL', 'SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash')
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'test-only')
    monkeypatch.setattr(main, 'get_current_curriculum', lambda: {'必修一': {'函数': ['函数值']}})
    monkeypatch.setattr(main, 'get_active_version_code', lambda:'A')
    calls=[]
    body={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
        'compulsory':'必修一','chapter':'函数','knowledge':'函数值','question_form':'detailed_answer',
        'knowledge_points':[]})}}]}
    def post(provider,payload,**kwargs):
        calls.append(payload)
        return SimpleNamespace(status_code=200,json=lambda:body)
    monkeypatch.setattr(main,'post_chat_completion',post)
    data={'content':'已知函数$f(x)=x^2$，求$f(2)$。','expected_version_code':'A'}
    if quality is not None:data['quality']=quality
    result=client.post('/api/ai/classify',data=data,headers={'X-Local-Token':main.LOCAL_TOKEN})
    assert result.status_code == 200,result.text
    assert len(calls)==1 and calls[0]['max_tokens']==expected
    assert calls[0]['enable_thinking'] is (quality=='precise')
    diagnostics=result.json()['classification_diagnostics']
    assert diagnostics['effective_output_tokens']==expected
    assert diagnostics['policy']['mode']==('precise' if quality=='precise' else 'quick')
    assert db_session.query(Question).count()==0


def test_bad_per_request_quality_fails_before_model_request(client, monkeypatch):
    import main
    monkeypatch.setattr(main,'post_chat_completion',lambda *a,**k:pytest.fail('No paid request'))
    response=client.post('/api/ai/classify',data={'content':'求值。','quality':'invalid'},
        headers={'X-Local-Token':main.LOCAL_TOKEN})
    assert response.status_code==400


@pytest.mark.parametrize('failure',[None,'unknown_image','changed_pixels'])
def test_single_question_image_proof_uses_private_request_manifest(client,monkeypatch,tmp_path,db_session,failure):
    from PIL import Image,ImageDraw
    import main
    monkeypatch.setenv('PREFER_CLASSIFY_MODEL','SILICONFLOW/deepseek-ai/DeepSeek-V4.1-Flash')
    monkeypatch.setenv('SILICONFLOW_API_KEY','test-only')
    monkeypatch.setattr(main,'get_current_curriculum',lambda:{'必修一':{'函数':['函数值']}})
    monkeypatch.setattr(main,'get_active_version_code',lambda:'A')
    monkeypatch.setattr(main,'load_ai_knowledge_catalog',lambda *_a:[{'code':'demo:method','name':'因式分解法'}])
    root=tmp_path/'images';root.mkdir();path=root/'answer.png'
    picture=Image.new('RGB',(240,80),'white');ImageDraw.Draw(picture).text((4,15),'x^2-1=(x-1)(x+1)',fill='black');picture.save(path)
    monkeypatch.setattr(main,'UPLOAD_DIR',str(root))
    source=f'![原解](/{main.UPLOAD_DIR_REL}/answer.png)'
    calls=[]
    def post(provider,payload,**kwargs):
        calls.append(payload)
        assert 'image_evidence' in payload['messages'][0]['content']
        context=json.loads(payload['messages'][1]['content'][0]['text'])['classification_context']
        image=context['image_manifest'][0]
        formula='$x^2-1=(x-1)(x+1)$'
        point={'code':'demo:method','role':'auxiliary','evidence_source':'answer_markdown',
            'evidence':source,'reason':'原解明确使用因式分解。','image_evidence':{
                'image_id':'foreign' if failure=='unknown_image' else image['image_id'],
                'sha256':image['sha256'],'transcription':'【解析】'+formula+'，故方程的根为$-1,1$。',
                'step_quote':formula}}
        if failure=='changed_pixels':path.write_bytes(b'changed after the model received the original')
        return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'compulsory':'必修一','chapter':'函数','knowledge':'函数值',
                'question_form':'detailed_answer','knowledge_points':[point]})}}]})
    monkeypatch.setattr(main,'post_chat_completion',post)
    response=client.post('/api/ai/classify',data={'content':'求方程$x^2-1=0$的根。',
        'answer_markdown':source,'expected_version_code':'A'},headers={'X-Local-Token':main.LOCAL_TOKEN})
    assert response.status_code==200,response.text
    data=response.json();assert len(calls)==1
    if failure:
        assert data['knowledge_points']==[] and data['knowledge_point_diagnostics']['rejected']==1
    else:
        assert len(data['knowledge_points'])==1
        point=data['knowledge_points'][0]
        assert point['evidence']==source and point['reason'].startswith('图片解析转录，未经独立核验')
        assert 'image_evidence' not in point
        assert data['knowledge_point_diagnostics']['image_evidence'][0]['field']=='answer_markdown'
    assert str(tmp_path) not in response.text and db_session.query(Question).count()==0
