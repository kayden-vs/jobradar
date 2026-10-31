import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from tests.conftest import make_job
from pipeline.prefilter import prefilter, check_experience, check_ats_location
from pipeline.ranker import _recency_bonus
from storage import work
from storage.db import save_job, pending_notifications, make_job_id, make_url_id
from pipeline.dedup import deduplicate


def test_requirement_context_and_degree(profile):
    assert not check_experience('Work alongside senior engineers.', 'Backend Intern', profile)[0]
    for title in ['Staff Frontend Engineer','Director of Engineering','PhD Intern, Software Engineer']:
        assert not prefilter([make_job(title=title)],profile)
    assert not prefilter([make_job(description='2025 and 2026 graduates only eligible.')],profile)
    assert prefilter([make_job(description='2026-2027 graduates eligible.')],profile)
    assert check_ats_location(make_job(location='United States - Remote'))[0]
    assert not check_ats_location(make_job(location='India; United States - Remote'))[0]


def test_recency_format_independent(profile):
    from datetime import timedelta
    for days in [7,12,20]:
        iso=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        assert _recency_bonus(f'{days} days ago',profile.get('ranker_weights',{'recency_7d':4,'recency_14d':2,'recency_30d':1})) == _recency_bonus(iso,{'recency_7d':4,'recency_14d':2,'recency_30d':1})


def test_url_identity_and_richer_merge(tmp_db):
    a=make_job(description='stub')
    b=make_job(title='Software Intern',description='richer description',source='serper')
    result=deduplicate([a,b],tmp_db)
    assert len(result)==1 and result[0]['description']=='richer description'
    assert set(result[0]['sources']) == {'greenhouse','serper'}
    assert make_job_id(a)!=make_job_id(dict(a,url=a['url']+'2'))
    assert make_url_id(dict(a,url='https://example.com/x?utm_source=hi&id=4')) == make_url_id(dict(a,url='https://example.com/x?id=4'))
    assert make_job_id(make_job(title='Intern 2026',url='')) != make_job_id(make_job(title='Intern 2027',url=''))


def test_pending_cache_and_notification_ack(tmp_db,profile):
    job=make_job(score=7)
    work.put('score',work.identity(job),job,tmp_db)
    assert len(work.pending('score',tmp_db))==1
    work.record(job,profile,tmp_db)
    assert work.cached(job,profile,tmp_db)['score']==7
    assert work.cached(dict(job,description='new requirements'),profile,tmp_db) is None
    save_job(job,score=7,db_path=tmp_db)
    from notify.telegram_bot import deliver_pending
    with patch('notify.telegram_bot.Bot') as bot, patch('notify.telegram_bot.asyncio.sleep',new=AsyncMock()):
        bot.return_value.__aenter__.return_value.send_message=AsyncMock(side_effect=RuntimeError('offline'))
        assert deliver_pending(tmp_db)==(0,0)
        assert len(pending_notifications(tmp_db))==1
        bot.return_value.__aenter__.return_value.send_message=AsyncMock()
        assert deliver_pending(tmp_db)==(0,1)
        assert pending_notifications(tmp_db)==[]


def test_provider_retry_and_circuit(monkeypatch):
    from pipeline import ai
    class Failure(Exception):
        code=503
    client=MagicMock()
    client.models.generate_content.side_effect=Failure()
    monkeypatch.setattr(ai,'_failures',0)
    monkeypatch.setattr(ai,'_open_until',0)
    with patch.object(ai,'gemini_throttle'),patch.object(ai.time,'sleep'):
        for _ in range(3):
            with pytest.raises(ai.ProviderUnavailable): ai.generate(client,model='test')
        assert client.models.generate_content.call_count==9
        with pytest.raises(ai.ProviderUnavailable): ai.generate(client,model='test')
        assert client.models.generate_content.call_count==9
    monkeypatch.setattr(ai,'_open_until',0)
    monkeypatch.setattr(ai,'_failures',0)


def test_failure_is_pending_not_low(tmp_db,profile):
    from pipeline import scorer
    job=make_job()
    with patch.object(scorer,'score_job',return_value=dict(job,score=-1,evaluation_status='failed')):
        assert scorer.score_all([job],profile,tmp_db)==([],[],[])
    assert work.pending('score',tmp_db)
    assert work.cached(job,profile,tmp_db) is None


