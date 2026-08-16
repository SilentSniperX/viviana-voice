#!/usr/bin/env python3
"""Resolve the under-specified S5b clauses against the reference flag file.

Frozen spec section C leaves several details unstated; the shipped Round-3c
engine settles some of them but the flag file was produced by round3.py, which
is not in the handoff. This enumerates the candidate readings and reports which
combination reproduces the reference flags. It fits the IMPLEMENTATION to the
reference engine — it does not touch a strategy rule or look at P&L.
"""
import csv, itertools, os, sys
from datetime import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reference_engine import (Bar, in_rth, to_5m, _parse_ts)  # noqa

OR_START = time(9, 30); OB_END = time(10, 0); LATCH_FROM = time(10, 0)
CAP = time(11, 30)


def s5b(bars, latch_cap, scan_cap, need_no_new_extreme, two_bar_delay,
        band_sticky, vol_shift):
    bars = [b for b in bars if in_rth(b)]
    res = {"side": 0, "state": "WAITING", "latch_ts": None, "band": False,
           "confirm_ts": None, "invalid_ts": None}
    ob = [b for b in bars if OR_START <= b.ts.time() < OB_END]
    if len(ob) < 6:
        return res
    obh = max(b.high for b in ob); obl = min(b.low for b in ob)
    vols = [b.volume for b in bars]

    def vmean(j):
        hi = j if vol_shift else j + 1          # shift(1) excludes the current bar
        lo = max(0, hi - 12)
        win = vols[lo:hi]
        return sum(win) / len(win) if len(win) >= 6 else None

    side = 0; leg_lo = leg_hi = None; pull = False; pull_j = None
    for j, b in enumerate(bars):
        if b.ts.time() < LATCH_FROM:
            continue
        if scan_cap and b.ts.time() > CAP:
            break
        if side == 0:
            if latch_cap and b.ts.time() > CAP:
                break
            if b.close > obh:
                side = 1
            elif b.close < obl:
                side = -1
            else:
                continue
            res["side"] = side; res["state"] = "LATCHED"; res["latch_ts"] = b.ts
            hist = bars[:j + 1]
            leg_lo = min(x.low for x in hist); leg_hi = max(x.high for x in hist)
            continue
        if side == 1:
            new_ext = b.high > leg_hi
            leg_hi = max(leg_hi, b.high)
        else:
            new_ext = b.low < leg_lo
            leg_lo = min(leg_lo, b.low)
        leg = leg_hi - leg_lo
        if leg <= 0:
            continue
        retr = (leg_hi - b.low) / leg if side == 1 else (b.high - leg_lo) / leg
        if retr > 1.0:
            res["state"] = "INVALIDATED"; res["invalid_ts"] = b.ts
            return res
        in_band = 0.25 <= retr <= 0.75
        if in_band and not pull and (not new_ext if need_no_new_extreme else True):
            pull = True; pull_j = j; res["band"] = True; res["state"] = "PULLBACK"
        if not pull:
            continue
        if two_bar_delay and j - pull_j < 2:
            continue
        if j < 2:
            continue
        p1, p2 = bars[j - 1], bars[j - 2]
        if side == 1:
            fail = (b.low >= p1.low - 1.0 and p1.low >= p2.low - 1.0)
            resume = b.close > p1.high and b.close > b.open
        else:
            fail = (b.high <= p1.high + 1.0 and p1.high <= p2.high + 1.0)
            resume = b.close < p1.low and b.close < b.open
        gate = True if band_sticky else in_band
        vm = vmean(j)
        press = vm is not None and b.volume >= 1.2 * vm
        if gate and fail and resume and press:
            res["state"] = "CONFIRMED"; res["confirm_ts"] = b.ts
            return res
    return res


def load_sessions():
    import zipfile, io
    days = {}
    raw = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "raw")
    zips = [os.path.join(raw, f) for f in sorted(os.listdir(raw))
            if f.lower().endswith(".zip")] if os.path.isdir(raw) else []
    if not zips:
        raise SystemExit("no archives in data/raw/ — see docs/PARITY_PROCEDURE.md")
    for zp in zips:
      with zipfile.ZipFile(zp) as z:
        for m in sorted(z.namelist()):
            if "_unadj_" in m or not m.lower().endswith(".csv"):
                continue
            with io.TextIOWrapper(z.open(m), encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) < 6:
                        continue
                    try:
                        ts = _parse_ts(row[0])
                        b = Bar(ts, float(row[1]), float(row[2]), float(row[3]),
                                float(row[4]), float(row[5]))
                    except ValueError:
                        continue
                    if in_rth(b):
                        days.setdefault(ts.date(), []).append(b)
    return {d: to_5m(sorted(v, key=lambda x: x.ts)) for d, v in days.items()}


def main():
    print("loading sessions ...")
    sessions = load_sessions()
    flags = {r["date"]: r for r in csv.DictReader(open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "research", "master_handoff", "01_CLAUDE_ROUND3_PART1",
                     "s5b_day_flags_allmult.csv")))}
    common = [(d, b) for d, b in sorted(sessions.items()) if str(d) in flags]
    print(f"{len(common)} sessions with reference flags\n")

    grid = list(itertools.product([False, True], repeat=6))
    rows = []
    for latch_cap, scan_cap, nne, delay, sticky, shift in grid:
        if scan_cap and not latch_cap:
            continue                            # scan cap implies latch cap
        ok_l = ok_c = ok_b = ok_i = 0
        for d, bars in common:
            f = flags[str(d)]
            r = s5b(bars, latch_cap, scan_cap, nne, delay, sticky, shift)
            if r["side"] == int(f["latch"]):
                ok_l += 1
            if (r["state"] == "CONFIRMED") == (f["re12"] == "1"):
                ok_c += 1
            if r["band"] == (f["band"] == "True"):
                ok_b += 1
            if (r["state"] == "INVALIDATED") == (f["invalid"] == "1"):
                ok_i += 1
        n = len(common)
        rows.append((ok_l + ok_c + ok_b + ok_i, ok_l, ok_c, ok_b, ok_i,
                     latch_cap, scan_cap, nne, delay, sticky, shift))
    rows.sort(reverse=True)
    n = len(common)
    print(f"{'total':>6} {'latch':>6} {'conf':>6} {'band':>6} {'inval':>6}  "
          f"latchcap scancap noNewExt 2barDelay sticky volShift")
    for r in rows[:12]:
        print(f"{r[0]:6d} {r[1]:6d} {r[2]:6d} {r[3]:6d} {r[4]:6d}  "
              f"{str(r[5]):8s} {str(r[6]):7s} {str(r[7]):8s} {str(r[8]):9s} "
              f"{str(r[9]):6s} {str(r[10])}")
    print(f"\n(perfect would be {4*n} = 4 x {n})")


if __name__ == "__main__":
    main()
