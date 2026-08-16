import pandas as pd, numpy as np, zipfile, io, os, math, json
from datetime import time

ZIP_NEW='/mnt/data/NQ_1m_by_year_2023-2026(5).zip'
ZIP_OLD='/mnt/data/NQ_1m_history_pre2023(1).zip'
YEARS=list(range(2008,2027))
COST_DOLLARS=15.0
POINT_VALUE=20.0
COST_POINTS=COST_DOLLARS/POINT_VALUE

# One-shot locked rules
# OR: 09:30-09:44 ET. Direction = first 5m close outside OR from 09:45-10:30,
# body >= 50% of bar range. Benchmark enters next bar open, stop opposite OR edge, exit 16:00/stop.
# Collective refuses breakout, waits through 11:30 for pullback that touches broken OR edge,
# no 5m close through opposite OR edge. Countertrend pressure proxy = 1m signed volume by candle direction.
# Failure: after pullback touch, at least 2 opposite-pressure 5m bars; the latest opposite-pressure bar
# fails to extend the adverse extreme vs prior opposite-pressure bar (<= 0.25 of 20-bar median 5m TR allowance).
# Reassertion: subsequent bar original-side pressure >0/<0, body >=50% range, closes beyond prior 2-bar high/low.
# Enter next 5m open; stop beyond pullback extreme. Exit on stop, or first bar close with opposing pressure
# and close through prior 2-bar low/high (executed next bar open), else 15:55 close.

def load_year(year):
    if year < 2023:
        zp=ZIP_OLD; member=f'NQ_adj_1m_{year}.csv'
        chunks=[(zp,member)]
    elif year==2023:
        chunks=[(ZIP_OLD,'NQ_adj_1m_2023.csv'), (ZIP_NEW,'NQ_adj_1m_2023.csv')]
    else:
        chunks=[(ZIP_NEW,f'NQ_adj_1m_{year}.csv')]
    dfs=[]
    for zp,member in chunks:
        with zipfile.ZipFile(zp) as z:
            with z.open(member) as f:
                df=pd.read_csv(f, header=None, names=['dt','open','high','low','close','volume'])
        df['dt']=pd.to_datetime(df['dt'])
        dfs.append(df)
    df=pd.concat(dfs,ignore_index=True).drop_duplicates('dt').sort_values('dt')
    return df

def prep_5m(df):
    # Signed-volume proxy from each minute: volume signed by minute close-vs-open.
    s=np.sign(df['close'].to_numpy()-df['open'].to_numpy())
    # zero-body minute inherits close-vs-prior-close sign, else 0 if unchanged
    prev=np.r_[np.nan, df['close'].to_numpy()[:-1]]
    zero=(s==0)
    s2=np.sign(df['close'].to_numpy()-prev)
    s[zero]=np.nan_to_num(s2[zero], nan=0.0)
    df=df.copy(); df['sv']=df['volume'].to_numpy()*s
    df=df.set_index('dt')
    # RTH only
    r=df.between_time('09:30','15:59', inclusive='both')
    agg=r.resample('5min', origin='start_day', offset='0min', label='left', closed='left').agg(
        open=('open','first'), high=('high','max'), low=('low','min'), close=('close','last'), volume=('volume','sum'), sv=('sv','sum'), n=('close','count'))
    agg=agg.dropna(subset=['open','high','low','close'])
    # require reasonably complete 5m bar; holiday early closes still okay until missing bars
    agg['tr']=agg['high']-agg['low']
    agg['body_frac']=(agg['close']-agg['open']).abs()/agg['tr'].replace(0,np.nan)
    agg['medtr20']=agg['tr'].rolling(20,min_periods=5).median()
    agg['date']=agg.index.date
    return agg

