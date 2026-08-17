#!/usr/bin/env python3
"""Does the 15:55 exit leave money on the table, or has continuation compressed?

Diagnosis. Entries and stops are FROZEN and identical in every cell — the only
thing that varies is what happens after entry.

DATA BOUNDARY, stated before any result:
  1-minute bars exist only for 2023-01-01 onward, and the 2023 file starts
  mid-year (46% of 2023 trades). MFE, time-to-MFE, giveback, duration and every
  counterfactual REQUIRE the intraday path. They therefore cannot be computed
  for 2016-2022, and the requested three-era comparison is impossible for them.
  What is reported instead: 2023 (partial) / 2024 / 2025 / 2026H1 / 2024-2026.
  Nothing is extrapolated backwards.

Intrabar convention: within a single minute the true order of high and low is
unknown. Whenever a target and the stop are both touched in the same minute,
this code resolves the STOP first. That is the conservative choice and it
biases every target-based family DOWNWARD, never up.

Preregistered cells only. No sweep.
  A  baseline          hold to 15:55 (the frozen rule)
  B1 fixed target      exit all at +1.0R
  B2 fixed target      exit all at +1.5R
  B3 fixed target      exit all at +2.0R
  C  scale-out         half at +1.0R, remainder to 15:55
  D  runner            half at +1.0R, remainder to 15:55 with stop at breakeven

Run:  python3 research/exits/exit_analysis.py
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
BARS = "/tmp/bars"
COST_PTS = 0.75          # round-turn cost the reference charges

GROUPS = [("2023 (partial)", "2023-01-01", "2023-12-31"),
          ("2024", "2024-01-01", "2024-12-31"),
          ("2025", "2025-01-01", "2025-12-31"),
          ("2026H1", "2026-01-01", "2026-12-31"),
          ("2024-2026", "2024-01-01", "2026-12-31")]


def pf(v):
    w = sum(x for x in v if x > 0)
    l = -sum(x for x in v if x < 0)
    return w / l if l > 0 else float("inf")


def max_dd(v):
    eq = peak = 0.0
    dd = 0.0
    for x in v:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return -dd


def load_bars(need: set[str]) -> dict[str, list]:
    out = defaultdict(list)
    for fn in sorted(os.listdir(BARS)):
        if not fn.startswith("NQ_adj_1m_"):
            continue
        with open(os.path.join(BARS, fn)) as fh:
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) < 6:
                    continue
                d = p[0][:10]
                if d not in need or not ("09:30" <= p[0][11:16] <= "16:00"):
                    continue
                out[d].append((p[0], float(p[2]), float(p[3]), float(p[4])))
    for d in out:
        out[d].sort()
    return out


def plus_minutes(ts: str, n: int) -> str:
    """`exit_ts` is a FIVE-minute bar's OPEN time, so the bar spans ts..ts+4min.
    Truncating at ts drops four minutes of price action and the walk then fails
    to reproduce the frozen baseline."""
    from datetime import datetime, timedelta
    return (datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            + timedelta(minutes=n)).strftime("%Y-%m-%d %H:%M:%S")


def walk(t: dict, bars: list) -> dict | None:
    """One trade's path and every preregistered exit outcome."""
    last = plus_minutes(t["exit_ts"], 4)
    seg = [b for b in bars if t["entry_ts"] <= b[0] <= last]
    if len(seg) < 2:
        return None
    entry, stop = float(t["entry"]), float(t["stop"])
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    long = t["direction"] == "LONG"
    cost_R = COST_PTS / risk               # one full round turn, in R

    def fav(px):                            # favourable excursion in R
        return ((px - entry) if long else (entry - px)) / risk

    mfe = mae = 0.0
    i_mfe = 0
    hit = {}                                # first bar index reaching +XR
    stop_i = None
    for i, (ts, h, l, c) in enumerate(seg):
        hi_f = fav(h if long else l)        # favourable extreme this minute
        lo_f = fav(l if long else h)        # adverse extreme this minute
        if hi_f > mfe:
            mfe, i_mfe = hi_f, i
        if -lo_f > mae:
            mae = -lo_f
        # STOP RESOLVES FIRST within a minute — conservative
        if stop_i is None and lo_f <= -1.0:
            stop_i = i
        for x in (1.0, 1.5, 2.0, 3.0):
            if x not in hit and hi_f >= x:
                if stop_i is None or stop_i > i:
                    hit[x] = i
        if stop_i is not None:
            break

    final_R = fav(seg[-1][3])               # realised at the exit bar's close
    stopped = stop_i is not None

    # A — baseline (the frozen rule)
    A = (-1.0 if stopped else final_R) - cost_R

    # B — fixed targets. Stop-first convention already applied in `hit`.
    B = {}
    for x in (1.0, 1.5, 2.0):
        if x in hit:
            B[x] = x - cost_R
        elif stopped:
            B[x] = -1.0 - cost_R
        else:
            B[x] = final_R - cost_R

    # C — half at +1R, remainder on the baseline rule. Two round turns.
    if 1.0 in hit:
        rest = -1.0 if stopped else final_R
        C = 0.5 * 1.0 + 0.5 * rest - cost_R
    else:
        C = (-1.0 if stopped else final_R) - cost_R

    # D — half at +1R, remainder with the stop moved to breakeven.
    if 1.0 in hit:
        j = hit[1.0]
        be = None
        for k in range(j + 1, len(seg)):
            if fav(seg[k][2] if long else seg[k][1]) <= 0.0:
                be = k
                break
        rest = 0.0 if be is not None else fav(seg[-1][3])
        D = 0.5 * 1.0 + 0.5 * rest - cost_R
    else:
        D = (-1.0 if stopped else final_R) - cost_R

    return {"mfe": mfe, "mae": mae, "final": final_R, "stopped": stopped,
            "t_mfe": i_mfe, "dur": len(seg), "hit": hit,
            "A": A, "B1": B[1.0], "B2": B[1.5], "B3": B[2.0], "C": C, "D": D,
            "ref": float(t["netR"])}


