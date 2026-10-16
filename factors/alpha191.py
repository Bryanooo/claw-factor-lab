"""GTJA Alpha191, explicit versioned interpretation of the 2017 report.

Axes: dates x instruments. No forward fill, future data, silent zero replacement,
or external code evaluation. See docs/alpha191.md for ambiguous source definitions.
"""
import ast
import hashlib
import operator
from pathlib import Path
import numpy as np
import pandas as pd

VERSION = 'gtja-2017-v1'
SET_ID = 'alpha191@' + VERSION
SOURCE_URL = 'https://guorn.com/static/upload/file/3/134065454575605.pdf'
FORMULAS = {int(n): expr for n, expr in (line.split('|', 1) for line in
    Path(__file__).with_suffix('.formulas').read_text().splitlines() if line)}
assert sorted(FORMULAS) == list(range(1, 192))
ALIASES = {
    'R':'C/DELAY(C,1)-1',
    'DTM':'IIF(O<=DELAY(O,1),0,MAX(H-O,O-DELAY(O,1)))',
    'DBM':'IIF(O>=DELAY(O,1),0,MAX(O-L,O-DELAY(O,1)))',
    'HD':'H-DELAY(H,1)', 'LD':'DELAY(L,1)-L',
    'TR':'MAX(MAX(H-L,ABS(H-DELAY(C,1))),ABS(L-DELAY(C,1)))',
}
NOTES = {
 10:'MAX(x,5) is a rolling maximum; repair misplaced parenthesis.',
 21:'REGBETA against sequence 1..6; intercept included.',
 22:'SMEAN spelling interpreted as SMA.',
 23:'Repair STD(CLOSE:20) and malformed ternary punctuation.',
 28:'Preserve printed TSMAX(LOW,9) in second denominator; not the common KDJ correction.',
 30:'Requires supplied point-in-time MKT/SMB/HML; no index-only proxy. OLS with intercept, last residual.',
 36:'Repair misplaced windows: CORR(...,6), then SUM(...,2).',
 54:'STD has no window in report; explicitly choose 10 sessions (same as CORR).',
 116:'SEQUENCE,20 interpreted as sequence 1..20.',
 127:'MEAN has no window; explicitly choose 12 sessions.',
 131:'DELAT spelling interpreted as DELTA.',
 143:'SELF starts at 1 at first valid close. Multiply by raw positive return, not 1+return; underflow may occur.',
 146:'Final SMA missing m: choose m=1. Preserve printed squared smoothed-return denominator.',
 149:'FILTER then 252 selected benchmark-down observations, not 252 calendar sessions; carry last fitted beta on non-selected days.',
 159:'HGIH corrected to HIGH; preserve printed 6*24 weight for the third term.',
 162:'Repair malformed trailing MIN so both rolling extrema apply to RSI12.',
 165:'Rescaled range: range of cumulative deviations inside each 48-session window, divided by window STD.',
 166:'Repair malformed ratio/window punctuation; retain printed first-power centered numerator (not skewness cube).',
 172:'Cancel common TR normalization algebraically; zero directional denominator remains missing.',
 181:'Missing denominator SUM window set to 20; benchmark CLOSE is price, not return; preserve subtraction.',
 183:'Same rescaled-range convention as 165, with 24 sessions.',
 190:'Balance parentheses; squared deviations use return minus geometric threshold consistently.',
}


def implementation_hash():
    return hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_suffix('.formulas').read_bytes()).hexdigest()


def required_fields(number):
    names=set()
    def visit(expr):
        for node in ast.walk(ast.parse(expr,mode='eval')):
            if isinstance(node,ast.Name):
                name=node.id
                if name in ALIASES:visit(ALIASES[name])
                elif name.startswith('A') and name[1:].isdigit():visit(FORMULAS[int(name[1:])])
                elif name in {'O','H','L','C','V','W','A','BC','BO','MKT','SMB','HML'}:names.add(name)
    visit(FORMULAS[number]);return sorted(names)


def _clean(x):
    if isinstance(x,pd.DataFrame):return x.replace([np.inf,-np.inf],np.nan)
    return x


def _rolling(x,n):
    if not isinstance(n,int) or n<1:raise ValueError('Positive integer window required')
    return x.rolling(n,min_periods=n)


