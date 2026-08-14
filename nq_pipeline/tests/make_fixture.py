#!/usr/bin/env python3
"""SYNTHETIC TEST FIXTURE GENERATOR — NOT MARKET DATA.

Generates fake per-contract NQ-shaped 1-minute CSVs purely to exercise the
build/audit/zip pipeline (roll detection, back-adjustment arithmetic, session
classification, ZIP validation). Prices and volumes are random walks with a
deliberate per-contract basis so roll offsets are non-zero and detectable.

NEVER ship the output of this script as real data. Real runs must use
--source databento or genuine vendor per-contract files.

Usage: python make_fixture.py <raw_dir> [start_year] [end_year]
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from build_nq_dataset import (ET, contract_expiry, quarterly_contracts)  # noqa: E402

SCHEMA = ["timestamp", "open", "high", "low", "close", "volume"]


def session_minutes(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """All expected NQ Globex minutes (ET): Sun 18:00 -> Fri 17:00,
    minus the daily 17:00-17:59 maintenance break."""
    idx = pd.date_range(start, end, freq="1min", tz=ET)
    dow, hour = idx.dayofweek, idx.hour
    keep = ~(
        (dow == 5)
        | ((dow == 6) & (hour < 18))
        | ((dow == 4) & (hour >= 17))
        | ((dow <= 3) & (hour == 17))
    )
    return idx[keep]


def gen_contract(sym: str, base_price: float, seed: int) -> pd.DataFrame:
    """Active window: from ~110 days before expiry to expiry 09:30 ET."""
    rng = np.random.default_rng(seed)
    expiry = contract_expiry(sym).tz_localize(ET) + pd.Timedelta(hours=9, minutes=30)
    start = expiry - pd.Timedelta(days=110)
    idx = session_minutes(start, expiry - pd.Timedelta(minutes=1))

    n = len(idx)
    close = base_price + np.cumsum(rng.normal(0, 1.2, n))
    close = np.maximum(close, 100.0)
    o = np.empty(n)
    o[0] = close[0]
    o[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.8, n))
    hi = np.maximum(o, close) + spread
    lo = np.minimum(o, close) - spread

    # volume ramps up into the active quarter then dies before expiry,
    # so the volume-crossover roll rule has something real to detect
    days_to_exp = (expiry - idx).days.to_numpy()
    vol_shape = np.clip((110 - days_to_exp) / 100, 0.02, 1.0)
    vol_shape = np.where(days_to_exp < 7, 0.05, vol_shape)
    vol = (rng.poisson(200, n) * vol_shape).astype(int) + 1

    q = 0.25  # NQ tick
    df = pd.DataFrame({
        "timestamp": idx,
        "open": np.round(o / q) * q,
        "high": np.round(hi / q) * q,
        "low": np.round(lo / q) * q,
        "close": np.round(close / q) * q,
        "volume": vol,
    })
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    return df


def main():
    raw_dir = sys.argv[1]
    start_y = int(sys.argv[2]) if len(sys.argv) > 2 else 2016
    end_y = int(sys.argv[3]) if len(sys.argv) > 3 else 2020
    os.makedirs(raw_dir, exist_ok=True)

    syms = quarterly_contracts(start_y, end_y)
    base = 4500.0
    for i, sym in enumerate(syms):
        base += 150.0  # secular drift so later contracts sit higher
        df = gen_contract(sym, base + 20.0 * (i % 3), seed=1000 + i)
        out = df.copy()
        # store as UTC to exercise the DST-aware conversion path
        out["timestamp"] = out["timestamp"].dt.tz_convert("UTC").dt.strftime(
            "%Y-%m-%d %H:%M:%S")
        out[SCHEMA].to_csv(os.path.join(raw_dir, f"{sym}.csv"), index=False)
        print(f"  fixture {sym}: {len(df):,} bars")


if __name__ == "__main__":
    main()
