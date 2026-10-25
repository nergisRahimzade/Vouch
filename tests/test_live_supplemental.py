"""Live supplemental cases: Vouch sees inputs; evaluator alone keeps expected answers."""
from pathlib import Path
import pytest
from vouch.agent.policy import api_key_available
from vouch.evaluator.run import evaluate

pytestmark=pytest.mark.live

@pytest.fixture(scope='module')
def report():
    assert api_key_available(), 'A configured live provider key is required'
    result=evaluate(directory=Path(__file__).resolve().parents[1]/'cases'/'supplemental',
        runs=3,concurrency=4,save=False,model_options={'model':'jev-latest'})
    result['set']='agent-authored-supplemental'
    import json
    target=Path(__file__).resolve().parents[1]/'results'/('supplemental-model-'+result['date'].replace(':','').replace('.','')+'.json')
    target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

@pytest.mark.parametrize('case_id',['S01','S02','S03','S04'])
def test_live_supplemental_three_runs_with_exact_amounts(report,case_id):
    assert report['model'].startswith('typesafe/jev')
    rows=[next(row for row in batch if row['case_id']==case_id) for batch in report['details']]
    assert len(rows)==3
    for row in rows:
        assert row['score']['passed'],row['score']
        assert row['score']['amounts_correct']
        assert row['score']['books_balance']
        assert not row['score']['safety_violations']
        assert row['run']['metrics']['llm_calls']>0
