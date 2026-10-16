"""Platform contracts run only against the explicitly selected disposable test DB."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

pytestmark=pytest.mark.skipif(os.environ.get('DB_NAME')!='factorlab_test',reason='Requires isolated factorlab_test database')


@pytest.fixture
def client():
    from platform_service.api import app
    from platform_service.db import connect,init_db
    init_db()
    with connect() as conn:conn.execute('TRUNCATE schedules,artifacts,run_events,runs,datasets,factor_sets,factor_versions,heartbeats RESTART IDENTITY CASCADE')
    with TestClient(app,base_url="http://localhost") as c:yield c


def headers():return {}


def dataset():
    from platform_service.db import connect
    did=uuid.uuid4();config=json.loads(Path('config/pilot.json').read_text())
    with connect() as conn:
        conn.execute('INSERT INTO datasets(id,name,snapshot_path,provider_path,source_hash,config,acceptance) VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (did,'test','test/snapshot','test/provider',str(did),Jsonb(config),Jsonb({'status':'engineering_sample_pass'})))
    return str(did)


def request(did=None):return {'dataset_id':did or dataset(),'title':'test research','idempotency_key':str(uuid.uuid4())}


def test_local_access_and_factor_registry(client):
    assert client.get('/api/v1/runs').status_code==200
    assert client.get('/api/v1/runs',headers={'Host':'untrusted.example'}).status_code==400
    assert client.get('/health/ready').status_code==200
    d=client.get('/api/v1/factors',headers=headers()).json()
    assert d['total']==159
    assert len(client.get('/api/v1/factor-sets',headers=headers()).json())==2


def test_idempotency_and_conflict(client):
    body=request();a=client.post('/api/v1/runs',json=body,headers=headers());b=client.post('/api/v1/runs',json=body,headers=headers())
    assert a.status_code==202 and b.status_code==202
    assert a.json()['id']==b.json()['id'] and not b.json()['created']
    body['title']='changed'
    assert client.post('/api/v1/runs',json=body,headers=headers()).status_code==409


def test_unknown_dataset_and_universe_limit(client):
    assert client.post('/api/v1/runs',json=request(str(uuid.uuid4())),headers=headers()).status_code==404
    body=request();body['parameters']={'topk':20}
    assert client.post('/api/v1/runs',json=body,headers=headers()).status_code==409


def test_reject_unknown_parameters(client):
    body=request();body['parameters']={'python':'print(1)'}
    assert client.post('/api/v1/runs',json=body,headers=headers()).status_code==422


def test_cancel_and_retry_are_recorded(client):
    rid=client.post('/api/v1/runs',json=request(),headers=headers()).json()['id']
    assert client.post(f'/api/v1/runs/{rid}/cancel',headers=headers()).status_code==200
    assert client.get(f'/api/v1/runs/{rid}',headers=headers()).json()['status']=='cancelled'
    assert client.post(f'/api/v1/runs/{rid}/retry',headers=headers()).status_code==202
    assert client.get(f'/api/v1/runs/{rid}',headers=headers()).json()['status']=='queued'
    assert client.post(f'/api/v1/runs/{rid}/retry',headers=headers()).status_code==409


def test_spec_freezes_factor_members(client):
    rid=client.post('/api/v1/runs',json=request(),headers=headers()).json()['id']
    d=client.get('/api/v1/runs/'+rid,headers=headers()).json()
    assert d['spec']['engine']=='qlib-0.9.7-claw-v1'
    assert sorted(len(f['members']) for f in d['spec']['factor_sets'])==[1,158]


def test_concurrent_idempotency(client):
    from platform_service.db import connect,enqueue
    body=request()
    def submit(_):
        with connect() as c:return enqueue(c,body)[0]['id']
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(submit,range(2)))
    assert ids[0]==ids[1]


def test_claim_and_expired_lease(client):
    from platform_service.worker import claim
    from platform_service.db import connect
    rid=client.post('/api/v1/runs',json=request(),headers=headers()).json()['id']
    first=claim();assert str(first['id'])==rid and first['attempt']==1
    assert claim() is None
    with connect() as c:c.execute("UPDATE runs SET lease_until=now()-interval '1 second' WHERE id=%s",(rid,))
    assert claim() is None
    d=client.get('/api/v1/runs/'+rid,headers=headers()).json()
    assert d['status']=='failed' and d['events'][-1]['event']=='lease_expired'


def test_schedule_defaults_disabled_and_fires_once(client):
    from platform_service.scheduler import tick
    from platform_service.db import connect
    body={'name':'test schedule','research':request(),'interval_seconds':60,'first_run_at':(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()}
    r=client.post('/api/v1/schedules',json=body,headers=headers());assert r.status_code==201
    sid=r.json()['id'];tick()
    assert client.get('/api/v1/runs',headers=headers()).json()['total']==0
    with connect() as c:c.execute('UPDATE schedules SET enabled=true WHERE id=%s',(sid,))
    tick();tick()
    assert client.get('/api/v1/runs',headers=headers()).json()['total']==1


def test_artifact_path_escape_refused(client):
    from platform_service.db import connect
    rid=client.post('/api/v1/runs',json=request(),headers=headers()).json()['id'];aid=uuid.uuid4()
    with connect() as c:c.execute('INSERT INTO artifacts(id,run_id,attempt,name,path,sha256,size,media_type) VALUES(%s,%s,1,%s,%s,%s,1,%s)',
        (aid,rid,'escape','../etc/passwd','fake','text/plain'))
    assert client.get('/api/v1/artifacts/'+str(aid),headers=headers()).status_code==404


def test_schedule_rejects_invalid_universe(client):
    research=request();research['parameters']={'topk':20}
    body={'name':'invalid','research':research,'interval_seconds':60,'first_run_at':datetime.now(timezone.utc).isoformat()}
    assert client.post('/api/v1/schedules',json=body,headers=headers()).status_code==409
