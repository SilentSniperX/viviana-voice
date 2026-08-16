import pandas as pd, numpy as np, zipfile
from datetime import time, timedelta
ZIP_NEW='/mnt/data/NQ_1m_by_year_2023-2026(5).zip'; ZIP_OLD='/mnt/data/NQ_1m_history_pre2023(1).zip'
COST_POINTS=.75; PV=20.0

def load_year(year):
    if year<2023: chunks=[(ZIP_OLD,f'NQ_adj_1m_{year}.csv')]
    elif year==2023: chunks=[(ZIP_OLD,'NQ_adj_1m_2023.csv'),(ZIP_NEW,'NQ_adj_1m_2023.csv')]
    else: chunks=[(ZIP_NEW,f'NQ_adj_1m_{year}.csv')]
    ds=[]
    for z,m in chunks:
        with zipfile.ZipFile(z) as zz:
            d=pd.read_csv(zz.open(m),header=None,names=['dt','open','high','low','close','volume'])
        d['dt']=pd.to_datetime(d.dt); ds.append(d)
    d=pd.concat(ds).drop_duplicates('dt').sort_values('dt')
    s=np.sign(d.close-d.open); prev=d.close.shift(1); z=(s==0); s.loc[z]=np.sign(d.loc[z,'close']-prev.loc[z]).fillna(0)
    d['sv']=d.volume*s
    d['tr']=d.high-d.low; d['body_frac']=(d.close-d.open).abs()/d.tr.replace(0,np.nan)
    d=d.set_index('dt').between_time('09:30','15:59')
    d['date']=d.index.date
    return d

