"""Versioned claw-data daily snapshot -> Qlib binary provider.

Input units: stock_daily vol=100 shares, amount=1000 CNY.
Output factor = adj_factor / (first_adj_factor * first_raw_close).
Adjusted price=raw*factor; adjusted volume=raw_shares/factor.
"""
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

VERSION = 'claw-qlib-daily-v1'


def symbol_id(code):
    m = re.fullmatch(r'(\d{6})\.(SH|SZ)', code)
    if not m:
        raise ValueError(f'Unsupported phase-one symbol: {code}')
    return m[2] + m[1]


def read_snapshot(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['status'] != 'extracted':
        raise ValueError('Incomplete snapshot')
    for evidence in [*manifest.get('source_audits', {}).values(), *manifest.get('supplemental_sources', {}).values()]:
        if 'sha256' in evidence and hashlib.sha256((root / evidence['file']).read_bytes()).hexdigest() != evidence['sha256']:
            raise ValueError('Source audit checksum mismatch')
    result = {}
    for query in manifest['queries']:
        if not query['exhausted']:
            raise ValueError('Unfinished pagination')
        rows = []
        for page in query['pages']:
            path = root / query['dataset'] / query['key'] / page['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != page['sha256']:
                raise ValueError(f'Snapshot checksum mismatch: {path}')
            rows.extend(json.loads(path.read_text())['data'])
        if len(rows) != query['rows']:
            raise ValueError('Row-count mismatch')
        result[(query['dataset'], query['key'])] = rows
    return manifest, result


def frame(rows, date_col='trade_date', fields=()):
    if not rows:
        return pd.DataFrame(columns=list(fields), index=pd.DatetimeIndex([], name='date'))
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df[date_col])
    for field in fields:
        df[field] = pd.to_numeric(df[field], errors='raise')
    df = df[['date', *fields]].drop_duplicates()
    if df['date'].duplicated().any():
        raise ValueError('Conflicting duplicate business keys')
    return df.set_index('date').sort_index()


def convert_stock(bars, factors, limits, calendar):
    if bars.empty:
        raise ValueError('Empty daily bars')
    if not bars.index.isin(calendar).all():
        raise ValueError('Bars outside exchange calendar')
    prices = bars[['open', 'high', 'low', 'close']]
    if not np.isfinite(bars.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError('Invalid daily numbers')
    if ((bars.high < prices.max(axis=1)) | (bars.low > prices.min(axis=1))).any():
        raise ValueError('Invalid OHLC geometry')
    if (bars[['vol', 'amount']] < 0).any().any():
        raise ValueError('Negative volume/amount')
    factors = factors.reindex(bars.index).adj_factor
    if not np.isfinite(factors).all() or (factors <= 0).any():
        raise ValueError('Missing or invalid adjustment factor')
    limits = limits.reindex(bars.index)
    if not np.isfinite(limits.to_numpy()).all() or (limits.down_limit <= 0).any() or (limits.up_limit < limits.down_limit).any():
        raise ValueError('Missing or invalid trading limits')
    factor = factors / (factors.iloc[0] * bars.close.iloc[0])
    out = prices.mul(factor, axis=0)
    out['factor'] = factor
    out['volume'] = bars.vol * 100 / factor
    out['amount'] = bars.amount * 1000
    out['vwap'] = out.amount / (bars.vol * 100).replace(0, np.nan) * factor
    out['change'] = bars.pct_chg / 100
    # At-open limits; no use of today's close/high/low to decide opening tradability.
    out['buy_block'] = (bars.open >= limits.up_limit - 0.005).astype(float)
    out['sell_block'] = (bars.open <= limits.down_limit + 0.005).astype(float)
    for field in ['open', 'high', 'low', 'close']:
        if not np.allclose(out[field] / out.factor, bars[field], rtol=1e-10):
            raise ValueError('Raw-price roundtrip failed')
    if not np.allclose(out.volume * out.factor, bars.vol * 100):
        raise ValueError('Volume roundtrip failed')
    return out.reindex(calendar)


def write_bins(root, symbol, data, calendar):
    folder = Path(root) / 'features' / symbol.lower()
    folder.mkdir(parents=True)
    for col in data:
        values = data[col].reindex(calendar).to_numpy(dtype='<f4')
        np.concatenate([np.array([0], dtype='<f4'), values]).tofile(folder / f'{col}.day.bin')


def adapt(snapshot, output):
    manifest, raw = read_snapshot(snapshot)
    config = manifest['config']
    calendar = pd.DatetimeIndex(sorted(r['cal_date'] for r in raw[('trade_calendar', 'SSE')]))
    sz = pd.DatetimeIndex(sorted(r['cal_date'] for r in raw[('trade_calendar', 'SZSE')]))
    if calendar.empty or calendar.has_duplicates or not calendar.equals(sz):
        raise ValueError('Missing, duplicate or inconsistent exchange calendars')
    audit = {'adapter_version': VERSION, 'calendar_sessions': len(calendar), 'stocks': {},
             'blockers': [], 'limitations': list(manifest['limitations']),
             'index_weight_rows': len(raw[('index_weight', config['benchmark'])]),
             'source_audit_scope': 'Captured latest 200 states per dataset; not full historical certification.'}
    if audit['index_weight_rows'] == 0:
        audit['limitations'].append('No benchmark constituent history in requested interval; fixed sample only.')
    frames = {}
    for code in config['symbols']:
        try:
            bars = frame(raw[('stock_daily', code)], fields=['open','high','low','close','vol','amount','pct_chg'])
            factors = frame(raw[('adj_factor', code)], fields=['adj_factor'])
            limits = frame(raw[('stock_limit', code)], fields=['up_limit','down_limit'])
            missing = calendar.difference(bars.index)
            suspension_dates = {pd.Timestamp(r['trade_date']) for r in raw[('stock_suspend', code)] if r.get('suspend_type') == 'S'}
            unexplained = [d.strftime('%Y-%m-%d') for d in missing if d not in suspension_dates]
            profiles = raw[('stock_basic', code)]
            if len(profiles) != 1 or pd.Timestamp(profiles[0]['list_date']) > calendar[0]:
                raise ValueError('Sample requires one profile and listing before start')
            if unexplained:
                raise ValueError(f'Unexplained missing sessions: {unexplained[:10]}')
            out = convert_stock(bars, factors, limits, calendar)
            # Full/partial suspensions are conservatively untradable all session.
            for row in raw[('stock_suspend', code)]:
                day = pd.Timestamp(row['trade_date'])
                if row.get('suspend_type') == 'S' and day in out.index:
                    out.loc[day, ['buy_block','sell_block']] = 1.0
            frames[symbol_id(code)] = out
            audit['stocks'][code] = {'status': 'pass', 'bars': len(bars), 'missing_sessions': len(missing),
                'suspension_explained': len(missing), 'factor_rows': len(factors), 'limit_rows': len(limits),
                'start': str(bars.index.min().date()), 'end': str(bars.index.max().date())}
        except (ValueError, KeyError) as exc:
            audit['stocks'][code] = {'status': 'fail', 'reason': str(exc)}
            audit['blockers'].append(f'{code}: {exc}')
    benchmark = frame(raw[('index_daily',config['benchmark'])], fields=['close','open'])
    if len(calendar.difference(benchmark.index)):
        audit['blockers'].append('Incomplete benchmark calendar')
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    audit['status'] = 'blocked' if audit['blockers'] else 'engineering_sample_pass'
    (root / 'acceptance.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    if audit['blockers']:
        raise ValueError('Data acceptance blocked; see acceptance.json')
    (root / 'calendars').mkdir()
    (root / 'calendars/day.txt').write_text(''.join(f'{d:%Y-%m-%d}\n' for d in calendar))
    (root / 'instruments').mkdir()
    inst = []
    for code, df in frames.items():
        write_bins(root, code, df, calendar)
        inst.append(f'{code}\t{calendar[0]:%Y-%m-%d}\t{calendar[-1]:%Y-%m-%d}\n')
    (root / 'instruments/pilot.txt').write_text(''.join(inst))
    benchmark['factor'] = 1.0
    write_bins(root, symbol_id(config['benchmark']), benchmark, calendar)
    inst.append(f'{symbol_id(config["benchmark"])}\t{calendar[0]:%Y-%m-%d}\t{calendar[-1]:%Y-%m-%d}\n')
    (root / 'instruments/all.txt').write_text(''.join(inst))
    (root / 'manifest.json').write_text(json.dumps({'adapter_version': VERSION,
        'source_manifest_sha256': hashlib.sha256((Path(snapshot)/'manifest.json').read_bytes()).hexdigest(),
        'config': config, 'files': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}}, indent=2))
    return audit
