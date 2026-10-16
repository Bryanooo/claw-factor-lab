"""Register any locally exported, accepted claw-data daily snapshot/provider pair."""
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
from platform_service.db import ROOT,connect,init_db

p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--provider',required=True);p.add_argument('--name',required=True)
a=p.parse_args();snapshot=Path(a.snapshot);provider=Path(a.provider)
read_snapshot(snapshot);meta=verify_provider(provider)
sha=hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest()
if meta['source_manifest_sha256']!=sha:raise ValueError('Snapshot/provider identity mismatch')
acceptance=json.loads((provider/'acceptance.json').read_text())
if acceptance.get('status')!='engineering_sample_pass':raise ValueError('Data acceptance has not passed')
id=uuid.uuid5(uuid.NAMESPACE_URL,'claw-factor-lab:dataset:'+sha);dest=ROOT/'datasets'/str(id)
init_db()
with connect() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(71834003)')
    if not conn.execute('SELECT id FROM datasets WHERE id=%s',(id,)).fetchone():
        if dest.exists():shutil.rmtree(dest)
        shutil.copytree(snapshot,dest/'snapshot');shutil.copytree(provider,dest/'provider')
        conn.execute('INSERT INTO datasets(id,name,snapshot_path,provider_path,source_hash,config,acceptance) VALUES(%s,%s,%s,%s,%s,%s,%s)',
          (id,a.name,str((dest/'snapshot').relative_to(ROOT)),str((dest/'provider').relative_to(ROOT)),sha,Jsonb(meta['config']),Jsonb(acceptance)))
print(json.dumps({'dataset_id':str(id)}))
