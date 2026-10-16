"""Create a new immutable snapshot/provider pair with audited claw-data FF returns.
Never overwrite or modify an already registered dataset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from adapter import read_snapshot
from pipeline import verify_provider


def attach(snapshot,provider,ff,description,out):
    snapshot=Path(snapshot);provider=Path(provider);ff=Path(ff);out=Path(out)
    source,_=read_snapshot(snapshot);meta=verify_provider(provider)
    if meta['source_manifest_sha256']!=hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest():raise ValueError('Snapshot/provider identity mismatch')
    data=pd.read_csv(ff,parse_dates=['date']).set_index('date')
    calendar=pd.DatetimeIndex(pd.read_csv(provider/'calendars/day.txt',header=None)[0])
    if data.index.has_duplicates:raise ValueError('Duplicate FF dates')
    if not np.isfinite(data.reindex(calendar)[['mkt','smb','hml']].to_numpy(dtype=float)).all():raise ValueError('Daily decimal mkt/smb/hml must cover snapshot calendar')
    out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(snapshot,out/'snapshot');shutil.copytree(provider,out/'provider')
    shutil.copyfile(ff,out/'snapshot/alpha191_ff.csv');shutil.copyfile(ff,out/'provider/alpha191_ff.csv')
    source.setdefault('supplemental_sources',{})['alpha191_ff']={'file':'alpha191_ff.csv','sha256':hashlib.sha256(ff.read_bytes()).hexdigest(),'description':description,'units':'daily decimal returns, not percent; no future revisions guaranteed by this script'}
    (out/'snapshot/manifest.json').write_text(json.dumps(source,indent=2,ensure_ascii=False))
    meta['source_manifest_sha256']=hashlib.sha256((out/'snapshot/manifest.json').read_bytes()).hexdigest()
    meta['files']['alpha191_ff.csv']=hashlib.sha256(ff.read_bytes()).hexdigest()
    (out/'provider/manifest.json').write_text(json.dumps(meta,indent=2))
    read_snapshot(out/'snapshot');verify_provider(out/'provider')
    print(json.dumps({'snapshot':str(out/'snapshot'),'provider':str(out/'provider')}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--provider',required=True);p.add_argument('--ff',required=True);p.add_argument('--source-description',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();attach(a.snapshot,a.provider,a.ff,a.source_description,a.out)