def _where(condition,a,b):
    if not isinstance(condition,pd.DataFrame):raise ValueError('Panel condition required')
    result=pd.DataFrame(np.where(condition.to_numpy()==1,a,b),index=condition.index,columns=condition.columns)
    return result.where(condition.notna())


def _logic(a,b,op):
    return op(a==1,b==1).astype(float).where(a.notna() & b.notna())


def _sma(x,n,m):
    if not 0<m<=n:raise ValueError('Invalid SMA parameters')
    # Recursion starts at first finite value, resets after missing input. Missing is not zero.
    out=np.full(x.shape,np.nan);state=np.full(x.shape[1],np.nan)
    for i,row in enumerate(x.to_numpy(dtype=float)):
        state=np.where(np.isfinite(row),np.where(np.isfinite(state),(row*m+state*(n-m))/n,row),np.nan)
        out[i]=state
    return pd.DataFrame(out,index=x.index,columns=x.columns)


def _weighted(x,n,linear=False):
    weights=np.arange(1,n+1,dtype=float) if linear else .9**np.arange(n-1,-1,-1)
    weights/=weights.sum()
    return _rolling(x,n).apply(lambda a:np.dot(a,weights),raw=True)


def _slope(x,n):
    t=np.arange(1,n+1,dtype=float);t-=t.mean()
    return _rolling(x,n).apply(lambda a:np.dot(a,t)/np.dot(t,t),raw=True)


def _regresi(y,mkt,smb,hml,n):
    out=pd.DataFrame(np.nan,index=y.index,columns=y.columns)
    for col in y:
        xv=np.column_stack([np.ones(len(y)),mkt[col],smb[col],hml[col]])
        yy=y[col].to_numpy()
        for end in range(n-1,len(y)):
            x=xv[end-n+1:end+1];target=yy[end-n+1:end+1]
            if np.isfinite(x).all() and np.isfinite(target).all() and np.linalg.matrix_rank(x)==4:
                out.iloc[end,out.columns.get_loc(col)]=target[-1]-x[-1]@np.linalg.lstsq(x,target,rcond=None)[0]
    return out


def _filter_beta(y,x,condition,n):
    result=pd.DataFrame(np.nan,index=y.index,columns=y.columns)
    for col in y:
        selected=(condition[col]==1)&x[col].notna()&y[col].notna()
        xx=x.loc[selected,col];yy=y.loc[selected,col]
        beta=yy.rolling(n,min_periods=n).cov(xx)/xx.rolling(n,min_periods=n).var()
        result[col]=beta.reindex(y.index).ffill().where(y[col].notna()&x[col].notna()&condition[col].notna())
    return result


def _self143(c):
    result=pd.DataFrame(np.nan,index=c.index,columns=c.columns)
    for col in c:
        state=1.;previous=np.nan;values=[]
        for value in c[col]:
            if not np.isfinite(value):values.append(np.nan);previous=np.nan;continue
            if np.isfinite(previous) and value>previous:state*=value/previous-1
            values.append(state);previous=value
        result[col]=values
    return result


FUNCTIONS = {
 'SUM':lambda x,n:_rolling(x,n).sum(), 'MEAN':lambda x,n:_rolling(x,n).mean(),
 'STD':lambda x,n:_rolling(x,n).std(ddof=1),
 'TSMIN':lambda x,n:_rolling(x,n).min(), 'TSMAX':lambda x,n:_rolling(x,n).max(),
 'PROD':lambda x,n:_rolling(x,n).apply(np.prod,raw=True),
 'DELAY':lambda x,n:x.shift(n) if isinstance(n,int) and n>=0 else (_ for _ in ()).throw(ValueError('Negative lag forbidden')),
 'DELTA':lambda x,n:x-x.shift(n) if isinstance(n,int) and n>=0 else (_ for _ in ()).throw(ValueError('Negative lag forbidden')),
 'RANK':lambda x:x.rank(axis=1,method='average',pct=True),
 'TSRANK':lambda x,n:_rolling(x,n).rank(method='average',pct=True),
 'CORR':lambda x,y,n:_rolling(x,n).corr(y).where((_rolling(x,n).std()>1e-12)&(_rolling(y,n).std()>1e-12)).clip(-1,1),
 'COV':lambda x,y,n:_rolling(x,n).cov(y,ddof=1),
 'MIN':np.minimum,'MAX':np.maximum,'ABS':np.abs,'SIGN':np.sign,
 'LOG':lambda x:np.log(x.where(x>0)), 'SMA':_sma,
 'WMA':_weighted,'DECAYLINEAR':lambda x,n:_weighted(x,n,True),
 'COUNT':lambda x,n:_rolling(x,n).sum(),'IIF':_where,
 'AND':lambda a,b:_logic(a,b,operator.and_), 'OR':lambda a,b:_logic(a,b,operator.or_),
 'SUMIF':lambda x,n,c:_rolling(_where(c,x,0),n).sum(),
 'SLOPE':_slope,'REGRESI':_regresi,'FILTERBETA':_filter_beta,'SELF143':_self143,
 'LOWDAY':lambda x,n:_rolling(x,n).apply(lambda a:np.argmin(a[::-1]),raw=True),
 'HIGHDAY':lambda x,n:_rolling(x,n).apply(lambda a:np.argmax(a[::-1]),raw=True),
 'RANGE_SUMAC':lambda x,n:_rolling(x,n).apply(lambda a:np.ptp(np.cumsum(a-a.mean())),raw=True),
}
BIN_OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow}
CMP_OPS={ast.Lt:operator.lt,ast.Gt:operator.gt,ast.LtE:operator.le,ast.GtE:operator.ge,ast.Eq:operator.eq}


