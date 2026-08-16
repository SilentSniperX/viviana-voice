#!/usr/bin/env python3
"""Frozen-spec reference engine — ORB (section B) and S5b (section C).

This is a dependency-free re-implementation of the two frozen research engines,
written so that every branch is traceable to a named clause of
`spec/STRATEGY_SPEC_FROZEN.md` and to the shipped research source:

  ORB : spec section B
        research/.../attachments/backtest_orb_collective_management.py
        (the benchmark ORB part: lines 22-34 and the hard-stop/hold-to-close
        loop; the *collective management* clauses of that script are DEAD per
        spec section G and are NOT implemented here)
  S5b : spec section C
        research/.../attachments/tmp_s5b_round3c.py  (s5b_day, Round-3c engine)

It exists so that the Pine v6 implementation has a runnable, testable twin: the
same bar series fed to both must produce the same trade list and the same S5b
state timestamps. It performs no optimisation and exposes no tunable strategy
parameter.

Bar convention
--------------
A bar is (ts, open, high, low, close, volume) where `ts` is the bar's OPEN time
in America/New_York, naive. 5m bars are labelled left/closed left, exactly as
`f5()` in the research engines.

Usage
-----
    python3 tests/reference_engine.py --minute NQ_1m.csv --out trades.csv
    python3 tests/reference_engine.py --five-min NQ_5m.csv --out trades.csv --s5b s5b.csv

Input CSV is headerless or headed `datetime,open,high,low,close,volume`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import namedtuple
from datetime import datetime, time, timedelta

Bar = namedtuple("Bar", "ts open high low close volume")

# --- frozen constants (spec B "Verified reference", research engines) --------
COST_POINTS = 0.75          # round-turn cost, backtest_orb_*.py line 3
POINT_VALUE = 20.0          # $/point
BODY_FILTER = 0.50          # spec B: body/range >= 0.50
OR_START = time(9, 30)      # spec B: opening range 09:30:00 ..
OR_END = time(9, 45)        # .. 09:44:59 (exclusive upper bound)
QUAL_START = time(9, 45)    # spec B: qualification window 09:45 ..
QUAL_END = time(10, 30)     # .. 10:30 inclusive (bar open time)
RTH_END = time(16, 0)       # RTH close; last 5m bar opens 15:55
OB_END = time(10, 0)        # spec C: S5b opening balance 09:30-09:59
S5B_LATCH_FROM = time(10, 0)      # spec C: latch scan starts at the 10:00 bar
S5B_ENTRY_DEADLINE = time(11, 30)  # spec C: no standalone entries after 11:30
S5B_BAND_LO = 0.25          # spec C: valid pullback 25%..
S5B_BAND_HI = 0.75          # ..75%
S5B_INVALIDATE = 1.00       # spec C: retracement > 100% invalidates state
S5B_FAIL_TOL = 1.0          # spec C: 1.0 point "no meaningful progress" tolerance
S5B_VOL_MULT = 1.2          # spec C: volume >= 1.2 * rolling mean
S5B_VOL_LEN = 12            # spec C: rolling 12-bar mean volume
S5B_VOL_MIN_OBS = 6         # spec C: mean may begin with 6 observations

# S5b states, spec section D.
S5B_WAITING = "WAITING_FOR_LATCH"
S5B_LATCHED = "LATCHED"
S5B_PULLBACK = "PULLBACK_ACTIVE"
S5B_FAILURE = "COUNTERATTACK_FAILURE"
S5B_CONFIRMED = "CONFIRMED"
S5B_INVALIDATED = "INVALIDATED"


# ---------------------------------------------------------------------------
# Loading / aggregation
# ---------------------------------------------------------------------------

def read_bars(path: str) -> list[Bar]:
    """Read an OHLCV CSV. Accepts a header row or none."""
    out: list[Bar] = []
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if not row or len(row) < 6:
                continue
            try:
                ts = _parse_ts(row[0])
                out.append(Bar(ts, float(row[1]), float(row[2]), float(row[3]),
                               float(row[4]), float(row[5])))
            except ValueError:
                continue  # header line
    out.sort(key=lambda b: b.ts)
    # drop_duplicates('dt') as in the research loaders
    dedup: list[Bar] = []
    for b in out:
        if dedup and dedup[-1].ts == b.ts:
            continue
        dedup.append(b)
    return dedup


def _parse_ts(s: str) -> datetime:
    s = s.strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y%m%d %H%M%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(s)


def in_rth(b: Bar) -> bool:
    return OR_START <= b.ts.time() < RTH_END


def to_5m(minute_bars: list[Bar]) -> list[Bar]:
    """Aggregate 1m -> 5m, label='left', closed='left' (research f5())."""
    buckets: dict[datetime, list[Bar]] = {}
    for b in minute_bars:
        key = b.ts - timedelta(minutes=b.ts.minute % 5, seconds=b.ts.second,
                               microseconds=b.ts.microsecond)
        buckets.setdefault(key, []).append(b)
    out = []
    for key in sorted(buckets):
        grp = buckets[key]
        out.append(Bar(key, grp[0].open, max(x.high for x in grp),
                       min(x.low for x in grp), grp[-1].close,
                       sum(x.volume for x in grp)))
    return out


def group_by_day(bars: list[Bar]) -> dict:
    days: dict = {}
    for b in bars:
        days.setdefault(b.ts.date(), []).append(b)
    return days


def body_fraction(b: Bar) -> float | None:
    """spec B: candle body / candle full range. Zero-range bar -> undefined.

    The research engines compute this with `tr.replace(0, nan)`, so a zero-range
    bar yields NaN and every `>= 0.5` comparison against it is False.
    """
    rng = b.high - b.low
    if rng <= 0:
        return None
    return abs(b.close - b.open) / rng


# ---------------------------------------------------------------------------
# ORB — spec section B
# ---------------------------------------------------------------------------

def orb_day(day5: list[Bar], day1m: list[Bar] | None = None) -> dict | None:
    """Run the frozen ORB on one session. Returns a trade dict or None.

    `day5`  : that session's 5-minute bars (09:30..15:55).
    `day1m` : optional 1-minute bars for the same session. When supplied the
              engine applies the research engines' exact data-sufficiency guards
              (>=10 minute bars in the opening range; the entry minute must
              exist). When absent the 5m-equivalent guards are used and the
              result is tagged guard_mode="5m".
    """
    rth5 = [b for b in day5 if in_rth(b)]
    if not rth5:
        return None

    # --- opening range 09:30:00-09:44:59 -----------------------------------
    or_bars = [b for b in rth5 if OR_START <= b.ts.time() < OR_END]
    if day1m is not None:
        or_min = [b for b in day1m if OR_START <= b.ts.time() < OR_END]
        if len(or_min) < 10:                       # research guard: len(ors)<10
            return None
    elif len(or_bars) < 3:                         # 5m-equivalent guard
        return None
    orh = max(b.high for b in or_bars)
    orl = min(b.low for b in or_bars)

    # --- first qualified breakout, 09:45..10:30 inclusive -------------------
    sig_i = None
    side = 0
    for i, b in enumerate(rth5):
        if not (QUAL_START <= b.ts.time() <= QUAL_END):
            continue
        bf = body_fraction(b)
        if bf is None or bf < BODY_FILTER:
            continue
        if b.close > orh:                          # spec B: qualified LONG
            sig_i, side = i, 1
            break
        if b.close < orl:                          # spec B: qualified SHORT
            sig_i, side = i, -1
            break
    if sig_i is None:
        return None                                # no qualified breakout

    sig = rth5[sig_i]
    want = sig.ts + timedelta(minutes=5)

    # --- entry = next 5m bar open ------------------------------------------
    # Research guard: the entry bar must actually exist in the data.
    if day1m is not None and not any(b.ts == want for b in day1m):
        return None                                # research guard: gap at entry
    if sig_i + 1 >= len(rth5) or rth5[sig_i + 1].ts != want:
        return None                                # 5m-equivalent gap guard
    entry_bar_i = sig_i + 1
    # The 5m bar open equals the first 1m open of the same bucket, so this is
    # the same price the 1-minute research engine uses.
    entry_px = rth5[entry_bar_i].open

    stop = orl if side == 1 else orh               # spec B: LONG stop = ORL
    risk = abs(entry_px - stop)
    # spec B: "If next-bar entry is already beyond the stop/invalidation, SKIP."
    if risk <= 0:
        return None
    if side == 1 and entry_px <= stop:
        return None
    if side == -1 and entry_px >= stop:
        return None

    # --- management: hard stop has priority, otherwise RTH close ------------
    exit_px = exit_ts = None
    reason = "close"
    for b in rth5[entry_bar_i:]:                   # entry bar is included
        if side == 1 and b.low <= stop:
            exit_px, exit_ts, reason = stop, b.ts, "stop"
            break
        if side == -1 and b.high >= stop:
            exit_px, exit_ts, reason = stop, b.ts, "stop"
            break
    if exit_px is None:                            # spec B: exit at RTH close
        last = rth5[-1]
        exit_px, exit_ts, reason = last.close, last.ts, "close"

    net_points = side * (exit_px - entry_px) - COST_POINTS
    return {
        "date": str(sig.ts.date()),
        "side": side,
        "direction": "LONG" if side == 1 else "SHORT",
        "signal_ts": sig.ts,
        "entry_ts": rth5[entry_bar_i].ts,
        "entry": entry_px,
        "or_high": orh,
        "or_low": orl,
        "stop": stop,
        "exit_ts": exit_ts,
        "exit": exit_px,
        "exit_reason": reason,
        "risk": risk,
        "net_points": net_points,
        "netR": net_points / risk,
        "dollars": net_points * POINT_VALUE,
        "guard_mode": "1m" if day1m is not None else "5m",
    }


# ---------------------------------------------------------------------------
# S5b — spec section C, mirroring tmp_s5b_round3c.py::s5b_day
# ---------------------------------------------------------------------------

def s5b_day(day5: list[Bar]) -> dict:
    """Run the frozen S5b classifier on one session's 5m bars.

    Returns the state timeline: latch, pullback activation, failure,
    confirmation and invalidation timestamps plus the terminal state.

    S5b is a CLASSIFIER (spec D). It never filters or re-times the ORB entry.
    """
    bars = [b for b in day5 if in_rth(b)]
    res = {"side": 0, "direction": "NONE", "state": S5B_WAITING,
           "latch_ts": None, "pullback_ts": None, "failure_ts": None,
           "confirmed_ts": None, "invalidated_ts": None,
           "ob_high": None, "ob_low": None, "entry_eligible": False}

    # spec C: opening balance = first six 5m bars, 09:30-09:59.
    ob = [b for b in bars if OR_START <= b.ts.time() < OB_END]
    if len(ob) < 6:
        return res
    obh = max(b.high for b in ob)
    obl = min(b.low for b in ob)
    res["ob_high"], res["ob_low"] = obh, obl

    # spec C: rolling 12-bar mean volume, minimum 6 observations, session-scoped
    # and INCLUDING the current bar (tmp_s5b_round3c.py default vol_shift=False).
    vols = [b.volume for b in bars]

    def vol_mean(j: int) -> float | None:
        lo = max(0, j - S5B_VOL_LEN + 1)
        win = vols[lo:j + 1]
        if len(win) < S5B_VOL_MIN_OBS:
            return None
        return sum(win) / len(win)

    side = 0
    leg_low = leg_high = None
    pull = False
    pull_start_j = None
    failure_ts = None

    for j, b in enumerate(bars):
        if b.ts.time() < S5B_LATCH_FROM:
            continue

        # --- direction latch ------------------------------------------------
        if side == 0:
            if b.close > obh:
                side = 1
            elif b.close < obl:
                side = -1
            else:
                continue
            res["side"] = side
            res["direction"] = "LONG" if side == 1 else "SHORT"
            res["state"] = S5B_LATCHED
            res["latch_ts"] = b.ts
            hist = bars[:j + 1]                    # session through latch bar
            leg_low = min(x.low for x in hist)
            leg_high = max(x.high for x in hist)
            continue

        # --- leg update + retracement --------------------------------------
        if side == 1:
            made_new_extreme = b.high > leg_high
            leg_high = max(leg_high, b.high)
            leg = leg_high - leg_low
            if leg <= 0:
                continue
            retr = (leg_high - b.low) / leg
        else:
            made_new_extreme = b.low < leg_low
            leg_low = min(leg_low, b.low)
            leg = leg_high - leg_low
            if leg <= 0:
                continue
            retr = (b.high - leg_low) / leg

        # spec C.5: >100% retracement invalidates the S5b STATE only.
        if retr > S5B_INVALIDATE:
            res["state"] = S5B_INVALIDATED
            res["invalidated_ts"] = b.ts
            return res

        # spec C.4: valid pullback 25%-75%. The reference engine additionally
        # requires that the bar did not itself make a new leg extreme.
        if S5B_BAND_LO <= retr <= S5B_BAND_HI and not pull and not made_new_extreme:
            pull = True
            pull_start_j = j
            res["state"] = S5B_PULLBACK
            res["pullback_ts"] = b.ts
        if not pull:
            continue
        if j - pull_start_j < 2 or j < 2:
            continue

        prev1, prev2 = bars[j - 1], bars[j - 2]
        if side == 1:
            # spec C.6: two consecutive bars with no meaningful new progress.
            fail = (b.low >= prev1.low - S5B_FAIL_TOL
                    and prev1.low >= prev2.low - S5B_FAIL_TOL)
            # spec C.7: reassertion structure + direction.
            resume = b.close > prev1.high and b.close > b.open
        else:
            fail = (b.high <= prev1.high + S5B_FAIL_TOL
                    and prev1.high <= prev2.high + S5B_FAIL_TOL)
            resume = b.close < prev1.low and b.close < b.open

        if fail and failure_ts is None:
            failure_ts = b.ts
            res["failure_ts"] = b.ts
            if res["state"] == S5B_PULLBACK:
                res["state"] = S5B_FAILURE

        vm = vol_mean(j)
        pressure = vm is not None and b.volume >= S5B_VOL_MULT * vm

        # spec C.8: pullback + failure + reassertion => CONFIRMED.
        if fail and resume and pressure:
            res["state"] = S5B_CONFIRMED
            res["confirmed_ts"] = b.ts
            res["failure_ts"] = res["failure_ts"] or b.ts
            # spec C: no standalone S5b entries after the 11:30 5m bar.
            res["entry_eligible"] = b.ts.time() <= S5B_ENTRY_DEADLINE
            return res

    return res


def alignment(orb_side: int, s5b: dict) -> str:
    """spec section D relationship state."""
    if s5b["state"] != S5B_CONFIRMED or orb_side == 0:
        return "UNRESOLVED"
    return "ALIGNED" if s5b["side"] == orb_side else "DISAGREEMENT"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

TRADE_COLUMNS = ["date", "side", "direction", "signal_ts", "entry_ts", "entry",
                 "or_high", "or_low", "stop", "exit_ts", "exit", "exit_reason",
                 "risk", "net_points", "netR", "dollars", "guard_mode"]

S5B_COLUMNS = ["date", "direction", "state", "latch_ts", "pullback_ts",
               "failure_ts", "confirmed_ts", "invalidated_ts", "ob_high",
               "ob_low", "entry_eligible"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--minute", help="1-minute OHLCV CSV (exact research guards)")
    src.add_argument("--five-min", dest="five_min", help="5-minute OHLCV CSV")
    ap.add_argument("--out", required=True, help="ORB trade list output CSV")
    ap.add_argument("--s5b", help="optional S5b state timeline output CSV")
    a = ap.parse_args(argv)

    if a.minute:
        m = [b for b in read_bars(a.minute) if in_rth(b)]
        five = to_5m(m)
        days1 = group_by_day(m)
    else:
        five = [b for b in read_bars(a.five_min) if in_rth(b)]
        days1 = {}
    days5 = group_by_day(five)

    trades, states = [], []
    for d in sorted(days5):
        day5 = days5[d]
        t = orb_day(day5, days1.get(d) if a.minute else None)
        if t:
            trades.append(t)
        if a.s5b:
            s = s5b_day(day5)
            s["date"] = str(d)
            states.append(s)

    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
    print(f"{len(trades)} trades -> {a.out}")

    if a.s5b:
        with open(a.s5b, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=S5B_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(states)
        print(f"{len(states)} sessions -> {a.s5b}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
