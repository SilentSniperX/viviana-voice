#!/usr/bin/env python3
"""ORB parity checker: TradingView 'List of Trades' export vs the verified
research reference. Usage: python parity_check.py TV_EXPORT.csv
[--ref parity_reference_orb_2016_2026.csv] [--price-tol 0.25] [--offset-ok]
--offset-ok : ignore constant per-contract price offsets (unadjusted TV feed)."""
import sys, argparse, pandas as pd, numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("tv"); ap.add_argument("--ref",default="parity_reference_orb_2016_2026.csv")
ap.add_argument("--price-tol",type=float,default=0.25); ap.add_argument("--offset-ok",action="store_true")
a=ap.parse_args()

ref=pd.read_csv(a.ref)
ref["date"]=pd.to_datetime(ref["date"]).dt.date
tv_raw=pd.read_csv(a.tv)
# TradingView export: one row per fill; pivot entry/exit rows into trades
cols={c.lower():c for c in tv_raw.columns}
def col(*names):
    for n in names:
        if n in cols: return cols[n]
    raise SystemExit(f"column not found: {names} — adjust names for your export locale")
tnum=col("trade #","trade  #","trade")
ttyp=col("type"); tdt=col("date/time","date"); tpx=col("price","price usd")
tv=[]
for n,g in tv_raw.groupby(tv_raw[tnum]):
    ent=g[g[ttyp].str.contains("Entry",case=False)]; ex=g[g[ttyp].str.contains("Exit",case=False)]
    if len(ent)==0: continue
    e=ent.iloc[0]
    side="LONG" if "long" in str(e[ttyp]).lower() else "SHORT"
    ets=pd.to_datetime(e[tdt]); epx=float(e[tpx])
    if len(ex): x=ex.iloc[0]; xts=pd.to_datetime(x[tdt]); xpx=float(x[tpx])
    else: xts=pd.NaT; xpx=np.nan
    tv.append(dict(date=ets.date(),side=side,entry_ts=ets,entry=epx,exit_ts=xts,exit_px=xpx))
tv=pd.DataFrame(tv)
print(f"TV trades parsed: {len(tv)} | reference: {len(ref)}")
lo,hi=tv.date.min(),tv.date.max()
r=ref[(ref.date>=lo)&(ref.date<=hi)].copy()
print(f"overlap window {lo} → {hi}: reference has {len(r)} trades")

m=r.merge(tv,on="date",how="outer",suffixes=("_ref","_tv"),indicator=True)
miss=m[m._merge=="left_only"]; extra=m[m._merge=="right_only"]; both=m[m._merge=="both"].copy()
print(f"\nMISSING_DAY (ref only): {len(miss)}"); print(miss.date.tolist()[:15])
print(f"EXTRA_DAY (TV only): {len(extra)}");  print(extra.date.tolist()[:15])

both["dir_ok"]=both.side_ref==both.side_tv
both["etime_ok"]=pd.to_datetime(both.entry_ts_tv).dt.strftime("%H:%M")==pd.to_datetime(both.entry_ts_ref).dt.strftime("%H:%M")
off=0.0
if a.offset_ok and len(both):
    off_series=(both.entry_tv-both.entry_ref)
    both["day_off"]=off_series
    both["eprice_ok"]=True  # offset absorbed; report offsets instead
else:
    both["eprice_ok"]=(both.entry_tv-both.entry_ref).abs()<=a.price_tol
both["xtime_ok"]=pd.to_datetime(both.exit_ts_tv).dt.strftime("%H:%M")==pd.to_datetime(both.exit_ts_ref).dt.strftime("%H:%M")
for cat,ok in [("DIRECTION","dir_ok"),("ENTRY_TIME","etime_ok"),("ENTRY_PRICE","eprice_ok"),("EXIT_TIME","xtime_ok")]:
    bad=both[~both[ok]]
    print(f"{cat} mismatches: {len(bad)}")
    if len(bad): print(bad[["date","side_ref","side_tv","entry_ts_ref","entry_ts_tv","entry_ref","entry_tv"]].head(8).to_string())
if a.offset_ok and len(both):
    print("\nper-window entry price offsets (should be piecewise-constant per contract):")
    print(both.groupby(pd.to_datetime(both.date.astype(str)).dt.to_period("Q")).day_off.median().round(2).to_string())
n_ok=(both.dir_ok&both.etime_ok&both.eprice_ok&both.xtime_ok).sum()
print(f"\nFULL-MATCH trades: {n_ok}/{len(both)} in overlap  → PARITY {'PASS' if n_ok==len(both) and len(miss)+len(extra)==0 else 'INCOMPLETE — see buckets above'}")
