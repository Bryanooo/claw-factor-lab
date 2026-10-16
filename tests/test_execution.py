"""Integration contract: Qlib must not execute a closing signal on the same day."""
import numpy as np
import pandas as pd
import qlib
from qlib.backtest import backtest, executor
from qlib.contrib.strategy import TopkDropoutStrategy
from adapter import write_bins


def test_signal_lag_and_open_limit(tmp_path):
    dates = pd.bdate_range('2024-01-02',periods=8)
    (tmp_path/'calendars').mkdir();(tmp_path/'instruments').mkdir()
    (tmp_path/'calendars/day.txt').write_text(''.join(f'{d:%Y-%m-%d}\n' for d in dates))
    names=['SH600000','SH000300']
    (tmp_path/'instruments/all.txt').write_text(''.join(f'{s}\t{dates[0]:%Y-%m-%d}\t{dates[-1]:%Y-%m-%d}\n' for s in names))
    prices=pd.DataFrame({k:np.full(8,v) for k,v in {'open':10,'close':10,'factor':1,'volume':100000,'change':0,'buy_block':0,'sell_block':0}.items()},index=dates)
    # First eligible open is limit up; must defer the buy one more day.
    prices.loc[dates[2],'buy_block']=1
    for s in names:write_bins(tmp_path,s,prices,dates)
    qlib.init(provider_uri=str(tmp_path),region='cn',kernels=1,expression_cache=None,dataset_cache=None)
    signal=pd.Series([1.0]*6,index=pd.MultiIndex.from_product([dates[1:7],['SH600000']],names=['datetime','instrument']))
    strategy=TopkDropoutStrategy(signal=signal,topk=1,n_drop=1,hold_thresh=1,forbid_all_trade_at_limit=False)
    reports,_=backtest(start_time=dates[0],end_time=dates[-2],strategy=strategy,
       executor=executor.SimulatorExecutor(time_per_step='day',generate_portfolio_metrics=True),account=10000,
       benchmark='SH000300',exchange_kwargs={'codes':['SH600000'],'freq':'day','deal_price':'open',
        'limit_threshold':('$buy_block','$sell_block'),'open_cost':0,'close_cost':0,'min_cost':0,'trade_unit':100})
    report,_=reports['1day']
    assert (report.loc[:dates[2],'turnover']==0).all()
    assert report.loc[dates[3],'turnover']>0
