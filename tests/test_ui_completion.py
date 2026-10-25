"""Human answers resume the same case; posting still needs actual approvals and a separate role."""
import time
from fastapi.testclient import TestClient
from vouch.ui.server import create_app


def wait(client,rid):
    for _ in range(600):
        run=client.get('/api/runs/'+rid).json()
        if run['status']!='running':return run
        time.sleep(.01)
    raise AssertionError('run did not complete')


def test_tax_manager_answer_resumes_same_database_and_retains_history(tmp_path):
    with TestClient(create_app(tmp_path/'api.db')) as c:
        rid=c.post('/api/runs',json={'scenario_id':'P10'}).json()['run_id']
        initial=wait(c,rid)
        assert initial['decision']['decision']=='escalate'
        q=next(q for q in c.get('/api/questions').json() if q['about']=='nonqualifying_event')
        payload={'run_id':rid,'actor_id':'P-PAY','data':{'method':'individual','reason':'Individual payroll treatment authorized'}}
        assert c.post('/api/questions/'+q['request_id'],json=payload).status_code==403
        payload['actor_id']=q['recipient']
        assert c.post('/api/questions/'+q['request_id'],json=payload).status_code==200
        result=wait(c,rid)
        assert result['decision']['decision']=='classify_and_book'
        assert result['decision']['wage_taxable']=='4165.00'
        assert result['decision']['payroll_method']=='individual'
        assert len(result['timeline'])>len(initial['timeline'])
        assert c.get('/api/audit').json()['verified']


def test_posting_rejects_missing_approvals_and_unauthorized_actor(tmp_path):
    with TestClient(create_app(tmp_path/'api.db')) as c:
        rid=c.post('/api/runs',json={'scenario_id':'P01'}).json()['run_id'];wait(c,rid)
        entries=[e for e in c.get('/api/entries').json() if e['origin']=='vouch' and e['status']=='draft']
        eid=entries[0]['entry_id'];url='/api/entries/'+eid+'/post'
        assert c.post(url,json={'reason':'Reviewed accounting'}).status_code==403
        while c.get('/api/approvals').json():
            item=c.get('/api/approvals').json()[0]
            assert c.post('/api/approvals/'+item['request_id'],json={'decision':'approve','reason':'Independent evidence review'}).status_code==200
        assert c.post(url,json={'reason':'Reviewed accounting','actor_id':'P-TAX'}).status_code==403
        assert c.post(url,json={'reason':'Controller verified all approvals'}).status_code==200
        assert next(e for e in c.get('/api/entries').json() if e['entry_id']==eid)['status']=='posted'
        assert all(p['status']=='draft' for p in c.get('/api/payroll-export').json() if p['origin']=='vouch')
        assert c.get('/api/audit').json()['verified']


def test_scores_prefer_complete_suite_over_a_newer_single_case_rehearsal(tmp_path):
    import json
    results=tmp_path/'results';results.mkdir()
    base={'status':'scored','mode':'model','model':'jev-test','llm_calls_per_case':3}
    (results/'public-full.json').write_text(json.dumps({**base,'date':'2026-01-01','cases':15,'passed_all_runs':14}))
    (results/'public-rehearsal.json').write_text(json.dumps({**base,'date':'2026-01-02','cases':1,'passed_all_runs':1}))
    with TestClient(create_app(tmp_path/'api.db',results_dir=results)) as c:
        report=c.get('/api/scores').json()
        assert report['cases']==15 and report['passed_all_runs']==14
