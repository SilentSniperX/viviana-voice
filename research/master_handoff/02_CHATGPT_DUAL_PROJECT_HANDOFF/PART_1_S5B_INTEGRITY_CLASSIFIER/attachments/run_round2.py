import zipfile,pandas as pd,numpy as np,json
ZIP='/mnt/data/NQ_1m_by_year_2023-2026(5).zip'; years=[2024,2025,2026]; COST=15.;PV=20.
frames=[]
with zipfile.ZipFile(ZIP) as z:
 for y in years:
  with z.open(f'NQ_adj_1m_{y}.csv') as f:frames.append(pd.read_csv(f,header=None,names=['dt','open','high','low','close','vol'],parse_dates=['dt']))
m1=pd.concat(frames,ignore_index=True).sort_values('dt').drop_duplicates('dt').set_index('dt')
rth=m1.between_time('09:30','15:59'); daily=rth.groupby(rth.index.date).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'));daily['range']=daily.high-daily.low;daily['clv']=(daily.close-daily.low)/daily.range;daily['prev_high']=daily.high.shift(1);daily['prev_low']=daily.low.shift(1);daily['prev_mid']=((daily.high+daily.low)/2).shift(1);daily['prev_clv']=daily.clv.shift(1)
m5=m1.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','vol':'sum'}).dropna().between_time('09:30','15:59');m5['date']=m5.index.date
rng=(m5.high-m5.low).replace(0,np.nan);m5['body_frac']=(m5.close-m5.open).abs()/rng;m5['pressure']=m5.vol*((m5.close-m5.open)/rng).fillna(0);m5['ema21']=m5.close.ewm(span=21,adjust=False).mean();m5['ema_slope3']=m5.ema21-m5.ema21.shift(3);m5['vmed12']=m5.vol.shift(1).rolling(12,min_periods=6).median();m5['pabsmed12']=m5.pressure.abs().shift(1).rolling(12,min_periods=6).median()
# overnight session date mapping 18:00-> next day, 00:00-09:29 same day
pre=m1[(m1.index.time>=pd.Timestamp('18:00').time()) | (m1.index.time<=pd.Timestamp('09:29').time())].copy()
sd=np.array([ (ts+pd.Timedelta(days=1)).date() if ts.time()>=pd.Timestamp('18:00').time() else ts.date() for ts in pre.index ])
on=pre.groupby(sd).agg(onh=('high','max'),onl=('low','min'))

def sim(entry_time,direction,entry,stop,target=None,end='15:59'):
 risk=abs(entry-stop)
 if not np.isfinite(risk) or risk<=.25:return None
 endt=pd.Timestamp.combine(pd.Timestamp(entry_time.date()),pd.Timestamp(end).time());path=m1.loc[(m1.index>=entry_time)&(m1.index<=endt)]
 if path.empty:return None
 exitpx=None;reason='close'
 for t,r in path.iterrows():
  hs=(r.low<=stop) if direction==1 else (r.high>=stop); ht=False if target is None else ((r.high>=target) if direction==1 else (r.low<=target))
  if hs and ht:exitpx=stop;reason='both_stop_first';break
  if hs:exitpx=stop;reason='stop';break
  if ht:exitpx=target;reason='target';break
 if exitpx is None:exitpx=float(path.close.iloc[-1])
 pts=(exitpx-entry)*direction;netd=pts*PV-COST;netR=netd/(risk*PV)
 return {'entry_time':entry_time,'dir':direction,'entry':entry,'stop':stop,'target':np.nan if target is None else target,'exit':exitpx,'risk_pts':risk,'pts':pts,'net_dollars':netd,'netR':netR,'reason':reason}

def add(out,tr,name,d):
 if tr: tr.update(strategy=name,date=str(d));out.append(tr)

def s6():
 out=[]
 for d,g in m5.groupby('date'):
  rows=list(g.between_time('09:45','11:30').iterrows())
  for i in range(1,len(rows)-1):
   t,b=rows[i]; prev=rows[i-1][1]
   # trend side + EMA slope; pullback touches EMA and closes back with trend
   direction=1 if (b.ema_slope3>0 and b.close>b.ema21 and b.low<=b.ema21) else (-1 if (b.ema_slope3<0 and b.close<b.ema21 and b.high>=b.ema21) else 0)
   if not direction:continue
   tc,c=rows[i+1]
   conf=(direction==1 and c.close>b.high and c.close>c.open) or (direction==-1 and c.close<b.low and c.close<c.open)
   if not conf or i+2>=len(rows):continue
   te,e=rows[i+2];entry=float(e.open);stop=float(min(b.low,c.low)-.25 if direction==1 else max(b.high,c.high)+.25);risk=abs(entry-stop);target=entry+direction*2*risk
   add(out,sim(te,direction,entry,stop,target),'S6_Rajan_EMA_TrendPullback',d);break
 return pd.DataFrame(out)

