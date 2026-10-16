import numpy as np
import pandas as pd
import pytest
from factors.alpha191 import Engine,FORMULAS,FUNCTIONS,required_fields


def panels(n=720,k=4):
    rng=np.random.default_rng(812);idx=pd.date_range('2018-01-01',periods=n,freq='B');cols=[f'S{i:02d}' for i in range(k)]
    def panel(x):return pd.DataFrame(x,index=idx,columns=cols)
    c=panel(20*np.exp(rng.normal(0,.12,(n,k))));o=c*panel(np.exp(rng.normal(0,.008,(n,k))))
    h=np.maximum(c,o)*panel(1+rng.uniform(.001,.03,(n,k)));l=np.minimum(c,o)*panel(1-rng.uniform(.001,.03,(n,k)))
    v=panel(rng.uniform(1e5,2e6,(n,k)));w=(h+l+c)/3
    bc=np.exp(np.cumsum(rng.normal(0,.012,n)))*3000
    fields=dict(C=c,O=o,H=h,L=l,V=v,W=w,A=w*v,BC=panel(np.repeat(bc[:,None],k,axis=1)),BO=panel(np.repeat((bc*np.exp(rng.normal(0,.008,n)))[:,None],k,axis=1)))
    for name in ('MKT','SMB','HML'):fields[name]=panel(np.repeat(rng.normal(0,.01,n)[:,None],k,axis=1))
    return fields


def test_all_191_execute_and_no_infinite_results():
    values,missing=Engine(panels(k=32)).calculate()
    assert len(values)==191 and not missing
    for name,v in values.items():
        assert v.shape==(720,32),name
        assert not np.isinf(v.to_numpy()).any(),name
        assert v.notna().any().any(),name


def test_missing_ff_is_explicit_not_index_proxy():
    p=panels(80)
    for name in ('MKT','SMB','HML'):p.pop(name)
    with pytest.raises(ValueError,match='alpha191.030'):Engine(p).calculate([30])
    v,missing=Engine(p).calculate([14,30],missing_policy='exclude')
    assert list(v)==['alpha191.014'] and missing=={'alpha191.030':['HML','MKT','SMB']}
    assert set(required_fields(75))=={'C','O','BC','BO'}


def test_prefix_invariance_all_factors():
    p=panels(640);full,_=Engine(p).calculate();short,_=Engine({k:v.iloc[:590] for k,v in p.items()}).calculate()
    for name in full:pd.testing.assert_frame_equal(full[name].iloc[:590],short[name],rtol=1e-7,atol=1e-9)


def test_hand_calculated_operators_and_ties():
    x=pd.DataFrame({'a':[1.,2.,3.,4.],'b':[1.,1.,3.,2.]})
    np.testing.assert_allclose(FUNCTIONS['RANK'](x).iloc[0],[.75,.75])
    assert FUNCTIONS['TSRANK'](x,3).iloc[2,0]==1
    assert FUNCTIONS['HIGHDAY'](x,3).iloc[3,1]==1
    assert FUNCTIONS['LOWDAY'](x,3).iloc[3,1]==2
    assert FUNCTIONS['SMA'](x,3,1).iloc[2,0]==pytest.approx(17/9)
    assert FUNCTIONS['DECAYLINEAR'](x,3).iloc[2,0]==pytest.approx(14/6)
    assert FUNCTIONS['WMA'](x,3).iloc[2,0]==pytest.approx((.81+1.8+3)/2.71)
    assert FUNCTIONS['SLOPE'](x,3).iloc[2,0]==pytest.approx(1)


def test_formula_golden_values_independent_references():
    p=panels(70);e=Engine(p)
    pd.testing.assert_frame_equal(e.factor(14),p['C']-p['C'].shift(5))
    pd.testing.assert_frame_equal(e.factor(191),p['V'].rolling(20).mean().rolling(5).corr(p['L'])+(p['H']+p['L'])/2-p['C'],rtol=1e-7,atol=1e-9)
    numerator=((p['C']>p['O'])&(p['BC']<p['BO'])).rolling(50).sum()
    denominator=(p['BC']<p['BO']).rolling(50).sum()
    pd.testing.assert_frame_equal(e.factor(75),numerator/denominator)
    expected=p['C'].rolling(48).apply(lambda a:np.ptp(np.cumsum(a-a.mean())),raw=True)/p['C'].rolling(48).std()
    pd.testing.assert_frame_equal(e.factor(165),expected)


