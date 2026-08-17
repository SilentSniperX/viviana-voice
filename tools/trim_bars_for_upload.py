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

Point it at a FOLDER. Loose .csv/.txt files and .zip archives are both read --
you do not need to unzip anything first. Writes NQ_rth_1m_2016_2022.zip next
to that folder. Upload that.

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

OUT_NAME = "NQ_rth_1m_2016_2022.zip"


def is_rth(ts: str) -> bool:
    """True if this bar OPENS inside regular hours."""
    try:
        hh, mm = ts[11:13], ts[14:16]
        minute = int(hh) * 60 + int(mm)
    except ValueError:
        return False
    return RTH_OPEN <= minute < RTH_CLOSE


def trim_lines(lines, stats: dict) -> list[str]:
    """Keep the regular-hours rows out of an iterable of text lines."""
    kept: list[str] = []
    for lineno, line in enumerate(lines):
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

    return kept


def new_stats() -> dict:
    return {"read": 0, "kept": 0, "years": set(), "bad": 0, "header": False}


def is_unadjusted(name: str) -> bool:
    """True for the raw, non-back-adjusted series.

    These MUST be excluded. A folder typically holds both `NQ_adj_1m_2024.csv`
    and `NQ_unadj_1m_2024.csv`: identical timestamps, different prices, because
    one has the contract-roll gaps removed and the other has not. Reading both
    would give two conflicting rows per minute, and since rows are deduplicated
    on timestamp, whichever happened to be read last would silently win --
    producing a price series that is neither one thing nor the other. The
    strategy is measured on the adjusted series, so that is the one to keep.
    """
    return "unadj" in os.path.basename(name).lower()


def read_source(path: str):
    """Yield (label, rows, stats) for a data file.

    A .zip is opened in place and every .csv/.txt member inside it is read, so
    the folder can hold the archives exactly as they were downloaded. Anything
    else is read as a plain text file.
    """
    name = os.path.basename(path)

    if is_unadjusted(name):
        stats = new_stats()
        stats["error"] = "unadjusted series — skipped, the adjusted one is used"
        yield name, [], stats
        return

    if path.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            stats = new_stats()
            stats["error"] = "not a readable zip"
            yield name, [], stats
            return
        with zf:
            members = [m for m in zf.namelist()
                       if m.lower().endswith((".csv", ".txt"))
                       and not m.startswith("__MACOSX")]
            if not members:
                stats = new_stats()
                stats["error"] = "zip contains no .csv or .txt"
                yield name, [], stats
                return
            for member in sorted(members):
                if is_unadjusted(member):
                    st = new_stats()
                    st["error"] = ("unadjusted series — skipped, "
                                   "the adjusted one is used")
                    yield f"{name} > {os.path.basename(member)}", [], st
                    continue
                stats = new_stats()
                with zf.open(member) as fh:
                    text = fh.read().decode("utf-8", errors="replace")
                rows = trim_lines(text.splitlines(), stats)
                yield f"{name} > {os.path.basename(member)}", rows, stats
        return

    stats = new_stats()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        rows = trim_lines(fh, stats)
    yield name, rows, stats


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    src = os.path.abspath(sys.argv[1])
    if not os.path.isdir(src):
        print(f"not a folder: {src}")
        return 2

    # Never re-read our own output if it was written beside the inputs.
    files = sorted(f for f in os.listdir(src)
                   if f.lower().endswith((".csv", ".txt", ".zip"))
                   and f != OUT_NAME)
    if not files:
        print(f"no .csv, .txt or .zip files in {src}")
        return 1

    print(f"reading {len(files)} file(s) from {src}\n")

    by_year: dict[str, list[str]] = {}
    total_read = total_bad = 0

    for name in files:
        for label, rows, st in read_source(os.path.join(src, name)):
            if st.get("error"):
                print(f"  {label:<44} SKIPPED — {st['error']}")
                continue
            total_read += st["read"]
            total_bad += st["bad"]
            span = (f"{min(st['years'])}-{max(st['years'])}"
                    if st["years"] else "-")
            note = " (header dropped)" if st["header"] else ""
            if st["bad"]:
                note += f"  {st['bad']} unparsable row(s)"
            print(f"  {label:<44} {st['read']:>9,} rows -> "
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
    out = os.path.join(os.path.dirname(src), OUT_NAME)
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
