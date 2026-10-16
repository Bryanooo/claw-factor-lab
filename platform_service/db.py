import hashlib
import json
import os
from pathlib import Path
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(os.environ.get('STORAGE_ROOT', '/storage'))
ENGINE = 'qlib-0.9.7-claw-v1'


def connect():
    return psycopg.connect(host=os.environ.get('DB_HOST','localhost'), dbname=os.environ.get('DB_NAME','factorlab'),
        user=os.environ.get('DB_USER','factorlab'), password=os.environ['DB_PASSWORD'],
        row_factory=dict_row, connect_timeout=5)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def init_db():
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(71834001)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())


def seed_factors():
    from qlib.contrib.data.loader import Alpha158DL
    expressions,names=Alpha158DL.get_feature_config()
    rows=[]
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(71834002)')
        for name,expr in zip(names,expressions):
            fid='alpha158.'+name+'@qlib-0.9.7'
            conn.execute('''INSERT INTO factor_versions(id,name,version,expression,description,engine_version,content_hash)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (fid,'alpha158.'+name,'qlib-0.9.7',expr,'Qlib Alpha158 built-in feature',ENGINE,canonical_hash(expr)))
            rows.append(fid)
        mid='momentum20@1'
        expr='$close/Ref($close,20)-1'
        conn.execute('''INSERT INTO factor_versions(id,name,version,expression,description,engine_version,content_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (mid,'momentum20','1',expr,'20 trading-session adjusted-price momentum',ENGINE,canonical_hash(expr)))
        for fid,name,members in [('alpha158@qlib-0.9.7','Alpha158',rows),('momentum20@1','Momentum20',[mid])]:
            conn.execute('INSERT INTO factor_sets(id,name,version,members,content_hash) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (fid,name,'1',Jsonb(members),canonical_hash(members)))


def event(conn, run_id, name, details=None):
    conn.execute('INSERT INTO run_events(run_id,event,details) VALUES(%s,%s,%s)',(run_id,name,Jsonb(details or {})))


def enqueue(conn, request, origin='api'):
    from .models import RunRequest
    request=RunRequest.model_validate(request).model_dump(mode='json')
    key=request['idempotency_key']; hashed=canonical_hash(request)
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
    old=conn.execute('SELECT * FROM runs WHERE idempotency_key=%s',(key,)).fetchone()
    if old:
        if old['request_hash']!=hashed:raise ValueError('Idempotency key already belongs to another request')
        return old,False
    ds=conn.execute('SELECT * FROM datasets WHERE id=%s',(request['dataset_id'],)).fetchone()
    if ds is None:raise LookupError('Dataset not registered')
    config=dict(ds['config']);config.update(request['parameters'])
    if config['topk']>len(config['symbols']) or config['n_drop']>config['topk']:
        raise ValueError('topk/n_drop exceed dataset universe or portfolio size')
    factor_sets=conn.execute('SELECT id,content_hash,members FROM factor_sets ORDER BY id').fetchall()
    # v0.1 exposes exactly the verified pair; external registrations cannot alter this template.
    factor_sets=[f for f in factor_sets if f['id'] in ('alpha158@qlib-0.9.7','momentum20@1')]
    if len(factor_sets)!=2:raise ValueError('Built-in factor sets not initialized')
    spec={'template':'daily-baselines-v1','engine':ENGINE,'source_hash':ds['source_hash'],
          'factor_sets':factor_sets,'config':config}
    rid=uuid.uuid4()
    row=conn.execute('''INSERT INTO runs(id,idempotency_key,request_hash,title,external_ref,dataset_id,spec,status,origin)
        VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING *''',
        (rid,key,hashed,request['title'],request['external_ref'],ds['id'],Jsonb(spec),origin)).fetchone()
    event(conn,rid,'queued',{'origin':origin})
    return row,True


def heartbeat(name):
    with connect() as conn:
        conn.execute('INSERT INTO heartbeats(name) VALUES(%s) ON CONFLICT(name) DO UPDATE SET updated_at=now()',(name,))
