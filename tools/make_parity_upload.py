#!/usr/bin/env python3
"""Shrink the raw FirstRate NQ archives to an uploadable, parity-complete subset.

The full archives are ~125 MB because they carry all 23 trading hours. Every
rule in `spec/STRATEGY_SPEC_FROZEN.md` lives inside 09:30-16:00 New York:

  * the opening range is 09:30-09:44:59
  * the qualification window ends at the 10:30 bar
  * the S5b opening balance is 09:30-09:59 and the classifier runs from 10:00
  * both exits are the ORB stop and the RTH close

so discarding the overnight session is **lossless for parity**, not a sample.
This keeps 09:30:00-15:59:59 and drops everything else.

Rough sizes (zipped):
    2008-2026 RTH   ~30 MB      full canonical window
    2016-2026 RTH   ~18 MB      matches the reference CSVs, fits one upload
    2024-2026 RTH    ~5 MB      the modern era only

Run this on the machine that has the archives:

    python3 make_parity_upload.py --from 2016 \
        "NQ_1m_history_pre2023(1).zip" "NQ_1m_by_year_2023-2026(5).zip"

It writes `NQ_rth_1m_<from>_<to>.zip` next to itself and prints the final size.
Upload that. It needs only the Python standard library — no pandas, no installs.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import zipfile
from collections import defaultdict

RTH_START = (9, 30)
RTH_END = (16, 0)          # exclusive; the last kept minute opens 15:59


def in_rth(hhmm: str) -> bool:
    """hhmm is the HH:MM:SS field of the timestamp."""
    try:
        h = int(hhmm[0:2])
        m = int(hhmm[3:5])
    except (ValueError, IndexError):
        return False
    t = h * 60 + m
    return RTH_START[0] * 60 + RTH_START[1] <= t < RTH_END[0] * 60 + RTH_END[1]


def year_of(stamp: str) -> int | None:
    try:
        return int(stamp[0:4])
    except ValueError:
        return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archives", nargs="+", help="the FirstRate .zip files (or .csv)")
    ap.add_argument("--from", dest="year_from", type=int, default=2008)
    ap.add_argument("--to", dest="year_to", type=int, default=2026)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    out_name = a.out or f"NQ_rth_1m_{a.year_from}_{a.year_to}.zip"
    per_year: dict[int, list[str]] = defaultdict(list)
    seen: dict[int, set] = defaultdict(set)
    total_in = kept = 0

    def handle_stream(label: str, fh) -> None:
        nonlocal total_in, kept
        n_kept = 0
        for line in fh:
            total_in += 1
            # The FirstRate export is plain CSV with the timestamp first. Work on
            # raw text: it is much faster than csv parsing and preserves the
            # original formatting byte for byte.
            if len(line) < 19:
                continue
            stamp = line[:19]
            y = year_of(stamp)
            if y is None or not (a.year_from <= y <= a.year_to):
                continue
            if not in_rth(stamp[11:19]):
                continue
            if stamp in seen[y]:          # the 2023 file ships in both archives
                continue
            seen[y].add(stamp)
            per_year[y].append(line.rstrip("\r\n"))
            n_kept += 1
            kept += 1
        print(f"  {label}: kept {n_kept:,} RTH minutes")

    for path in a.archives:
        if not os.path.exists(path):
            print(f"!! not found: {path}", file=sys.stderr)
            return 2
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for member in sorted(zf.namelist()):
                    if not member.lower().endswith(".csv"):
                        continue
                    with io.TextIOWrapper(zf.open(member), encoding="utf-8",
                                          errors="replace") as fh:
                        handle_stream(f"{os.path.basename(path)}:{member}", fh)
        else:
            with open(path, encoding="utf-8", errors="replace") as fh:
                handle_stream(os.path.basename(path), fh)

    if not per_year:
        print("no rows matched — check the year range and the file layout",
              file=sys.stderr)
        return 1

    with zipfile.ZipFile(out_name, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for y in sorted(per_year):
            rows = sorted(per_year[y])          # timestamp-first sorts correctly
            z.writestr(f"NQ_rth_1m_{y}.csv", "\n".join(rows) + "\n")
            print(f"  {y}: {len(rows):,} minutes")

    size = os.path.getsize(out_name)
    print(f"\nread {total_in:,} rows, kept {kept:,} ({100.0*kept/max(total_in,1):.1f}%)")
    print(f"wrote {out_name}  —  {size/1e6:.1f} MB")
    if size > 30e6:
        print("\nStill over a 30 MB upload limit. Narrow the window, e.g.:")
        print(f"  python3 {os.path.basename(__file__)} --from 2016 <archives>")
        print("or send it in two halves:")
        print(f"  python3 {os.path.basename(__file__)} --from 2008 --to 2015 <archives>")
        print(f"  python3 {os.path.basename(__file__)} --from 2016 --to 2026 <archives>")
    else:
        print("Fits in one upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
