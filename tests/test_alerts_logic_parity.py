#!/usr/bin/env python3
"""Prove the manual-alert indicator carries the SAME frozen logic as the
audited strategy.

Two Pine files now implement the same rules. That is a maintenance hazard: the
alert file is the one the user will actually trade from, and it would be easy
for it to drift from the audited one silently. Every line compared here is a
line that decides a trade.

This does not re-derive the strategy — `tests/test_reference_engine.py` does
that. It checks that the port is a port.

Run:  python3 tests/test_alerts_logic_parity.py
"""

from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRATEGY = os.path.join(REPO, "pine", "nq_orb_s5b_v1.pine")
ALERTS = os.path.join(REPO, "pine", "nq_orb_s5b_alerts.pine")

FAILURES: list[str] = []

# Every frozen constant. A single digit different here is a different strategy.
CONSTANTS = ("TZ", "BODY_FILTER", "OR_OPEN_MIN", "OR_END_MIN", "QUAL_START_MIN",
             "QUAL_END_MIN", "OB_END_MIN", "LATCH_FROM_MIN", "S5B_DEADLINE",
             "BAND_LO", "BAND_HI", "INVALID_RETR", "FAIL_TOL", "VOL_MULT",
             "VOL_LEN", "VOL_MIN_OBS", "FINAL_BAR_MIN")

# Every expression that decides something. Keyed by the binding name.
EXPRESSIONS = (
    "nyMinute", "nyDate", "isNewSession", "isSessionLast",
    "inOrWindow", "inQualWindow",
    "barRange", "bodyFrac", "bodyQualifies", "orReady",
    "longBreak", "shortBreak",
    "barIsNextAfterSignal",
    "obReady",
)

# Multi-line blocks compared as normalised text.
BLOCKS = {
    "opening range accumulation": (
        r"if inOrWindow and barstate\.isconfirmed.*?orBarCount \+= 1"),
    "range lock": (
        r"if not inOrWindow and nyMinute >= OR_END_MIN and orBarCount > 0\s+"
        r"orLocked := true"),
    "first qualified breakout": (
        r"if longBreak.*?pendingEntry := true\s*\n\s*else if shortBreak"
        r".*?pendingEntry := true"),
    "skip condition": (
        r"skip = \(orbSide ==  1 and candidateEntry <= candidateStop\).*?"
        r"math\.abs\(candidateEntry - candidateStop\) <= 0"),
    "stop trigger": (
        r"\(orbSide == 1 \? low <= stopPx : high >= stopPx\)"),
    "rolling volume mean": (
        r"volSum := volSum \+ volume.*?volN >= VOL_MIN_OBS \? volSum / volN : na"),
    "s5b latch": (
        r"if close > obH\s+s5bSide := 1\s+else if close < obL\s+s5bSide := -1"),
    "s5b leg extremes": (
        r"legLow    := sessLow\s+legHigh   := sessHigh"),
    "s5b retracement": (
        r"retr = s5bSide == 1 \? \(legHigh - low\) / leg : \(high - legLow\) / leg"),
    "s5b invalidation": (
        r"if retr > INVALID_RETR"),
    "s5b band": (
        r"inBand = retr >= BAND_LO and retr <= BAND_HI"),
    "s5b failure test": (
        r"failCond = s5bSide == 1 \?\s+\(low  >= low\[1\]  - FAIL_TOL and "
        r"low\[1\]  >= low\[2\]  - FAIL_TOL\) :\s+"
        r"\(high <= high\[1\] \+ FAIL_TOL and high\[1\] <= high\[2\] \+ FAIL_TOL\)"),
    "s5b reassertion": (
        r"resume = s5bSide == 1 \?\s+\(close > high\[1\] and close > open\) :\s+"
        r"\(close < low\[1\]  and close < open\)"),
    "s5b pressure": (
        r"pressure = not na\(volMean\) and volume >= VOL_MULT \* volMean"),
    "s5b confirmation": (
        r"if failCond and resume and pressure"),
    "s5b window": (
        r"nyMinute >= LATCH_FROM_MIN and nyMinute <= S5B_DEADLINE"),
    "s5b sessionIdx guard": (
        r"if pullActive and inBand and sessionIdx >= 3"),
}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def strip_comments(src: str) -> str:
    out = []
    for line in src.split("\n"):
        q = ""
        cut = len(line)
        i = 0
        while i < len(line):
            c = line[i]
            if q:
                if c == q:
                    q = ""
            elif c in "'\"":
                q = c
            elif c == "/" and line[i + 1:i + 2] == "/":
                cut = i
                break
            i += 1
        out.append(line[:cut].rstrip())
    return "\n".join(out)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def binding(src: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}\s*=\s*(.+)$", src, re.M)
    return norm(m.group(1)) if m else None


