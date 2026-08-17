#!/usr/bin/env python3
"""Shrink 1-minute NQ bar files to regular trading hours and zip them.

Run this ON YOUR OWN MACHINE, on the folder holding the 2016-2022 data. It
does not need this repo, a network connection, or any package that is not in
a stock Python 3 install.

Why this exists: the full-session files are ~72% overnight bars, and the ORB
strategy never looks at a single one of them. The opening range is built from
09:30, the last decision is the 15:55 bar, and everything in between is
regular hours. Dropping the overnight session is not a compromise — those
rows are unused. It is what turns ~100 MB into one upload.

    python3 trim_bars_for_upload.py "C:\\Users\\you\\Desktop\\NQ history"

Writes NQ_rth_1m_2016_2022.zip next to the input folder. Upload that.

Input rows must look like:

    2024-01-02 09:30:00,16820.25,16834.5,16818.0,16831.75,4821

That is: `YYYY-MM-DD HH:MM:SS` in NEW YORK time on the bar's OPEN, then
open, high, low, close, volume. Header row optional — it is detected and
dropped. If your file is in UTC or has a different column order, say so
rather than converting it by guesswork; the wrong timezone silently shifts
every opening range by hours and the result looks plausible.
"""

from __future__ import annotations

import os
import sys
import zipfile

# 09:30 and 16:00 New York, as minutes past midnight. The final ORB bar opens
# at 15:55, so the envelope must extend to 16:00 to contain it.
RTH_OPEN = 9 * 60 + 30
RTH_CLOSE = 16 * 60

YEARS = range(2016, 2023)


def is_rth(ts: str) -> bool:
    """True if this bar OPENS inside regular hours."""
    try:
        hh, mm = ts[11:13], ts[14:16]
        minute = int(hh) * 60 + int(mm)
    except ValueError:
        return False
    return RTH_OPEN <= minute < RTH_CLOSE


def trim(path: str) -> tuple[list[str], dict]:
    """Return the regular-hours rows of one file, plus what was seen."""
    kept: list[str] = []
    stats = {"read": 0, "kept": 0, "years": set(), "bad": 0, "header": False}

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            stats["read"] += 1

            # A header has a non-digit where the year belongs.
            if lineno == 0 and not line[:1].isdigit():
                stats["header"] = True
                continue

            parts = line.split(",")
            if len(parts) < 6 or len(parts[0]) < 16:
                stats["bad"] += 1
                continue

            if is_rth(parts[0]):
                kept.append(line)
                stats["kept"] += 1
                stats["years"].add(parts[0][:4])

    return kept, stats


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    src = os.path.abspath(sys.argv[1])
    if not os.path.isdir(src):
        print(f"not a folder: {src}")
        return 2

    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".csv", ".txt")))
    if not files:
        print(f"no .csv or .txt files in {src}")
        return 1

    print(f"reading {len(files)} file(s) from {src}\n")

    by_year: dict[str, list[str]] = {}
    total_read = total_bad = 0

    for name in files:
        rows, st = trim(os.path.join(src, name))
        total_read += st["read"]
        total_bad += st["bad"]
        span = f"{min(st['years'])}-{max(st['years'])}" if st["years"] else "-"
        note = " (header dropped)" if st["header"] else ""
        if st["bad"]:
            note += f"  {st['bad']} unparsable row(s)"
        print(f"  {name:<40} {st['read']:>9,} rows -> "
              f"{st['kept']:>8,} RTH   {span}{note}")
        for row in rows:
            by_year.setdefault(row[:4], []).append(row)

    if not by_year:
        print("\nNo regular-hours rows found. Two likely causes:")
        print("  - the timestamps are UTC, not New York time")
        print("  - the columns are in a different order")
        print("Do not convert by guesswork — report what you have instead.")
        return 1

    # A bar can appear in more than one input file. Deduplicate on the
    # timestamp and sort, so the output is a clean single series per year.
    print()
    out = os.path.join(os.path.dirname(src), "NQ_rth_1m_2016_2022.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for year in sorted(by_year):
            seen: dict[str, str] = {}
            for row in by_year[year]:
                seen[row.split(",", 1)[0]] = row
            rows = [seen[k] for k in sorted(seen)]
            sessions = len({r[:10] for r in rows})
            dropped = len(by_year[year]) - len(rows)
            flag = "" if year in map(str, YEARS) else "   <- outside 2016-2022"
            dup = f"   {dropped:,} duplicate(s) removed" if dropped else ""
            print(f"  {year}: {len(rows):>8,} bars   {sessions:>3} sessions"
                  f"{dup}{flag}")
            z.writestr(f"NQ_rth_1m_{year}.csv", "\n".join(rows) + "\n")

    missing = [str(y) for y in YEARS if str(y) not in by_year]
    size = os.path.getsize(out)
    print(f"\nwrote {out}  ({size / 1e6:.1f} MB)")
    if total_bad:
        print(f"note: {total_bad:,} of {total_read:,} input rows were "
              f"unparsable and skipped")
    if missing:
        print(f"WARNING: no data for {', '.join(missing)} — "
              f"the three-era test needs 2016-2022 complete")

    print("\nUpload that zip, or commit it to the repo. Nothing else needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
