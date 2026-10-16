"""Read-only claw-data export; exhausted cursor pages and raw responses are retained."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def get_json(base, path, params):
    url = base.rstrip('/') + path + '?' + urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(url, timeout=40) as response:
                return json.load(response)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def fetch_records(base, dataset, params, folder):
    rows, pages, cursor = [], [], None
    seen = set()
    while True:
        query = dict(params, limit=1000)
        if cursor:
            query['cursor'] = cursor
        data = get_json(base, '/api/v1/data/datasets/' + dataset + '/records', query)
        if data['meta'].get('served_from') != 'local_db' or data['meta'].get('fallback_used'):
            raise ValueError('Only local claw-data snapshots are accepted')
        file = folder / f'{len(pages):04d}.json'
        file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        pages.append({'file': str(file.name), 'sha256': digest(file), 'rows': len(data['data'])})
        rows.extend(data['data'])
        if not data['page']['has_more']:
            break
        cursor = data['page'].get('next_cursor')
        if not cursor or cursor in seen:
            raise ValueError('Missing or repeating pagination cursor')
        seen.add(cursor)
    return rows, pages


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='config/pilot.json')
    p.add_argument('--out', required=True)
    p.add_argument('--base-url', default=os.getenv('CLAW_DATA_URL', 'http://127.0.0.1:8000'))
    args = p.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'extracting', 'created_at': datetime.now(timezone.utc).isoformat(),
                'source': args.base_url, 'config': config, 'queries': [],
                'limitations': ['Read API pages are not a database-wide atomic snapshot.',
                    'Historical payloads reflect current stored revisions, not original historical vintages.',
                    'Fixed sample is an engineering test and has selection/survivorship bias.']}
    (root / 'config.json').write_text(json.dumps(config, indent=2))
    jobs = [('trade_calendar', {'exchange': ex, 'is_open': 1}, ex) for ex in ['SSE', 'SZSE']]
    jobs += [('index_daily', {'ts_code': config['benchmark']}, config['benchmark'])]
    jobs += [('index_weight', {'index_code': config['benchmark']}, config['benchmark'])]
    for symbol in config['symbols']:
        jobs += [(name, {'ts_code': symbol}, symbol) for name in
                 ['stock_daily', 'adj_factor', 'stock_limit', 'stock_suspend', 'stock_basic']]
    for dataset, filters, key in jobs:
        params = filters if dataset == 'stock_basic' else dict(filters, start_date=config['start'], end_date=config['end'])
        folder = root / dataset / key
        folder.mkdir(parents=True)
        rows, pages = fetch_records(args.base_url, dataset, params, folder)
        manifest['queries'].append({'dataset': dataset, 'key': key, 'params': params,
                                    'rows': len(rows), 'pages': pages, 'exhausted': True})
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        print(f'{dataset} {key}: {len(rows)} rows', flush=True)
    # Capture source audit states separately; absence/timeout is not a passing audit.
    manifest['source_audits'] = {}
    for dataset in ['stock_daily', 'adj_factor', 'stock_limit', 'trade_calendar', 'index_weight']:
        try:
            result = get_json(args.base_url, '/api/v1/ops/orchestration-v2/dataset-states',
                              {'dataset_name': dataset, 'limit': 200})
            target = root / f'audit-{dataset}.json'
            target.write_text(json.dumps(result, indent=2))
            manifest['source_audits'][dataset] = {'file': target.name, 'sha256': digest(target)}
        except Exception as exc:
            manifest['source_audits'][dataset] = {'error': type(exc).__name__}
    manifest['status'] = 'extracted'
    manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('snapshot:', root)


if __name__ == '__main__':
    main()
