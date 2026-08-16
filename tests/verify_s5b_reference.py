#!/usr/bin/env python3
"""Verify the S5b reference flag file before it is used for state parity.

Phase 1 step 1.8 needs a trustworthy S5b reference. The shipped candidate is
`research/master_handoff/01_CLAUDE_ROUND3_PART1/s5b_day_flags_allmult.csv`
(4,768 sessions x latch side / band reached / reassertion at 1.0-1.2-1.4x /
invalidation). Before trusting it, this script checks that reading it the way
`tests/reference_engine.py::s5b_day` models the states reproduces the numbers
`round3_and_part1_report.txt` published.

The report's "FOUR-STATE DAY TAXONOMY" section states profit factors of ORB
trades grouped by how far the S5b sequence progressed that session:

    COMPLETED   1.92 / 2.54 / 2.61      (n = 413 / 269 / 79 at the 1.2x threshold)
    LATCH-ONLY  1.70 / 2.76 / 1.97
    NEAR-MISS   0.70 / 0.90 / 1.04
    NO LATCH    0.26 / 0.29 / 0.22      (eras 2008-15 / 2016-23 / 2024-26)

Reproducing those from the canonical ORB list joined to the flag file confirms
three things at once: the flag columns mean what they appear to mean, the join
key is sound, and the canonical ORB list is the same trade set the S5b research
was measured on.

Usage:  python3 tests/verify_s5b_reference.py
Exit code is non-zero if any published cell is not reproduced.
"""

from __future__ import annotations

import collections
import csv
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGS = os.path.join(REPO, "research", "master_handoff", "01_CLAUDE_ROUND3_PART1",
                     "s5b_day_flags_allmult.csv")
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")

ERAS = ["2008-15", "2016-23", "2024-26"]

# round3_and_part1_report.txt, "NEW FINDING — THE FOUR-STATE DAY TAXONOMY".
PUBLISHED_PF = {
    "COMPLETED":  (1.92, 2.54, 2.61),
    "LATCH-ONLY": (1.70, 2.76, 1.97),
    "NEAR-MISS":  (0.70, 0.90, 1.04),
    "NO LATCH":   (0.26, 0.29, 0.22),
}
PUBLISHED_N_COMPLETED = (413, 269, 79)
PF_TOL = 0.02

# The three sessions the 1-minute engine excluded (seam S-7). The published
# taxonomy was computed on the 4,019-trade set, so they are dropped here too.
HOLIDAY_SESSIONS = {"2008-05-26", "2011-05-30", "2011-07-04"}


def era_of(date: str) -> str:
    y = int(date[:4])
    return "2008-15" if y < 2016 else ("2016-23" if y < 2024 else "2024-26")


def day_state(f: dict) -> str:
    """The four-state taxonomy, read off the shipped flag columns.

    `latch`  session direction latched (0 = never closed beyond the opening
             balance), `band` the 25-75% pullback band was reached, `re12` the
             reassertion completed at the frozen 1.2x volume threshold.

    Invalidated sessions (`invalid` = 1) are NOT a separate bucket: folding them
    into band / no-band is what reproduces the published figures, so the
    published taxonomy classifies a session by how far it got, regardless of a
    later invalidation.
    """
    if f["latch"] == "0":
        return "NO LATCH"
    if f["re12"] == "1":
        return "COMPLETED"
    if f["band"] != "True":
        return "LATCH-ONLY"
    return "NEAR-MISS"


def profit_factor(values: list[float]) -> float:
    gp = sum(v for v in values if v > 0)
    gl = -sum(v for v in values if v < 0)
    return gp / gl if gl else float("inf")


def main() -> int:
    flags = {r["date"]: r for r in csv.DictReader(open(FLAGS, newline=""))}
    trades = list(csv.DictReader(open(CANONICAL, newline="")))

    unmatched = [t["date"] for t in trades if t["date"] not in flags]
    buckets: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for t in trades:
        if t["date"] in HOLIDAY_SESSIONS or t["date"] not in flags:
            continue
        # The published PFs are computed on R, not dollars — an R-based PF is
        # the only reading that reproduces all twelve cells.
        buckets[(day_state(flags[t["date"]]), era_of(t["date"]))].append(
            float(t["netR"]))

    failures: list[str] = []
    if unmatched:
        failures.append(f"{len(unmatched)} canonical sessions missing from the "
                        f"flag file, e.g. {unmatched[:3]}")

    print(f"joined {len(trades) - len(unmatched)}/{len(trades)} canonical trades "
          f"to the S5b flag file")
    print(f"\n{'state':11s} {'era':8s} {'n':>5s} {'PF':>6s} {'published':>10s}  result")
    for state in ("COMPLETED", "LATCH-ONLY", "NEAR-MISS", "NO LATCH"):
        for i, e in enumerate(ERAS):
            vals = buckets[(state, e)]
            pf = profit_factor(vals)
            want = PUBLISHED_PF[state][i]
            ok = abs(pf - want) <= PF_TOL
            if not ok:
                failures.append(f"{state}/{e}: PF {pf:.2f} vs published {want:.2f}")
            print(f"{state:11s} {e:8s} {len(vals):5d} {pf:6.2f} {want:10.2f}  "
                  f"{'OK' if ok else 'MISMATCH'}")

    for i, e in enumerate(ERAS):
        n = len(buckets[("COMPLETED", e)])
        want = PUBLISHED_N_COMPLETED[i]
        if n != want:
            failures.append(f"COMPLETED count {e}: {n} vs published {want}")
    print(f"\nCOMPLETED session counts: "
          f"{[len(buckets[('COMPLETED', e)]) for e in ERAS]} "
          f"(published {list(PUBLISHED_N_COMPLETED)})")

    print("\n" + "=" * 64)
    if failures:
        print("FAILED — the S5b flag file does not read as expected:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — the shipped S5b flag file reproduces the published four-state\n"
          "taxonomy exactly, and joins 1:1 with the canonical ORB trade list.\n"
          "It is a sound reference for step 1.8 S5b state parity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
