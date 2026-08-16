#!/usr/bin/env python3
"""Phase 1 steps 1.7 + 1.8 — clean-room parity run against the raw FirstRate data.

Reads the raw 1-minute NQ archives, runs the frozen-spec engine
(`tests/reference_engine.py`) over every session, and grades the result against
`reference/canonical_orb_trades.csv` and the validated S5b references.

This is the run that closes "PYTHON REFERENCE = PINE LOGIC": the engine here is
the twin of `pine/nq_orb_s5b_v1.pine`, clause for clause. It does not touch
TradingView — that third leg needs a chart export
(`docs/PARITY_PROCEDURE.md` §2-§3).

Input — any of these, placed in `data/raw/` (git-ignored):
  * `NQ_1m_history_pre2023(1).zip`   and/or  `NQ_1m_by_year_2023-2026(5).zip`
  * any other .zip containing per-year CSVs
  * loose .csv files

CSV layout is the FirstRate export the research used: headerless
`datetime,open,high,low,close,volume`, one row per minute. A header row is
tolerated. Files are streamed and processed year by year, so memory stays flat.

Usage:
    python3 tests/run_full_parity.py                     # everything found
    python3 tests/run_full_parity.py --from 2016         # restrict the window
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reference_engine import (  # noqa: E402
    Bar, S5B_COLUMNS, TRADE_COLUMNS, _parse_ts, in_rth, orb_day, s5b_day, to_5m,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(REPO, "data", "raw")
OUT_DIR = os.path.join(REPO, "tests", "_parity_run")


# ---------------------------------------------------------------------------
# Raw ingestion
# ---------------------------------------------------------------------------

def iter_source_files(raw_dir: str):
    """Yield (label, byte-stream-opener) for every CSV inside raw_dir."""
    if not os.path.isdir(raw_dir):
        raise SystemExit(f"no raw data directory: {raw_dir}\n"
                         f"Place the FirstRate archives there (git-ignored) and "
                         f"re-run. See docs/PARITY_PROCEDURE.md section 4.")
    entries = sorted(os.listdir(raw_dir))
    found = False
    for name in entries:
        path = os.path.join(raw_dir, name)
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for member in sorted(zf.namelist()):
                    if member.lower().endswith(".csv"):
                        found = True
                        yield f"{name}:{member}", (path, member)
        elif name.lower().endswith(".csv"):
            found = True
            yield name, (path, None)
    if not found:
        raise SystemExit(f"{raw_dir} contains no .csv or .zip files")


def open_source(handle) -> io.TextIOWrapper:
    path, member = handle
    if member is None:
        return open(path, newline="")
    zf = zipfile.ZipFile(path)
    return io.TextIOWrapper(zf.open(member), encoding="utf-8", newline="")


def load_rth_by_year(raw_dir: str, year_from: int, year_to: int) -> dict:
    """Stream every source file, keep RTH minutes only, bucket by (year, date).

    Duplicate timestamps are dropped, keeping the first occurrence — the same
    `drop_duplicates('dt')` the research loaders apply where the 2023 file
    appears in both archives.
    """
    by_year: dict[int, dict] = defaultdict(dict)   # year -> date -> {ts: Bar}
    stats = Counter()
    for label, handle in iter_source_files(raw_dir):
        rows_kept = 0
        with open_source(handle) as fh:
            for row in csv.reader(fh):
                if len(row) < 6:
                    continue
                try:
                    ts = _parse_ts(row[0])
                    bar = Bar(ts, float(row[1]), float(row[2]), float(row[3]),
                              float(row[4]), float(row[5]))
                except (ValueError, IndexError):
                    continue                      # header or malformed line
                if not (year_from <= ts.year <= year_to):
                    continue
                if not in_rth(bar):
                    continue
                day = by_year[ts.year].setdefault(ts.date(), {})
                if ts in day:
                    stats["duplicate_minutes_dropped"] += 1
                    continue
                day[ts] = bar
                rows_kept += 1
        stats["files"] += 1
        print(f"  {label}: {rows_kept:,} RTH minutes")
    return by_year, stats


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--from", dest="year_from", type=int, default=2008)
    ap.add_argument("--to", dest="year_to", type=int, default=2026)
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args(argv)

    os.makedirs(a.out_dir, exist_ok=True)
    print(f"reading raw 1-minute data from {a.raw_dir}")
    by_year, stats = load_rth_by_year(a.raw_dir, a.year_from, a.year_to)
    if not by_year:
        raise SystemExit("no bars in the requested window")

    trades, states = [], []
    session_count = 0
    for year in sorted(by_year):
        days = by_year[year]
        for date in sorted(days):
            minute_bars = [days[date][t] for t in sorted(days[date])]
            five = to_5m(minute_bars)
            session_count += 1
            t = orb_day(five, minute_bars)     # exact research guards on 1m data
            if t:
                trades.append(t)
            s = s5b_day(five)
            s["date"] = str(date)
            states.append(s)
        print(f"  {year}: {len(days)} sessions, {sum(1 for t in trades if t['date'][:4] == str(year))} trades")

    trades_path = os.path.join(a.out_dir, "engine_orb_trades.csv")
    states_path = os.path.join(a.out_dir, "engine_s5b_states.csv")
    with open(trades_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
    with open(states_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=S5B_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(states)

    print(f"\nsessions processed : {session_count}")
    print(f"ORB trades         : {len(trades)}  -> {trades_path}")
    print(f"S5b timelines      : {len(states)}  -> {states_path}")
    print(f"duplicate minutes  : {stats['duplicate_minutes_dropped']:,}")

    print("\n--- ORB trade-list parity ---")
    import parity_orb
    rc_orb = parity_orb.main([
        "--candidate", trades_path, "--auto-window",
        "--report", os.path.join(REPO, "tests", "parity_report_orb_clean_room.md")])

    print("\n--- S5b state parity ---")
    import parity_s5b
    rc_s5b = parity_s5b.main([
        "--candidate", states_path,
        "--report", os.path.join(REPO, "tests", "parity_report_s5b_clean_room.md")])

    print("\n" + "=" * 66)
    print(f"ORB parity exit code {rc_orb}, S5b parity exit code {rc_s5b}")
    print("Non-zero means unresolved mismatches — read the reports before "
          "touching the Pine.")
    return rc_orb or rc_s5b


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
