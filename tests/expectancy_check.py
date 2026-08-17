#!/usr/bin/env python3
"""Reproduce docs/EXPECTANCY_REVIEW.md — is the 65-trade TradingView run evidence
of negative expectancy?

Answers, from data rather than opinion:
  1. what the reference itself did over the same recent window
  2. the honest sampling distribution of a 65-trade sample (bootstrap)
  3. whether the direction rule beats random and fade baselines
  4. whether S5b can influence P&L at all

Sections 1, 2 and 4 need no market data. Section 3 needs the raw archive in
`data/raw/`; it is skipped with a notice when absent.

Run:  python3 tests/expectancy_check.py
"""
from __future__ import annotations
import csv, io, os, random, re, statistics, sys, zipfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reference_engine import (Bar, _parse_ts, in_rth, to_5m, body_fraction,
                              OR_START, OR_END, QUAL_START, QUAL_END,
                              BODY_FILTER, COST_POINTS, POINT_VALUE)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
RAW = os.path.join(REPO, "data", "raw")

# The TradingView run being reviewed.
TV = dict(n=65, usd=-6014.0, pf=0.57, win=35.38, stop_share=53.8)


def perf(sub):
    d = [float(r["dollars"]) for r in sub]
    gp = sum(x for x in d if x > 0); gl = -sum(x for x in d if x < 0)
    stops = sum(1 for r in sub if r["exit_reason"] == "stop")
    return dict(n=len(d), usd=sum(d), pf=gp / gl if gl else float("inf"),
                win=100 * sum(1 for x in d if x > 0) / len(d),
                stops=100 * stops / len(d))