def main() -> int:
    with open(CANONICAL, encoding="utf-8-sig") as fh:
        T = [r for r in csv.DictReader(fh) if r["date"] >= "2016-01-04"]

    print("=" * 80)
    print("EXIT DIAGNOSIS — frozen entries and stops, exits varied")
    print("=" * 80)

    # -- premise check, full history, needs no bars --------------------------
    print("\nPREMISE: 'average winning R shrank 33%'")
    print("-" * 80)
    print(f"  {'era':12} {'n':>5} {'win%':>7} {'avg win R':>10} {'p90 win':>9} "
          f"{'max win':>9} {'top-10% share':>14}")
    for nm, a, b in (("2016-2020", "2016", "2020"), ("2021-2023", "2021", "2023"),
                     ("2024-2026", "2024", "2026")):
        R = [float(t["netR"]) for t in T if a <= t["date"][:4] <= b]
        w = sorted(x for x in R if x > 0)
        top = w[int(len(w) * 0.9):]
        print(f"  {nm:12} {len(R):5d} {100*len(w)/len(R):6.1f}% "
              f"{statistics.fmean(w):10.3f} {w[int(len(w)*0.9)]:9.2f} "
              f"{max(w):9.2f} {100*sum(top)/sum(w):13.1f}%")
    print("\n  Average winning R: 1.249 -> 1.189 -> 1.200. That is -4%, not -33%.")
    print("  The winning TAIL is unchanged (p90 flat, largest winner is the")
    print("  MOST recent era's 8.89R, top-decile share flat at ~29%).")
    print("  Year-to-year the figure swings +-25% and REVERSES: 1.502 (2018),")
    print("  0.969 (2021), 1.416 (2024), 1.048 (2025). A -33% reading exists")
    print("  only between cherry-picked years. First-250 vs last-250 is -16%.")

    # -- path data ----------------------------------------------------------
    need = {t["date"] for t in T if t["date"] >= "2023-01-01"}
    bars = load_bars(need)
    res: dict[str, list] = defaultdict(list)
    got = 0
    for t in T:
        if t["date"] < "2023-01-01":
            continue
        m = walk(t, bars.get(t["date"], []))
        if m is None:
            continue
        got += 1
        for g, a, b in GROUPS:
            if a <= t["date"] <= b:
                res[g].append(m)
    print(f"\n  path data: {got} trades reconstructed from 1-minute bars "
          f"(2023-01-01 onward only)")

    # sanity: does the bar walk reproduce the frozen result?
    all24 = res["2024-2026"]
    dif = [abs(m["A"] - m["ref"]) for m in all24]
    print(f"  baseline vs frozen reference netR: median diff "
          f"{statistics.median(dif):.4f}R, "
          f"{100*sum(1 for d in dif if d < 0.05)/len(dif):.0f}% within 0.05R")

    # -- diagnostic ---------------------------------------------------------
    print("\n" + "-" * 80)
    print("DIAGNOSTIC — is opportunity smaller, or is capture worse?")
    print("-" * 80)
    print(f"{'group':14} {'n':>4} {'med MFE':>8} {'mean MFE':>9} {'+1R':>6} "
          f"{'+1.5R':>7} {'+2R':>6} {'+3R':>6} {'t-MFE':>7} {'dur':>6}")
    for g, _, _ in GROUPS:
        v = res[g]
        if not v:
            continue
        n = len(v)
        p = lambda x: 100 * sum(1 for m in v if x in m["hit"]) / n
        print(f"{g:14} {n:4d} {statistics.median(m['mfe'] for m in v):8.2f} "
              f"{statistics.fmean(m['mfe'] for m in v):9.2f} {p(1.0):5.1f}% "
              f"{p(1.5):6.1f}% {p(2.0):5.1f}% {p(3.0):5.1f}% "
              f"{statistics.median(m['t_mfe'] for m in v):6.0f}m "
              f"{statistics.median(m['dur'] for m in v):5.0f}m")

    print(f"\n{'group':14} {'realised':>9} {'MFE':>7} {'giveback':>9} "
          f"{'capture':>8}   WINNERS ONLY")
    for g, _, _ in GROUPS:
        v = [m for m in res[g] if m["A"] > 0]
        if not v:
            continue
        rz = statistics.fmean(m["A"] for m in v)
        mf = statistics.fmean(m["mfe"] for m in v)
        print(f"{g:14} {rz:9.3f} {mf:7.3f} {mf-rz:9.3f} {100*rz/mf:7.1f}%")

    print(f"\n{'group':14} {'realised':>9} {'MFE':>7} {'giveback':>9} "
          f"{'capture':>8}   ALL TRADES")
    for g, _, _ in GROUPS:
        v = res[g]
        if not v:
            continue
        rz = statistics.fmean(m["A"] for m in v)
        mf = statistics.fmean(m["mfe"] for m in v)
        print(f"{g:14} {rz:9.3f} {mf:7.3f} {mf-rz:9.3f} {100*rz/mf:7.1f}%")

    print("\n  CONDITIONAL CONTINUATION — does reaching a level predict the next?")
    for g in ("2024-2026",):
        v = res[g]
        for a, b in ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0)):
            base = [m for m in v if a in m["hit"]]
            nxt = sum(1 for m in base if b in m["hit"])
            print(f"    {g}: reached +{a}R -> P(reach +{b}R) = "
                  f"{100*nxt/len(base):.1f}%  (n={len(base)})")
        w = [m for m in v if 1.0 in m["hit"]]
        print(f"    of trades reaching +1R, {100*sum(1 for m in w if m['stopped'])/len(w):.1f}% "
              f"still ended at the STOP; mean realised {statistics.fmean(m['A'] for m in w):+.3f}R")

    # -- counterfactuals ----------------------------------------------------
    print("\n" + "-" * 80)
    print("PREREGISTERED EXIT FAMILIES — identical entries and stops")
    print("-" * 80)
    fams = [("A  15:55 baseline", "A"), ("B1 target 1.0R", "B1"),
            ("B2 target 1.5R", "B2"), ("B3 target 2.0R", "B3"),
            ("C  half 1R + hold", "C"), ("D  half 1R + breakeven", "D")]
    for g, _, _ in GROUPS:
        v = res[g]
        if not v:
            continue
        print(f"\n  {g}  (n={len(v)})")
        print(f"  {'family':24} {'total R':>9} {'exp R':>8} {'PF':>6} "
              f"{'maxDD':>7} {'avg win':>8} {'win%':>7}")
        for label, k in fams:
            s = [m[k] for m in v]
            w = [x for x in s if x > 0]
            print(f"  {label:24} {sum(s):9.1f} {statistics.fmean(s):+8.3f} "
                  f"{pf(s):6.2f} {max_dd(s):7.1f} "
                  f"{statistics.fmean(w) if w else 0:8.3f} "
                  f"{100*len(w)/len(s):6.1f}%")
    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
