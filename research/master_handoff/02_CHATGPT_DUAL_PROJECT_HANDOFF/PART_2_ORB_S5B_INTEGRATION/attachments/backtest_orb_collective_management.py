import pandas as pd, numpy as np, zipfile
from datetime import time
ZIP_NEW='/mnt/data/NQ_1m_by_year_2023-2026(5).zip'; ZIP_OLD='/mnt/data/NQ_1m_history_pre2023(1).zip'; COST=.75; PV=20

def load(y):
    if y<2023: chunks=[(ZIP_OLD,f'NQ_adj_1m_{y}.csv')]
    elif y==2023: chunks=[(ZIP_OLD,'NQ_adj_1m_2023.csv'),(ZIP_NEW,'NQ_adj_1m_2023.csv')]
    else: chunks=[(ZIP_NEW,f'NQ_adj_1m_{y}.csv')]
    ds=[]
    for z,m in chunks:
        with zipfile.ZipFile(z) as zz: d=pd.read_csv(zz.open(m),header=None,names=['dt','open','high','low','close','volume'])
        d.dt=pd.to_datetime(d.dt); ds.append(d)
    d=pd.concat(ds).drop_duplicates('dt').sort_values('dt')
    s=np.sign(d.close-d.open); prev=d.close.shift(); zero=s==0; s.loc[zero]=np.sign(d.loc[zero,'close']-prev.loc[zero]).fillna(0)
    d['sv']=d.volume*s; d['tr']=d.high-d.low; d['bf']=(d.close-d.open).abs()/d.tr.replace(0,np.nan)
    return d.set_index('dt').between_time('09:30','15:59')

def f5(m):
    f=m.resample('5min').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),sv=('sv','sum'))
    f=f.dropna(); f['tr']=f.high-f.low; f['bf']=(f.close-f.open).abs()/f.tr.replace(0,np.nan); return f

def trade(md,fd):
    ors=md.between_time('09:30','09:44')
    if len(ors)<10:return None
    orh=ors.high.max(); orl=ors.low.min(); sig=None; side=None
    for ts,r in fd.between_time('09:45','10:30').iterrows():
        if r.bf>=.5 and r.close>orh: sig=ts;side=1;break
        if r.bf>=.5 and r.close<orl: sig=ts;side=-1;break
    if sig is None:return None
    start=sig+pd.Timedelta(minutes=5)
    if start not in md.index:return None
    ent_ts=start; ent=float(md.loc[start,'open']); init_stop=float(orl if side==1 else orh); risk=abs(ent-init_stop)
    if risk<=0 or (side==1 and ent<=init_stop) or (side==-1 and ent>=init_stop):return None
    stop=init_stop; touched=False; opp=[]; fail=False; managed=False; exit_px=None; exit_ts=None; reason='close'
    pull_ext=None
    sub=md.loc[ent_ts:]
    idxs=list(sub.index)
    for i,(ts,r) in enumerate(sub.iterrows()):
        # hard stop current stop
        if side==1 and r.low<=stop: exit_px=stop;exit_ts=ts;reason='stop';break
        if side==-1 and r.high>=stop: exit_px=stop;exit_ts=ts;reason='stop';break
        # collective management only through 11:30 and only until resolved once
        if managed or ts.time()>time(11,30): continue
        if not touched:
            if side==1 and r.low<=orh: touched=True; pull_ext=float(r.low)
            elif side==-1 and r.high>=orl: touched=True; pull_ext=float(r.high)
            else: continue
        else:
            pull_ext=min(pull_ext,float(r.low)) if side==1 else max(pull_ext,float(r.high))
        # if countertrend pressure succeeds with structure and closes back inside OR: exit next minute open
        if i>=2:
            prev2=sub.iloc[i-2:i]
            opppress=(r.sv<0 if side==1 else r.sv>0)
            success=(r.close<prev2.low.min() if side==1 else r.close>prev2.high.max())
            inside=(r.close<orh if side==1 else r.close>orl)
            directional=(r.close<r.open if side==1 else r.close>r.open)
            if opppress and success and inside and directional and r.bf>=.5:
                if i+1<len(sub): exit_ts=sub.index[i+1]; exit_px=float(sub.iloc[i+1].open)
                else: exit_ts=ts; exit_px=float(r.close)
                reason='counterattack_succeeds';break
        # failure bookkeeping
        isopp=(r.sv<0 if side==1 else r.sv>0)
        if isopp:
            ext=float(r.low if side==1 else r.high);opp.append(ext)
            if len(opp)>=2:
                fail=(ext>=opp[-2] if side==1 else ext<=opp[-2])
        # once failure exists, original pressure + structure reassertion tightens stop behind pullback, then hands off to close
        if fail and i>=2:
            prev2=sub.iloc[i-2:i]
            orig=(r.sv>0 if side==1 else r.sv<0)
            struct=(r.close>prev2.high.max() if side==1 else r.close<prev2.low.min())
            directional=(r.close>r.open if side==1 else r.close<r.open)
            if orig and struct and directional and r.bf>=.5:
                # migrate stop only if it tightens, never widen
                if side==1 and pull_ext>stop: stop=float(pull_ext)
                if side==-1 and pull_ext<stop: stop=float(pull_ext)
                managed=True
    if exit_px is None:
        exit_px=float(sub.iloc[-1].close); exit_ts=sub.index[-1]
    pts=side*(exit_px-ent)-COST
    return {'date':str(ent_ts.date()),'side':side,'entry_ts':ent_ts,'entry':ent,'initial_stop':init_stop,'final_stop':stop,'risk':risk,'exit_ts':exit_ts,'exit':exit_px,'reason':reason,'netR':pts/risk,'dollars':pts*PV}

def stats(q):
    if q.empty:return {}
    r=q.netR; d=q.dollars; eq=r.cumsum();dd=eq-eq.cummax();gp=d[d>0].sum();gl=-d[d<0].sum()
    return {'trades':len(q),'win%':(d>0).mean()*100,'netR':r.sum(),'avgR':r.mean(),'$':d.sum(),'PF':gp/gl if gl else np.inf,'maxDD_R':dd.min()}

all=[]; yrs=[]
for y in range(2008,2027):
    print('year',y,flush=True);m=load(y);f=f5(m);tr=[]
    dates=sorted(set(m.index.date))
    for dt in dates:
        md=m[m.index.date==dt]; fd=f[f.index.date==dt]; x=trade(md,fd)
        if x:tr.append(x)
    q=pd.DataFrame(tr);all+=tr;yrs.append({'year':y,**stats(q)});print(y,stats(q),flush=True)
A=pd.DataFrame(all);A['date']=pd.to_datetime(A.date);A=A.sort_values('date');A.to_csv('/mnt/data/ORB_Collective_Management_2008_2026.csv',index=False)
pd.DataFrame(yrs).to_csv('/mnt/data/ORB_Collective_Management_yearly.csv',index=False)
for lo,label in [(2008,'2008-2026'),(2016,'2016-2026'),(2021,'2021-2026'),(2024,'2024-2026')]:
    q=A[A.date.dt.year>=lo];print(label,stats(q),flush=True)
print('reasons 2016+',A[A.date.dt.year>=2016].groupby('reason').agg(trades=('netR','size'),netR=('netR','sum'),dollars=('dollars','sum')).to_string(),flush=True)
