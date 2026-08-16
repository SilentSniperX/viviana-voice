import pandas as pd, numpy as np, zipfile, os
from datetime import time
ZIP_NEW='/mnt/data/NQ_1m_by_year_2023-2026(5).zip'; ZIP_OLD='/mnt/data/NQ_1m_history_pre2023(1).zip'
COST=.75

def load(y):
    if y<2023: chunks=[(ZIP_OLD,f'NQ_adj_1m_{y}.csv')]
    elif y==2023: chunks=[(ZIP_OLD,'NQ_adj_1m_2023.csv'),(ZIP_NEW,'NQ_adj_1m_2023.csv')]
    else: chunks=[(ZIP_NEW,f'NQ_adj_1m_{y}.csv')]
    ds=[]
    for z,m in chunks:
        with zipfile.ZipFile(z) as zz:
            d=pd.read_csv(zz.open(m),header=None,names=['dt','open','high','low','close','volume'])
        d.dt=pd.to_datetime(d.dt); ds.append(d)
    d=pd.concat(ds).drop_duplicates('dt').sort_values('dt')
    return d.set_index('dt').between_time('09:30','15:59')

def f5(m):
    f=m.resample('5min',label='left',closed='left').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna()
    return f.between_time('09:30','15:55')

def s5b_day(fd, vol_shift=False):
    # require OB first six bars 09:30 through 09:55
    ob=fd.between_time('09:30','09:55')
    if len(ob)<6: return None
    obh=float(ob.high.max()); obl=float(ob.low.min())
    # rolling mean. Test include current vs prior
    if vol_shift:
        vmean=fd.volume.shift(1).rolling(12,min_periods=6).mean()
    else:
        vmean=fd.volume.rolling(12,min_periods=6).mean()
    side=0; dir_ts=None; leg_low=None; leg_high=None; pull=False; pull_start_j=None; dead=False
    rows=list(fd.iterrows())
    for j,(ts,r) in enumerate(rows):
        if ts.time() < time(10,0): continue
        if side==0:
            if r.close>obh: side=1
            elif r.close<obl: side=-1
            else: continue
            dir_ts=ts
            # leg extremes: session up to and including direction bar
            hist=fd.loc[:ts]
            leg_low=float(hist.low.min()); leg_high=float(hist.high.max())
            continue
        # update trend extreme and compute retrace vs leg range
        if side==1:
            prev_leg_high=leg_high
            made_new_extreme=float(r.high)>leg_high
            leg_high=max(leg_high,float(r.high)); leg=leg_high-leg_low
            if leg<=0: continue
            retr=(leg_high-float(r.low))/leg
            if retr>1.0:
                return {'side':side,'dir_ts':dir_ts,'invalid_ts':ts,'event_ts':None}
            if .25 <= retr <= .75 and not pull and not made_new_extreme:
                pull=True; pull_start_j=j
            if not pull: continue
            if j - pull_start_j < 2: continue
            # failure two consecutive no new progress with 1pt tolerance
            if j<2: continue
            r1=rows[j-1][1]; r2=rows[j-2][1]
            fail=(float(r.low) >= float(r1.low)-1.0 and float(r1.low)>=float(r2.low)-1.0)
            resume=float(r.close)>float(r1.high) and float(r.close)>float(r.open)
        else:
            prev_leg_low=leg_low
            made_new_extreme=float(r.low)<leg_low
            leg_low=min(leg_low,float(r.low)); leg=leg_high-leg_low
            if leg<=0: continue
            retr=(float(r.high)-leg_low)/leg
            if retr>1.0:
                return {'side':side,'dir_ts':dir_ts,'invalid_ts':ts,'event_ts':None}
            if .25 <= retr <= .75 and not pull and not made_new_extreme:
                pull=True; pull_start_j=j
            if not pull: continue
            if j - pull_start_j < 2: continue
            if j<2: continue
            r1=rows[j-1][1]; r2=rows[j-2][1]
            fail=(float(r.high) <= float(r1.high)+1.0 and float(r1.high)<=float(r2.high)+1.0)
            resume=float(r.close)<float(r1.low) and float(r.close)<float(r.open)
        vm=vmean.loc[ts]
        pressure=pd.notna(vm) and float(r.volume)>=1.2*float(vm)
        if fail and resume and pressure:
            return {'side':side,'dir_ts':dir_ts,'invalid_ts':None,'event_ts':ts}
    return {'side':side if side else None,'dir_ts':dir_ts,'invalid_ts':None,'event_ts':None}

def standalone(fd, vol_shift=False):
    st=s5b_day(fd,vol_shift)
    if not st or st['event_ts'] is None:return None
    ts=st['event_ts']; side=st['side']
    if ts.time()>time(11,30):return None
    nxt=ts+pd.Timedelta(minutes=5)
    if nxt not in fd.index:return None
    ent=float(fd.loc[nxt,'open'])
    pos=fd.index.get_loc(ts)
    lo=max(0,pos-6); win=fd.iloc[lo:pos+1]
    stop=float(win.low.min()-2 if side==1 else win.high.max()+2)
    risk=abs(ent-stop)
    if risk<5: return None
    if (side==1 and ent<=stop) or (side==-1 and ent>=stop): return None
    # stop touch on 5m from entry through close
    exitpx=None; reason='close'; exit_ts=None
    for t,r in fd.loc[nxt:].iterrows():
        if side==1 and r.low<=stop: exitpx=stop;reason='stop';exit_ts=t;break
        if side==-1 and r.high>=stop: exitpx=stop;reason='stop';exit_ts=t;break
    if exitpx is None:
        rr=fd.loc[nxt:].iloc[-1]; exitpx=float(rr.close); exit_ts=fd.loc[nxt:].index[-1]
    pts=side*(exitpx-ent)-COST
    return pts/risk

for shift in [False,True]:
    vals=[]; n=0
    for y in [2024,2025,2026]:
        m=load(y); f=f5(m)
        for dt,g in f.groupby(f.index.date):
            x=standalone(g,shift)
            if x is not None: vals.append(x)
    a=np.array(vals); gp=a[a>0].sum();gl=-a[a<0].sum();eq=np.cumsum(a);dd=(eq-np.maximum.accumulate(eq)).min() if len(a) else 0
    print('SHIFT',shift,'n',len(a),'sumR',a.sum(),'win',(a>0).mean(),'PF',gp/gl,'DD',dd)