def benchmark_day(d):
    # d is 5m bars one day
    if len(d)<10: return None
    ors=d.between_time('09:30','09:40')
    if len(ors)<3: return None
    orh=ors['high'].max(); orl=ors['low'].min()
    sigs=d.between_time('09:45','10:30')
    sig=None; side=None
    for ts,r in sigs.iterrows():
        if r['body_frac']>=0.5 and r['close']>orh:
            sig=ts; side=1; break
        if r['body_frac']>=0.5 and r['close']<orl:
            sig=ts; side=-1; break
    if sig is None: return None
    pos=d.index.get_loc(sig)
    if pos+1>=len(d): return None
    ent_ts=d.index[pos+1]; ent=float(d.iloc[pos+1]['open'])
    stop=orl if side==1 else orh
    risk=abs(ent-stop)
    if risk<=0: return None
    # If entry is already beyond wrong side due gap, invalid
    if side==1 and ent<=stop: return None
    if side==-1 and ent>=stop: return None
    exit_px=None; exit_ts=None; reason='close'
    after=d.loc[ent_ts:]
    for ts,r in after.iterrows():
        if side==1 and r['low']<=stop:
            exit_px=stop; exit_ts=ts; reason='stop'; break
        if side==-1 and r['high']>=stop:
            exit_px=stop; exit_ts=ts; reason='stop'; break
    if exit_px is None:
        # last available RTH bar close
        r=after.iloc[-1]; exit_px=float(r['close']); exit_ts=after.index[-1]
    pts=side*(exit_px-ent)-COST_POINTS
    return dict(date=str(ent_ts.date()), side=side, signal_ts=sig, entry_ts=ent_ts, entry=ent, stop=stop,
                exit_ts=exit_ts, exit=exit_px, reason=reason, risk=risk, net_points=pts, netR=pts/risk, dollars=pts*POINT_VALUE)

def collective_day(d):
    if len(d)<10: return None
    ors=d.between_time('09:30','09:40')
    if len(ors)<3: return None
    orh=ors['high'].max(); orl=ors['low'].min()
    sigs=d.between_time('09:45','10:30')
    sig=None; side=None
    for ts,r in sigs.iterrows():
        if r['body_frac']>=0.5 and r['close']>orh:
            sig=ts; side=1; break
        if r['body_frac']>=0.5 and r['close']<orl:
            sig=ts; side=-1; break
    if sig is None: return None
    sigpos=d.index.get_loc(sig)
    # search after signal through 11:30
    cand=d.iloc[sigpos+1:]
    cand=cand[cand.index.time<=time(11,30)]
    if len(cand)<3: return None
    touch_seen=False
    pull_ext=None
    opp_bars=[]
    failure_ts=None
    fail_idx=None
    tol_default=max((orh-orl)*0.03,0.25)  # only fallback when rolling TR missing
    # Direction invalid if closes all way through opposite OR edge before entry
    for j,(ts,r) in enumerate(cand.iterrows()):
        if side==1 and r['close']<orl: return None
        if side==-1 and r['close']>orh: return None
        # meaningful pullback = tag the broken OR boundary after breakout
        if not touch_seen:
            if side==1 and r['low']<=orh:
                touch_seen=True; pull_ext=float(r['low'])
            elif side==-1 and r['high']>=orl:
                touch_seen=True; pull_ext=float(r['high'])
            else:
                continue
        else:
            if side==1: pull_ext=min(pull_ext,float(r['low']))
            else: pull_ext=max(pull_ext,float(r['high']))
        # Track bars where pressure is countertrend
        opp=(r['sv']<0 if side==1 else r['sv']>0)
        if opp:
            extreme=float(r['low'] if side==1 else r['high'])
            opp_bars.append((ts,extreme,float(r['sv'])))
            if len(opp_bars)>=2:
                prev_ext=opp_bars[-2][1]
                tol=0.25*(float(r['medtr20']) if pd.notna(r['medtr20']) and r['medtr20']>0 else tol_default)
                # failure = latest opposing pressure does not materially extend adverse price extreme
                failed=(extreme >= prev_ext-tol) if side==1 else (extreme <= prev_ext+tol)
                if failed:
                    failure_ts=ts; fail_idx=j; break
    if failure_ts is None: return None
    # Need reassertion AFTER the failure bar, through 11:30
    cand_idx=list(cand.index)
    fpos=cand_idx.index(failure_ts)
    re_ts=None
    for k in range(fpos+1,len(cand)):
        ts=cand.index[k]; r=cand.iloc[k]
        # continue updating pullback extreme until entry trigger
        if side==1: pull_ext=min(pull_ext,float(r['low']))
        else: pull_ext=max(pull_ext,float(r['high']))
        if side==1 and r['close']<orl: return None
        if side==-1 and r['close']>orh: return None
        if k<2: continue
        prev2=cand.iloc[k-2:k]
        pressure_ok=(r['sv']>0 if side==1 else r['sv']<0)
        structure_ok=(r['close']>prev2['high'].max() if side==1 else r['close']<prev2['low'].min())
        if pressure_ok and structure_ok and r['body_frac']>=0.5:
            re_ts=ts; break
    if re_ts is None: return None
    gpos=d.index.get_loc(re_ts)
    if gpos+1>=len(d): return None
    ent_ts=d.index[gpos+1]; ent=float(d.iloc[gpos+1]['open'])
    stop=float(pull_ext)
    risk=abs(ent-stop)
    if risk<=0: return None
    if side==1 and ent<=stop: return None
    if side==-1 and ent>=stop: return None
    # Do not chase a trigger if entry already exceeds yesterday-like excessive multiple? no extra filter: frozen.
    # Manage: hard stop; otherwise exit after structure flip + successful opposite pressure.
    after=d.loc[ent_ts:]
    exit_px=None; exit_ts=None; reason='close'
    rows=list(after.iterrows())
    for ii,(ts,r) in enumerate(rows):
        if side==1 and r['low']<=stop:
            exit_px=stop; exit_ts=ts; reason='stop'; break
        if side==-1 and r['high']>=stop:
            exit_px=stop; exit_ts=ts; reason='stop'; break
        if ii>=2:
            prev2=pd.DataFrame([rows[ii-2][1],rows[ii-1][1]])
            opp=(r['sv']<0 if side==1 else r['sv']>0)
            succeeds=(r['close']<prev2['low'].min() if side==1 else r['close']>prev2['high'].max())
            if opp and succeeds:
                # execute at next bar open if exists; else current close
                if ii+1<len(rows):
                    exit_ts=rows[ii+1][0]; exit_px=float(rows[ii+1][1]['open'])
                else:
                    exit_ts=ts; exit_px=float(r['close'])
                reason='thesis_flip'; break
    if exit_px is None:
        r=after.iloc[-1]; exit_px=float(r['close']); exit_ts=after.index[-1]
    pts=side*(exit_px-ent)-COST_POINTS
    return dict(date=str(ent_ts.date()), side=side, signal_ts=sig, failure_ts=failure_ts, reassert_ts=re_ts,
                entry_ts=ent_ts, entry=ent, stop=stop, exit_ts=exit_ts, exit=exit_px, reason=reason,
                risk=risk, net_points=pts, netR=pts/risk, dollars=pts*POINT_VALUE)

