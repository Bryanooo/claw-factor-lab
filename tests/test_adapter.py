import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from adapter import convert_stock, frame, read_snapshot, symbol_id, write_bins
from pipeline import validate_segments, metric_summary


def inputs():
    dates = pd.date_range('2020-01-01', periods=3)
    # Ex-dividend event: raw price halves, factor doubles, adjusted price unchanged.
    bars = pd.DataFrame({'open':[10,5,6], 'high':[11,6,7], 'low':[9,4,5], 'close':[10,5,6],
                         'vol':[10,20,30], 'amount':[10,10,18], 'pct_chg':[0,0,20]}, index=dates)
    factors = pd.DataFrame({'adj_factor':[1,2,2]}, index=dates)
    limits = pd.DataFrame({'up_limit':[11,6,7], 'down_limit':[9,4,5]}, index=dates)
    return bars,factors,limits,dates


def test_adjustment_units_and_roundtrip():
    b,f,l,c=inputs(); o=convert_stock(b,f,l,c)
    np.testing.assert_allclose(o.close,[1,1,1.2])
    np.testing.assert_allclose(o.close/o.factor,b.close)
    np.testing.assert_allclose(o.volume*o.factor,b.vol*100)
    np.testing.assert_allclose(o.vwap,o.close)
    assert o.amount.iloc[0]==10000


def test_missing_factor_fails_closed():
    b,f,l,c=inputs()
    with pytest.raises(ValueError,match='adjustment'): convert_stock(b,f.iloc[:2],l,c)


def test_conflicting_revisions_rejected():
    with pytest.raises(ValueError,match='Conflicting'):
        frame([{'trade_date':'2020-01-01','adj_factor':1},{'trade_date':'2020-01-01','adj_factor':2}],fields=['adj_factor'])


def test_same_value_revisions_coalesce():
    assert len(frame([{'trade_date':'2020-01-01','adj_factor':1}]*2,fields=['adj_factor']))==1


def test_gap_stays_nan_and_limit_uses_open():
    b,f,l,c=inputs(); b.loc[c[1],'open']=6
    o=convert_stock(b.drop(c[2]),f,l,c)
    assert o.loc[c[1],'buy_block']==1
    assert o.loc[c[2]].isna().all()


def test_invalid_ohlc_rejected():
    b,f,l,c=inputs();b.loc[c[0],'high']=8
    with pytest.raises(ValueError,match='geometry'):convert_stock(b,f,l,c)


def test_binary_alignment(tmp_path):
    b,f,l,c=inputs();o=convert_stock(b,f,l,c);write_bins(tmp_path,'SH600000',o,c)
    v=np.fromfile(tmp_path/'features/sh600000/close.day.bin',dtype='<f4')
    np.testing.assert_allclose(v,[0,1,1,1.2])


def test_snapshot_tamper_rejected(tmp_path):
    d=tmp_path/'stock_daily/600000.SH';d.mkdir(parents=True);(d/'0.json').write_text('{}')
    (tmp_path/'manifest.json').write_text(json.dumps({'status':'extracted','queries':[{
        'dataset':'stock_daily','key':'600000.SH','exhausted':True,'rows':0,
        'pages':[{'file':'0.json','sha256':'wrong'}]}]}))
    with pytest.raises(ValueError,match='checksum'):read_snapshot(tmp_path)


def test_label_boundary_requires_embargo():
    c=pd.bdate_range('2020-01-01',periods=30)
    conf={'segments':{'train':[str(c[0].date()),str(c[8].date())],
        'valid':[str(c[9].date()),str(c[15].date())], 'test':[str(c[20].date()),str(c[25].date())]}}
    with pytest.raises(ValueError,match='overlaps'):validate_segments(conf,c)
    conf['segments']['valid'][0]=str(c[11].date());validate_segments(conf,c)


def test_drawdown_includes_initial_capital():
    df=pd.DataFrame({'return':[-.1,0], 'cost':[0,0],'bench':[0,0],'turnover':[0,0]},index=pd.date_range('2020-01-01',periods=2))
    assert metric_summary(df)['net']['max_drawdown']==pytest.approx(-.1)


def test_symbol_mapping_rejects_unknown_market():
    assert symbol_id('000001.SZ')=='SZ000001'
    with pytest.raises(ValueError):symbol_id('123456.BJ')


def test_constant_signal_ic_is_reported_as_undefined(tmp_path):
    from pipeline import ic_summary
    ix=pd.MultiIndex.from_product([pd.date_range('2024-01-01',periods=2),['A','B','C']],names=['datetime','instrument'])
    scores=pd.Series([1,2,3,1,1,1],index=ix)
    labels=pd.Series([1,2,3,1,2,3],index=ix)
    summary=ic_summary(scores,labels,tmp_path/'ic.csv')
    assert summary['valid_days']==1
    assert summary['total_days']==2
    assert summary['skipped_undefined_days']==1


def test_all_constant_signals_have_null_ic(tmp_path):
    from pipeline import ic_summary
    ix=pd.MultiIndex.from_product([pd.date_range('2024-01-01',periods=2),['A','B','C']],names=['datetime','instrument'])
    result=ic_summary(pd.Series(1.,index=ix),pd.Series([1,2,3]*2,index=ix),tmp_path/'ic.csv')
    assert result['ic'] is None and result['rank_ic'] is None
    assert result['skipped_undefined_days']==2