def test_regression_residual_and_filtered_beta_reference():
    p=panels(90);y=1+2*p['MKT']-3*p['SMB']+.2*p['HML']
    residual=FUNCTIONS['REGRESI'](y,p['MKT'],p['SMB'],p['HML'],60)
    assert np.nanmax(abs(residual.to_numpy()))<1e-12
    c=pd.DataFrame(np.tile(np.arange(90)%2==0,(4,1)).T,index=y.index,columns=y.columns).astype(float)
    beta=FUNCTIONS['FILTERBETA'](2*p['MKT']+1,p['MKT'],c,20)
    assert beta.iloc[:38].isna().all().all()
    np.testing.assert_allclose(beta.iloc[38:].to_numpy(),2,rtol=1e-9,atol=1e-9)


def test_missing_and_constant_inputs_not_fabricated():
    x=pd.DataFrame({'a':[1.,2.,np.nan,4.,5.],'b':[2.,3.,np.nan,5.,6.]})
    e=Engine({'C':x})
    assert e.expression('IIF(C>DELAY(C,1),1,0)').iloc[2:4].isna().all().all()
    assert e.expression('C/(C-C)').isna().all().all()
    assert e.expression('C**0').iloc[2].isna().all()
    assert e.expression('CORR(C,C*0+1,2)').isna().all().all()
    assert e.expression('SMA(C,3,1)').iloc[3,0]==4
    with pytest.raises(ValueError):e.expression('DELAY(C,-1)')
    with pytest.raises(ValueError):e.expression('__import__("os")')


def test_factor143_literal_self_recursion():
    p={'C':pd.DataFrame({'a':[100.,110.,100.,120.]})}
    np.testing.assert_allclose(Engine(p).factor(143)['a'],[1,.1,.1,.02])


def test_ff_attachment_creates_new_identity_and_checks_integrity(tmp_path):
    import hashlib,json
    from scripts.attach_alpha191_ff import attach
    from adapter import read_snapshot
    from pipeline import verify_provider
    snapshot=tmp_path/'snapshot';snapshot.mkdir()
    (snapshot/'manifest.json').write_text(json.dumps({'status':'extracted','queries':[],'config':{}}))
    original=(snapshot/'manifest.json').read_bytes()
    provider=tmp_path/'provider';(provider/'calendars').mkdir(parents=True)
    (provider/'calendars/day.txt').write_text('2020-01-02\n2020-01-03\n')
    (provider/'manifest.json').write_text(json.dumps({'source_manifest_sha256':hashlib.sha256(original).hexdigest(),'files':{'calendars/day.txt':hashlib.sha256((provider/'calendars/day.txt').read_bytes()).hexdigest()}}))
    ff=tmp_path/'ff.csv';ff.write_text('date,mkt,smb,hml\n2020-01-02,0.01,-0.02,0.03\n2020-01-03,-0.01,0.02,0.01\n')
    out=tmp_path/'new';attach(snapshot,provider,ff,'synthetic test fixture',out)
    meta=verify_provider(out/'provider');read_snapshot(out/'snapshot')
    assert (snapshot/'manifest.json').read_bytes()==original
    assert meta['source_manifest_sha256']!=hashlib.sha256(original).hexdigest()
    from factors.qlib_bridge import missing_inputs
    assert missing_inputs(provider)=={'alpha191.030':['HML','MKT','SMB']}
    assert missing_inputs(out/'provider')=={}
    (out/'snapshot/alpha191_ff.csv').write_text('tampered')
    with pytest.raises(ValueError,match='checksum'):read_snapshot(out/'snapshot')
    ff.write_text('date,mkt,smb,hml\n2020-01-02,0.01,0.02,0.03\n')
    with pytest.raises(ValueError,match='cover snapshot'):attach(snapshot,provider,ff,'missing date',tmp_path/'invalid')
