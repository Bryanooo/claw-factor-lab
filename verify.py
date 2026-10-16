"""Read-back acceptance of Qlib float32 bins against the exact claw-data snapshot."""
import argparse
import json
from pathlib import Path
import numpy as np
import qlib
from qlib.data import D
from adapter import read_snapshot, frame, symbol_id
from pipeline import verify_provider


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--snapshot',required=True);p.add_argument('--provider',required=True)
    p.add_argument('--canonical-probe');p.add_argument('--out',required=True)
    a=p.parse_args()
    if Path(a.out).exists():raise ValueError('Refusing to overwrite verification')
    meta,raw=read_snapshot(a.snapshot); verify_provider(a.provider)
    qlib.init(provider_uri=str(Path(a.provider).resolve()),region='cn',kernels=2,expression_cache=None,dataset_cache=None)
    config=meta['config'];checked=0
    for code in config['symbols']:
        expected=frame(raw[('stock_daily',code)],fields=['close','vol','amount'])
        data=D.features([symbol_id(code)],['$close/$factor','$volume*$factor','$amount'],
            start_time=config['start'],end_time=config['end']).droplevel('instrument').reindex(expected.index)
        np.testing.assert_allclose(data.iloc[:,0],expected.close,rtol=2e-6)
        np.testing.assert_allclose(data.iloc[:,1],expected.vol*100,rtol=2e-6)
        np.testing.assert_allclose(data.iloc[:,2],expected.amount*1000,rtol=2e-6)
        checked+=len(expected)
    canonical_rows=0
    if a.canonical_probe:
        probe=json.loads(Path(a.canonical_probe).read_text())
        for row in probe['data']:
            assert row['volume_unit']=='share' and row['amount_unit']=='CNY' and row['adjustment']=='none'
            expected={r['trade_date']:r for r in raw[('stock_daily',row['instrument_id'])]}[row['period_start']]
            for field in ['open','high','low','close']:
                np.testing.assert_allclose(float(expected[field]),row[field],rtol=1e-10)
            np.testing.assert_allclose(float(expected['vol'])*100,row['volume'],rtol=1e-10)
            np.testing.assert_allclose(float(expected['amount'])*1000,row['amount'],rtol=1e-10)
            canonical_rows+=1
    report={'status':'pass','stock_rows_roundtrip':checked,'canonical_rows_crosschecked':canonical_rows,
            'binary_relative_tolerance':2e-6,'independence_note':'Canonical comparison is a mapping check against the same underlying source, not independent market-data validation.'}
    Path(a.out).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()
