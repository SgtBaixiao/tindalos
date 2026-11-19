"""Behavioral acceptance for revisions, jobs, actual sessions and continuation."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from tindalos import history, memory_entries as memory, web
from tindalos.jobs import JobRuntime, CapacityError
from tindalos.serve import default_generate
from tindalos.eval_.runner import _claim_supported, _l4_faithfulness, _final_verdict


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv('TINDALOS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('TINDALOS_SITE_DB', str(tmp_path/'site.db'))
    monkeypatch.setenv('TINDALOS_FRONTEND_DIST', str(tmp_path/'absent'))
    history.init_db()
    return tmp_path


def campaign(cid='test'):
    return {'id':cid,'title':'Test','rules':'COC7','premise':'Hope is alive.', 'acts':[],
            'npcs':{'npc-1':{'id':'npc-1','name':'Hope','archetype':'witness'}},'clues':[],'relations':[]}


def wait(runtime, jid):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        job=runtime.get(jid)
        if job['status'] in ('completed','failed','cancelled','interrupted'):
            return job
        time.sleep(.01)
    pytest.fail('job did not terminate within 10 seconds')


def test_legacy_snapshot_migration_retains_revision(data):
    # Use the old owning schema, then run the additive migration twice.
    with sqlite3.connect(history.db_path()) as c:
        c.execute('DROP TABLE campaign_revisions')
        c.execute('DROP TABLE campaigns')
        c.execute('CREATE TABLE campaigns(id TEXT PRIMARY KEY,title TEXT,rules TEXT,created_at TEXT,event_count INTEGER,size INTEGER,snapshot_json TEXT)')
        c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?,?)',('old','Original','COC7','2026-01-01',0,10,json.dumps(campaign('old'))))
    history.init_db(); history.init_db()
    assert history.get_campaign('old')['revision']==1
    assert history.get_revision('old',1)['title']=='Test'
    assert len(history.list_revisions('old'))==1


def test_concurrent_edit_cas_preserves_winner(data):
    history.register_campaign('test','Test','COC7',campaign())
    from tindalos.workspace import save_node
    def save(i):
        try:
            return save_node('test','npc-1',{'description':str(i)},1)['revision']
        except history.RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(save,range(20)))
    assert results.count(2)==1 and results.count('conflict')==19
    assert history.get_revision('test',1)['npcs']['npc-1'].get('description') is None
    assert len(history.list_revisions('test'))==2


def test_jobs_idempotency_capacity_and_cancellation(data):
    release=threading.Event(); started=threading.Event(); calls=[]
    def generate(req,emit):
        calls.append(req); started.set(); release.wait(5)
        emit('write','finished model call')
        return campaign(req['id'])
    runtime=JobRuntime(generate,workers=1,capacity=2)
    try:
        a=runtime.submit({'id':'a'},key='same')
        assert started.wait(2)
        with ThreadPoolExecutor(max_workers=8) as pool:
            duplicates=list(pool.map(lambda _: runtime.submit({'id':'a'},key='same'),range(30)))
        assert {j['id'] for j in duplicates}=={a['id']}
        with pytest.raises(history.RevisionConflict): runtime.submit({'id':'b'},key='same')
        b=runtime.submit({'id':'b'})
        with pytest.raises(CapacityError): runtime.submit({'id':'c'})
        assert runtime.cancel(a['id'])['status']=='cancelled'
        release.set(); assert wait(runtime,b['id'])['status']=='completed'
        assert history.get_campaign('a') is None
        assert len(calls)==2
        frames=runtime.events(a['id']); terminal=[f for f in frames if f['payload'].get('done')]
        assert len(terminal)==1 and terminal[0]['payload']['status']=='cancelled'
    finally:
        release.set(); runtime.close()


def test_publish_failure_is_terminal_failure_without_revision(data,monkeypatch):
    runtime=JobRuntime(lambda req,emit:campaign())
    def fail(*args,**kwargs): raise OSError('disk full injection')
    monkeypatch.setattr(history,'_write_campaign',fail)
    try:
        job=runtime.submit({})
        assert wait(runtime,job['id'])['status']=='failed'
        assert history.get_campaign('test') is None
        assert runtime.events(job['id'])[-1]['payload']['status']=='failed'
    finally: runtime.close()


def test_interrupted_recovery_and_sequence_replay(data):
    runtime=JobRuntime(lambda req,emit:(emit('write','writing'),campaign())[1])
    j=runtime.submit({}); wait(runtime,j['id']); events=runtime.events(j['id'])
    assert [e['sequence'] for e in events]==list(range(1,len(events)+1))
    assert runtime.events(j['id'],events[0]['sequence'])==events[1:]
    runtime.close()
    with closing(history._connect()) as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=?",(j['id'],)); c.commit()
    recovered=JobRuntime(lambda req,emit:campaign())
    try:
        assert recovered.get(j['id'])['status']=='interrupted'
        assert recovered.events(j['id'])[-1]['payload']['status']=='interrupted'
    finally: recovered.close()


def test_actual_records_idempotency_audience_and_concurrent_indices(data):
    db=data/'memory.db'
    a=memory.record_session('a','Hope survived',db_path=db,request_key='one',facts=[{'subject_key':'Hope.life','content':'alive','audience':'public'}])
    b=memory.record_session('a','Hope survived',db_path=db,request_key='one',facts=[{'subject_key':'Hope.life','content':'alive','audience':'public'}])
    assert a['session_id']==b['session_id']
    with pytest.raises(memory.SessionConflict):
        memory.record_session('a','Hope survived',db_path=db,request_key='one',facts=[{'subject_key':'Hope.life','content':'dead'}])
    memory.record_session('a','The hidden room was found',db_path=db,facts=[{'subject_key':'room.secret','content':'hidden clue','audience':'kp'}])
    assert len(memory.current_facts('a',db,audience='public'))==1
    assert len(memory.current_facts('a',db,audience='kp'))==2
    assert memory.current_facts('b',db)==[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        indices=list(pool.map(lambda i: memory.record_session('a',f'Played {i}',db_path=db)['session_index'],range(20)))
    assert len(set(indices))==20
    assert 'hidden clue' in memory.continuation_context('a',db)


def frames(client,jid):
    res=client.get(f'/api/jobs/{jid}/events')
    return [json.loads(b.split('data:',1)[1]) for b in res.text.split('\n\n') if 'data:' in b]


def test_real_offline_import_edit_reload_session_continue_eval(data):
    import fitz
    pdf=fitz.open(); page=pdf.new_page(); page.insert_text((72,72),'Harbour mystery. Hope is alive. The key is in the cellar.')
    raw=pdf.tobytes(); pdf.close()
    with TestClient(web.create_app()) as client:
        upload=client.post('/api/modules',files={'file':('original.pdf',raw,'application/pdf')})
        assert upload.status_code==201
        mid=upload.json()['module']['id']
        original_body={'module_id':mid}
        submitted=client.post('/api/jobs',json=original_body,headers={'Idempotency-Key':'create'})
        assert submitted.status_code==202
        jid=submitted.json()['job']['id']; terminal=frames(client,jid)[-1]
        assert terminal['status']=='completed',terminal
        cid=terminal['campaign']['id']
        assert client.post('/api/jobs',json=original_body,headers={'Idempotency-Key':'create'}).json()['job']['id']==jid
        rec=client.get(f'/api/campaigns/{cid}').json(); node=rec['campaign']['acts'][0]['id']
        saved=client.patch(f'/api/campaigns/{cid}/nodes/{node}',json={'expected_revision':1,'patch':{'summary':'KP adopted edits'}})
        assert saved.status_code==200 and saved.json()['revision']==2
        assert client.patch(f'/api/campaigns/{cid}/nodes/{node}',json={'expected_revision':1,'patch':{'summary':'stale'}}).status_code==409
        assert client.get(f'/api/campaigns/{cid}').json()['campaign']['acts'][0]['summary']=='KP adopted edits'
        session={'summary':'Hope survived, the cellar key was recovered.','request_key':'played','facts':[{'subject_key':'Hope.life','content':'alive'}]}
        assert client.post(f'/api/sessions/{cid}',json=session).status_code==200
        session['facts'][0]['content']='dead'
        assert client.post(f'/api/sessions/{cid}',json=session).status_code==409
        continuation={'campaign_id':cid,'expected_revision':2}
        j=client.post('/api/jobs',json=continuation,headers={'Idempotency-Key':'continue'}).json()['job']
        after=frames(client,j['id'])[-1]; assert after['status']=='completed',after
        assert len(after['campaign']['acts'])==len(rec['campaign']['acts'])*2
        assert after['campaign']['acts'][0]['summary']=='KP adopted edits'
        assert after['revision']==3
        assert after['campaign']['npcs']==rec['campaign']['npcs']
        event_ids={e['id'] for a in after['campaign']['acts'] for s in a['scenes'] for e in s['events']}
        for clue in after['campaign']['clues']:
            for ref in clue['source_refs']:
                if ref.startswith('proposal:event:'):
                    assert ref.removeprefix('proposal:event:') in event_ids
                    assert ref.removeprefix('proposal:event:') in clue['linked_event_ids']
        assert client.post('/api/jobs',json=continuation,headers={'Idempotency-Key':'continue'}).json()['job']['id']==j['id']
        trace=client.post('/api/eval/run',json={'campaign_id':cid}).json()['trace']
        assert trace['params']['revision']==3
        assert trace['verdict']!='pass'  # Missing judge/human layer cannot mean acceptance.
        review=client.post(f"/api/eval/runs/{trace['run_id']}/reviews",json={'revision':3,'usable':False,'notes':'This clue needs an alternative acquisition path.'})
        assert review.status_code==201
        detail=client.get(f"/api/eval/runs/{trace['run_id']}").json()
        assert detail['run']['verdict']==trace['verdict']
        assert detail['annotations'][-1]['layer']=='L5'
        assert client.post(f"/api/eval/runs/{trace['run_id']}/reviews",json={'revision':2,'usable':True,'notes':'wrong version'}).status_code==409
        revisions=client.get(f'/api/campaigns/{cid}/revisions').json()['revisions']; assert len(revisions)==3
        restored=client.post(f'/api/campaigns/{cid}/restore/2',json={'expected_revision':3}).json()
        assert restored['revision']==4 and len(restored['campaign']['acts'])==len(rec['campaign']['acts'])


def test_evidence_unknown_is_not_entailment():
    assert not _claim_supported('霍普已经死亡',[{'score':1,'text':'霍普还活着'}])
    raw=campaign(); raw['npcs']['npc-1']['description']='霍普已经死亡'
    from tindalos.models import Campaign
    result=_l4_faithfulness(Campaign.model_validate(raw),lambda q,**kw:[{'score':1,'text':'霍普还活着'}],None)
    assert result['contradicted']>=1
    assert _final_verdict(True,{'L4':result})[0]=='fail'
    unrelated=_l4_faithfulness(Campaign.model_validate(raw),lambda q,**kw:[{'score':1,'text':'霍普不知道阿明还活着'}],None)
    assert unrelated['contradicted']==0
    assert _final_verdict(True,{'L3':{'status':'skipped'},'L5':{'status':'skipped'}})[0]=='warning'


def test_denied_or_quoted_claim_is_never_supported():
    assert not _claim_supported('霍普还活着', [{'score':1,'text':'霍普还活着只是谣言，实际上霍普已经死亡。'}])
    assert not _claim_supported('Hope is alive', [{'score':1,'text':'The witness says “Hope is alive”, but this is false.'}])
    assert _claim_supported('Hope is alive', [{'score':1,'text':'Hope is alive. The key is missing.'}])
    from tindalos.models import Campaign
    doc=campaign(); doc['npcs']['npc-1']['description']='霍普还活着'
    result=_l4_faithfulness(Campaign.model_validate(doc),lambda q,**kw:[{'score':1,'text':'霍普还活着只是谣言，实际上霍普已经死亡。'}],None)
    assert result['unknown']==1 and result['supported']==0
    result=_l4_faithfulness(Campaign.model_validate(doc),lambda q,**kw:[{'score':1,'text':'霍普已经死亡只是谣言。'}],None)
    assert result['unknown']==1 and result['contradicted']==0


def test_runtime_lease_and_expired_publication(data):
    runtime=JobRuntime(lambda req,emit:(time.sleep(.02),campaign())[1],max_duration=.001)
    try:
        with pytest.raises(RuntimeError,match='only one'): JobRuntime(lambda req,emit:campaign())
        job=runtime.submit({})
        assert wait(runtime,job['id'])['status']=='failed'
        assert history.get_campaign('test') is None
    finally: runtime.close()


def test_colon_domain_id_save_and_facts_before_long_summaries(data):
    from tindalos.workspace import save_node
    doc=campaign(); doc['npcs']={'source:npc-1':{**doc['npcs']['npc-1'],'id':'source:npc-1'}}
    history.register_campaign('test','Test','COC7',doc)
    assert save_node('test','source:npc-1',{'description':'adopted'},1)['revision']==2
    db=data/'memory.db'
    memory.record_session('test','x'*30000,db_path=db,facts=[{'subject_key':'Hope.life','content':'alive'}])
    assert 'Hope.life' in memory.continuation_context('test',db)


def test_event_replay_paginates_without_losing_terminal(data):
    def generate(req,emit):
        for i in range(300): emit('write',str(i))
        return campaign()
    runtime=JobRuntime(generate)
    try:
        job=runtime.submit({}); assert wait(runtime,job['id'])['status']=='completed'
        events=[]; after=0
        while page:=runtime.events(job['id'],after):
            events.extend(page); after=page[-1]['sequence']
        assert len(events)==302
        assert events[-1]['payload']['status']=='completed'
        assert [e['sequence'] for e in events]==list(range(1,303))
    finally: runtime.close()


def test_planner_called_once_and_edges_remain_original(data):
    from tindalos.generator import DeterministicGenerator
    from tindalos.pipeline import run_pipeline
    class Counter(DeterministicGenerator):
        acts=0; npcs=0
        def generate_acts(self,*args,**kw): self.acts+=1; return super().generate_acts(*args,**kw)
        def generate_npcs(self,*args,**kw): self.npcs+=1; return super().generate_npcs(*args,**kw)
    gen=Counter(); result=run_pipeline('Harbour',generator=gen)
    assert gen.acts==1 and gen.npcs==1
    for act in result['campaign'].acts:
        for scene in act.scenes:
            assert all(len(e.next_event_ids)<=1 for e in scene.events)
