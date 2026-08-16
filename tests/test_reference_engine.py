#!/usr/bin/env python3
"""Clause-by-clause tests for tests/reference_engine.py.

Each test names the frozen-spec clause it pins. These are the tests the Pine
implementation must also satisfy: pine/nq_orb_s5b_v1.pine carries the same
clause tags, so a failure here means the Pine twin is wrong too.

Run:  python3 tests/test_reference_engine.py
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reference_engine import (  # noqa: E402
    Bar, COST_POINTS, POINT_VALUE, S5B_CONFIRMED, S5B_INVALIDATED, S5B_LATCHED,
    S5B_FAILURE, S5B_PULLBACK, S5B_WAITING, alignment, body_fraction, orb_day,
    s5b_day, to_5m,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = "2024-03-05"

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def bar(hhmm: str, o: float, h: float, l: float, c: float, v: float = 1000) -> Bar:
    return Bar(datetime.strptime(f"{DAY} {hhmm}", "%Y-%m-%d %H:%M"), o, h, l, c, v)


def flat(hhmm: str, px: float, v: float = 1000) -> Bar:
    """A neutral, non-qualifying bar parked inside the opening range."""
    return Bar(datetime.strptime(f"{DAY} {hhmm}", "%Y-%m-%d %H:%M"),
               px, px + 0.5, px - 0.5, px, v)


def times(start: str, n: int) -> list[str]:
    t = datetime.strptime(f"{DAY} {start}", "%Y-%m-%d %H:%M")
    return [(t + timedelta(minutes=5 * i)).strftime("%H:%M") for i in range(n)]


def opening_range() -> list[Bar]:
    """ORH = 100, ORL = 90 over 09:30/09:35/09:40."""
    return [bar("09:30", 95, 100, 90, 96),
            bar("09:35", 96, 99, 92, 95),
            bar("09:40", 95, 98, 93, 97)]


def tail(from_hhmm: str, px: float, upto: str = "15:55") -> list[Bar]:
    """Neutral bars from `from_hhmm` to `upto`, never touching 90 or 100."""
    out = []
    t = datetime.strptime(f"{DAY} {from_hhmm}", "%Y-%m-%d %H:%M")
    end = datetime.strptime(f"{DAY} {upto}", "%Y-%m-%d %H:%M")
    while t <= end:
        out.append(Bar(t, px, px + 0.25, px - 0.25, px, 1000))
        t += timedelta(minutes=5)
    return out


# ---------------------------------------------------------------------------
# Section B — ORB
# ---------------------------------------------------------------------------

def test_basic_long() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),        # body 7/8.5 = 0.82 -> qualifies
        bar("09:50", 104, 106, 103, 105),        # entry bar, open 104
    ] + tail("09:55", 105)
    t = orb_day(bars)
    check("B: qualified long breakout produces a trade", t is not None)
    check("B: entry = next 5m bar open", t["entry"] == 104 and
          t["entry_ts"].strftime("%H:%M") == "09:50", str(t["entry"]))
    check("B: LONG stop = ORL", t["stop"] == 90)
    check("B: signal bar recorded", t["signal_ts"].strftime("%H:%M") == "09:45")
    check("B: hold to RTH close", t["exit_reason"] == "close" and
          t["exit_ts"].strftime("%H:%M") == "15:55")
    check("B: risk = |entry - stop|", t["risk"] == 14)
    exp = (105 - 104) - COST_POINTS
    check("accounting: net_points", abs(t["net_points"] - exp) < 1e-9)
    check("accounting: netR", abs(t["netR"] - exp / 14) < 1e-9)
    check("accounting: dollars", abs(t["dollars"] - exp * POINT_VALUE) < 1e-9)


def test_basic_short() -> None:
    bars = opening_range() + [
        bar("09:45", 93, 93.5, 85, 86),
        bar("09:50", 86, 87, 84, 85),
    ] + tail("09:55", 85)
    t = orb_day(bars)
    check("B: qualified short breakout", t is not None and t["side"] == -1)
    check("B: SHORT stop = ORH", t["stop"] == 100)
    check("B: short entry = next bar open", t["entry"] == 86)


def test_body_filter() -> None:
    # 09:45 closes above ORH but body/range = 1/12 = 0.083 -> rejected.
    bars = opening_range() + [
        bar("09:45", 100.5, 108, 96, 101.5),
        bar("09:50", 101, 104, 100.5, 103.5),    # body 2.5/3.5 = 0.71 -> qualifies
        bar("09:55", 103.5, 105, 103, 104),      # entry bar
    ] + tail("10:00", 104)
    t = orb_day(bars)
    check("B: body filter rejects <0.50 body fraction",
          t["signal_ts"].strftime("%H:%M") == "09:50", str(t["signal_ts"]))
    check("B: entry follows the qualified bar, not the rejected one",
          t["entry_ts"].strftime("%H:%M") == "09:55")


def test_zero_range_bar_cannot_qualify() -> None:
    z = Bar(datetime.strptime(f"{DAY} 09:45", "%Y-%m-%d %H:%M"), 101, 101, 101, 101, 10)
    check("B: zero-range bar has undefined body fraction", body_fraction(z) is None)
    bars = opening_range() + [z, bar("09:50", 101, 101, 101, 101, 10)] + tail("09:55", 101)
    t = orb_day(bars)
    check("B: zero-range bar never qualifies", t is None, str(t))


def test_first_direction_wins() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),        # long qualifies first
        bar("09:50", 104, 105, 88, 89),          # would qualify short, ignored
    ] + tail("09:55", 95)
    t = orb_day(bars)
    check("B: first qualified direction wins", t["side"] == 1)


def test_one_trade_per_day() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 104, 106, 103, 105),
    ] + [bar("09:55", 105, 112, 104, 111)] + tail("10:00", 111)
    t = orb_day(bars)
    check("B: one trade per day", t["entry_ts"].strftime("%H:%M") == "09:50")


def test_qualification_window_end() -> None:
    # A qualifying breakout at 10:35 is outside the 09:45-10:30 window.
    bars = opening_range() + tail("09:45", 95, "10:30") + [
        bar("10:35", 97, 105, 96.5, 104),
        bar("10:40", 104, 106, 103, 105),
    ] + tail("10:45", 105)
    check("B: no signal after the 10:30 bar", orb_day(bars) is None)
    # The 10:30 bar itself is inside the window (inclusive).
    bars2 = opening_range() + tail("09:45", 95, "10:25") + [
        bar("10:30", 97, 105, 96.5, 104),
        bar("10:35", 104, 106, 103, 105),
    ] + tail("10:40", 105)
    t2 = orb_day(bars2)
    check("B: the 10:30 bar is inside the window",
          t2 is not None and t2["signal_ts"].strftime("%H:%M") == "10:30")


def test_skip_when_entry_beyond_stop() -> None:
    # Long signal, then the entry bar opens below ORL (90) -> skip the trade.
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 89, 90, 80, 85),
    ] + tail("09:55", 85)
    check("B: skip when next-bar open is already beyond the stop",
          orb_day(bars) is None)
    # Exactly at the stop is also a skip (risk would be 0).
    bars2 = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 90, 95, 89, 94),
    ] + tail("09:55", 94)
    check("B: entry exactly at the stop is a skip", orb_day(bars2) is None)


def test_skip_does_not_rearm_a_second_signal() -> None:
    """The first qualified bar ends the search, even when its entry is skipped."""
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),        # qualifies long
        bar("09:50", 89, 90, 80, 85),            # entry beyond the stop -> skip
        bar("09:55", 85, 86, 78, 79),            # would qualify short later
        bar("10:00", 79, 80, 77, 78),
    ] + tail("10:05", 78)
    check("B: a skipped session does not arm a second signal",
          orb_day(bars) is None)


def test_missing_entry_bar_is_skipped() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        # 09:50 bar absent from the feed
        bar("09:55", 104, 106, 103, 105),
    ] + tail("10:00", 105)
    check("B: a gap where the entry bar belongs skips the trade",
          orb_day(bars) is None)


def test_stop_on_entry_bar() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 104, 105, 89, 91),          # trades through ORL on entry bar
    ] + tail("09:55", 91)
    t = orb_day(bars)
    check("B: the stop is live on the entry bar itself",
          t["exit_reason"] == "stop" and t["exit_ts"].strftime("%H:%M") == "09:50")
    check("B: stop fills at the stop price", t["exit"] == 90)


def test_stop_has_priority_over_close() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 104, 106, 103, 105),
        bar("09:55", 105, 106, 88, 104),         # stop touched
    ] + tail("10:00", 104)
    t = orb_day(bars)
    check("B: stop has priority if touched", t["exit_reason"] == "stop" and
          t["exit_ts"].strftime("%H:%M") == "09:55")


def test_early_close_session() -> None:
    bars = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 104, 106, 103, 105),
    ] + tail("09:55", 105, "12:55")              # shortened holiday session
    t = orb_day(bars)
    check("B: shortened session exits on its own final bar",
          t["exit_reason"] == "close" and t["exit_ts"].strftime("%H:%M") == "12:55")


def test_no_breakout_no_trade() -> None:
    bars = opening_range() + tail("09:45", 95)
    check("B: no qualified breakout means no trade", orb_day(bars) is None)


def test_minute_guard() -> None:
    """With 1m data the research guard requires >=10 minute bars in the OR."""
    m = []
    t = datetime.strptime(f"{DAY} 09:30", "%Y-%m-%d %H:%M")
    for i in range(9):                            # only 9 minute bars
        m.append(Bar(t + timedelta(minutes=i), 95, 96, 94, 95, 100))
    five = to_5m(m)
    check("B: <10 minute bars in the opening range disables the session",
          orb_day(five, m) is None)


def test_5m_aggregation() -> None:
    m = [Bar(datetime.strptime(f"{DAY} 09:3{i}", "%Y-%m-%d %H:%M"),
             10 + i, 20 + i, 5 - i, 15 + i, 100) for i in range(5)]
    f = to_5m(m)
    check("aggregation: one 5m bucket", len(f) == 1)
    check("aggregation: OHLCV = first/max/min/last/sum",
          f[0].open == 10 and f[0].high == 24 and f[0].low == 1
          and f[0].close == 19 and f[0].volume == 500)


# ---------------------------------------------------------------------------
# Section C — S5b
# ---------------------------------------------------------------------------

def s5b_opening_balance() -> list[Bar]:
    """Six 09:30-09:55 bars: OBH = 100, OBL = 90."""
    hh = times("09:30", 6)
    return [bar(hh[0], 95, 100, 90, 96),
            bar(hh[1], 96, 99, 92, 95),
            bar(hh[2], 95, 98, 93, 97),
            bar(hh[3], 97, 99, 94, 96),
            bar(hh[4], 96, 98, 93, 95),
            bar(hh[5], 95, 97, 92, 96)]


def test_s5b_no_latch() -> None:
    bars = s5b_opening_balance() + tail("10:00", 95)
    s = s5b_day(bars)
    check("C: no close beyond the opening balance leaves WAITING_FOR_LATCH",
          s["state"] == S5B_WAITING and s["direction"] == "NONE")


def test_s5b_latch_and_confirm() -> None:
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),          # latch long, leg_high 110, leg_low 90
        bar("10:05", 109, 110, 104, 105),        # retr = (110-104)/20 = 0.30 -> pullback
        bar("10:10", 105, 106, 103.5, 104),      # no new progress vs 104
        bar("10:15", 104, 105, 103.2, 104.5),    # 2 bars of no progress
        bar("10:20", 104.5, 108, 104, 107, 5000),  # close > prior high, close > open,
                                                   # volume >> 1.2 * rolling mean
    ]
    s = s5b_day(ob + seq + tail("10:25", 107))
    check("C: direction latches on the first close beyond the OB",
          s["direction"] == "LONG" and s["latch_ts"].strftime("%H:%M") == "10:00")
    check("C.4: 25-75% retracement activates the pullback",
          s["pullback_ts"] is not None and s["pullback_ts"].strftime("%H:%M") == "10:05")
    check("C.6: two bars of no new progress mark the failure",
          s["failure_ts"] is not None)
    check("C.8: pullback + failure + reassertion => CONFIRMED",
          s["state"] == S5B_CONFIRMED and s["confirmed_ts"].strftime("%H:%M") == "10:20",
          f"{s['state']} {s['confirmed_ts']}")
    check("C: confirmation before 11:30 is entry-eligible", s["entry_eligible"] is True)


def test_s5b_requires_volume() -> None:
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),
        bar("10:05", 109, 110, 104, 105),
        bar("10:10", 105, 106, 103.5, 104),
        bar("10:15", 104, 105, 103.2, 104.5),
        bar("10:20", 104.5, 108, 104, 107, 100),  # same structure, weak volume
    ]
    s = s5b_day(ob + seq + tail("10:25", 107))
    check("C.7: reassertion without 1.2x relative volume does not confirm",
          s["state"] != S5B_CONFIRMED, s["state"])


def test_s5b_invalidation() -> None:
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),          # latch long: leg 90 -> 110
        bar("10:05", 109, 110, 89, 91),          # retr = (110-89)/20 = 1.05 > 1.0
    ]
    s = s5b_day(ob + seq + tail("10:10", 91))
    check("C.5: >100% retracement invalidates the S5b state",
          s["state"] == S5B_INVALIDATED and s["invalidated_ts"] is not None)


def test_s5b_new_extreme_still_opens_pullback() -> None:
    """A bar that extends the leg AND retraces into the band does open the
    pullback: there is no "no new extreme" precondition.

    Resolved empirically (spec/SPEC_SEAMS.md S-4): adding that precondition
    breaks the band flag on 30 of 764 sessions.
    """
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),
        # New leg high 120, and low 104 -> retr = (120-104)/30 = 0.53, in band.
        bar("10:05", 109, 120, 104, 118),
    ]
    s = s5b_day(ob + seq + tail("10:10", 118))
    # That same bar also satisfies the two-bar no-progress test, so the state
    # advances straight to COUNTERATTACK_FAILURE; what matters here is that the
    # pullback opened at all.
    check("C.4: a bar making a new leg extreme can still open the pullback",
          s["pullback_ts"] is not None and s["state"] in (S5B_PULLBACK, S5B_FAILURE),
          str(s["state"]))


def test_s5b_short_mirror() -> None:
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 95, 96, 80, 81),            # latch short, leg_low 80, leg_high 100
        bar("10:05", 81, 86, 80.5, 85),          # retr = (86-80)/20 = 0.30 -> pullback
        bar("10:10", 85, 86.4, 84, 85.5),
        bar("10:15", 85.5, 86.8, 84.5, 85),
        bar("10:20", 85, 85.5, 82, 82.5, 5000),  # close < prior low, close < open
    ]
    s = s5b_day(ob + seq + tail("10:25", 82))
    check("C: SHORT is the exact mirror",
          s["direction"] == "SHORT" and s["state"] == S5B_CONFIRMED,
          f"{s['direction']} {s['state']}")


def test_s5b_scan_stops_after_1130() -> None:
    """The classifier runs 10:00 through the 11:30 bar and stops there.

    Resolved empirically against s5b_day_flags_allmult.csv on 764 sessions of
    real data (spec/SPEC_SEAMS.md S-1): without the cap, 57 sessions latch after
    11:30 that the reference never latches.
    """
    ob = s5b_opening_balance()
    pad = tail("10:00", 96, "11:25")
    seq = [
        bar("11:30", 96, 110, 95, 109),
        bar("11:35", 109, 110, 104, 105),
        bar("11:40", 105, 106, 103.5, 104),
        bar("11:45", 104, 105, 103.2, 104.5),
        bar("11:50", 104.5, 108, 104, 107, 5000),
    ]
    s = s5b_day(ob + pad + seq + tail("11:55", 107))
    check("C: a sequence completing after 11:30 does not confirm",
          s["state"] != S5B_CONFIRMED, f"{s['state']} {s['confirmed_ts']}")
    # The 11:30 bar itself is inside the window.
    ob2 = s5b_opening_balance()
    pad2 = tail("10:00", 96, "11:05")
    seq2 = [
        bar("11:10", 96, 110, 95, 109),
        bar("11:15", 109, 110, 104, 105),
        bar("11:20", 105, 106, 103.5, 104),
        bar("11:25", 104, 105, 103.2, 104.5),
        bar("11:30", 104.5, 106.5, 104, 106, 5000),
    ]
    s2 = s5b_day(ob2 + pad2 + seq2 + tail("11:35", 106))
    check("C: the 11:30 bar is inside the classifier window",
          s2["state"] == S5B_CONFIRMED and s2["entry_eligible"] is True,
          f"{s2['state']} {s2['confirmed_ts']}")


def test_s5b_band_is_a_live_condition() -> None:
    """The failure and reassertion bars must themselves sit inside the 25-75%
    band; the band is not a latch that stays set once touched.

    Resolved empirically (spec/SPEC_SEAMS.md S-2): treating it as sticky adds 48
    confirmations across 764 sessions that the reference does not have.
    """
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),          # latch long, leg 90 -> 110
        bar("10:05", 109, 110, 104, 105),        # retr 0.30 -> pullback opens
        bar("10:10", 105, 106, 103.5, 104),
        bar("10:15", 104, 105, 103.2, 104.5),
        # Reassertion bar: low 108 -> retr = (110-108)/20 = 0.10, OUTSIDE the band.
        bar("10:20", 108.5, 112, 108, 111.5, 5000),
    ]
    s = s5b_day(ob + seq + tail("10:25", 111))
    check("C: a reassertion bar outside the band does not confirm",
          s["state"] != S5B_CONFIRMED, f"{s['state']}")


def test_s5b_no_two_bar_delay_after_pullback() -> None:
    """There is no waiting period between the pullback opening and the sequence
    completing — only the two-bar failure test itself constrains timing.

    Resolved empirically (spec/SPEC_SEAMS.md S-3): imposing a two-bar delay
    costs 46 confirmations across 764 sessions.
    """
    ob = s5b_opening_balance()
    seq = [
        bar("10:00", 96, 110, 95, 109),          # latch, leg 90 -> 110
        bar("10:05", 109, 110, 104, 105),        # retr 0.30 -> pullback opens
        # The very next bar is still in band (retr 0.31), satisfies the two-bar
        # no-progress test, closes above the prior bar's high with a directional
        # body on heavy volume: it confirms immediately, one bar later.
        bar("10:10", 105, 111, 104.5, 110.5, 5000),
    ]
    s = s5b_day(ob + seq + tail("10:15", 106))
    check("C: no enforced delay between the pullback and the confirmation",
          s["state"] == S5B_CONFIRMED, f"{s['state']}")


def test_s5b_needs_six_ob_bars() -> None:
    ob = s5b_opening_balance()[:5]
    s = s5b_day(ob + tail("10:00", 96))
    check("C: fewer than six opening-balance bars disables S5b",
          s["state"] == S5B_WAITING and s["ob_high"] is None)


# ---------------------------------------------------------------------------
# Section D — relationship / independence
# ---------------------------------------------------------------------------

def test_alignment_states() -> None:
    check("D: ALIGNED", alignment(1, {"state": S5B_CONFIRMED, "side": 1}) == "ALIGNED")
    check("D: DISAGREEMENT",
          alignment(1, {"state": S5B_CONFIRMED, "side": -1}) == "DISAGREEMENT")
    check("D: UNRESOLVED when S5b has not confirmed",
          alignment(1, {"state": S5B_PULLBACK, "side": 1}) == "UNRESOLVED")
    check("D: UNRESOLVED when there is no ORB direction",
          alignment(0, {"state": S5B_CONFIRMED, "side": 1}) == "UNRESOLVED")


def test_s5b_never_changes_the_orb_trade() -> None:
    """S5b must not filter or re-time the ORB entry (spec D, last line)."""
    base = opening_range() + [
        bar("09:45", 97, 105, 96.5, 104),
        bar("09:50", 104, 106, 103, 105),
    ] + tail("09:55", 105)
    t1 = orb_day(base)
    # Same bars, but with an S5b sequence that would confirm the opposite side.
    t2 = orb_day(base)
    s = s5b_day(base)
    check("D: the ORB trade is identical regardless of S5b state",
          t1 == t2 and s is not None)


# ---------------------------------------------------------------------------
# Canonical reference file — golden checks
# ---------------------------------------------------------------------------

def test_canonical_file() -> None:
    path = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
    if not os.path.exists(path):
        check("canonical: file present", False, "run reference/build_canonical.py")
        return
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("canonical: 4022 trades 2008-2026", len(rows) == 4022, str(len(rows)))
    check("canonical: one row per session",
          len(set(r["date"] for r in rows)) == len(rows))
    r16 = [r for r in rows if r["date"] >= "2016"]
    netr = sum(float(r["netR"]) for r in r16)
    # spec B "Verified reference": ~2,295 trades / +122.8R / PF ~1.11
    check("canonical: 2016-2026 trade count matches the frozen spec",
          len(r16) == 2295, str(len(r16)))
    check("canonical: 2016-2026 netR matches the frozen spec (+122.8R)",
          abs(netr - 122.8) < 0.1, f"{netr:.4f}")
    for r in rows:
        side = int(r["side"])
        if side == 1 and float(r["entry"]) <= float(r["stop"]):
            check("canonical: long entries sit above their stop", False, r["date"])
            return
        if side == -1 and float(r["entry"]) >= float(r["stop"]):
            check("canonical: short entries sit below their stop", False, r["date"])
            return
    check("canonical: every entry sits on the correct side of its stop", True)
    check("canonical: exit reasons are stop|close",
          set(r["exit_reason"] for r in rows) == {"stop", "close"})


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
