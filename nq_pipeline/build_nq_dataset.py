#!/usr/bin/env python3
"""Build the NQ 1-minute continuous dataset (adjusted + unadjusted), 2016-2020.

Produces exactly the deliverable defined in the dataset specification:

    NQ_1m_by_year_2016-2020.zip
      NQ_adj_1m_2016.csv ... NQ_adj_1m_2020.csv
      NQ_unadj_1m_2016.csv ... NQ_unadj_1m_2020.csv

Schema (all 10 files):  timestamp,open,high,low,close,volume
Timestamps: bar OPEN times, America/New_York wall time, YYYY-MM-DD HH:MM:SS.

METHODOLOGY
-----------
Roll rule (volume-based, identical for adjusted and unadjusted):
    For each CME quarterly NQ contract (H/M/U/Z, expiring 9:30 ET on the 3rd
    Friday of the contract month), the front contract rolls to the next
    quarterly at the first 18:00 ET session open AFTER the first full Globex
    session (trade date) on which the next contract's total volume exceeds
    the current front contract's total volume. The roll timestamp is
    therefore known precisely: it is that 18:00 ET session-open bar.
    Both series use the exact same roll timestamps by construction.

Adjustment (difference / back-adjustment, "Panama" style):
    At each roll the per-minute close spread (incoming - outgoing) is
    measured over the last `--overlap-minutes` minutes both contracts
    traded before the roll timestamp; the offset is the MEDIAN spread.
    Offsets are cumulatively ADDED to all bars BEFORE each roll, so the most
    recent prices are unchanged and no artificial jump exists at any roll.
    The unadjusted series is the identical stitch with no offsets applied,
    so rolls appear there as genuine price gaps.

No bars are invented, interpolated, forward-filled, smoothed, or deleted.
The only price modification anywhere is the explicit back-adjustment offset
applied to the adjusted series.

DATA SOURCES
------------
  --source databento   Fetch per-contract 1-min OHLCV from Databento
                       GLBX.MDP3 (CME Globex MDP 3.0). Requires network
                       access to hist.databento.com and DATABENTO_API_KEY.
                       Databento ts_event is the bar-open time in UTC; it is
                       converted timezone-aware (DST-correct) to
                       America/New_York.
  --source local       Read per-contract CSVs the user supplies in --raw-dir
                       (one file per contract: NQH2016.csv ... NQH2021.csv,
                       schema timestamp,open,high,low,close,volume).
                       --input-tz declares the raw timestamp zone
                       (UTC or America/New_York).

Usage:
    python build_nq_dataset.py --source local --raw-dir raw/ --input-tz UTC \
        --out-dir out/ [--start 2016 --end 2020]
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit as audit_mod  # noqa: E402

ET = "America/New_York"
QUARTER_MONTHS = {"H": 3, "M": 6, "U": 9, "Z": 12}
MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}


# ---------------------------------------------------------------------------
# Contract calendar
# ---------------------------------------------------------------------------

def third_friday(year: int, month: int) -> pd.Timestamp:
    d = pd.Timestamp(year=year, month=month, day=1)
    fridays = pd.date_range(d, d + pd.offsets.MonthEnd(0), freq="W-FRI")
    return fridays[2]


def quarterly_contracts(start_year: int, end_year: int) -> list:
    """Contracts needed to cover Jan `start_year` .. Dec `end_year`.

    Includes the front contract at the start (the H contract of start_year)
    and the post-December roll target (H of end_year+1).
    """
    out = []
    for y in range(start_year, end_year + 2):
        for m in (3, 6, 9, 12):
            out.append(f"NQ{MONTH_CODE[m]}{y}")
    # trim: nothing after H of end_year+1
    return [c for c in out if (int(c[3:]), QUARTER_MONTHS[c[2]])
            <= (end_year + 1, 3)]


def contract_expiry(symbol: str) -> pd.Timestamp:
    code, year = symbol[2], int(symbol[3:])
    return third_friday(year, QUARTER_MONTHS[code])


# ---------------------------------------------------------------------------
# Loading per-contract bars
# ---------------------------------------------------------------------------

def load_contract_csv(path: str, input_tz: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if list(df.columns) != audit_mod.SCHEMA:
        raise ValueError(f"{path}: columns must be {audit_mod.SCHEMA}")
    ts = pd.to_datetime(df["timestamp"])
    if input_tz.upper() == "UTC":
        ts = ts.dt.tz_localize("UTC").dt.tz_convert(ET)
    else:
        ts = ts.dt.tz_localize(ET)
    df["timestamp"] = ts
    df = df.sort_values("timestamp").reset_index(drop=True)
    if df["timestamp"].duplicated().any():
        raise ValueError(f"{path}: duplicate timestamps in raw contract data")
    return df


def fetch_databento_contracts(symbols, start, end, raw_dir):
    """Download per-contract ohlcv-1m from Databento GLBX.MDP3 into raw_dir.

    ts_event on ohlcv-1m is the bar OPEN time (UTC). Stored as UTC CSVs;
    the build step converts to America/New_York.
    """
    try:
        import databento as db
    except ImportError:
        sys.exit("databento package not installed: pip install databento")
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("DATABENTO_API_KEY is not set")
    client = db.Historical(key)
    os.makedirs(raw_dir, exist_ok=True)
    for sym in symbols:
        dest = os.path.join(raw_dir, f"{sym}.csv")
        if os.path.exists(dest):
            print(f"  {sym}: already fetched")
            continue
        print(f"  fetching {sym} ...")
        data = client.timeseries.get_range(
            dataset="GLBX.MDP3", schema="ohlcv-1m",
            symbols=[sym], stype_in="raw_symbol",
            start=start, end=end,
        )
        df = data.to_df().reset_index()
        df = df.rename(columns={"ts_event": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.strftime(
            "%Y-%m-%d %H:%M:%S")
        df[audit_mod.SCHEMA].to_csv(dest, index=False)


# ---------------------------------------------------------------------------
# Volume-based roll schedule
# ---------------------------------------------------------------------------

def trade_date(ts: pd.Series) -> pd.Series:
    """CME trade date: sessions open 18:00 ET and belong to the NEXT day."""
    naive = ts.dt.tz_localize(None)
    return (naive + pd.Timedelta(hours=6)).dt.normalize()


def compute_roll_schedule(contracts: dict, order: list) -> list:
    """Return [(roll_ts_et, old_sym, new_sym), ...].

    Roll at the first 18:00 ET session open after the first trade date on
    which next-contract session volume exceeds front-contract session volume
    (evaluated only within ~3 weeks of front expiry so early anomalies are
    ignored), falling back to the session open 8 calendar days before expiry
    if volume never crosses (defensive; should not trigger on real data).
    """
    rolls = []
    for old_sym, new_sym in zip(order, order[1:]):
        old, new = contracts[old_sym], contracts[new_sym]
        expiry = contract_expiry(old_sym)
        window_start = expiry - pd.Timedelta(days=21)

        ov = old.assign(td=trade_date(old["timestamp"])).groupby("td")["volume"].sum()
        nv = new.assign(td=trade_date(new["timestamp"])).groupby("td")["volume"].sum()
        both = pd.DataFrame({"old": ov, "new": nv}).dropna()
        both = both[(both.index >= window_start) & (both.index <= expiry)]

        crossed = both.index[both["new"] > both["old"]]
        roll_td = crossed[0] if len(crossed) else (expiry - pd.Timedelta(days=8)).normalize()
        # effective at the 18:00 ET open of the session AFTER roll_td
        roll_ts = (pd.Timestamp(roll_td) + pd.Timedelta(hours=18)).tz_localize(ET)
        # snap forward to the first bar the new contract actually has
        nxt = new.loc[new["timestamp"] >= roll_ts, "timestamp"]
        if nxt.empty:
            raise RuntimeError(f"{new_sym}: no data at/after roll {roll_ts}")
        rolls.append((nxt.iloc[0], old_sym, new_sym))
    return rolls


# ---------------------------------------------------------------------------
# Stitch + back-adjust
# ---------------------------------------------------------------------------

def stitch_unadjusted(contracts, order, rolls, start_ts, end_ts):
    """Concatenate front-contract segments; also return per-segment symbol."""
    bounds = [start_ts] + [r[0] for r in rolls] + [end_ts]
    parts = []
    for i, sym in enumerate(order):
        seg = contracts[sym]
        seg = seg[(seg["timestamp"] >= bounds[i]) & (seg["timestamp"] < bounds[i + 1])]
        if not seg.empty:
            parts.append(seg.assign(contract=sym))
    df = pd.concat(parts, ignore_index=True).sort_values("timestamp").reset_index(drop=True)
    if df["timestamp"].duplicated().any():
        raise RuntimeError("duplicate timestamps after stitching")
    return df


def compute_roll_offsets(contracts, rolls, overlap_minutes: int):
    """Median close spread (new - old) over the last overlap window."""
    offsets = []
    for roll_ts, old_sym, new_sym in rolls:
        old = contracts[old_sym].set_index("timestamp")["close"]
        new = contracts[new_sym].set_index("timestamp")["close"]
        common = old.index.intersection(new.index)
        common = common[common < roll_ts][-overlap_minutes:]
        if len(common) < 10:
            raise RuntimeError(
                f"roll {old_sym}->{new_sym}: only {len(common)} overlapping "
                f"minutes before {roll_ts}; cannot measure spread")
        spread = float((new.loc[common] - old.loc[common]).median())
        offsets.append({"roll_ts": roll_ts, "old": old_sym, "new": new_sym,
                        "offset": spread, "overlap_minutes": len(common)})
    return offsets


def apply_back_adjustment(unadj: pd.DataFrame, offsets: list) -> pd.DataFrame:
    """Difference back-adjustment: bars before roll k gain sum(offsets k..K)."""
    adj = unadj.copy()
    ordered = sorted(offsets, key=lambda x: x["roll_ts"])  # oldest -> newest
    ts = adj["timestamp"]
    total = np.zeros(len(adj))
    cum = 0.0
    for o in reversed(ordered):            # newest roll first
        cum += o["offset"]
        total[(ts < o["roll_ts"]).to_numpy()] = cum
    adj[["open", "high", "low", "close"]] = (
        adj[["open", "high", "low", "close"]].to_numpy() + total[:, None])
    return adj


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_year_csvs(df: pd.DataFrame, prefix: str, out_dir: str, years) -> list:
    paths = []
    naive = df["timestamp"].dt.tz_localize(None)
    out = df.copy()
    out["timestamp"] = naive.dt.strftime("%Y-%m-%d %H:%M:%S")
    out["volume"] = out["volume"].astype("int64")
    for y in years:
        sel = out[naive.dt.year == y]
        p = os.path.join(out_dir, f"{prefix}_{y}.csv")
        sel[audit_mod.SCHEMA].to_csv(p, index=False, float_format="%.2f")
        paths.append(p)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=["databento", "local"], required=True)
    ap.add_argument("--raw-dir", default="raw")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--input-tz", default="UTC",
                    choices=["UTC", "America/New_York"],
                    help="timezone of raw per-contract CSV timestamps")
    ap.add_argument("--start", type=int, default=2016)
    ap.add_argument("--end", type=int, default=2020)
    ap.add_argument("--overlap-minutes", type=int, default=120)
    ap.add_argument("--step-tolerance", type=float, default=1.0,
                    help="min offset change (points) counted as a roll step")
    ap.add_argument("--vendor-label", default=None,
                    help="source/vendor string recorded in the audit report")
    args = ap.parse_args()

    years = list(range(args.start, args.end + 1))
    order = quarterly_contracts(args.start, args.end)
    os.makedirs(args.out_dir, exist_ok=True)

    if args.source == "databento":
        fetch_databento_contracts(
            order,
            start=f"{args.start - 1}-12-01",
            end=f"{args.end + 1}-01-05",
            raw_dir=args.raw_dir)
        args.input_tz = "UTC"
        vendor = args.vendor_label or "Databento GLBX.MDP3 (CME Globex MDP 3.0), per-contract ohlcv-1m"
    else:
        vendor = args.vendor_label or f"user-supplied per-contract CSVs in {args.raw_dir}/"

    print("loading contracts ...")
    contracts = {}
    for sym in order:
        p = os.path.join(args.raw_dir, f"{sym}.csv")
        if not os.path.exists(p):
            sys.exit(f"missing raw contract file: {p}")
        contracts[sym] = load_contract_csv(p, args.input_tz)
        print(f"  {sym}: {len(contracts[sym]):,} bars")

    start_ts = pd.Timestamp(f"{args.start}-01-01 00:00").tz_localize(ET)
    end_ts = pd.Timestamp(f"{args.end}-12-31 23:59").tz_localize(ET) + pd.Timedelta(minutes=1)

    print("computing volume-based roll schedule ...")
    rolls = compute_roll_schedule(contracts, order)
    rolls = [r for r in rolls if start_ts <= r[0] < end_ts]
    for r in rolls:
        print(f"  roll {r[1]} -> {r[2]} at {r[0]}")

    print("stitching unadjusted continuous ...")
    unadj = stitch_unadjusted(contracts, order, rolls, start_ts, end_ts)

    print("measuring roll spreads ...")
    offsets = compute_roll_offsets(contracts, rolls, args.overlap_minutes)
    for o in offsets:
        print(f"  {o['old']}->{o['new']} @ {o['roll_ts']}: "
              f"offset {o['offset']:+.2f} ({o['overlap_minutes']} overlap min)")

    print("applying difference back-adjustment ...")
    adj = apply_back_adjustment(unadj, offsets)

    print("writing per-year CSVs ...")
    unadj_paths = write_year_csvs(unadj.drop(columns="contract"), "NQ_unadj_1m",
                                  args.out_dir, years)
    adj_paths = write_year_csvs(adj.drop(columns="contract"), "NQ_adj_1m",
                                args.out_dir, years)

    pd.DataFrame(offsets).assign(
        roll_ts=lambda d: d["roll_ts"].dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    ).to_csv(os.path.join(args.out_dir, "roll_schedule.csv"), index=False)

    print("auditing ...")
    file_audits = [audit_mod.audit_file(p) for p in adj_paths + unadj_paths]
    comparisons = [audit_mod.compare_adj_unadj(a, u, args.step_tolerance)
                   for a, u in zip(adj_paths, unadj_paths)]

    zip_path = os.path.join(args.out_dir, f"NQ_1m_by_year_{args.start}-{args.end}.zip")
    print("zipping + validating ...")
    audit_mod.build_zip(zip_path, adj_paths + unadj_paths)
    expected = [os.path.basename(p) for p in adj_paths + unadj_paths]
    zip_info = audit_mod.validate_zip(zip_path, expected)

    meta = {
        "SOURCE / VENDOR": vendor,
        "DATA RANGE": f"{args.start}-01-01 .. {args.end}-12-31 (ET)",
        "TIMEZONE": "America/New_York (DST-aware conversion from raw tz: "
                    + args.input_tz + ")",
        "BAR TIMESTAMP CONVENTION": "bar OPEN time",
        "ROLL METHOD": "volume-based: roll to next quarterly at the first "
                       "18:00 ET session open after next-contract session "
                       "volume first exceeds front-contract volume (within 21 "
                       "days of expiry); identical roll timestamps for "
                       "adjusted and unadjusted",
        "ADJUSTMENT METHOD": "difference back-adjustment (Panama): median "
                             f"close spread over last {args.overlap_minutes} "
                             "overlapping minutes before each roll, "
                             "cumulatively added to all earlier bars; latest "
                             "prices unchanged",
    }
    report = audit_mod.render_report(meta, file_audits, comparisons, zip_info)
    with open(os.path.join(args.out_dir, "audit_report.md"), "w") as f:
        f.write(report)
    with open(os.path.join(args.out_dir, "audit_report.json"), "w") as f:
        f.write(audit_mod.audits_to_json(meta, file_audits, comparisons, zip_info))

    print(report)
    print("\nDONE:", zip_path)


if __name__ == "__main__":
    main()