def make5(m):
    f=m.resample('5min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),sv=('sv','sum'),volume=('volume','sum'))
    f=f.dropna(); f['tr']=f.high-f.low; f['body_frac']=(f.close-f.open).abs()/f.tr.replace(0,np.nan); f['date']=f.index.date
    return f

def day_trade(mday,fday):
    ors=mday.between_time('09:30','09:44')
    if len(ors)<10:return None
    orh=ors.high.max(); orl=ors.low.min()
    sig=None; side=None
    for ts,r in fday.between_time('09:45','10:30').iterrows():
        if r.body_frac>=.5 and r.close>orh: sig=ts; side=1; break
        if r.body_frac>=.5 and r.close<orl: sig=ts; side=-1; break
    if sig is None:return None
    start=sig+pd.Timedelta(minutes=5)
    cand=mday[(mday.index>=start)&(mday.index.time<=time(11,30))]
    if len(cand)<4:return None
    touch=False; pull_ext=None; opp=[]; fail=None
    for ts,r in cand.iterrows():
        if side==1 and r.close<orl:return None
        if side==-1 and r.close>orh:return None
        if not touch:
            if side==1 and r.low<=orh: touch=True; pull_ext=float(r.low)
            elif side==-1 and r.high>=orl: touch=True; pull_ext=float(r.high)
            else: continue
        else:
            pull_ext=min(pull_ext,float(r.low)) if side==1 else max(pull_ext,float(r.high))
        isopp=(r.sv<0 if side==1 else r.sv>0)
        if isopp:
            ext=float(r.low if side==1 else r.high); opp.append((ts,ext))
            if len(opp)>=2:
                # strict no-progress failure on lower timeframe
                failed=(ext>=opp[-2][1]) if side==1 else (ext<=opp[-2][1])
                if failed: fail=ts; break
    if fail is None:return None
    # Reassertion on 1m: original pressure + directional body + close through prior 2 minute extremes
    after=cand[cand.index>fail]
    re=None
    for ts,r in after.iterrows():
        pull_ext=min(pull_ext,float(r.low)) if side==1 else max(pull_ext,float(r.high))
        if side==1 and r.close<orl:return None
        if side==-1 and r.close>orh:return None
        pos=mday.index.get_loc(ts)
        if pos<2:continue
        p2=mday.iloc[pos-2:pos]
        pressure=(r.sv>0 if side==1 else r.sv<0)
        structure=(r.close>p2.high.max() if side==1 else r.close<p2.low.min())
        directional=(r.close>r.open if side==1 else r.close<r.open)
        if pressure and structure and directional and r.body_frac>=.5:
            re=ts;break
    if re is None:return None
    p=mday.index.get_loc(re)
    if p+1>=len(mday):return None
    ent_ts=mday.index[p+1]; ent=float(mday.iloc[p+1].open); stop=float(pull_ext); risk=abs(ent-stop)
    if risk<=0:return None
    if side==1 and ent<=stop:return None
    if side==-1 and ent>=stop:return None
    sub=mday.loc[ent_ts:]
    # hard stop first, then hold-close variant
    ex_hold=None; reason_hold='close'
    for ts,r in sub.iterrows():
        if side==1 and r.low<=stop: ex_hold=stop; reason_hold='stop'; break
        if side==-1 and r.high>=stop: ex_hold=stop; reason_hold='stop'; break
    if ex_hold is None: ex_hold=float(sub.iloc[-1].close)
    hp=side*(ex_hold-ent)-COST_POINTS
    # 5m management variant: evaluate completed 5m bars after entry; exit next 5m open after flip
    fsub=fday[fday.index>=ent_ts.floor('5min')]
    ex_dyn=None; reason_dyn='close'
    # intrabar stop checked using minute data chronologically while also finding 5m flip timestamp.
    flip_exit_ts=None
    for i in range(2,len(fsub)):
        r=fsub.iloc[i]; p2=fsub.iloc[i-2:i]
        oppp=(r.sv<0 if side==1 else r.sv>0)
        succeeds=(r.close<p2.low.min() if side==1 else r.close>p2.high.max())
        if oppp and succeeds:
            if i+1<len(fsub): flip_exit_ts=fsub.index[i+1]
            else: flip_exit_ts=fsub.index[i]+pd.Timedelta(minutes=5)
            break
    for ts,r in sub.iterrows():
        if side==1 and r.low<=stop: ex_dyn=stop; reason_dyn='stop'; break
        if side==-1 and r.high>=stop: ex_dyn=stop; reason_dyn='stop'; break
        if flip_exit_ts is not None and ts>=flip_exit_ts:
            ex_dyn=float(r.open); reason_dyn='thesis_flip'; break
    if ex_dyn is None: ex_dyn=float(sub.iloc[-1].close)
    dp=side*(ex_dyn-ent)-COST_POINTS
    return {'date':str(ent_ts.date()),'side':side,'signal_ts':sig,'failure_ts':fail,'reassert_ts':re,'entry_ts':ent_ts,'entry':ent,'stop':stop,'risk':risk,
            'hold_netR':hp/risk,'hold_dollars':hp*PV,'hold_reason':reason_hold,'dyn_netR':dp/risk,'dyn_dollars':dp*PV,'dyn_reason':reason_dyn}

def stats(df,rcol,dcol):
    if df.empty:return {}
    r=df[rcol]; d=df[dcol]; eq=r.cumsum(); dd=eq-eq.cummax(); gp=d[d>0].sum(); gl=-d[d<0].sum()
    return {'trades':len(df),'win%':(d>0).mean()*100,'netR':r.sum(),'avgR':r.mean(),'dollars':d.sum(),'PF':gp/gl if gl else np.inf,'maxDD_R':dd.min()}

all=[]; yearly=[]
for y in range(2016,2027):
    print('year',y,flush=True)
    m=load_year(y); f=make5(m); tr=[]
    for date,md in m.groupby('date'):
        fd=f[f.date==date]
        x=day_trade(md,fd)
        if x:tr.append(x)
    q=pd.DataFrame(tr); all+=tr
    yearly.append({'year':y,**{'hold_'+k:v for k,v in stats(q,'hold_netR','hold_dollars').items()},**{'dyn_'+k:v for k,v in stats(q,'dyn_netR','dyn_dollars').items()}})
    print(y,stats(q,'hold_netR','hold_dollars'),stats(q,'dyn_netR','dyn_dollars'),flush=True)
A=pd.DataFrame(all); A['date']=pd.to_datetime(A.date); A=A.sort_values('date')
A.to_csv('/mnt/data/Failed_Counterattack_1m_execution_2016_2026.csv',index=False)
Y=pd.DataFrame(yearly);Y.to_csv('/mnt/data/Failed_Counterattack_1m_yearly.csv',index=False)
for lo,label in [(2016,'2016-2026'),(2021,'2021-2026'),(2024,'2024-2026')]:
    q=A[A.date.dt.year>=lo]
    print(label,'HOLD',stats(q,'hold_netR','hold_dollars'),'DYN',stats(q,'dyn_netR','dyn_dollars'),flush=True)