def main() -> int:
    rows = list(csv.DictReader(open(CANON)))
    print("=" * 72)
    print("1. THE REFERENCE OVER THE SAME RECENT WINDOW")
    last = rows[-TV["n"]:]
    a = perf(last)
    print(f"   reference last {a['n']} trades ({last[0]['date']} .. {last[-1]['date']})")
    print(f"     net ${a['usd']:>10,.0f}   PF {a['pf']:.2f}   win {a['win']:.1f}%   stops {a['stops']:.1f}%")
    print(f"   TradingView reported")
    print(f"     net ${TV['usd']:>10,.0f}   PF {TV['pf']:.2f}   win {TV['win']:.1f}%   stops {TV['stop_share']:.1f}%")
    print(f"   -> the reference lost ${a['usd'] - TV['usd']:,.0f} MORE than TradingView"
          if a["usd"] < TV["usd"] else "   -> TradingView lost more than the reference")

    print("=" * 72)
    print("2. CAN 65 TRADES RESOLVE A PF~1.10 EDGE?  (bootstrap, 20,000 samples)")
    mod = [r for r in rows if r["date"] >= "2024"]
    d = [float(r["dollars"]) for r in mod]
    rnd = random.Random(11)
    N, T = TV["n"], 20000
    pfs, usds, wins = [], [], []
    for _ in range(T):
        s = [rnd.choice(d) for _ in range(N)]
        gp = sum(x for x in s if x > 0); gl = -sum(x for x in s if x < 0)
        pfs.append(gp / gl if gl else float("inf")); usds.append(sum(s))
        wins.append(100 * sum(1 for x in s if x > 0) / N)
    q = lambda v, p: sorted(v)[int(p * len(v))]
    print(f"   drawn from the 2024-2026 reference (n={len(d)}, PF {perf(mod)['pf']:.2f})")
    print(f"     net $ 5th {q(usds,.05):>9,.0f}  median {statistics.median(usds):>9,.0f}  95th {q(usds,.95):>9,.0f}")
    print(f"     PF    5th {q(pfs,.05):.2f}       median {statistics.median(pfs):.2f}       95th {q(pfs,.95):.2f}")
    pct = lambda v, t: 100 * sum(1 for x in v if x <= t) / len(v)
    print(f"   P(net <= {TV['usd']:,.0f}) = {pct(usds, TV['usd']):.1f}%   "
          f"P(PF <= {TV['pf']}) = {pct(pfs, TV['pf']):.1f}%   "
          f"P(win% <= {TV['win']}) = {pct(wins, TV['win']):.1f}%")
    joint = 100 * sum(1 for i in range(T) if usds[i] <= TV["usd"]
                      and pfs[i] <= TV["pf"] and wins[i] <= TV["win"]) / T
    print(f"   P(all three at once) = {joint:.2f}%")

    print("=" * 72)
    print("4. CAN S5b AFFECT P&L?")
    src = open(os.path.join(REPO, "pine", "nq_orb_s5b_v1.pine")).read()
    s5b = ["s5bSide", "s5bStateN", "alignmentStr", "confirmTs", "s5bEntryOk",
           "pullActive", "legHigh", "legLow"]
    orders = [l.strip() for l in src.split("\n")
              if re.search(r"strategy\.(entry|exit|close|close_all|order|cancel)", l)]
    leaks = [l for l in orders if any(n in l for n in s5b)]
    print(f"   {len(orders)} order-placing lines, {len(leaks)} reference an S5b variable")
    print("   -> S5b CANNOT affect P&L" if not leaks else f"   -> LEAK: {leaks}")

    print("=" * 72)
    print("3. BASELINES — identical entry bar and risk distance, direction varied")
    if not os.path.isdir(RAW) or not any(f.endswith(".zip") for f in os.listdir(RAW)):
        print("   SKIPPED — no archive in data/raw/ (see docs/PARITY_PROCEDURE.md)")
        return 0
    days = {}
    for name in sorted(os.listdir(RAW)):
        if not name.endswith(".zip"):
            continue
        with zipfile.ZipFile(os.path.join(RAW, name)) as z:
            for m in sorted(z.namelist()):
                if "_unadj_" in m or not m.endswith(".csv"):
                    continue
                with io.TextIOWrapper(z.open(m), encoding="utf-8") as fh:
                    for row in csv.reader(fh):
                        if len(row) < 6:
                            continue
                        try:
                            ts = _parse_ts(row[0]); b = Bar(ts, *map(float, row[1:6]))
                        except ValueError:
                            continue
                        if in_rth(b):
                            days.setdefault(ts.date(), []).append(b)
    days = {k: to_5m(sorted(v, key=lambda x: x.ts)) for k, v in days.items()}

    def run(mode, seed=0):
        r = random.Random(seed); out = []
        for dt in sorted(days):
            rth = [b for b in days[dt] if in_rth(b)]
            orb = [b for b in rth if OR_START <= b.ts.time() < OR_END]
            if len(orb) < 3:
                continue
            orh = max(b.high for b in orb); orl = min(b.low for b in orb)
            sig = side = None
            for i, b in enumerate(rth):
                if not (QUAL_START <= b.ts.time() <= QUAL_END):
                    continue
                bf = body_fraction(b)
                if bf is None or bf < BODY_FILTER:
                    continue
                if b.close > orh: sig, side = i, 1; break
                if b.close < orl: sig, side = i, -1; break
            if sig is None or sig + 1 >= len(rth):
                continue
            if rth[sig + 1].ts != rth[sig].ts + timedelta(minutes=5):
                continue
            ent = rth[sig + 1].open
            ref_stop = orl if side == 1 else orh
            risk = abs(ent - ref_stop)
            if risk <= 0 or (side == 1 and ent <= ref_stop) or (side == -1 and ent >= ref_stop):
                continue
            take = side if mode == "follow" else (-side if mode == "fade" else r.choice([1, -1]))
            # Same RISK, correct side. Reusing the original stop would put the
            # inverted entry on the wrong side of it and the skip rule would
            # delete almost every trade — a degenerate comparison.
            stop = ent - risk if take == 1 else ent + risk
            px = None
            for b in rth[sig + 1:]:
                if (take == 1 and b.low <= stop) or (take == -1 and b.high >= stop):
                    px = stop; break
            if px is None:
                px = rth[-1].close
            out.append((take * (px - ent) - COST_POINTS) * POINT_VALUE)
        return out

    def show(lbl, v):
        gp = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
        print(f"   {lbl:24s} n={len(v):4d}  net=${sum(v):>9,.0f}  "
              f"PF={gp/gl if gl else 0:.2f}  win={100*sum(1 for x in v if x>0)/len(v):.1f}%")
    show("FOLLOW ORB (the rule)", run("follow"))
    show("FADE ORB (inverted)", run("fade"))
    for k in range(5):
        show(f"RANDOM direction #{k+1}", run("random", 200 + k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
