#!/usr/bin/env python3
"""Reproduce docs/DEPLOYMENT_ASSESSMENT.md tasks 2 and 3 from measured data.

Task 2 (year-by-year, worst-case stats) uses reference/canonical_orb_trades.csv.
Task 3 (prop survivability) additionally needs per-trade INTRADAY adverse
excursion, which only the TradingView Strategy Tester export carries — trailing
drawdown is an intraday rule and closed-trade equity understates it.

    python3 tests/risk_report.py [--tv-export EXPORT.csv]
"""
from __future__ import annotations
import argparse, collections, csv, os, random, statistics, sys
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
NQ, MNQ = 20.0, 2.0
TRADES_PER_YEAR = 219


def load_reference(since="2016"):
    return [(r["date"], float(r["net_points"]), r["direction"], r["exit_reason"])
            for r in csv.DictReader(open(CANON)) if r["date"] >= since]


def load_tv(path):
    """Per-trade points and intraday MAE from a Strategy Tester export."""
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    tr = collections.defaultdict(dict)
    for r in rows:
        tr[int(r["Trade number"])]["exit" if "Exit" in r["Type"] else "entry"] = r
    mult = None
    for n in sorted(tr):
        if "entry" in tr[n]:
            mult = round(float(tr[n]["entry"]["Size (value)"]) /
                         float(tr[n]["entry"]["Price USD"]), 2)
            break
    out = []
    for n in sorted(tr):
        t = tr[n]
        if "exit" not in t or "entry" not in t:
            continue
        out.append(dict(date=t["entry"]["Date and time"][:10],
                        pnl=float(t["exit"]["Net PnL USD"]) / mult,
                        mae=-abs(float(t["exit"]["Adverse excursion USD"])) / mult))
    return out, mult


def drawdown(points_seq):
    eq = peak = worst = 0.0
    trough = None
    for d, p in points_seq:
        eq += p
        peak = max(peak, eq)
        if eq - peak < worst:
            worst, trough = eq - peak, d
    return worst, trough


def task2(P):
    print("=" * 78)
    print("TASK 2 — RISK SURVIVAL (reference, 1 NQ, $20/pt, costs included)")
    print(f"{'year':6s}{'trades':>7s}{'net pts':>10s}{'net $':>11s}{'PF':>6s}"
          f"{'win%':>7s}{'maxDD pts':>11s}{'maxDD $':>10s}")
    by = collections.defaultdict(list)
    for d, p, _, _ in P:
        by[d[:4]].append((d, p))
    for y in sorted(by) + ["ALL"]:
        rows = [(d, p) for d, p, _, _ in P] if y == "ALL" else by[y]
        v = [p for _, p in rows]
        gp = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
        m, _ = drawdown(rows)
        print(f"{y:6s}{len(v):7d}{sum(v):10,.1f}{sum(v)*NQ:11,.0f}"
              f"{gp/gl if gl else 0:6.2f}{100*sum(1 for x in v if x>0)/len(v):7.1f}"
              f"{m:11,.1f}{m*NQ:10,.0f}")

    v = [p for _, p, _, _ in P]
    m, at = drawdown([(d, p) for d, p, _, _ in P])
    mo = collections.defaultdict(float); qt = collections.defaultdict(float)
    for d, p, _, _ in P:
        mo[d[:7]] += p
        qt[f"{d[:4]}Q{(int(d[5:7])-1)//3+1}"] += p
    streak = cur = wstreak = wcur = 0
    for p in v:
        cur = cur + 1 if p < 0 else 0; streak = max(streak, cur)
        wcur = wcur + 1 if p > 0 else 0; wstreak = max(wstreak, wcur)
    W = [x for x in v if x > 0]; L = [x for x in v if x < 0]
    print(f"\n  max drawdown (closed)     {m:,.1f} pts = ${m*NQ:,.0f} NQ / "
          f"${m*MNQ:,.0f} MNQ   trough {at}")
    print(f"  worst month               {min(mo.items(), key=lambda x: x[1])}")
    print(f"  worst quarter             {min(qt.items(), key=lambda x: x[1])}")
    print(f"  longest losing streak     {streak} trades   (winning {wstreak})")
    print(f"  largest loss / win        {min(v):,.1f} / {max(v):,.1f} pts")
    print(f"  average loser / winner    {statistics.mean(L):,.1f} / "
          f"{statistics.mean(W):,.1f} pts")
    print(f"  expectancy                {statistics.mean(v):+.2f} pts/trade "
          f"= ${statistics.mean(v)*NQ:+,.0f} NQ / ${statistics.mean(v)*MNQ:+.2f} MNQ")
    for side in ("LONG", "SHORT"):
        s = [p for _, p, dn, _ in P if dn == side]
        gp = sum(x for x in s if x > 0); gl = -sum(x for x in s if x < 0)
        print(f"  {side:5s} n={len(s):4d} {sum(s):9,.1f} pts  PF {gp/gl:.2f}  "
              f"win {100*sum(1 for x in s if x>0)/len(s):.1f}%")


