"""Operator-only import from a read-only pilot export; idempotent by snapshot hash."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from psycopg.types.json import Jsonb
from adapter import read_snapshot
from pipeline import verify_provider
from platform_service.db import ROOT,connect,enqueue,event,init_db,seed_factors
from platform_service.artifacts import insert,inventory


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);args=p.parse_args()
    src=Path(args.source)
    snapshot=src/'snapshot-20261016';provider=src/'provider-20261016';run=src/'run-20261016-release'
    source,_=read_snapshot(snapshot);meta=verify_provider(provider)
    hashed=hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest()
    if meta['source_manifest_sha256']!=hashed:raise ValueError('Snapshot/provider mismatch')
    dsid=uuid.uuid5(uuid.NAMESPACE_URL,'claw-factor-lab:dataset:'+hashed)
    dest=ROOT/'datasets'/str(dsid)
    init_db();seed_factors()
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(71834003)')
        if not conn.execute('SELECT id FROM datasets WHERE id=%s',(dsid,)).fetchone():
            if dest.exists():shutil.rmtree(dest)  # Unregistered incomplete import only; stable private path.
            shutil.copytree(snapshot,dest/'snapshot');shutil.copytree(provider,dest/'provider')
            acceptance=json.loads((provider/'acceptance.json').read_text())
            conn.execute('''INSERT INTO datasets(id,name,snapshot_path,provider_path,source_hash,config,acceptance)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(dsid,'A-share fixed 12-stock pilot 2020–2025',
                str((dest/'snapshot').relative_to(ROOT)),str((dest/'provider').relative_to(ROOT)),hashed,Jsonb(meta['config']),Jsonb(acceptance)))
        request={'dataset_id':str(dsid),'idempotency_key':'import:'+hashed,'title':'Imported daily baseline pilot · 2026-10-16'}
        row,created=enqueue(conn,request,origin='import')
        if created:
            folder=ROOT/'runs'/str(row['id'])/'attempt-1'
            shutil.copytree(run,folder/'result')
            shutil.make_archive(str(folder/'research-bundle'),'zip',folder/'result')
            result=json.loads((folder/'result/results.json').read_text())
            if result['source_manifest_sha256']!=hashed:raise ValueError('Result references different snapshot')
            insert(conn,inventory(row['id'],1,folder))
            conn.execute("UPDATE runs SET status='succeeded',attempt=1,result=%s,started_at=%s,finished_at=%s WHERE id=%s",
                         (Jsonb(result),result['created_at'],result['created_at'],row['id']))
            event(conn,row['id'],'imported',{'original_created_at':result['created_at']})
    print(json.dumps({'dataset_id':str(dsid),'imported_run_id':str(row['id']),'created':created}))


if __name__=='__main__':main()
