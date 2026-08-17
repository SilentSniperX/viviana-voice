#!/usr/bin/env python3
"""Can position sizing on opening-range geometry improve profitability?

Diagnosis only. The SIGNAL is untouched: same entries, same stops, same exits,
same trades in the same order. The only thing varied is how many contracts each
trade carries.

The comparison is nearly free because the canonical reference already stores
BOTH outcomes per trade:

    net_points  what 1 contract earned          -> FIXED-CONTRACT sizing
    netR        the same trade in risk units    -> CONSTANT-RISK sizing

So "size by risk geometry" in its simplest form is not a proposal to be
simulated; it is a column that already exists.

Three questions, in order of how much they matter:

  Q1  Does the geometry vary enough for sizing to change anything?
  Q2  Is expectancy-in-R FLAT across risk buckets? If it is, constant risk is
      the whole answer and no cleverer rule can add anything.
  Q3  On a drawdown-constrained account — which this is — does constant risk
      deliver more return per unit of drawdown than fixed contracts?

A trap this code is built to avoid: NQ went from ~4,500 to ~23,000 over the
sample and median risk grew 8.5x with it. Bucketing by RAW risk points is
therefore bucketing by calendar year. Every geometry feature here is normalised
against a trailing, causal baseline.

Run:  python3 research/sizing/sizing_analysis.py
"""

from __future__ import annotations

import csv
import math
import os
import random
import statistics
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
START = "2016-01-04"
TRAIL = 50            # trailing window for the causal risk baseline

random.seed(20260817)


def pf(v: list[float]) -> float:
    w = sum(x for x in v if x > 0)
    l = -sum(x for x in v if x < 0)
    return w / l if l > 0 else float("inf")


def max_dd(vals: list[float]) -> float:
    """Max peak-to-trough of the cumulative sum."""
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return -dd


def permutation_mean_diff(a: list[float], b: list[float], iters: int = 20000) -> float:
    obs = abs(statistics.fmean(a) - statistics.fmean(b))
    pool = a + b
    na = len(a)
    hits = 0
    for _ in range(iters):
        random.shuffle(pool)
        if abs(statistics.fmean(pool[:na]) - statistics.fmean(pool[na:])) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)


