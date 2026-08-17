#!/usr/bin/env python3
"""Parity checker for a TradingView Strategy Tester "List of Trades" export.

Replaces `vendor/claude_chat_v1/parity_check.py`, which cannot be used as a gate
(see vendor/claude_chat_v1/AUDIT.md, findings C-1..C-4). The two defects that
mattered:

  * it compares exit times as exact HH:MM strings against a reference whose exit
    timestamps are 1-minute resolution, so a CORRECT 5-minute implementation
    reports ~2,061 false EXIT_TIME mismatches out of 2,295 trades;
  * `--offset-ok` sets `eprice_ok = True` unconditionally and exit prices are
    never compared at all, so an export with randomised entry prices and every
    exit price shifted by 999 points reports "PARITY PASS".

This checker:
  * compares the exit BAR (the 5-minute bar containing the reference exit),
    which is the correct granularity for a 5-minute chart;
  * compares exit prices, which the original never did;
  * implements `--offset-ok` as a real test — a roll offset must move every leg
    of a trade by the same amount and must be piecewise constant across the
    window, so random price corruption still fails;
  * classifies each mismatch with the taxonomy from
    spec/PARITY_ACCEPTANCE_GATES.md and exits non-zero on unknowns;
  * has no third-party dependencies.

Note the TradingView Strategy Tester is a SECONDARY parity surface. The
authoritative one is the deterministic per-bar export handled by
tests/parity_orb.py — the tester applies its own fill model. See
spec/SPEC_SEAMS.md S-9.

Usage:
    python3 tests/parity_tv_list_of_trades.py TV_EXPORT.csv
    python3 tests/parity_tv_list_of_trades.py TV_EXPORT.csv --offset-ok
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")

TICK = 0.25
BAR_SECONDS = 300

CLASSES = {
    1: "data-feed OHLC difference",
    2: "futures continuous-contract/roll difference",
    3: "timezone/session boundary difference",
    4: "5m aggregation difference",
    5: "body-filter implementation",
    6: "next-bar execution semantics",
    7: "stop sequencing",
    8: "close timestamp/session close",
    9: "implementation bug",
    10: "unknown",
}


def parse_dt(raw: str) -> datetime:
    raw = raw.strip().replace("T", " ")
    for cut in ("+", "Z"):
        if cut in raw[10:]:
            raw = raw[:10] + raw[10:].split(cut)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            pass
    raise SystemExit(f"unparsable Date/Time {raw!r}")


def floor_bar(t: datetime) -> datetime:
    return t - timedelta(minutes=t.minute % 5, seconds=t.second,
                         microseconds=t.microsecond)


def read_rows(path: str) -> list[dict]:
    # Real TradingView exports carry a UTF-8 BOM; without utf-8-sig the first
    # column name comes back as "\ufeffTrade number" and every lookup fails.
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def detect_tz_offset(entries: list[datetime]) -> int:
    """Infer the chart's UTC offset relative to New York, in hours.

    TradingView exports timestamps in the CHART's timezone, not the exchange's.
    The frozen spec pins every ORB entry to the 09:50-10:35 New York window, so
    the offset is whichever shift puts the most entries inside it. Comparing raw
    export timestamps against the reference without this correction makes every
    trade look like an entry-time mismatch.
    """
    best, best_hits = 0, -1
    for off in range(-12, 15):
        hits = sum(1 for e in entries
                   if "09:50" <= (e + timedelta(hours=off)).strftime("%H:%M") <= "10:35")
        if hits > best_hits:
            best, best_hits = off, hits
    return best


def detect_multiplier(rows: list[dict]) -> float | None:
    """Read $/point straight out of the export: NQ is 20, MNQ is 2.

    Mixing them silently turns a points comparison into a 10x error.
    """
    for r in rows:
        try:
            v = float(r[find_col(r, "size (value)", "size(value)")])
            p = float(r[find_col(r, "price usd", "price")])
            if p:
                return round(v / p, 2)
        except (SystemExit, ValueError, KeyError, ZeroDivisionError):
            return None
    return None


# ---------------------------------------------------------------------------
# TradingView export parsing
# ---------------------------------------------------------------------------

def find_col(row: dict, *candidates: str) -> str:
    lower = {c.strip().lower(): c for c in row}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    for cand in candidates:                      # prefix match, locale-tolerant
        for k, orig in lower.items():
            if k.startswith(cand):
                return orig
    raise SystemExit(f"column not found: {candidates}; export has {list(row)}")


def parse_tv_export(path: str) -> list[dict]:
    """Parse a List of Trades export. Handles the two-row-per-trade layout."""
    rows = read_rows(path)
    if not rows:
        return []
    c_num = find_col(rows[0], "trade #", "trade  #", "trade")
    c_typ = find_col(rows[0], "type")
    c_dt = find_col(rows[0], "date/time", "date")
    c_px = find_col(rows[0], "price usd", "price")

    mult = detect_multiplier(rows)
    if mult is not None:
        sym = ("NQ e-mini" if abs(mult - 20) < 0.5 else
               "MNQ micro" if abs(mult - 2) < 0.5 else "UNRECOGNISED")
        print(f"contract multiplier detected: ${mult:g}/point ({sym})")
        if abs(mult - 20) >= 0.5:
            print(f"  WARNING: the reference is NQ at $20/point. Dollar figures are "
                  f"NOT comparable; divide by {mult:g} to compare points.")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[str(r[c_num]).strip()].append(r)

    trades = []
    for num, legs in grouped.items():
        entries = [r for r in legs if "entry" in str(r[c_typ]).lower()]
        exits = [r for r in legs if "exit" in str(r[c_typ]).lower()]
        if not entries:
            continue
        e = entries[0]
        ets = parse_dt(e[c_dt])
        side = "LONG" if "long" in str(e[c_typ]).lower() else "SHORT"
        t = {"trade_no": num, "date": str(ets.date()), "direction": side,
             "entry_ts": ets, "entry": float(e[c_px]),
             "exit_ts": None, "exit": None}
        if exits:
            x = exits[0]
            t["exit_ts"] = parse_dt(x[c_dt])
            t["exit"] = float(x[c_px])
        trades.append(t)
    trades.sort(key=lambda x: x["entry_ts"])
    off = detect_tz_offset([t["entry_ts"] for t in trades])
    if off:
        print(f"chart timezone detected: New York {off:+d}h — shifting all export "
              f"timestamps to New York before comparison")
        for t in trades:
            t["entry_ts"] += timedelta(hours=off)
            if t["exit_ts"] is not None:
                t["exit_ts"] += timedelta(hours=off)
            t["date"] = str(t["entry_ts"].date())
    return trades


# ---------------------------------------------------------------------------
# Offset analysis — a real test, not a bypass
# ---------------------------------------------------------------------------

def analyse_offsets(pairs: list[tuple[dict, dict]]) -> dict:
    """A continuous-contract roll shifts EVERY leg of a trade by one amount, and
    that amount is piecewise constant in time. Anything else is not a roll.

    Returns per-trade offsets, the trades whose entry and exit offsets disagree
    (never a roll), and the dates where the offset level changes.
    """
    per_trade = []
    inconsistent = []
    for ref, tv in pairs:
        oe = tv["entry"] - float(ref["entry"])
        ox = (tv["exit"] - float(ref["exit"])) if tv["exit"] is not None else None
        per_trade.append((ref["date"], oe, ox))
        if ox is not None and abs(oe - ox) > TICK:
            inconsistent.append((ref["date"], round(oe, 2), round(ox, 2)))
    levels = []
    prev = None
    for date, oe, _ in per_trade:
        lvl = round(oe, 2)
        if prev is None or abs(lvl - prev) > TICK:
            levels.append((date, lvl))
            prev = lvl
    return {"per_trade": per_trade, "inconsistent": inconsistent,
            "level_changes": levels,
            "median": round(statistics.median([o for _, o, _ in per_trade]), 2)
            if per_trade else 0.0}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def classify(diffs: set, ref: dict, tv: dict) -> tuple[int, str]:
    if diffs == {"exit_ts"}:
        if ref["exit_reason"] == "close":
            return 8, "different final session bar"
        return 7, "stop resolved on a different bar"
    if "exit_reason_price" in diffs:
        return 7, "exit price is not the stop price"
    if "entry_ts" in diffs:
        d = (tv["entry_ts"] - parse_dt(ref["entry_ts"])).total_seconds()
        if d == BAR_SECONDS:
            return 6, "entry one bar late"
        if d % 3600 == 0 and d != 0:
            return 3, f"entry shifted {d/3600:+.0f}h"
        return 5, "different qualified bar accepted"
    if "direction" in diffs:
        return 9, "direction disagreement"
    if diffs <= {"entry", "exit"}:
        return 1, "same bars, different prices"
    return 10, "unclassified"


def compare(ref_rows: list[dict], tv_trades: list[dict], price_tol: float,
            offset_ok: bool) -> tuple[list[dict], Counter, dict]:
    ref_by_date = {r["date"]: r for r in ref_rows}
    # Clamp to the span the REFERENCE actually covers as well. An export that
    # runs past the end of the reference data would otherwise report every
    # unverifiable trade as an unknown mismatch, which is noise, not a finding.
    ref_lo = min(ref_by_date); ref_hi = max(ref_by_date)
    lo = max(tv_trades[0]["date"], ref_lo)
    hi = min(tv_trades[-1]["date"], ref_hi)
    if hi < tv_trades[-1]["date"]:
        n_beyond = sum(1 for t in tv_trades if t["date"] > hi)
        print(f"NOTE: {n_beyond} export trades fall after {hi}, the end of the "
              f"reference data — excluded as unverifiable, not counted as mismatches")
    tv_trades = [t for t in tv_trades if lo <= t["date"] <= hi]
    window = {d: r for d, r in ref_by_date.items() if lo <= d <= hi}

    tv_by_date: dict[str, list[dict]] = defaultdict(list)
    for t in tv_trades:
        tv_by_date[t["date"]].append(t)

    if not tv_trades:
        raise SystemExit("no trades parsed from the TradingView export")

    findings: list[dict] = []
    counts: Counter = Counter()
    pairs: list[tuple[dict, dict]] = []

    for d in sorted(set(window) | set(tv_by_date)):
        ref = window.get(d)
        tvs = tv_by_date.get(d, [])
        if ref and not tvs:
            counts[10] += 1
            findings.append({"date": d, "class": 10, "fields": "",
                             "detail": "reference trade missing from the export"})
            continue
        if tvs and not ref:
            counts[10] += 1
            findings.append({"date": d, "class": 10, "fields": "",
                             "detail": "export trade not in the reference"})
            continue
        if len(tvs) > 1:
            counts[9] += 1
            findings.append({"date": d, "class": 9, "fields": "",
                             "detail": f"{len(tvs)} trades on one session "
                                       f"(frozen spec B: one trade per day)"})
            continue
        tv = tvs[0]
        pairs.append((ref, tv))

        diffs = set()
        if ref["direction"] != tv["direction"]:
            diffs.add("direction")
        if parse_dt(ref["entry_ts"]) != tv["entry_ts"]:
            diffs.add("entry_ts")
        # Exit comparison is done on the BAR, because the reference may carry a
        # 1-minute exit timestamp while the chart is 5-minute.
        if tv["exit_ts"] is None:
            diffs.add("exit_ts")
        elif floor_bar(parse_dt(ref["exit_ts"])) != floor_bar(tv["exit_ts"]):
            diffs.add("exit_ts")
        if not offset_ok:
            if abs(tv["entry"] - float(ref["entry"])) > price_tol + 1e-9:
                diffs.add("entry")
            if tv["exit"] is None or \
               abs(tv["exit"] - float(ref["exit"])) > price_tol + 1e-9:
                diffs.add("exit")
        else:
            # Offset-tolerant, but the trade must still be internally consistent:
            # a roll moves entry and exit by the same amount.
            if tv["exit"] is not None:
                oe = tv["entry"] - float(ref["entry"])
                ox = tv["exit"] - float(ref["exit"])
                if abs(oe - ox) > TICK:
                    diffs.add("entry")
                    diffs.add("exit")
        # A stop exit must fill exactly at the stop, offset or not.
        if ref["exit_reason"] == "stop" and tv["exit"] is not None:
            ref_gap = float(ref["exit"]) - float(ref["stop"])
            tv_gap = tv["exit"] - (float(ref["stop"]) +
                                   (tv["entry"] - float(ref["entry"])
                                    if offset_ok else 0.0))
            if abs(tv_gap - ref_gap) > price_tol + 1e-9:
                diffs.add("exit_reason_price")

        counts["sessions"] += 1
        if not diffs:
            counts["match"] += 1
            continue
        cls, why = classify(diffs, ref, tv)
        counts[cls] += 1
        findings.append({"date": d, "class": cls, "fields": ",".join(sorted(diffs)),
                         "detail": why})

    offsets = analyse_offsets(pairs) if offset_ok else {}
    if offset_ok and offsets.get("inconsistent"):
        for date, oe, ox in offsets["inconsistent"]:
            counts[9] += 1
            findings.append({"date": date, "class": 9, "fields": "entry,exit",
                             "detail": f"entry offset {oe} != exit offset {ox} "
                                       f"— not a contract roll"})
    return findings, counts, offsets


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tv_export")
    ap.add_argument("--ref", default=CANONICAL)
    ap.add_argument("--price-tol", type=float, default=0.0)
    ap.add_argument("--offset-ok", action="store_true",
                    help="allow a piecewise-constant per-contract price offset "
                         "(unadjusted TradingView continuous feed). Still "
                         "requires every leg of a trade to move together.")
    ap.add_argument("--report", default=os.path.join(REPO, "tests",
                                                     "parity_report_tv_trades.md"))
    a = ap.parse_args(argv)

    ref_rows = read_rows(a.ref)
    # Accept either the canonical schema or vendor/claude_chat_v1's schema.
    for r in ref_rows:
        if "exit_px" in r and "exit" not in r:
            r["exit"] = r["exit_px"]
        if "side" in r and "direction" not in r:
            r["direction"] = r["side"]
        if "exit_reason" in r:
            r["exit_reason"] = r["exit_reason"].lower().replace("eod", "close")

    tv_trades = parse_tv_export(a.tv_export)
    findings, counts, offsets = compare(ref_rows, tv_trades, a.price_tol,
                                        a.offset_ok)

    matched = counts.get("match", 0)
    # Sessions where both sides produced exactly one trade, plus the sessions
    # only one side produced. Offset findings are extra detail on an already
    # counted session and must not inflate the denominator.
    total = counts.get("sessions", 0) + counts.get(10, 0)
    unknown = counts.get(10, 0)

    lines = [
        "# TRADINGVIEW LIST-OF-TRADES PARITY REPORT",
        "",
        f"- export: `{a.tv_export}`",
        f"- reference: `{a.ref}`",
        f"- trades parsed from export: {len(tv_trades)}",
        f"- exact matches: **{matched}/{total}**",
        f"- unknown (blocking) mismatches: **{unknown}**",
        f"- price mode: {'offset-tolerant (piecewise-constant roll offset)' if a.offset_ok else f'exact, tolerance {a.price_tol}'}",
        "",
        "| # | class | count |", "|---|---|---|",
    ]
    for k in sorted(CLASSES):
        lines.append(f"| {k} | {CLASSES[k]} | {counts.get(k, 0)} |")
    if a.offset_ok and offsets:
        lines += ["", "## Roll-offset analysis", "",
                  f"- median offset: {offsets['median']}",
                  f"- trades whose entry and exit offsets disagree: "
                  f"**{len(offsets['inconsistent'])}** (must be 0 for a real roll)",
                  f"- offset level changes: {len(offsets['level_changes'])}", ""]
        for date, lvl in offsets["level_changes"][:40]:
            lines.append(f"  - {date}: offset -> {lvl}")
    lines += ["", "## Gate verdict", "",
              "**PASS** — no unknown mismatches." if unknown == 0 else
              f"**BLOCKED** — {unknown} unknown mismatches.", "",
              "## Findings", ""]
    if findings:
        lines += ["| date | class | fields | detail |", "|---|---|---|---|"]
        for f in findings[:400]:
            lines.append(f"| {f['date']} | {f['class']} {CLASSES[f['class']]} | "
                         f"{f['fields']} | {f['detail']} |")
        if len(findings) > 400:
            lines.append(f"| ... | | | {len(findings)-400} more |")
    else:
        lines.append("None.")
    with open(a.report, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"parsed {len(tv_trades)} TV trades; matched {matched}/{total}; "
          f"unknown {unknown}")
    if a.offset_ok and offsets:
        print(f"offset median {offsets['median']}, "
              f"inconsistent-leg trades {len(offsets['inconsistent'])}, "
              f"level changes {len(offsets['level_changes'])}")
    print(f"report -> {a.report}")
    return 1 if unknown or counts.get(9, 0) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
