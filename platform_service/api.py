from contextlib import asynccontextmanager
from pathlib import Path
import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb

from .db import ROOT, connect, enqueue, event, init_db, seed_factors
from .models import RunRequest, ScheduleRequest


@asynccontextmanager
async def lifespan(app):
    init_db();seed_factors()
    yield


app=FastAPI(title='claw-factor-lab',version='0.1.0',lifespan=lifespan)


# Local-only deployment; reject unexpected Host headers (including DNS rebinding).
from starlette.middleware.trustedhost import TrustedHostMiddleware
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','api'])

api=APIRouter(prefix='/api/v1')


@app.get('/',include_in_schema=False)
def home():
    return FileResponse(Path(__file__).resolve().parent.parent/'web/index.html')


@app.get('/health/live')
def live():return {'status':'ok'}


@app.get('/health/ready')
def ready():
    try:
        with connect() as conn:conn.execute('SELECT 1')
    except Exception:raise HTTPException(503,'Database unavailable')
    return {'status':'ready'}


@api.get('/status')
def status():
    with connect() as conn:
        counts=conn.execute('SELECT status,count(*) FROM runs GROUP BY status').fetchall()
        beats=conn.execute("SELECT name,updated_at,updated_at>now()-interval '60 seconds' AS healthy FROM heartbeats ORDER BY name").fetchall()
    return {'service':'claw-factor-lab','version':'0.1.0','runs':counts,'services':beats,'template':'daily-baselines-v1'}


@api.get('/datasets')
def datasets():
    with connect() as conn:
        return conn.execute('SELECT id,name,source_hash,config,acceptance,created_at FROM datasets ORDER BY created_at DESC').fetchall()


@api.get('/factors')
def factors(q:str=Query(default='',max_length=120),limit:int=Query(default=50,ge=1,le=200),offset:int=Query(default=0,ge=0)):
    with connect() as conn:
        params=('%'+q+'%',)
        total=conn.execute('SELECT count(*) AS n FROM factor_versions WHERE name ILIKE %s',params).fetchone()['n']
        rows=conn.execute('SELECT * FROM factor_versions WHERE name ILIKE %s ORDER BY id LIMIT %s OFFSET %s',params+(limit,offset)).fetchall()
    return {'items':rows,'total':total,'limit':limit,'offset':offset}


@api.get('/factor-sets')
def factor_sets():
    with connect() as conn:return conn.execute('SELECT * FROM factor_sets ORDER BY id').fetchall()


@api.post('/runs',status_code=202)
def submit(body:RunRequest):
    try:
        with connect() as conn:row,created=enqueue(conn,body.model_dump(mode='json'))
    except LookupError as exc:raise HTTPException(404,str(exc))
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'id':row['id'],'status':row['status'],'created':created}


@api.get('/runs')
def runs(status:str|None=None,limit:int=Query(default=30,ge=1,le=200),offset:int=Query(default=0,ge=0)):
    with connect() as conn:
        rows=conn.execute('''SELECT id,title,external_ref,status,attempt,dataset_id,created_at,started_at,finished_at,error,origin
            FROM runs WHERE (%s::text IS NULL OR status=%s) ORDER BY created_at DESC,id LIMIT %s OFFSET %s''',(status,status,limit,offset)).fetchall()
        total=conn.execute('SELECT count(*) AS n FROM runs WHERE (%s::text IS NULL OR status=%s)',(status,status)).fetchone()['n']
    return {'items':rows,'total':total,'limit':limit,'offset':offset}


@api.get('/runs/{run_id}')
def run(run_id:uuid.UUID):
    with connect() as conn:
        row=conn.execute('SELECT * FROM runs WHERE id=%s',(run_id,)).fetchone()
        if not row:raise HTTPException(404,'Run not found')
        row.pop('lease_token',None)
        row['artifacts']=conn.execute('SELECT id,name,sha256,size,media_type,attempt FROM artifacts WHERE run_id=%s ORDER BY attempt,name',(run_id,)).fetchall()
        row['events']=conn.execute('SELECT event,details,created_at FROM run_events WHERE run_id=%s ORDER BY id',(run_id,)).fetchall()
    return row


@api.post('/runs/{run_id}/cancel')
def cancel(run_id:uuid.UUID):
    with connect() as conn:
        row=conn.execute('SELECT status FROM runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
        if not row:raise HTTPException(404,'Run not found')
        if row['status']=='queued':
            conn.execute("UPDATE runs SET status='cancelled',cancel_requested=true,finished_at=now() WHERE id=%s",(run_id,))
        elif row['status']=='running':conn.execute('UPDATE runs SET cancel_requested=true WHERE id=%s',(run_id,))
        else:raise HTTPException(409,'Only queued/running tasks can be cancelled')
        event(conn,run_id,'cancel_requested')
    return {'id':run_id,'cancel_requested':True}


@api.post('/runs/{run_id}/retry',status_code=202)
def retry(run_id:uuid.UUID):
    with connect() as conn:
        row=conn.execute("UPDATE runs SET status='queued',cancel_requested=false,error=NULL,finished_at=NULL,lease_token=NULL,lease_until=NULL WHERE id=%s AND status IN ('failed','cancelled') RETURNING id",(run_id,)).fetchone()
        if not row:raise HTTPException(409,'Only failed/cancelled tasks can be retried')
        event(conn,run_id,'retry_requested')
    return {'id':run_id,'status':'queued'}


@api.get('/artifacts/{artifact_id}')
def download(artifact_id:uuid.UUID):
    with connect() as conn:row=conn.execute('SELECT * FROM artifacts WHERE id=%s',(artifact_id,)).fetchone()
    if not row:raise HTTPException(404,'Artifact not found')
    path=(ROOT/row['path']).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():raise HTTPException(404,'Artifact unavailable')
    return FileResponse(path,media_type=row['media_type'],filename=Path(row['name']).name,
                        headers={'ETag':'"'+row['sha256']+'"','X-Content-Type-Options':'nosniff'})


@api.get('/schedules')
def schedules():
    with connect() as conn:return conn.execute('SELECT * FROM schedules ORDER BY created_at DESC').fetchall()


@api.post('/schedules',status_code=201)
def add_schedule(body:ScheduleRequest):
    sid=uuid.uuid4()
    with connect() as conn:
        ds=conn.execute('SELECT config FROM datasets WHERE id=%s',(body.research.dataset_id,)).fetchone()
        if not ds:raise HTTPException(404,'Dataset not found')
        if body.research.parameters.topk>len(ds['config']['symbols']):
            raise HTTPException(409,'topk exceeds dataset universe')
        conn.execute('''INSERT INTO schedules(id,name,request,interval_seconds,enabled,next_run_at) VALUES(%s,%s,%s,%s,%s,%s)''',
            (sid,body.name,Jsonb(body.research.model_dump(mode='json')),body.interval_seconds,body.enabled,body.first_run_at))
    return {'id':sid,'enabled':body.enabled}


@api.post('/schedules/{schedule_id}/disable')
def disable_schedule(schedule_id:uuid.UUID):
    with connect() as conn:
        if not conn.execute('UPDATE schedules SET enabled=false WHERE id=%s RETURNING id',(schedule_id,)).fetchone():raise HTTPException(404,'Schedule not found')
    return {'id':schedule_id,'enabled':False}


app.include_router(api)