class Engine:
    def __init__(self,fields):
        self.fields=fields;self.cache={}
        if 'C' not in fields:raise ValueError('C required')
        ref=fields['C']
        if not ref.index.is_monotonic_increasing or ref.index.has_duplicates or ref.columns.has_duplicates:raise ValueError('Unique sorted dates and unique instruments required')
        for name,value in fields.items():
            if not value.index.equals(ref.index) or not value.columns.equals(ref.columns):raise ValueError('Panel alignment mismatch: '+name)
    def expression(self,text):return self.evaluate(ast.parse(text,mode='eval').body)
    def evaluate(self,node):
        key=ast.dump(node)
        if key in self.cache:return self.cache[key]
        with np.errstate(all='ignore'):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float):result=node.value
            elif isinstance(node,ast.Name):
                if node.id in self.fields:result=self.fields[node.id]
                elif node.id in ALIASES:result=self.expression(ALIASES[node.id])
                elif node.id.startswith('A') and node.id[1:].isdigit():result=self.expression(FORMULAS[int(node.id[1:])])
                else:raise ValueError('Missing input: '+node.id)
            elif isinstance(node,ast.BinOp) and type(node.op) in BIN_OPS:
                a=self.evaluate(node.left);b=self.evaluate(node.right);result=BIN_OPS[type(node.op)](a,b)
                if isinstance(a,pd.DataFrame):result=result.where(a.notna())
                if isinstance(b,pd.DataFrame):result=result.where(b.notna())
            elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                result=self.evaluate(node.operand)*(-1 if isinstance(node.op,ast.USub) else 1)
            elif isinstance(node,ast.Compare) and len(node.ops)==1 and type(node.ops[0]) in CMP_OPS:
                a=self.evaluate(node.left);b=self.evaluate(node.comparators[0]);result=CMP_OPS[type(node.ops[0])](a,b).astype(float)
                if isinstance(a,pd.DataFrame):result=result.where(a.notna())
                if isinstance(b,pd.DataFrame):result=result.where(b.notna())
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
                result=FUNCTIONS[node.func.id](*(self.evaluate(x) for x in node.args))
            else:raise ValueError('Unsupported formula syntax')
        result=_clean(result);self.cache[key]=result;return result
    def factor(self,number):
        out=self.expression(FORMULAS[number])
        # A missing instrument-session cannot receive a manufactured cross-sectional observation.
        return out.where(self.fields['C'].notna())
    def calculate(self,numbers=None,missing_policy='error'):
        if missing_policy not in ('error','exclude'):raise ValueError('Unknown missing-data policy')
        numbers=list(range(1,192)) if numbers is None else list(numbers)
        missing={f'alpha191.{n:03d}':[x for x in required_fields(n) if x not in self.fields] for n in numbers}
        missing={k:v for k,v in missing.items() if v}
        if missing and missing_policy=='error':raise ValueError('Missing required factor inputs: '+str(missing))
        values={}
        for n in numbers:
            name=f'alpha191.{n:03d}'
            if name not in missing:values[name]=self.factor(n)
        return values,missing