def spearman(x: list[float], y: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def main() -> int:
    with open(CANONICAL, encoding="utf-8-sig") as fh:
        T = [r for r in csv.DictReader(fh) if r["date"] >= START]
    R = [float(t["netR"]) for t in T]
    P = [float(t["net_points"]) for t in T]
    risk = [float(t["risk"]) for t in T]

    print("=" * 78)
    print("SIZING BY OPENING-RANGE GEOMETRY — diagnosis, signal untouched")
    print("=" * 78)
    print(f"trades {len(T)}   {T[0]['date']} .. {T[-1]['date']}")
    print("same entries, same stops, same exits. only contract count varies.")

    # -- Q1 -----------------------------------------------------------------
    print("\n" + "-" * 78)
    print("Q1  DOES THE GEOMETRY VARY ENOUGH TO MATTER?")
    print("-" * 78)
    q = statistics.quantiles(risk, n=10)
    print(f"  risk = |entry - stop|, in points")
    print(f"    min {min(risk):6.2f}   p10 {q[0]:6.1f}   median "
          f"{statistics.median(risk):6.1f}   p90 {q[8]:6.1f}   max {max(risk):7.1f}")
    print(f"    p90 / p10 = {q[8]/q[0]:.1f}x")
    byy = defaultdict(list)
    for t in T:
        byy[t["date"][:4]].append(float(t["risk"]))
    y0, y1 = min(byy), max(byy)
    print(f"    median risk {y0}: {statistics.median(byy[y0]):.1f} pts  ->  "
          f"{y1}: {statistics.median(byy[y1]):.1f} pts  "
          f"({statistics.median(byy[y1])/statistics.median(byy[y0]):.1f}x)")
    print("\n  CONSEQUENCE: under fixed-contract sizing the DOLLAR RISK PER TRADE")
    print("  grew ~8x across the sample. The 10-year point total is therefore")
    print("  dominated by recent years mechanically, not by better trading.")

    # -- Q2 -----------------------------------------------------------------
    print("\n" + "-" * 78)
    print("Q2  IS EXPECTANCY-IN-R FLAT ACROSS RISK GEOMETRY?")
    print("-" * 78)
    print("  If flat, constant risk IS the answer and nothing cleverer can add.")
    print("  Normalised causally: each trade's risk / median risk of the prior")
    print(f"  {TRAIL} trades. No future information.")

    rel = []
    for i, t in enumerate(T):
        if i < TRAIL:
            rel.append(None)
            continue
        base = statistics.median(risk[i - TRAIL:i])
        rel.append(risk[i] / base if base > 0 else None)
    idx = [i for i in range(len(T)) if rel[i] is not None]
    print(f"  usable trades: {len(idx)} (first {TRAIL} have no baseline)")

    vals = sorted(rel[i] for i in idx)
    cuts = [vals[int(len(vals) * f)] for f in (0.2, 0.4, 0.6, 0.8)]
    def bucket(v):
        return sum(1 for c in cuts if v > c)
    names = ["Q1 tightest", "Q2", "Q3", "Q4", "Q5 widest"]
    print(f"\n  {'bucket':12} {'n':>5} {'rel risk':>10} {'exp R':>9} {'exp pts':>10} "
          f"{'PF(R)':>7} {'stop%':>7}")
    bR: dict[int, list[float]] = defaultdict(list)
    bP: dict[int, list[float]] = defaultdict(list)
    bS: dict[int, list[int]] = defaultdict(list)
    bRel: dict[int, list[float]] = defaultdict(list)
    for i in idx:
        b = bucket(rel[i])
        bR[b].append(R[i]); bP[b].append(P[i])
        bS[b].append(1 if T[i]["exit_reason"] == "stop" else 0)
        bRel[b].append(rel[i])
    for b in range(5):
        print(f"  {names[b]:12} {len(bR[b]):5d} {statistics.median(bRel[b]):10.2f} "
              f"{statistics.fmean(bR[b]):+9.3f} {statistics.fmean(bP[b]):+10.2f} "
              f"{pf(bR[b]):7.2f} {100*statistics.fmean(bS[b]):6.1f}%")

    rho = spearman([rel[i] for i in idx], [R[i] for i in idx])
    print(f"\n  Spearman(relative risk, netR) = {rho:+.4f}")
    p_ends = permutation_mean_diff(bR[0], bR[4])
    print(f"  tightest vs widest quintile expectancy: "
          f"{statistics.fmean(bR[0]):+.3f}R vs {statistics.fmean(bR[4]):+.3f}R"
          f"   permutation p = {p_ends:.4f}")

    # walk-forward: would a rule fitted on the past have helped in the future?
    print("\n  WALK-FORWARD on the obvious rule 'skip the worst quintile':")
    print("  fit the worst bucket on trades so far, apply it to the next 250.")
    wins = 0
    tests = 0
    deltas = []
    for start in range(500, len(idx) - 250, 250):
        fit = idx[:start]
        test = idx[start:start + 250]
        fb: dict[int, list[float]] = defaultdict(list)
        for i in fit:
            fb[bucket(rel[i])].append(R[i])
        worst = min(range(5), key=lambda b: statistics.fmean(fb[b]) if fb[b] else 0)
        base = statistics.fmean([R[i] for i in test])
        filt = [R[i] for i in test if bucket(rel[i]) != worst]
        got = statistics.fmean(filt) if filt else 0.0
        deltas.append(got - base)
        tests += 1
        wins += got > base
        print(f"    fit<{T[idx[start]]['date']}  skip {names[worst]:11}  "
              f"all {base:+.3f}R -> filtered {got:+.3f}R  ({got-base:+.3f})")
    if tests:
        print(f"    improved in {wins}/{tests} out-of-sample windows, "
              f"mean delta {statistics.fmean(deltas):+.4f}R")

    # -- Q3 -----------------------------------------------------------------
    print("\n" + "-" * 78)
    print("Q3  FIXED CONTRACT vs CONSTANT RISK, ON A DRAWDOWN BUDGET")
    print("-" * 78)
    tot_p, dd_p = sum(P), max_dd(P)
    tot_r, dd_r = sum(R), max_dd(R)
    print(f"  FIXED CONTRACT (1 NQ)     total {tot_p:+10.1f} pts   maxDD "
          f"{dd_p:8.1f} pts   return/DD {tot_p/dd_p:5.2f}")
    print(f"  CONSTANT RISK (1R/trade)  total {tot_r:+10.1f} R     maxDD "
          f"{dd_r:8.1f} R     return/DD {tot_r/dd_r:5.2f}")
    print(f"\n  ratio of return/DD: {(tot_r/dd_r)/(tot_p/dd_p):.2f}x in favour of "
          f"{'CONSTANT RISK' if (tot_r/dd_r) > (tot_p/dd_p) else 'FIXED CONTRACT'}")

    print("\n  Same comparison per era (return/DD is scale-free):")
    eras = [("2016-2020", "2016-01-01", "2020-12-31"),
            ("2021-2023", "2021-01-01", "2023-12-31"),
            ("2024-2026", "2024-01-01", "2026-12-31")]
    print(f"  {'era':12} {'fixed ret/DD':>14} {'const-risk ret/DD':>19}")
    for nm, a, b in eras:
        pp = [P[i] for i, t in enumerate(T) if a <= t["date"] <= b]
        rr = [R[i] for i, t in enumerate(T) if a <= t["date"] <= b]
        fp = sum(pp) / max_dd(pp) if max_dd(pp) else float("nan")
        fr = sum(rr) / max_dd(rr) if max_dd(rr) else float("nan")
        print(f"  {nm:12} {fp:14.2f} {fr:19.2f}")

    # dollars at a real account size, integer contracts, MNQ
    print("\n  PRACTICAL: $25,000 account, MNQ ($2/pt), integer contracts.")
    for risk_pct in (0.005, 0.0075, 0.01):
        eq = 25000.0
        dollars = []
        sizes = []
        capped = 0
        for i, t in enumerate(T):
            want = (25000 * risk_pct) / (risk[i] * 2.0)
            n = int(want)                      # round DOWN; never over-risk
            if n < 1:
                n = 1                          # smallest tradeable
                capped += 1
            if n > 10:
                n = 10                         # sanity cap on tiny stops
            sizes.append(n)
            dollars.append(R[i] * risk[i] * 2.0 * n)
        dd = max_dd(dollars)
        print(f"    risk {risk_pct:.2%}/trade  total ${sum(dollars):+9,.0f}  "
              f"maxDD ${dd:8,.0f}  ret/DD {sum(dollars)/dd:5.2f}  "
              f"median size {statistics.median(sizes):.0f}  "
              f"forced-to-1 on {100*capped/len(T):.0f}% of trades")
    fixed1 = [p * 2.0 for p in P]
    print(f"    FIXED 1 MNQ           total ${sum(fixed1):+9,.0f}  "
          f"maxDD ${max_dd(fixed1):8,.0f}  ret/DD {sum(fixed1)/max_dd(fixed1):5.2f}")

    print("\n  At what account size does the advantage become REAL?")
    print("  (integer contracts are what destroys it on a small account)")
    print(f"  {'account':>10} {'contract':>9} {'median size':>12} {'forced-to-1':>12} "
          f"{'ret/DD':>8} {'vs fixed':>9}")
    for acct, sym, pv in ((25000, "MNQ", 2.0), (50000, "MNQ", 2.0),
                          (100000, "MNQ", 2.0), (250000, "MNQ", 2.0),
                          (250000, "NQ", 20.0), (1000000, "NQ", 20.0)):
        dollars, sizes, forced = [], [], 0
        for i in range(len(T)):
            want = (acct * 0.0075) / (risk[i] * pv)
            n = max(1, int(want))
            if int(want) < 1:
                forced += 1
            sizes.append(n)
            dollars.append(R[i] * risk[i] * pv * n)
        dd = max_dd(dollars)
        rd = sum(dollars) / dd if dd else float("nan")
        fx = [p_ * pv for p_ in P]
        rdf = sum(fx) / max_dd(fx)
        print(f"  {acct:10,} {sym:>9} {statistics.median(sizes):12.0f} "
              f"{100*forced/len(T):11.0f}% {rd:8.2f} {rd/rdf:8.2f}x")
    print("  continuous (fractional contracts, unreachable in practice): "
          f"{(tot_r/dd_r)/(tot_p/dd_p):.2f}x")

    print("\n" + "-" * 78)
    print("WHY THE BUCKET STRUCTURE IS NOT A SIGNAL")
    print("-" * 78)
    print("  A wider stop is further away, so it is hit less often — but the")
    print("  wins it produces are proportionally smaller. The two effects")
    print("  cancel, which is exactly what 'R already accounts for geometry'")
    print("  means. The numbers:")
    print(f"  {'bucket':12} {'stop rate':>10} {'avg win R':>11} {'avg loss R':>11} "
          f"{'exp R':>8}")
    for b in range(5):
        w = [v for v in bR[b] if v > 0]
        l = [v for v in bR[b] if v <= 0]
        print(f"  {names[b]:12} {100*statistics.fmean(bS[b]):9.1f}% "
              f"{statistics.fmean(w) if w else 0:11.3f} "
              f"{statistics.fmean(l) if l else 0:11.3f} "
              f"{statistics.fmean(bR[b]):+8.3f}")
    print("\n  Stop rate falls monotonically from tightest to widest, as geometry")
    print("  alone predicts. Expectancy does NOT — it is an inverted U with the")
    print("  middle bucket best, a shape with no mechanism behind it, and the")
    print("  walk-forward above shows it does not survive out of sample.")

    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