def bust(trades, contracts, limit, pt=MNQ):
    """Strict trailing drawdown: threshold follows the high-water mark including
    open-trade excursion and never locks."""
    eq = peak = 0.0
    for i, s in enumerate(trades):
        if eq + s["mae"] * contracts * pt - peak <= -limit:
            return i + 1, s["date"]
        eq += s["pnl"] * contracts * pt
        peak = max(peak, eq)
        if eq - peak <= -limit:
            return i + 1, s["date"]
    return None, None


def peak_trough(trades, contracts, pt=MNQ):
    eq = peak = worst = 0.0
    for s in trades:
        worst = min(worst, eq + s["mae"] * contracts * pt - peak)
        eq += s["pnl"] * contracts * pt
        peak = max(peak, eq)
        worst = min(worst, eq - peak)
    return worst


def task3(seq):
    print("=" * 78)
    print("TASK 3 — PROP SURVIVABILITY (MNQ $2/pt, intraday-aware, strict trailing)")
    print(f"{'size':>7s}{'peak-trough':>14s}   {'$2,500':>22s}{'$3,500':>22s}{'$5,000':>22s}")
    for c in (1, 2, 5):
        cells = []
        for lim in (2500, 3500, 5000):
            n, d = bust(seq, c, lim)
            cells.append(f"BUST t#{n} {d}" if n else "survives")
        print(f"{c:>5d}MNQ{peak_trough(seq, c):>14,.0f}   "
              f"{cells[0]:>22s}{cells[1]:>22s}{cells[2]:>22s}")

    rnd = random.Random(3)
    print(f"\n  bust probability, 10,000 bootstrapped sequences")
    print(f"{'size':>7s}{'horizon':>10s}{'$2,500':>10s}{'$3,500':>10s}{'$5,000':>10s}")
    for c in (1, 2, 5):
        for h, lbl in ((TRADES_PER_YEAR, "1 year"), (TRADES_PER_YEAR * 2, "2 years")):
            out = []
            for lim in (2500, 3500, 5000):
                b = sum(1 for _ in range(10000)
                        if bust([rnd.choice(seq) for _ in range(h)], c, lim)[0])
                out.append(f"{100*b/10000:9.1f}%")
            print(f"{c:>5d}MNQ{lbl:>10s}{out[0]}{out[1]}{out[2]}")

    exp = statistics.mean(s["pnl"] for s in seq)
    print(f"\n  what limit 1 MNQ would actually need:")
    for lim in (5000, 7500, 10000, 12500):
        r2 = sum(1 for _ in range(4000)
                 if bust([rnd.choice(seq) for _ in range(TRADES_PER_YEAR*2)], 1, lim)[0])
        print(f"    ${lim:>6,d}: {100*r2/4000:5.1f}% two-year bust")
    print(f"\n  expectancy {exp:+.2f} pts/trade = ${exp*MNQ:+.2f} -> "
          f"${exp*MNQ*TRADES_PER_YEAR:+,.0f}/year per MNQ")
    print(f"  VERDICT: a trailing-drawdown prop account is not viable at any of "
          f"1/2/5 MNQ.\n  Self-funded worst peak-to-trough: "
          + ", ".join(f"{c} MNQ ${peak_trough(seq, c):,.0f}" for c in (1, 2, 5)))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tv-export", default=None,
                    help="Strategy Tester export, for the intraday task-3 numbers")
    a = ap.parse_args(argv)
    task2(load_reference())
    if a.tv_export and os.path.exists(a.tv_export):
        seq, mult = load_tv(a.tv_export)
        print(f"\nintraday excursions from {os.path.basename(a.tv_export)} "
              f"({len(seq)} trades, ${mult:g}/pt source)")
        task3(seq)
    else:
        print("\nTASK 3 skipped — pass --tv-export EXPORT.csv for intraday excursions")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