@pytest.mark.parametrize('source',['freshers_blogs/site','greenhouse'])
def test_slot_backfill_and_source_family(tmp_db,profile,source):
    from pipeline import scorer
    profile['hard_reject'].update(max_ai_jobs_per_run=120,tier_a_size=100)
    jobs=[make_job(company=f'Company{i}',url=f'https://example.org/{i}',source='greenhouse' if i<100 else source) for i in range(120)]
    with patch('pipeline.enrich.enrich',side_effect=lambda j:j), patch.object(scorer,'score_job',side_effect=lambda j,p:dict(j,score=2)):
        urgent,digest,low=scorer.score_all(jobs,profile,tmp_db)
    assert len(low)==120


def test_telegram_batch_persists_and_recovers(tmp_db,monkeypatch):
    from sources import telegram_channels as tg
    monkeypatch.setenv('JOBRADAR_DB_PATH',tmp_db)
    raw='Hiring Backend Intern at Acme. Apply https://example.org/job'
    posts=[(raw,datetime.now(timezone.utc),'internfreak')]
    with patch.object(tg,'_gemini_client'),patch.object(tg,'generate',side_effect=RuntimeError('503')):
        assert tg._parse_posts_with_gemini(posts)==[]
    assert len(work.pending('telegram',tmp_db))==1
    response=SimpleNamespace(text=json.dumps([dict(post_index=1,title='Backend Intern',company='Acme',url='https://example.org/job')]))
    with patch.object(tg,'_gemini_client'),patch.object(tg,'generate',return_value=response):
        jobs=tg._parse_posts_with_gemini([])
    assert jobs[0]['description']==raw
    assert work.pending('telegram',tmp_db)==[]


def test_provider_transient_recovers_and_client_error_does_not_retry(monkeypatch):
    from pipeline import ai
    class Transient(Exception): code=503
    class BadRequest(Exception): code=400
    client=MagicMock()
    monkeypatch.setattr(ai,'_open_until',0)
    monkeypatch.setattr(ai,'_failures',0)
    response=object()
    with patch.object(ai,'gemini_throttle') as throttle,patch.object(ai.time,'sleep'):
        client.models.generate_content.side_effect=[Transient(),response]
        assert ai.generate(client,model='test') is response
        assert throttle.call_count==2
        client.models.generate_content.reset_mock()
        client.models.generate_content.side_effect=BadRequest()
        with pytest.raises(BadRequest): ai.generate(client,model='test')
        assert client.models.generate_content.call_count==1


def test_legacy_rows_preserve_delivery_and_refresh_evidence(tmp_db):
    import sqlite3
    from storage.db import init_db,mark_job_notified
    job=make_job()
    save_job(job,score=8,db_path=tmp_db)
    mark_job_notified(job,1,tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        conn.execute("UPDATE jobs SET id='legacy-title-id'")
    init_db(tmp_db)
    save_job(dict(job,description='Updated full job description'),score=9,db_path=tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        rows=conn.execute('SELECT id,score,notified,description FROM jobs').fetchall()
    assert rows==[('legacy-title-id',9,1,'Updated full job description')]
    assert pending_notifications(tmp_db)==[]


def test_incomplete_jobs_defer_without_scoring(tmp_db,profile):
    from pipeline import scorer
    job=make_job(source='internshala',description_complete=False)
    with patch('pipeline.enrich.enrich',side_effect=lambda j:j),patch.object(scorer,'score_job') as score:
        assert scorer.score_all([job],profile,tmp_db)==([],[],[])
        score.assert_not_called()
    assert len(work.pending('score',tmp_db))==1


def test_cached_rejection_does_not_consume_next_run_budget(tmp_db,profile):
    from pipeline import scorer
    job=make_job(description_complete=True)
    with patch.object(scorer,'score_job',side_effect=lambda j,p:dict(j,score=2)) as score:
        assert len(scorer.score_all([dict(job)],profile,tmp_db)[2])==1
        assert scorer.score_all([dict(job)],profile,tmp_db)==([],[],[])
        assert score.call_count==1


def test_new_expiry_evidence_withdraws_pending_match(tmp_db,profile):
    from pipeline import scorer
    job=make_job(description_complete=True)
    save_job(job,score=8,db_path=tmp_db)
    with patch.object(scorer,'score_job',return_value=dict(job,score=1,expired=True)):
        assert scorer.score_all([job],profile,tmp_db)==([],[],[])
    assert pending_notifications(tmp_db)==[]
