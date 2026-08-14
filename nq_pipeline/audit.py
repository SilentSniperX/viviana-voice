"""Data-quality audit for NQ 1-minute continuous futures CSVs.

Implements every check required by the dataset specification:
per-file integrity checks, Excel-truncation detection, session-envelope
checks, adjusted-vs-unadjusted comparison, and adjustment-step detection.

All timestamps are assumed to be bar-OPEN times in America/New_York wall
time, formatted "YYYY-MM-DD HH:MM:SS" (no offset suffix).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

EXCEL_ROW_LIMIT = 1_048_576
SCHEMA = ["timestamp", "open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Session envelope (Eastern wall time)
#
# Expected NQ Globex trading minutes, per spec:
#   Sunday        18:00-23:59
#   Monday-Thursday  00:00-16:59 and 18:00-23:59  (17:00-17:59 = maintenance)
#   Friday        00:00-16:59
#   Saturday      none
#
# Bars inside 17:00-17:59 Mon-Fri and Saturday bars are reported as their own
# categories; "outside expected hours" counts everything else that falls
# outside the envelope (e.g. Sunday before 18:00, Friday after 17:00).
# Holiday early closes REDUCE bar counts and are never flagged; only bars
# PRESENT outside the envelope are flagged.
# ---------------------------------------------------------------------------

def classify_sessions(ts: pd.Series) -> dict:
    """ts: naive datetime Series representing ET wall time (bar opens)."""
    dow = ts.dt.dayofweek  # Mon=0 .. Sun=6
    hour = ts.dt.hour

    saturday = dow == 5
    maint = (hour == 17) & (dow <= 4)          # Mon-Fri 17:00-17:59
    sunday_early = (dow == 6) & (hour < 18)     # Sunday before 18:00
    friday_late = (dow == 4) & (hour >= 18)     # Friday evening (no session)

    outside_other = (sunday_early | friday_late) & ~saturday & ~maint
    return {
        "saturday_bars": int(saturday.sum()),
        "bars_1700_1759": int(maint.sum()),
        "bars_outside_expected_hours": int(outside_other.sum()),
    }


@dataclass
class FileAudit:
    filename: str
    row_count: int = 0
    first_timestamp: str = ""
    last_timestamp: str = ""
    duplicate_timestamps: int = 0
    null_ohlc_rows: int = 0
    invalid_ohlc_rows: int = 0
    min_price: float = float("nan")
    max_price: float = float("nan")
    volume_present: bool = False
    saturday_bars: int = 0
    bars_1700_1759: int = 0
    bars_outside_expected_hours: int = 0
    excel_row_limit_hit: bool = False
    warnings: list = field(default_factory=list)


def load_series_csv(path: str) -> pd.DataFrame:
    """Load one dataset CSV without altering any values."""
    df = pd.read_csv(
        path,
        dtype={"open": "float64", "high": "float64", "low": "float64",
               "close": "float64", "volume": "float64"},
    )
    if list(df.columns) != SCHEMA:
        raise ValueError(f"{path}: columns {list(df.columns)} != {SCHEMA}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S")
    return df


def audit_file(path: str) -> FileAudit:
    a = FileAudit(filename=os.path.basename(path))
    df = load_series_csv(path)

    a.row_count = len(df)
    a.excel_row_limit_hit = a.row_count == EXCEL_ROW_LIMIT
    if a.row_count == 0:
        a.warnings.append("file is empty")
        return a

    ts = df["timestamp"]
    a.first_timestamp = ts.iloc[0].strftime("%Y-%m-%d %H:%M:%S")
    a.last_timestamp = ts.iloc[-1].strftime("%Y-%m-%d %H:%M:%S")
    a.duplicate_timestamps = int(ts.duplicated().sum())
    if not ts.is_monotonic_increasing:
        a.warnings.append("timestamps are not sorted ascending")

    ohlc = df[["open", "high", "low", "close"]]
    null_mask = ohlc.isna().any(axis=1)
    a.null_ohlc_rows = int(null_mask.sum())

    v = df.loc[~null_mask]
    invalid = (
        (v["high"] < v["open"]) | (v["high"] < v["close"]) | (v["high"] < v["low"])
        | (v["low"] > v["open"]) | (v["low"] > v["close"]) | (v["low"] > v["high"])
    )
    a.invalid_ohlc_rows = int(invalid.sum())

    a.min_price = float(v["low"].min())
    a.max_price = float(v["high"].max())
    a.volume_present = bool("volume" in df.columns and df["volume"].notna().any())

    a.__dict__.update(classify_sessions(ts))

    # Mid-session truncation heuristics for an annual file.
    year = ts.iloc[0].year
    first, last = ts.iloc[0], ts.iloc[-1]
    if first > pd.Timestamp(year=year, month=1, day=5):
        a.warnings.append(f"year starts late: {a.first_timestamp}")
    if last < pd.Timestamp(year=year, month=12, day=28):
        a.warnings.append(f"year ends early: {a.last_timestamp}")
    # A clean year-end lands at a session close (16:xx-17:00 ET) or at the
    # 23:59 bar of Dec 31 when Jan 1 trading continues past midnight.
    if last.month == 12 and last.day == 31:
        t = last.time()
        if not (t >= pd.Timestamp("1900-01-01 16:00").time() or last.dayofweek >= 4):
            a.warnings.append(
                f"last bar {a.last_timestamp} is mid-session (possible truncation)")
    return a


# ---------------------------------------------------------------------------
# Adjusted vs unadjusted comparison
# ---------------------------------------------------------------------------

def compare_adj_unadj(adj_path: str, unadj_path: str, step_tolerance: float = 1.0) -> dict:
    """Compare timestamp coverage and detect adjustment offset steps.

    step_tolerance: minimum change in (adj_close - unadj_close), in index
    points, treated as a genuine adjustment step (roll) rather than noise.
    """
    adj = load_series_csv(adj_path).set_index("timestamp")
    una = load_series_csv(unadj_path).set_index("timestamp")

    only_adj = adj.index.difference(una.index)
    only_una = una.index.difference(adj.index)
    common = adj.index.intersection(una.index)

    offset = (adj.loc[common, "close"] - una.loc[common, "close"]).sort_index()
    d = offset.diff().abs()
    step_mask = d > step_tolerance
    steps = [
        {"timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
         "offset_change": round(float(offset.diff().loc[t]), 4),
         "new_offset": round(float(offset.loc[t]), 4)}
        for t in offset.index[step_mask]
    ]
    return {
        "adjusted_file": os.path.basename(adj_path),
        "unadjusted_file": os.path.basename(unadj_path),
        "adjusted_rows": len(adj),
        "unadjusted_rows": len(una),
        "timestamps_only_in_adjusted": len(only_adj),
        "timestamps_only_in_unadjusted": len(only_una),
        "adjustment_steps_detected": len(steps),
        "adjustment_steps": steps,
    }


# ---------------------------------------------------------------------------
# ZIP build + validation
# ---------------------------------------------------------------------------

def build_zip(zip_path: str, csv_paths: list) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for p in csv_paths:
            z.write(p, arcname=os.path.basename(p))


def validate_zip(zip_path: str, expected_names: list) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt member in zip: {bad}")
        names = sorted(z.namelist())
        missing = sorted(set(expected_names) - set(names))
        extra = sorted(set(names) - set(expected_names))
        member_rows = {}
        for name in names:
            with z.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
                header = next(reader)
                if header != SCHEMA:
                    raise RuntimeError(f"{name}: bad header {header}")
                member_rows[name] = sum(1 for _ in reader)

    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    size = os.path.getsize(zip_path)
    return {
        "zip": os.path.basename(zip_path),
        "members": names,
        "missing_members": missing,
        "unexpected_members": extra,
        "member_data_rows": member_rows,
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "sha256": h.hexdigest(),
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(meta: dict, file_audits: list, comparisons: list, zip_info: dict) -> str:
    lines = ["# NQ 1-minute dataset audit report", ""]
    for k in ("SOURCE / VENDOR", "DATA RANGE", "TIMEZONE",
              "BAR TIMESTAMP CONVENTION", "ROLL METHOD", "ADJUSTMENT METHOD"):
        lines.append(f"**{k}:** {meta.get(k, 'UNKNOWN')}  ")
    lines += ["", "## Per-file audit", ""]
    hdr = ("| File | Rows | First TS | Last TS | Dups | Null OHLC | Invalid OHLC "
           "| Min px | Max px | Vol | Sat bars | 17:00-17:59 | Outside hrs "
           "| =1,048,576? | Warnings |")
    lines += [hdr, "|" + "---|" * 15]
    for a in file_audits:
        lines.append(
            f"| {a.filename} | {a.row_count:,} | {a.first_timestamp} | {a.last_timestamp} "
            f"| {a.duplicate_timestamps} | {a.null_ohlc_rows} | {a.invalid_ohlc_rows} "
            f"| {a.min_price} | {a.max_price} | {'yes' if a.volume_present else 'NO'} "
            f"| {a.saturday_bars} | {a.bars_1700_1759} | {a.bars_outside_expected_hours} "
            f"| {'**FLAG**' if a.excel_row_limit_hit else 'no'} "
            f"| {'; '.join(a.warnings) if a.warnings else '-'} |")

    lines += ["", "## Adjusted vs unadjusted", ""]
    lines += ["| Year file pair | Adj rows | Unadj rows | Only in adj | Only in unadj | Steps |",
              "|---|---|---|---|---|---|"]
    all_steps = []
    for c in comparisons:
        lines.append(
            f"| {c['adjusted_file']} / {c['unadjusted_file']} | {c['adjusted_rows']:,} "
            f"| {c['unadjusted_rows']:,} | {c['timestamps_only_in_adjusted']} "
            f"| {c['timestamps_only_in_unadjusted']} | {c['adjustment_steps_detected']} |")
        all_steps.extend(c["adjustment_steps"])
    lines += ["", "### Adjustment-step (suspected roll) timestamps", ""]
    if all_steps:
        lines += ["| Timestamp (ET) | Offset change | New offset |", "|---|---|---|"]
        for s in all_steps:
            lines.append(f"| {s['timestamp']} | {s['offset_change']} | {s['new_offset']} |")
    else:
        lines.append("None detected.")

    lines += ["", "## ZIP validation", "",
              f"- **File:** {zip_info['zip']}",
              f"- **Size:** {zip_info['size_bytes']:,} bytes ({zip_info['size_mb']} MB)",
              f"- **SHA-256:** `{zip_info['sha256']}`",
              f"- **Members present:** {len(zip_info['members'])}/10"
              f" (missing: {zip_info['missing_members'] or 'none'},"
              f" unexpected: {zip_info['unexpected_members'] or 'none'})", ""]
    return "\n".join(lines)


def audits_to_json(meta, file_audits, comparisons, zip_info) -> str:
    return json.dumps({
        "meta": meta,
        "files": [asdict(a) for a in file_audits],
        "comparisons": comparisons,
        "zip": zip_info,
    }, indent=2, default=str)
