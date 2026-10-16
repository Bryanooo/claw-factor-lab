"""Adapt Qlib public data APIs to the platform's panel factor engine."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .alpha191 import Engine, VERSION, implementation_hash, required_fields


def available_fields(provider):
    fields={'O','H','L','C','V','W','A','BC','BO'}
    # Only a file covered by the provider's manifest counts as supplied research data.
    root=Path(provider);manifest=json.loads((root/'manifest.json').read_text())
    if 'alpha191_ff.csv' in manifest['files']:
        fields.update({'MKT','SMB','HML'})
    return fields


def missing_inputs(provider):
    fields=available_fields(provider)
    return {f'alpha191.{n:03d}':missing for n in range(1,192)
            if (missing:=[f for f in required_fields(n) if f not in fields])}


def load_fields(provider,config,symbols):
    from qlib.data import D
    names={'O':'open','H':'high','L':'low','C':'close','V':'volume','W':'vwap','A':'amount','F':'factor'}
    data=D.features(symbols,['$'+n for n in names.values()],start_time=config['start'],end_time=config['end'])
    fields={key:data['$'+name].unstack('instrument').reindex(columns=symbols).astype(float) for key,name in names.items()}
    # Undo Qlib's per-security first-price normalization. Keep split-adjusted prices
    # in CNY and reciprocal split-adjusted volumes in shares; amount stays CNY.
    scale=1/fields.pop('F').iloc[0]
    if not np.isfinite(scale).all() or (scale<=0).any():raise ValueError('Alpha191 requires valid adjustment scale on first snapshot session')
    for key in ('O','H','L','C','W'):fields[key]=fields[key]*scale
    fields['V']=fields['V']/scale
    from adapter import symbol_id
    benchmark=D.features([symbol_id(config['benchmark'])],['$open','$close'],start_time=config['start'],end_time=config['end']).droplevel('instrument')
    for key,name in [('BO','$open'),('BC','$close')]:
        values=benchmark[name].reindex(fields['C'].index)
        if values.isna().any():raise ValueError('Incomplete benchmark input for Alpha191')
        fields[key]=pd.DataFrame({symbol:values for symbol in symbols})
    root=Path(provider);manifest=json.loads((root/'manifest.json').read_text())
    if 'alpha191_ff.csv' in manifest['files']:
        path=root/'alpha191_ff.csv'
        if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['files']['alpha191_ff.csv']:raise ValueError('Fama-French input checksum mismatch')
        ff=pd.read_csv(path,parse_dates=['date']).set_index('date')
        if ff.index.has_duplicates:raise ValueError('Duplicate Fama-French dates')
        ff=ff.reindex(fields['C'].index)
        if not np.isfinite(ff[['mkt','smb','hml']].to_numpy(dtype=float)).all():raise ValueError('Fama-French daily decimal returns must cover every snapshot session')
        for key in ('MKT','SMB','HML'):fields[key]=pd.DataFrame({symbol:ff[key.lower()] for symbol in symbols})
    return fields


def make_handler(provider,config,symbols,label,output):
    from qlib.data import D
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader
    fields=load_fields(provider,config,symbols)
    values,excluded=Engine(fields).calculate(missing_policy=config.get('missing_factor_policy','error'))
    features=pd.DataFrame({name:value.stack(dropna=False) for name,value in values.items()})
    features.index=features.index.set_names(['datetime','instrument']);features=features.sort_index()
    labels=D.features(symbols,[label],start_time=config['start'],end_time=config['end']).swaplevel().sort_index()
    labels.index=labels.index.set_names(['datetime','instrument']);labels.columns=['LABEL0']
    # Selection uses training coverage only; never select features using validation/test data.
    train=features.loc[config['segments']['train'][0]:config['segments']['train'][1]]
    unusable=train.columns[train.isna().all()].tolist()
    if unusable and config.get('missing_factor_policy','error')!='exclude':
        raise ValueError('Wholly missing training factors: '+str(unusable)+'. Explicit exclusion required.')
    usable=features.drop(columns=unusable)
    if usable.empty:raise ValueError('No usable Alpha191 training features')
    merged=pd.concat({'feature':usable,'label':labels},axis=1)
    quality={'factor_set':'alpha191@'+VERSION,'implementation_sha256':implementation_hash(),
        'requested':191,'computed':len(values),'excluded':excluded,'training_unusable':unusable,'model_features':len(usable.columns),
        'price_convention':'first-session anchored split-adjusted CNY; inverse split-adjusted shares; amount in CNY',
        'rank_universe':list(symbols),'per_factor':{name:{'finite_rows':int(v.notna().sum()),'missing_fraction':float(v.isna().mean())} for name,v in features.items()}}
    (Path(output)/'alpha191-quality.json').write_text(json.dumps(quality,indent=2))
    features.to_parquet(Path(output)/'alpha191-features.parquet')
    return DataHandlerLP(data_loader=StaticDataLoader(merged),infer_processors=[],learn_processors=[{'class':'DropnaLabel'}]),quality
