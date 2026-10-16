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
ALPHA191_ENGINE = 'qlib-0.9.7-claw-alpha191-v1'


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

        from factors.alpha191 import FORMULAS,VERSION,SET_ID,NOTES,required_fields,implementation_hash
        members=[]
        digest=implementation_hash()
        for number,expr in FORMULAS.items():
            name=f'alpha191.{number:03d}';fid=name+'@'+VERSION;members.append(fid)
            description='GTJA Alpha191; inputs: '+','.join(required_fields(number))+'. '+NOTES.get(number,'See versioned operator conventions.')
            conn.execute('INSERT INTO factor_versions(id,name,version,expression,description,engine_version,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (fid,name,VERSION,expr,description,ALPHA191_ENGINE,canonical_hash({'expression':expr,'implementation':digest})))
        set_hash=canonical_hash({'members':members,'implementation':digest})
        old=conn.execute('SELECT content_hash FROM factor_sets WHERE id=%s',(SET_ID,)).fetchone()
        if old and old['content_hash']!=set_hash:raise RuntimeError('Alpha191 implementation changed: publish a new factor-set version')
        conn.execute('INSERT INTO factor_sets(id,name,version,members,content_hash) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (SET_ID,'Alpha191',VERSION,Jsonb(members),set_hash))


def event(conn, run_id, name, details=None):
    conn.execute('INSERT INTO run_events(run_id,event,details) VALUES(%s,%s,%s)',(run_id,name,Jsonb(details or {})))


def enqueue(conn, request, origin='api'):
    from .models import RunRequest
    request=RunRequest.model_validate(request).model_dump(mode='json')
    hash_request=dict(request)
    if request['factor_set']=='alpha158@qlib-0.9.7' and request['missing_factor_policy']=='error':
        hash_request.pop('factor_set');hash_request.pop('missing_factor_policy')
    key=request['idempotency_key']; hashed=canonical_hash(hash_request)
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
    selected=request['factor_set']; excluded={}
    if selected=='alpha191@gtja-2017-v1':
        from factors.qlib_bridge import missing_inputs
        excluded=missing_inputs(ROOT/ds['provider_path'])
        if excluded and request['missing_factor_policy']=='error':
            raise ValueError('Alpha191 missing required inputs: '+str(excluded)+'. Supply data or explicitly choose missing_factor_policy=exclude.')
        config.update(factor_set=selected,missing_factor_policy=request['missing_factor_policy'])
    factor_sets=conn.execute('SELECT id,content_hash,members FROM factor_sets WHERE id IN (%s,%s) ORDER BY id',(selected,'momentum20@1')).fetchall()
    if len(factor_sets)!=2:raise ValueError('Selected factor set not initialized')
    spec={'template':'daily-alpha191-v1' if selected.startswith('alpha191') else 'daily-baselines-v1',
          'engine':ALPHA191_ENGINE if selected.startswith('alpha191') else ENGINE,'source_hash':ds['source_hash'],
          'factor_sets':factor_sets,'excluded_factors':excluded,'config':config}
    if selected.startswith('alpha191'):
        from factors.alpha191 import implementation_hash
        spec['factor_implementation_sha256']=implementation_hash()
    rid=uuid.uuid4()
    row=conn.execute('''INSERT INTO runs(id,idempotency_key,request_hash,title,external_ref,dataset_id,spec,status,origin)
        VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING *''',
        (rid,key,hashed,request['title'],request['external_ref'],ds['id'],Jsonb(spec),origin)).fetchone()
    event(conn,rid,'queued',{'origin':origin})
    return row,True


def heartbeat(name):
    with connect() as conn:
        conn.execute('INSERT INTO heartbeats(name) VALUES(%s) ON CONFLICT(name) DO UPDATE SET updated_at=now()',(name,))