def stats(trades):
    t=pd.DataFrame(trades)
    if t.empty: return {}
    pnl=t['dollars']; r=t['netR']
    eq=r.cumsum(); dd=eq-eq.cummax()
    wins=pnl[pnl>0].sum(); losses=-pnl[pnl<0].sum()
    return dict(trades=len(t), win_rate=(pnl>0).mean()*100, netR=r.sum(), avgR=r.mean(), net_dollars=pnl.sum(),
                PF=(wins/losses if losses>0 else np.inf), maxDD_R=dd.min(), avg_win_R=r[r>0].mean() if (r>0).any() else np.nan,
                avg_loss_R=r[r<0].mean() if (r<0).any() else np.nan)

all_b=[]; all_c=[]
yearly=[]
for year in YEARS:
    print('loading',year,flush=True)
    df=load_year(year)
    f=prep_5m(df)
    b=[]; c=[]
    for date,g in f.groupby('date'):
        rb=benchmark_day(g)
        if rb: b.append(rb)
        rc=collective_day(g)
        if rc: c.append(rc)
    all_b += b; all_c += c
    sb=stats(b); sc=stats(c)
    yearly.append({'year':year, **{f'B_{k}':v for k,v in sb.items()}, **{f'C_{k}':v for k,v in sc.items()}})
    print(year, 'B',sb, 'C',sc,flush=True)

B=pd.DataFrame(all_b); C=pd.DataFrame(all_c); Y=pd.DataFrame(yearly)
B.to_csv('/mnt/data/Samir_ORB_benchmark_retest_2008_2026.csv',index=False)
C.to_csv('/mnt/data/Failed_Counterattack_Continuation_v1_2008_2026.csv',index=False)
Y.to_csv('/mnt/data/Failed_Counterattack_vs_Samir_yearly.csv',index=False)

# summaries for key windows
summ=[]
for name,t in [('Samir ORB',B),('Failed Counterattack',C)]:
    t=t.copy(); t['year']=pd.to_datetime(t['date']).dt.year
    for lo,hi,label in [(2008,2026,'2008-2026'),(2016,2026,'2016-2026'),(2021,2026,'2021-2026'),(2024,2026,'2024-2026')]:
        q=t[(t.year>=lo)&(t.year<=hi)]
        summ.append({'strategy':name,'window':label,**stats(q.to_dict('records'))})
S=pd.DataFrame(summ)
S.to_csv('/mnt/data/Failed_Counterattack_vs_Samir_summary.csv',index=False)
print('\nSUMMARY\n',S.to_string(index=False),flush=True)