def s7():
 out=[]
 for d,g in m5.groupby('date'):
  if d not in on.index:continue
  onh=float(on.loc[d,'onh']);onl=float(on.loc[d,'onl']);rows=list(g.between_time('09:30','10:30').iterrows());taken=False
  for i,(t,b) in enumerate(rows[:-2]):
   cand=[]
   if b.high>onh and b.close<onh:cand.append((-1,float(b.high),float(b.low)))
   if b.low<onl and b.close>onl:cand.append((1,float(b.high),float(b.low)))
   for direction,sh,sl in cand:
    for j in [i+1,i+2]:
     if j>=len(rows)-1:break
     bc=rows[j][1];conf=(direction==1 and bc.close>sh) or (direction==-1 and bc.close<sl)
     if conf:
      te,e=rows[j+1];entry=float(e.open);stop=float(sl-.25 if direction==1 else sh+.25);risk=abs(entry-stop);target=entry+direction*2*risk
      add(out,sim(te,direction,entry,stop,target),'S7_TJR_OvernightSweepConfirm',d);taken=True;break
    if taken:break
   if taken:break
 return pd.DataFrame(out)

def s8():
 out=[]
 for d,g in m5.groupby('date'):
  orb=g.between_time('09:30','09:55')
  if len(orb)<6:continue
  oh=float(orb.high.max());ol=float(orb.low.min());rows=list(g.between_time('10:00','11:30').iterrows());taken=False
  for i,(t,b) in enumerate(rows[:-4]):
   direction=1 if (b.close>oh and b.body_frac>=.6 and b.vol>b.vmed12 and b.pressure>0) else (-1 if (b.close<ol and b.body_frac>=.6 and b.vol>b.vmed12 and b.pressure<0) else 0)
   if not direction:continue
   # don't chase initial move: need at least one counter bar or pause, then follow-through pressure bar within next 3
   pbext_low=1e18;pbext_high=-1e18;seen_counter=False
   for j in range(i+1,min(i+4,len(rows)-1)):
    bj=rows[j][1];pbext_low=min(pbext_low,bj.low);pbext_high=max(pbext_high,bj.high)
    if (direction==1 and bj.close<bj.open) or (direction==-1 and bj.close>bj.open):seen_counter=True
    if not seen_counter:continue
    med=bj.pabsmed12 if np.isfinite(bj.pabsmed12) else np.nan
    follow=(direction==1 and bj.close>b.high and bj.pressure>med and bj.close>bj.open) or (direction==-1 and bj.close<b.low and -bj.pressure>med and bj.close<bj.open)
    if follow:
     te,e=rows[j+1];entry=float(e.open);stop=float(pbext_low-.25 if direction==1 else pbext_high+.25);risk=abs(entry-stop);target=entry+direction*2*risk
     add(out,sim(te,direction,entry,stop,target),'S8_Fabio_MomentumFollowThroughProxy',d);taken=True;break
   if taken:break
 return pd.DataFrame(out)

def s9():
 out=[]
 for d,g in m5.groupby('date'):
  gg=g.copy();gg['box_hi']=gg.high.shift(1).rolling(6,min_periods=6).max();gg['box_lo']=gg.low.shift(1).rolling(6,min_periods=6).min();gg['box_rng']=gg.box_hi-gg.box_lo;gg['box_med30']=gg.box_rng.shift(1).rolling(30,min_periods=15).median();rows=list(gg.between_time('09:45','11:30').iterrows())
  for i,(t,b) in enumerate(rows[:-1]):
   if not np.isfinite(b.box_med30) or b.box_rng>.60*b.box_med30 or b.body_frac<.60 or b.vol<=b.vmed12:continue
   direction=1 if b.close>b.box_hi else (-1 if b.close<b.box_lo else 0)
   if not direction:continue
   te,e=rows[i+1];entry=float(e.open);stop=float(b.low-.25 if direction==1 else b.high+.25);risk=abs(entry-stop);target=entry+direction*3*risk
   add(out,sim(te,direction,entry,stop,target),'S9_Hanlin_RollingCompressionBreakout',d);break
 return pd.DataFrame(out)

def s10():
 out=[]
 for d,g in m5.groupby('date'):
  if d not in daily.index:continue
  r=daily.loc[d]
  if not np.isfinite(r.prev_clv):continue
  direction=-1 if r.prev_clv>=.80 else (1 if r.prev_clv<=.20 else 0)
  if not direction:continue
  entrybar=g.between_time('09:30','09:30')
  if entrybar.empty:continue
  te=entrybar.index[0];entry=float(entrybar.open.iloc[0]);stop=float(r.prev_low-.25 if direction==1 else r.prev_high+.25);target=float(r.prev_mid)
  # target must be in trade direction from entry
  if (target-entry)*direction<=0:continue
  add(out,sim(te,direction,entry,stop,target,end='12:00'),'S10_Larry_CLV_MeanReversion',d)
 return pd.DataFrame(out)

def summ(df):
 if df.empty:return{}
 eq=np.cumsum(df.netR.values);peak=np.maximum.accumulate(np.r_[0,eq]);dd=np.r_[0,eq]-peak;gp=df.loc[df.net_dollars>0,'net_dollars'].sum();gl=-df.loc[df.net_dollars<0,'net_dollars'].sum();pf=gp/gl if gl else np.inf
 return {'trades':len(df),'win_pct':round(100*(df.net_dollars>0).mean(),2),'netR':round(df.netR.sum(),2),'net_dollars':round(df.net_dollars.sum(),0),'PF':round(pf,2),'maxDD_R':round(dd.min(),2),'years':df.assign(year=pd.to_datetime(df.date).dt.year).groupby('year').netR.sum().round(2).to_dict()}
for f in [s6,s7,s8,s9,s10]:
 df=f();name=df.strategy.iloc[0] if len(df) else f.__name__;df.to_csv('/mnt/data/'+name+'.csv',index=False);print(name,json.dumps(summ(df),indent=2))
