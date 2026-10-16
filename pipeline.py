"""Daily factor, Alpha158/LightGBM and Qlib portfolio backtest from claw-data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd

from adapter import adapt, symbol_id

LABEL = 'Ref($open, -2)/Ref($open, -1)-1'


def validate_segments(config, calendar):
    """Forward labels need two future sessions; none may cross to the next split."""
    previous = None
    for name in ['train', 'valid', 'test']:
        start, end = map(pd.Timestamp, config['segments'][name])
        if start > end or start < calendar[0] or end > calendar[-1]:
            raise ValueError(f'Invalid segment {name}')
        dates = calendar[(calendar >= start) & (calendar <= end)]
        if dates.empty or calendar.get_loc(dates[-1]) + 2 >= len(calendar):
            raise ValueError('Insufficient forward label horizon')
        if previous is not None and previous >= start:
            raise ValueError('Label horizon overlaps next segment')
        previous = calendar[calendar.get_loc(dates[-1]) + 2]


def metric_summary(report):
    net = report['return'] - report['cost']
    gross = report['return']
    bench = report['bench']
    def stats(r):
        equity = (1 + r).cumprod()
        peak = equity.cummax().clip(lower=1)
        return {'total_return': float(equity.iloc[-1] - 1),
                'annualized_return': float(equity.iloc[-1] ** (252 / len(r)) - 1),
                'max_drawdown': float((equity / peak - 1).min()),
                'annualized_volatility': float(r.std() * np.sqrt(252))}
    years = {str(y): stats(group) for y, group in net.groupby(net.index.year)}
    return {'sessions': len(report), 'net': stats(net), 'gross': stats(gross),
            'benchmark': stats(bench), 'net_minus_benchmark_total_return': stats(net)['total_return'] - stats(bench)['total_return'],
            'average_daily_turnover': float(report.turnover.mean()), 'by_year_net': years}


def ic_summary(scores, labels, output):
    joined = pd.concat([scores.rename('score'), labels.rename('label')], axis=1).dropna()
    rows = []
    for date, group in joined.groupby(level='datetime'):
        if len(group) < 3 or group.score.nunique() < 2 or group.label.nunique() < 2:
            continue
        rows.append({'date': date, 'ic': group.score.corr(group.label),
                     'rank_ic': group.score.rank().corr(group.label.rank()), 'count': len(group)})
    df = pd.DataFrame(rows, columns=['date','ic','rank_ic','count']).set_index('date')
    df.to_csv(output)
    return {'ic': float(df.ic.mean()) if len(df) else None, 'rank_ic': float(df.rank_ic.mean()) if len(df) else None, 'valid_days': len(df),
            'total_days': int(joined.index.get_level_values('datetime').nunique()),
            'skipped_undefined_days': int(joined.index.get_level_values('datetime').nunique()) - len(df)}


def verify_provider(provider):
    path = Path(provider)
    manifest = json.loads((path/'manifest.json').read_text())
    for rel, expected in manifest['files'].items():
        if hashlib.sha256((path/rel).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Provider checksum mismatch: {rel}')
    return manifest


def research(provider, output):
    import qlib
    from qlib.data import D
    from qlib.data.dataset import DatasetH
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.strategy import TopkDropoutStrategy
    from qlib.backtest import backtest, executor
    from qlib.workflow import R

    provider = Path(provider).resolve()
    manifest = verify_provider(provider)
    config = manifest['config']
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    np.random.seed(config['seed'])
    qlib.init(provider_uri=str(provider), region='cn', kernels=2,
              redis_port=-1, expression_cache=None, dataset_cache=None,
              exp_manager={'class': 'MLflowExpManager', 'module_path': 'qlib.workflow.expm',
                           'kwargs': {'uri': (output/'mlruns').as_uri(), 'default_exp_name': 'claw-pilot'}})
    calendar = pd.DatetimeIndex(D.calendar(start_time=config['start'], end_time=config['end']))
    validate_segments(config, calendar)
    symbols = [symbol_id(c) for c in config['symbols']]
    alpha191_quality = None
    selected_set = config.get('factor_set', 'alpha158@qlib-0.9.7')
    if selected_set == 'alpha191@gtja-2017-v1':
        from factors.qlib_bridge import make_handler
        handler, alpha191_quality = make_handler(provider,config,symbols,LABEL,output)
    else:
        handler = Alpha158(start_time=config['start'], end_time=config['end'], instruments=symbols,
                           fit_start_time=config['segments']['train'][0], fit_end_time=config['segments']['train'][1],
                           infer_processors=[], learn_processors=[{'class':'DropnaLabel'}],
                           label=([LABEL], ['LABEL0']))
    dataset = DatasetH(handler=handler, segments={k: tuple(v) for k,v in config['segments'].items()})
    quality = {}
    for name in config['segments']:
        features = dataset.prepare(name, col_set='feature')
        quality[name] = {'rows': len(features), 'features': features.shape[1],
                         'missing_fraction': float(features.isna().to_numpy().mean())}
        if len(features) == 0 or (not alpha191_quality and features.isna().all().any()):
            raise ValueError('Empty segment or wholly missing feature')
    labels = dataset.prepare('test', col_set='label').iloc[:,0]
    factor = D.features(symbols, ['$close/Ref($close,20)-1'], start_time=config['segments']['test'][0],
                        end_time=config['segments']['test'][1])
    momentum = factor.iloc[:,0].swaplevel().sort_index()
    momentum.index = momentum.index.set_names(['datetime','instrument'])
    model = LGBModel(num_boost_round=150, early_stopping_rounds=20, learning_rate=0.03,
                     num_leaves=15, max_depth=5, min_data_in_leaf=80,
                     num_threads=2, seed=config['seed'], deterministic=True, force_col_wise=True)
    with R.start(experiment_name='claw-daily-pilot'):
        R.log_params(snapshot=manifest['source_manifest_sha256'], label=LABEL, seed=config['seed'])
        model.fit(dataset, verbose_eval=25)
        prediction = model.predict(dataset).sort_index()
        model.model.save_model(str(output/'lightgbm.txt'))
        results = {}
        for name, signal in [('momentum20', momentum), ('alpha191_lightgbm' if alpha191_quality else 'alpha158_lightgbm', prediction)]:
            signal.to_csv(output/f'{name}-predictions.csv', header=['score'])
            strategy = TopkDropoutStrategy(signal=signal, topk=config['topk'], n_drop=config['n_drop'],
                                           hold_thresh=1, only_tradable=False, forbid_all_trade_at_limit=False)
            portfolio, indicators = backtest(
                start_time=config['segments']['test'][0], end_time=config['segments']['test'][1],
                strategy=strategy, executor=executor.SimulatorExecutor(time_per_step='day', generate_portfolio_metrics=True),
                account=config['account'], benchmark=symbol_id(config['benchmark']),
                exchange_kwargs={'freq':'day', 'codes':symbols, 'deal_price':'open',
                    'limit_threshold': ('$buy_block', '$sell_block'),
                    'open_cost':config['buy_cost'], 'close_cost':config['sell_cost'], 'min_cost':config['min_cost'],
                    'trade_unit':100})
            report, positions = portfolio['1day']
            if not np.isfinite(report[['return','cost','bench','turnover']].to_numpy()).all():
                raise ValueError('Non-finite backtest output')
            report.to_csv(output/f'{name}-portfolio.csv')
            # Plain JSON positions avoid loading arbitrary pickle artifacts.
            position_rows = {str(day): pos.position for day, pos in positions.items()}
            (output/f'{name}-positions.json').write_text(json.dumps(position_rows, default=str, indent=2))
            results[name] = metric_summary(report)
            results[name]['signal_quality'] = ic_summary(signal, labels, output/f'{name}-ic.csv')
        R.log_metrics(**{f'{k}_net_return':v['net']['total_return'] for k,v in results.items()})
        recorder_id = R.get_recorder().id
    report = {'status':'engineering_workflow_complete', 'created_at':datetime.now(timezone.utc).isoformat(),
        'qlib_version':qlib.__version__, 'recorder_id': recorder_id,
        'source_manifest_sha256':manifest['source_manifest_sha256'], 'config':config,
        'label':LABEL, 'timing':'Features at t close, Qlib previous-session signal, execution at t+1 open.',
        'feature_quality': quality, 'results':results, 'alpha191_quality': alpha191_quality,
        'model_parameters': model.params, 'best_iteration': model.model.best_iteration,
        'training_round_limit':150, 'early_stopping_rounds':20, 'portfolio_risk_degree':0.95,
        'limitations':[
            'Fixed 12-stock sample; selection/survivorship bias; not a historical CSI300 constituent backtest.',
            'Current stored data revisions; no claim of historical publication-vintage replay.',
            'Open-price fills use daily bars; no order-book queue or partial-fill simulation.',
            'No volume capacity limit; combined cost assumptions are not historical broker fee schedules.',
            'Adjusted stock returns versus CSI300 price index: dividend treatment is not matched.',
            'Undefined daily IC (e.g. constant predictions) is excluded and its day count is disclosed.',
            'One long-only portfolio rebalance per day, hold_thresh=1; not a general lot-level T+1 execution engine.',
            'Ending positions are marked to market, not forcibly liquidated; closing liquidation costs are excluded.',
            'One train/validation/test split; no rolling or live validation; no investment-performance acceptance.']}
    (output/'results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for name in results:
        daily = pd.read_csv(output/f'{name}-portfolio.csv', index_col=0, parse_dates=True)
        ax.plot((1 + daily['return'] - daily['cost']).cumprod(), label=name + ' (net)')
    ax.plot((1 + daily['bench']).cumprod(), label='CSI300 price index', color='gray', linestyle='--')
    ax.set_title('Engineering pilot: fixed sample, not strategy validation')
    ax.set_ylabel('Growth of 1 unit'); ax.grid(alpha=.2); ax.legend()
    fig.tight_layout(); fig.savefig(output/'equity.png', dpi=150); plt.close(fig)
    shutil.copy('/opt/requirements.resolved.txt', output/'requirements.resolved.txt')
    source = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('.').glob('*.py')}
    (output/'code-hashes.json').write_text(json.dumps(source, indent=2))
    (output/'source').mkdir()
    for p in Path('.').glob('*.py'):
        shutil.copy2(p, output/'source'/p.name)
    if alpha191_quality:
        shutil.copytree(Path(__file__).parent/'factors',output/'source/factors',ignore=shutil.ignore_patterns('__pycache__'))
    print(json.dumps(report['results'], indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('adapt'); a.add_argument('--snapshot', required=True); a.add_argument('--out', required=True)
    r = sub.add_parser('research'); r.add_argument('--provider', required=True); r.add_argument('--out', required=True)
    args = p.parse_args()
    if args.command == 'adapt':
        print(json.dumps(adapt(args.snapshot,args.out),indent=2,ensure_ascii=False))
    else:
        research(args.provider,args.out)


if __name__ == '__main__':
    main()
