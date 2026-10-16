import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

from psycopg.types.json import Jsonb
from .artifacts import insert, inventory
from .db import ENGINE, ALPHA191_ENGINE, ROOT, connect, event, heartbeat, init_db

STOP=False

def stop(*_):
    global STOP
    STOP=True


def claim():
    with connect() as conn:
        expired=conn.execute("SELECT id,cancel_requested FROM runs WHERE status='running' AND lease_until<now() FOR UPDATE SKIP LOCKED").fetchall()
        for row in expired:
            status='cancelled' if row['cancel_requested'] else 'failed'
            conn.execute("UPDATE runs SET status=%s,error='Worker lease expired; explicit retry available',finished_at=now(),lease_token=NULL WHERE id=%s",(status,row['id']))
            event(conn,row['id'],'lease_expired',{'status':status})
        row=conn.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if row is None:return None
        token=uuid.uuid4()
        row=conn.execute("""UPDATE runs SET status='running',attempt=attempt+1,worker_id=%s,lease_token=%s,
            lease_until=now()+interval '120 seconds',started_at=now(),finished_at=NULL,error=NULL
            WHERE id=%s RETURNING *""",(os.environ.get('WORKER_ID','daily-worker'),token,row['id'])).fetchone()
        event(conn,row['id'],'started',{'attempt':row['attempt'],'engine':row['spec']['engine']})
        return row


def renew(row):
    with connect() as conn:
        current=conn.execute("""UPDATE runs SET lease_until=now()+interval '120 seconds'
            WHERE id=%s AND lease_token=%s AND status='running' RETURNING cancel_requested""",(row['id'],row['lease_token'])).fetchone()
    return current


def kill(child):
    if child.poll() is not None:return
    os.killpg(child.pid,signal.SIGTERM)
    try:child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid,signal.SIGKILL);child.wait()


def execute(row):
    folder=ROOT/'runs'/str(row['id'])/f'attempt-{row["attempt"]}'
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'spec.json').write_text(json.dumps(row['spec'],indent=2))
    state='failed';error=None;result=None;child=None
    try:
        if row['spec']['engine'] not in (ENGINE,ALPHA191_ENGINE):raise ValueError('Unsupported engine version for this worker')
        with connect() as conn:ds=conn.execute('SELECT * FROM datasets WHERE id=%s',(row['dataset_id'],)).fetchone()
        if row['spec']['engine']==ALPHA191_ENGINE:
            from factors.alpha191 import implementation_hash
            if row['spec'].get('factor_implementation_sha256')!=implementation_hash():raise ValueError('Factor implementation changed; use the original engine image')
        from pipeline import verify_provider
        source=ROOT/ds['provider_path'];manifest=verify_provider(source)
        if manifest['source_manifest_sha256']!=row['spec']['source_hash']:raise ValueError('Dataset source identity changed')
        provider=folder/'provider'
        shutil.copytree(source,provider)
        manifest['config']=row['spec']['config']
        (provider/'manifest.json').write_text(json.dumps(manifest,indent=2))
        child_env={k:v for k,v in os.environ.items() if k not in ('FACTORLAB_API_KEY','DB_PASSWORD','POSTGRES_PASSWORD')}
        with (folder/'execution.log').open('w') as log:
            child=subprocess.Popen([sys.executable,'/workspace/pipeline.py','research','--provider',str(provider),
                '--out',str(folder/'result')],cwd='/workspace',env=child_env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=time.monotonic()
            while child.poll() is None:
                heartbeat('worker')
                lease=renew(row)
                if not lease:raise RuntimeError('Lease ownership lost')
                if lease['cancel_requested'] or STOP:
                    kill(child);state='cancelled' if lease['cancel_requested'] else 'failed'
                    raise RuntimeError('Cancelled by user' if lease['cancel_requested'] else 'Worker shutting down; retry available')
                if time.monotonic()-started>3600:raise TimeoutError('Research exceeded one-hour execution limit')
                time.sleep(2)
        if child.returncode!=0:raise RuntimeError(f'Research process exited with code {child.returncode}; see execution.log')
        result=json.loads((folder/'result/results.json').read_text())
        if result.get('status')!='engineering_workflow_complete':raise ValueError('Invalid result status')
        shutil.make_archive(str(folder/'research-bundle'),'zip',folder/'result')
        state='succeeded'
    except Exception as exc:
        if child:kill(child)
        error=f'{type(exc).__name__}: {exc}'
    # Do not index temporary provider copies; the immutable dataset is already registered.
    if (folder/'provider').exists():shutil.rmtree(folder/'provider')
    items=inventory(row['id'],row['attempt'],folder)
    with connect() as conn:
        owner=conn.execute("SELECT cancel_requested FROM runs WHERE id=%s AND lease_token=%s AND status='running' FOR UPDATE",(row['id'],row['lease_token'])).fetchone()
        if not owner:return
        if owner['cancel_requested']:
            state='cancelled';result=None;error='Cancellation requested before result publication'
        insert(conn,items)
        conn.execute('''UPDATE runs SET status=%s,result=%s,error=%s,finished_at=now(),lease_until=NULL,lease_token=NULL
            WHERE id=%s''',(state,Jsonb(result),error,row['id']))
        event(conn,row['id'],state,{'attempt':row['attempt'],'artifact_count':len(items),'error':error})


def main():
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    init_db()
    while not STOP:
        try:
            heartbeat('worker');row=claim()
            if row:execute(row)
            else:time.sleep(2)
        except Exception as exc:
            print('Worker error:',type(exc).__name__,flush=True);time.sleep(3)


if __name__=='__main__':main()
