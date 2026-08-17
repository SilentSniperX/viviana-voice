#!/usr/bin/env python3
"""Is the ORB edge decaying? Diagnosis only — nothing here changes the strategy.

Two data regimes, and the difference matters for what can be claimed:

  TRADE LIST  2016-01-04 .. 2026-06-30, 2,295 trades. Supports frequency,
              stop rate, expectancy, profit factor, long/short, rolling windows.
  1-MIN BARS  2023-01-01 .. 2026-06-30 only. Path-dependent metrics — MFE/MAE
              ordering, re-entry into the opening range, favourable-then-stopped
              — exist ONLY here. They cannot be computed for 2016-2022, so the
              2016-2020 era has no path metrics and none are invented for it.

One definitional identity does a lot of work: the ORB stop IS the opposite
opening-range extreme (verified 2295/2295). A long entered above ORH stops at
ORL. So a stop-out is, by construction, a COMPLETE traversal back through the
opening range. "Reversal through the OR" and "stop-out" are the same event, and
that event IS measurable over the full history.

The weaker sense — price merely trading back INSIDE the range without reaching
the far side — needs bars, so it is reported for 2023+ only.

Run:  python3 research/decay/decay_analysis.py
"""

from __future__ import annotations

import csv
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
BARS = "/tmp/bars"
START, END = "2016-01-04", "2026-06-30"

ERAS = [("2016-2020", "2016-01-01", "2020-12-31"),
        ("2021-2023", "2021-01-01", "2023-12-31"),
        ("2024-2026", "2024-01-01", "2026-12-31")]

random.seed(20260817)          # deterministic; this is analysis, not a strategy


# ---------------------------------------------------------------------------
# Session denominator — the NYSE/CME equity-index holiday calendar
# ---------------------------------------------------------------------------

def easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    return date(y, mo, ((h + l - 7 * m + 114) % 31) + 1)


def nth_weekday(y: int, mo: int, weekday: int, n: int) -> date:
    d = date(y, mo, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(y: int, mo: int, weekday: int) -> date:
    d = date(y, mo + 1, 1) - timedelta(days=1) if mo < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(y: int) -> set[date]:
    h = {observed(date(y, 1, 1)), nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3),
         easter(y) - timedelta(days=2), last_weekday(y, 5, 0),
         observed(date(y, 7, 4)), nth_weekday(y, 9, 0, 1),
         nth_weekday(y, 11, 3, 4), observed(date(y, 12, 25))}
    if y >= 2022:
        h.add(observed(date(y, 6, 19)))
    return h


def sessions_between(a: str, b: str) -> list[str]:
    d0 = datetime.strptime(a, "%Y-%m-%d").date()
    d1 = datetime.strptime(b, "%Y-%m-%d").date()
    hol = set()
    for y in range(d0.year, d1.year + 1):
        hol |= holidays(y)
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5 and d not in hol:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Statistics, stdlib only
# ---------------------------------------------------------------------------

def norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def two_prop_z(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    """Two-sided z-test for a difference in proportions."""
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan")
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    return z, 2 * (1 - norm_cdf(abs(z)))


def permutation_mean_diff(a: list[float], b: list[float], iters: int = 20000) -> float:
    """Two-sided p for mean(a) - mean(b) under the null of exchangeability."""
    obs = abs(statistics.fmean(a) - statistics.fmean(b))
    pool = a + b
    na = len(a)
    hits = 0
    for _ in range(iters):
        random.shuffle(pool)
        if abs(statistics.fmean(pool[:na]) - statistics.fmean(pool[na:])) >= obs:
            hits += 1
    return (hits + 1) / (iters + 1)


def bootstrap_ci(vals: list[float], iters: int = 10000, lo=2.5, hi=97.5):
    n = len(vals)
    means = sorted(statistics.fmean(random.choices(vals, k=n)) for _ in range(iters))
    return means[int(lo / 100 * iters)], means[int(hi / 100 * iters)]


def pf(vals: list[float]) -> float:
    w = sum(v for v in vals if v > 0)
    l = -sum(v for v in vals if v < 0)
    return w / l if l > 0 else float("inf")


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def load_bars(dates_needed: set[str]) -> dict[str, list[tuple]]:
    """RTH 1-minute bars per session date, for the dates we can cover."""
    out: dict[str, list[tuple]] = defaultdict(list)
    if not os.path.isdir(BARS):
        return out
    for fn in sorted(os.listdir(BARS)):
        if not fn.startswith("NQ_adj_1m_"):
            continue
        with open(os.path.join(BARS, fn)) as fh:
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) < 6:
                    continue
                ts = p[0]
                d = ts[:10]
                if d not in dates_needed:
                    continue
                hm = ts[11:16]
                if not ("09:30" <= hm <= "16:00"):
                    continue
                out[d].append((ts, float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    for d in out:
        out[d].sort()
    return out


def opening_range(bars: list[tuple]) -> tuple[float, float] | None:
    """Rebuild the 09:30-09:44:59 range from the bars.

    The canonical export persisted only ONE side per trade — the stop side —
    so `or_high` is blank on longs and `or_low` is blank on shorts. The near
    boundary is exactly what "traded back inside the range" needs, so it is
    reconstructed here and cross-checked against the side that WAS recorded.
    """
    win = [b for b in bars if "09:30" <= b[0][11:16] <= "09:44"]
    if len(win) < 15:
        return None
    return max(b[2] for b in win), min(b[3] for b in win)


def path_metrics(t: dict, bars: list[tuple]) -> dict | None:
    """MFE/MAE ordering and OR re-entry, from entry bar to exit bar inclusive."""
    e_ts, x_ts = t["entry_ts"], t["exit_ts"]
    seg = [b for b in bars if e_ts <= b[0] <= x_ts]
    if len(seg) < 2:
        return None
    rng = opening_range(bars)
    if rng is None:
        return None
    orh, orl = rng
    entry, stop = float(t["entry"]), float(t["stop"])
    # The reconstruction must reproduce the side the export DID record, or the
    # near boundary cannot be trusted either.
    recorded = t["or_low"] if t["direction"] == "LONG" else t["or_high"]
    if recorded:
        want = orl if t["direction"] == "LONG" else orh
        if abs(float(recorded) - want) > 0.26:
            return {"or_mismatch": True}
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    long = t["direction"] == "LONG"

    mfe = mae = 0.0
    t_mfe = t_mae = None
    reentry_i = None
    first_1R = None          # index at which +1R favourable was first reached
    for i, (ts, o, h, l, c) in enumerate(seg):
        fav = (h - entry) if long else (entry - l)
        adv = (entry - l) if long else (h - entry)
        if fav > mfe:
            mfe, t_mfe = fav, i
        if adv > mae:
            mae, t_mae = adv, i
        if first_1R is None and fav >= risk:
            first_1R = i
        # traded back INSIDE the opening range
        if reentry_i is None:
            inside = (l <= orh) if long else (h >= orl)
            if inside:
                reentry_i = i
    return {"or_mismatch": False, "mfe_R": mfe / risk, "mae_R": mae / risk,
            "mfe_before_mae": (t_mfe is not None and
                               (t_mae is None or t_mfe < t_mae)),
            "reached_1R": first_1R is not None,
            "reentered_or": reentry_i is not None,
            "bars": len(seg)}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def block(name: str, rows: list[dict]) -> dict:
    if not rows:
        return {"era": name, "n": 0}
    R = [float(r["netR"]) for r in rows]
    P = [float(r["net_points"]) for r in rows]
    stops = sum(1 for r in rows if r["exit_reason"] == "stop")
    L = [r for r in rows if r["direction"] == "LONG"]
    S = [r for r in rows if r["direction"] == "SHORT"]
    return {"era": name, "n": len(rows), "stops": stops,
            "stop_rate": stops / len(rows),
            "exp_R": statistics.fmean(R), "exp_pts": statistics.fmean(P),
            "pf": pf(P), "win": sum(1 for p in P if p > 0) / len(rows),
            "nL": len(L), "nS": len(S),
            "expL": statistics.fmean([float(r["netR"]) for r in L]) if L else float("nan"),
            "expS": statistics.fmean([float(r["netR"]) for r in S]) if S else float("nan"),
            "pfL": pf([float(r["net_points"]) for r in L]) if L else float("nan"),
            "pfS": pf([float(r["net_points"]) for r in S]) if S else float("nan"),
            "stopL": sum(1 for r in L if r["exit_reason"] == "stop") / len(L) if L else float("nan"),
            "stopS": sum(1 for r in S if r["exit_reason"] == "stop") / len(S) if S else float("nan"),
            "R": R, "P": P}


def main() -> int:
    with open(CANONICAL, encoding="utf-8-sig") as fh:
        trades = [r for r in csv.DictReader(fh) if START <= r["date"] <= END]

    print("=" * 78)
    print("ORB EDGE DECAY DIAGNOSIS — no strategy change, diagnosis only")
    print("=" * 78)
    print(f"trades {len(trades)}   {trades[0]['date']} .. {trades[-1]['date']}")

    # --- identity check that collapses one of the requested metrics ---------
    ok = sum(1 for t in trades
             if abs(float(t["stop"]) - float(t["or_low" if t["direction"] == "LONG"
                                               else "or_high"])) < 1e-9)
    print(f"\nstop == opposite OR extreme: {ok}/{len(trades)}")
    print("  => a STOP-OUT IS a complete reversal back through the opening range.")
    print("     The two requested metrics are the same measurement.")

    # --- qualified breakout frequency --------------------------------------
    print("\n" + "-" * 78)
    print("QUALIFIED BREAKOUT FREQUENCY  (trades / RTH sessions)")
    print("-" * 78)
    sess = sessions_between(START, END)
    by_year_sess = defaultdict(int)
    for d in sess:
        by_year_sess[d[:4]] += 1
    by_year_tr = defaultdict(int)
    for t in trades:
        by_year_tr[t["date"][:4]] += 1
    print(f"{'year':6} {'sessions':>9} {'trades':>7} {'freq':>8}")
    for y in sorted(by_year_sess):
        s, n = by_year_sess[y], by_year_tr[y]
        print(f"{y:6} {s:9d} {n:7d} {100*n/s:7.1f}%")
    print("  calendar note: computed NYSE/CME holiday schedule; 2026 is part-year.")

    # --- yearly ------------------------------------------------------------
    print("\n" + "-" * 78)
    print("BY YEAR")
    print("-" * 78)
    print(f"{'year':6} {'n':>5} {'stop%':>7} {'exp R':>8} {'exp pts':>9} {'PF':>6} "
          f"{'win%':>6} {'L exp R':>8} {'S exp R':>8}")
    for y in sorted({t["date"][:4] for t in trades}):
        b = block(y, [t for t in trades if t["date"][:4] == y])
        print(f"{y:6} {b['n']:5d} {100*b['stop_rate']:6.1f}% {b['exp_R']:8.3f} "
              f"{b['exp_pts']:9.2f} {b['pf']:6.2f} {100*b['win']:5.1f}% "
              f"{b['expL']:8.3f} {b['expS']:8.3f}")

    # --- eras --------------------------------------------------------------
    print("\n" + "-" * 78)
    print("BY ERA")
    print("-" * 78)
    blocks = []
    for name, a, b_ in ERAS:
        rows = [t for t in trades if a <= t["date"] <= b_]
        blocks.append(block(name, rows))
    hdr = f"{'metric':34}" + "".join(f"{b['era']:>14}" for b in blocks)
    print(hdr)
    def line(label, fmt, key):
        print(f"{label:34}" + "".join(f"{fmt.format(b[key]):>14}" for b in blocks))
    line("trades", "{:d}", "n")
    line("stop-out / full OR reversal", "{:.1%}", "stop_rate")
    line("expectancy (R)", "{:+.3f}", "exp_R")
    line("expectancy (points)", "{:+.2f}", "exp_pts")
    line("profit factor", "{:.2f}", "pf")
    line("win rate", "{:.1%}", "win")
    print()
    line("LONG  n", "{:d}", "nL")
    line("LONG  expectancy (R)", "{:+.3f}", "expL")
    line("LONG  profit factor", "{:.2f}", "pfL")
    line("LONG  stop rate", "{:.1%}", "stopL")
    print()
    line("SHORT n", "{:d}", "nS")
    line("SHORT expectancy (R)", "{:+.3f}", "expS")
    line("SHORT profit factor", "{:.2f}", "pfS")
    line("SHORT stop rate", "{:.1%}", "stopS")

    # --- significance ------------------------------------------------------
    print("\n" + "-" * 78)
    print("IS THE DIFFERENCE REAL?  (2024-2026 vs each earlier era)")
    print("-" * 78)
    late = blocks[2]
    for early in blocks[:2]:
        z, p = two_prop_z(late["stops"], late["n"], early["stops"], early["n"])
        pp = permutation_mean_diff(list(late["R"]), list(early["R"]))
        d = late["exp_R"] - early["exp_R"]
        print(f"\n  {late['era']} vs {early['era']}")
        print(f"    stop rate      {late['stop_rate']:.1%} vs {early['stop_rate']:.1%}"
              f"   diff {100*(late['stop_rate']-early['stop_rate']):+.1f}pp"
              f"   z={z:+.2f}  p={p:.4f}")
        print(f"    expectancy R   {late['exp_R']:+.3f} vs {early['exp_R']:+.3f}"
              f"   diff {d:+.3f}R"
              f"   permutation p={pp:.4f}")
    for b in blocks:
        lo, hi = bootstrap_ci(list(b["R"]))
        print(f"\n  {b['era']} expectancy R = {b['exp_R']:+.3f}  "
              f"95% CI [{lo:+.3f}, {hi:+.3f}]  n={b['n']}")

    # --- rolling 250 -------------------------------------------------------
    print("\n" + "-" * 78)
    print("ROLLING 250 TRADES")
    print("-" * 78)
    W = 250
    roll = []
    for i in range(W, len(trades) + 1):
        w = trades[i - W:i]
        Rw = [float(t["netR"]) for t in w]
        Pw = [float(t["net_points"]) for t in w]
        st = sum(1 for t in w if t["exit_reason"] == "stop") / W
        roll.append((w[-1]["date"], st, statistics.fmean(Rw), pf(Pw)))
    print(f"  windows: {len(roll)}   (each ends on the date shown)")
    print(f"\n{'window end':12} {'stop%':>7} {'exp R':>8} {'PF':>6}")
    step = max(1, len(roll) // 24)
    for i in range(0, len(roll), step):
        d, st, ex, p_ = roll[i]
        print(f"{d:12} {100*st:6.1f}% {ex:8.3f} {p_:6.2f}")
    d, st, ex, p_ = roll[-1]
    print(f"{d:12} {100*st:6.1f}% {ex:8.3f} {p_:6.2f}   <- most recent")
    sr = [r[1] for r in roll]
    er = [r[2] for r in roll]
    print(f"\n  stop rate   min {100*min(sr):.1f}%  max {100*max(sr):.1f}%  "
          f"last {100*sr[-1]:.1f}%   percentile of last: "
          f"{100*sum(1 for v in sr if v <= sr[-1])/len(sr):.0f}")
    print(f"  expectancy  min {min(er):+.3f}R max {max(er):+.3f}R "
          f"last {er[-1]:+.3f}R   percentile of last: "
          f"{100*sum(1 for v in er if v <= er[-1])/len(er):.0f}")

    # --- path metrics, 2023+ ----------------------------------------------
    print("\n" + "-" * 78)
    print("PATH METRICS  (1-minute bars — 2023-01-01 onward ONLY)")
    print("-" * 78)
    need = {t["date"] for t in trades if t["date"] >= "2023-01-01"}
    bars = load_bars(need)
    print(f"  sessions with bars: {len(bars)} / {len(need)} trades in range")
    if not bars:
        print("  NO BAR DATA — path metrics unavailable.")
        return 1

    groups = [("2023", "2023-01-01", "2023-12-31"),
              ("2024", "2024-01-01", "2024-12-31"),
              ("2025", "2025-01-01", "2025-12-31"),
              ("2026H1", "2026-01-01", "2026-12-31"),
              ("2024-2026", "2024-01-01", "2026-12-31")]
    res: dict[str, list[dict]] = defaultdict(list)
    nomatch = nobar = 0
    cover: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in trades:
        if t["date"] < "2023-01-01":
            continue
        for g, a, b_ in groups:
            if a <= t["date"] <= b_:
                cover[g][1] += 1
        m = path_metrics(t, bars.get(t["date"], []))
        if m is None:
            nobar += 1
            continue
        if m.get("or_mismatch"):
            nomatch += 1
            continue
        m["stopped"] = t["exit_reason"] == "stop"
        for g, a, b_ in groups:
            if a <= t["date"] <= b_:
                res[g].append(m)
                cover[g][0] += 1
    print(f"  trades with no usable bars: {nobar}")
    print(f"  opening range reconstructed but disagreeing with the recorded "
          f"side: {nomatch}  (excluded)")
    print("  coverage per group (analysed / trades in range):")
    for g, _, _ in groups:
        c = cover[g]
        if c[1]:
            print(f"    {g:10} {c[0]:4d} / {c[1]:4d}  = {100*c[0]/c[1]:.0f}%")

    print(f"\n{'group':10} {'n':>5} {'re-enter OR':>12} {'MFE<MAE order':>14} "
          f"{'med MFE R':>10} {'med MAE R':>10} {'+1R then stop':>14}")
    for g, _, _ in groups:
        v = res[g]
        if not v:
            continue
        n = len(v)
        re_ = sum(1 for x in v if x["reentered_or"]) / n
        fb = sum(1 for x in v if x["mfe_before_mae"]) / n
        mfe = statistics.median(x["mfe_R"] for x in v)
        mae = statistics.median(x["mae_R"] for x in v)
        got1 = [x for x in v if x["reached_1R"]]
        rev = (sum(1 for x in got1 if x["stopped"]) / len(got1)) if got1 else float("nan")
        print(f"{g:10} {n:5d} {100*re_:11.1f}% {100*fb:13.1f}% "
              f"{mfe:10.2f} {mae:10.2f} {100*rev:13.1f}%")
    print("\n  re-enter OR   = price traded back inside the opening range after entry")
    print("  MFE<MAE order = the best favourable excursion happened BEFORE the worst")
    print("                  adverse one (a trade that 'worked first')")
    print("  +1R then stop = of trades that reached +1R favourable, the share that")
    print("                  still ended as a stop-out — the reversal you asked about")

    a23 = res["2023"]
    a246 = res["2024-2026"]
    if a23 and a246:
        print("\n  2024-2026 vs 2023 (the only pre-2024 era with bars):")
        for label, fn in (("re-entry into OR", lambda x: x["reentered_or"]),
                          ("MFE before MAE", lambda x: x["mfe_before_mae"])):
            x1 = sum(1 for x in a246 if fn(x)); n1 = len(a246)
            x2 = sum(1 for x in a23 if fn(x)); n2 = len(a23)
            z, p = two_prop_z(x1, n1, x2, n2)
            print(f"    {label:20} {x1/n1:.1%} vs {x2/n2:.1%}   z={z:+.2f} p={p:.4f}")
        g1 = [x for x in a246 if x["reached_1R"]]
        g2 = [x for x in a23 if x["reached_1R"]]
        if g1 and g2:
            x1 = sum(1 for x in g1 if x["stopped"])
            x2 = sum(1 for x in g2 if x["stopped"])
            z, p = two_prop_z(x1, len(g1), x2, len(g2))
            print(f"    {'+1R then stopped':20} {x1/len(g1):.1%} vs {x2/len(g2):.1%}"
                  f"   z={z:+.2f} p={p:.4f}")
    # --- structural break scan --------------------------------------------
    print("\n" + "-" * 78)
    print("STRUCTURAL BREAK SCAN — no pre-chosen boundaries")
    print("-" * 78)
    print("  Era boundaries were picked by hand, so a real break could sit")
    print("  between them. This tries EVERY split point with >=250 trades on")
    print("  each side and reports the most extreme one found.")
    allR = [float(t["netR"]) for t in trades]
    best = None
    for i in range(250, len(allR) - 250):
        a, b_ = allR[:i], allR[i:]
        ma, mb = statistics.fmean(a), statistics.fmean(b_)
        va, vb = statistics.pvariance(a), statistics.pvariance(b_)
        se = math.sqrt(va / len(a) + vb / len(b_))
        if se == 0:
            continue
        tstat = (mb - ma) / se
        if best is None or abs(tstat) > abs(best[1]):
            best = (i, tstat, ma, mb)
    i, tstat, ma, mb = best
    print(f"\n  most extreme split: after trade {i} ({trades[i-1]['date']})")
    print(f"    before {ma:+.3f}R (n={i})   after {mb:+.3f}R (n={len(allR)-i})"
          f"   t={tstat:+.2f}")
    print(f"    direction: {'IMPROVEMENT' if mb > ma else 'DETERIORATION'} after "
          f"the split")
    # The scan searched ~1,800 candidate splits, so the null distribution of the
    # MAXIMUM |t| is far wider than a single t-test. Calibrate it by permutation.
    hits = 0
    ITER = 2000
    for _ in range(ITER):
        perm = allR[:]
        random.shuffle(perm)
        mx = 0.0
        for j in range(250, len(perm) - 250, 25):
            a, b_ = perm[:j], perm[j:]
            se = math.sqrt(statistics.pvariance(a) / len(a) +
                           statistics.pvariance(b_) / len(b_))
            if se:
                mx = max(mx, abs((statistics.fmean(b_) - statistics.fmean(a)) / se))
        if mx >= abs(tstat):
            hits += 1
    print(f"    p (max-|t| calibrated by {ITER} permutations) = "
          f"{(hits+1)/(ITER+1):.4f}")

    # --- the most recent window against everything before it ---------------
    print("\n" + "-" * 78)
    print("MOST RECENT 250 TRADES vs ALL PRIOR")
    print("-" * 78)
    recent, prior = trades[-250:], trades[:-250]
    rR = [float(t["netR"]) for t in recent]
    pR = [float(t["netR"]) for t in prior]
    rs = sum(1 for t in recent if t["exit_reason"] == "stop")
    ps = sum(1 for t in prior if t["exit_reason"] == "stop")
    z, pv = two_prop_z(rs, len(recent), ps, len(prior))
    pp = permutation_mean_diff(rR, pR)
    lo, hi = bootstrap_ci(rR)
    print(f"  window: {recent[0]['date']} .. {recent[-1]['date']}")
    print(f"  stop rate    {rs/len(recent):.1%} vs {ps/len(prior):.1%}"
          f"   z={z:+.2f} p={pv:.4f}")
    print(f"  expectancy   {statistics.fmean(rR):+.3f}R vs "
          f"{statistics.fmean(pR):+.3f}R   permutation p={pp:.4f}")
    print(f"  recent 95% CI [{lo:+.3f}, {hi:+.3f}]  — contains zero: "
          f"{'YES' if lo <= 0 <= hi else 'NO'}")
    print(f"  points       {statistics.fmean([float(t['net_points']) for t in recent]):+.2f}"
          f" vs {statistics.fmean([float(t['net_points']) for t in prior]):+.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