def block(src: str, pattern: str) -> str | None:
    m = re.search(pattern, src, re.S)
    return norm(m.group(0)) if m else None


def main() -> int:
    strat = strip_comments(open(STRATEGY).read())
    alert = strip_comments(open(ALERTS).read())

    print("frozen constants")
    for name in CONSTANTS:
        a, b = binding(strat, name), binding(alert, name)
        check(f"{name} identical", a is not None and a == b,
              f"strategy={a!r} alerts={b!r}" if a != b else str(a))

    print("\ndecision expressions")
    for name in EXPRESSIONS:
        a, b = binding(strat, name), binding(alert, name)
        check(f"{name} identical", a is not None and a == b,
              f"\n          strategy: {a}\n          alerts:   {b}"
              if a != b else "")

    print("\nlogic blocks")
    for name, pattern in BLOCKS.items():
        a, b = block(strat, pattern), block(alert, pattern)
        check(f"{name} present and identical", a is not None and a == b,
              f"\n          strategy: {a}\n          alerts:   {b}"
              if a != b else "")

    print("\nthe alert file must not trade or reach outside itself")
    for forbidden, why in (
            (r"\bstrategy\s*\(", "declares a strategy — this must be an indicator"),
            (r"\bstrategy\.(entry|exit|close|close_all|order|cancel)",
             "places orders"),
            (r"\brequest\.", "reaches for another symbol or timeframe"),
            (r"\bwebhook\b", "mentions a webhook"),
            (r'"strategy_version"', "emits a machine payload")):
        hits = re.findall(forbidden, alert)
        check(f"no {why}", not hits, f"{len(hits)} occurrence(s)")

    print("\nevery required alert exists")
    for label, needle in (
            ("1 ORB LONG", "NQ LONG"),
            ("2 ORB SHORT", "NQ SHORT"),
            ("3 HIGH CONVICTION LONG", "HIGH CONVICTION LONG"),
            ("4 HIGH CONVICTION SHORT", "HIGH CONVICTION SHORT"),
            ("5 STOP", "NQ TRADE STOPPED"),
            ("6 SESSION CLOSE", "NQ SESSION EXIT"),
            ("6b EARLY CLOSE", "EARLY CLOSE"),
            ("7 NO TRADE", "NO TRADE TODAY"),
            ("test", "NQ ALERT TEST")):
        check(f"alert {label}", needle in alert)
    check("all nine alert() calls are present",
          alert.count("alert(") == 9, f"{alert.count('alert(')} found")

    print("\nextended-hours handling")
    # The 15:55 trigger MUST still be bounded to regular hours, or an
    # early-close day on an ETH chart exits at the 18:00 Globex reopen.
    check("the clock exit is bounded to regular hours",
          "isSessionLast and inRth and barstate.isconfirmed" in alert)
    check("leaving the session with a position open is a separate exit path",
          "leftSession and barstate.isconfirmed" in alert)
    check("and it books the LAST regular-hours bar's close, not the gap bar",
          "exitPx     := close[1]" in alert)
    check("the session-end definition covers both chart types",
          "sessionEnded = (isSessionLast and inRth) or leftSession" in alert)
    check("the no-trade summary uses that same definition",
          "if sessionEnded and barstate.isconfirmed and not summarySent" in alert)
    check("an extended-hours chart must be acknowledged, not merely tolerated",
          "ethUnexpected = cEthBars > 0 and not ethOk" in alert)
    # isSessionLast itself is unchanged, so the shared expression still matches
    # the audited strategy — the ETH handling is additive, not a rewrite.
    check("isSessionLast is still identical to the audited strategy",
          binding(strat, "isSessionLast") == binding(alert, "isSessionLast"))

    print("\nS5b is context only, never an entry")
    check("the conviction alerts say so",
          alert.count("THIS IS NOT A NEW ENTRY SIGNAL IN v1.") == 2)
    check("S5b state never gates the ORB entry",
          not re.search(r"if .*\bs5bStateN\b.*\n.*(orbSide := |tradeTaken := )", alert))

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("ALERT FILE CARRIES THE SAME FROZEN LOGIC AS THE AUDITED STRATEGY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
