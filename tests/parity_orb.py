#!/usr/bin/env python3
"""ORB trade-list parity comparator.

Compares a candidate ORB trade list against `reference/canonical_orb_trades.csv`
and classifies every mismatch using the taxonomy fixed in
`spec/PARITY_ACCEPTANCE_GATES.md`:

    1  data-feed OHLC difference
    2  futures continuous-contract/roll difference
    3  timezone/session boundary difference
    4  5m aggregation difference
    5  body-filter implementation
    6  next-bar execution semantics
    7  stop sequencing
    8  close timestamp/session close
    9  implementation bug
   10  unknown            <-- BLOCKER (gate: "Unknown mismatches are blockers")

The comparator never edits the strategy and never widens a tolerance to make a
mismatch disappear. Classification rules are explicit and conservative: anything
that does not match a stated rule falls through to `unknown`.

Inputs
------
--candidate  CSV in the tests/reference_engine.py output schema.
--tv-export  TradingView "Export chart data" CSV produced by
             pine/nq_orb_s5b_v1.pine; the trade list is reconstructed from the
             px_* parity series. See docs/PARITY_PROCEDURE.md.

Exit code is non-zero when any unknown mismatch remains.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")

TICK = 0.25
COMPARED_FIELDS = ["direction", "signal_ts", "entry_ts", "entry", "stop",
                   "exit_ts", "exit", "exit_reason"]

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


def ts(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")


def read(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def fnum(v: str) -> float | None:
    v = (v or "").strip()
    if v == "":
        return None
    return float(v)


# ---------------------------------------------------------------------------
# TradingView chart-data reconstruction
# ---------------------------------------------------------------------------

def from_tv_export(path: str) -> list[dict]:
    """Rebuild the trade list from the Pine parity series (one row per bar).

    Required columns (emitted by pine/nq_orb_s5b_v1.pine):
      time, px_orb_side, px_or_high, px_or_low, px_entry, px_stop,
      px_exit, px_exit_code
    """
    rows = read(path)
    if not rows:
        return []
    tcol = next((c for c in rows[0] if c.strip().lower() in
                 ("time", "date", "datetime")), None)
    if tcol is None:
        raise SystemExit("tv-export: no time column found")

    def when(r: dict) -> datetime:
        raw = r[tcol].strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d"):
            try:
                return datetime.strptime(raw.split("+")[0].rstrip("Z"), fmt)
            except ValueError:
                pass
        raise SystemExit(f"tv-export: unparsable timestamp {raw!r}")

    trades: list[dict] = []
    open_trade: dict | None = None
    for r in rows:
        t = when(r)
        entry = fnum(r.get("px_entry", ""))
        stop = fnum(r.get("px_stop", ""))
        exit_px = fnum(r.get("px_exit", ""))
        code = fnum(r.get("px_exit_code", "")) or 0
        side = fnum(r.get("px_orb_side", "")) or 0
        if entry is not None and stop is not None:
            open_trade = {
                "date": str(t.date()),
                "direction": "LONG" if side > 0 else "SHORT",
                "signal_ts": (t.replace(minute=t.minute)).strftime("%Y-%m-%d %H:%M:%S"),
                "entry_ts": t.strftime("%Y-%m-%d %H:%M:%S"),
                "entry": f"{entry}",
                "or_high": r.get("px_or_high", ""),
                "or_low": r.get("px_or_low", ""),
                "stop": f"{stop}",
            }
            # signal bar = the bar immediately before the entry bar
            sig = t.timestamp() - 300
            open_trade["signal_ts"] = datetime.fromtimestamp(sig).strftime(
                "%Y-%m-%d %H:%M:%S")
        if open_trade is not None and exit_px is not None and code in (1, 2):
            open_trade["exit_ts"] = t.strftime("%Y-%m-%d %H:%M:%S")
            open_trade["exit"] = f"{exit_px}"
            open_trade["exit_reason"] = "stop" if code == 1 else "close"
            trades.append(open_trade)
            open_trade = None
    return trades


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(can: dict, cand: dict, diffs: list[str]) -> tuple[int, str]:
    """Assign a taxonomy class to a per-trade mismatch. Conservative by design."""
    dset = set(diffs)

    # 2: a contract roll shifts every price by the same offset while the bar
    # structure (all timestamps, direction, exit reason) is unchanged.
    price_fields = {"entry", "stop", "exit"} & dset
    struct_fields = {"direction", "signal_ts", "entry_ts", "exit_ts",
                     "exit_reason"} & dset
    if price_fields and not struct_fields:
        offs = [fnum(cand[f]) - fnum(can[f]) for f in ("entry", "stop", "exit")
                if fnum(cand.get(f)) is not None and fnum(can.get(f)) is not None]
        if offs and max(offs) - min(offs) <= TICK and abs(offs[0]) > 4 * TICK:
            return 2, f"uniform price offset {offs[0]:+.2f} across all legs"
        # 1: small, non-uniform price differences with identical structure are a
        # feed OHLC difference (different ticks in the same bars).
        if offs and max(abs(o) for o in offs) <= 8 * TICK:
            return 1, "same bars, different prices"

    # 6: the whole trade is shifted by exactly one 5m bar.
    if "entry_ts" in dset and "signal_ts" in dset:
        if (ts(cand["entry_ts"]) - ts(can["entry_ts"])).total_seconds() == 300 and \
           (ts(cand["signal_ts"]) - ts(can["signal_ts"])).total_seconds() == 300:
            return 6, "entry and signal both one bar late"

    # 5: the opening range agrees but a different bar was accepted as the
    # qualified breakout -> body-filter implementation.
    if "signal_ts" in dset and "direction" not in dset:
        if can.get("stop") == cand.get("stop"):
            return 5, "same opening range, different qualified bar"

    # 7: one engine stopped out where the other held, or vice versa.
    if "exit_reason" in dset:
        return 7, f"{can['exit_reason']} vs {cand['exit_reason']}"

    # 8: same exit reason 'close', different final bar -> session-close boundary.
    if dset <= {"exit_ts", "exit"} and can["exit_reason"] == "close":
        return 8, "different final session bar"

    # 3: the sessions themselves are offset (all timestamps shifted by a whole
    # number of hours) -> timezone/session boundary.
    if {"signal_ts", "entry_ts"} <= dset:
        d = (ts(cand["entry_ts"]) - ts(can["entry_ts"])).total_seconds()
        if d % 3600 == 0 and d != 0:
            return 3, f"all timestamps shifted {d/3600:+.0f}h"

    # 4: timestamps land inside the canonical 5m bar -> aggregation difference.
    if {"entry_ts", "exit_ts"} & dset:
        def inside(a: str, b: str) -> bool:
            da, db = ts(a), ts(b)
            return 0 <= (db - da).total_seconds() < 300
        if all(inside(can[f], cand[f]) for f in ({"entry_ts", "exit_ts"} & dset)):
            return 4, "candidate timestamps fall inside the canonical 5m bars"

    # 9 vs 10: a direction disagreement on identical ranges is an implementation
    # bug; everything else is unknown and therefore a blocker.
    if "direction" in dset and can.get("stop") == cand.get("stop"):
        return 9, "direction disagreement on an identical opening range"
    return 10, "unclassified"


def compare(canonical: list[dict], candidate: list[dict],
            price_tol: float, auto_window: bool = False) -> tuple[list[dict], Counter]:
    can = {r["date"]: r for r in canonical}
    cand = {r["date"]: r for r in candidate}
    if auto_window and cand:
        # Restrict the canonical side to the span the candidate actually covers.
        # Without this, partial data coverage reports every out-of-window
        # canonical trade as a missing-trade blocker, which is noise, not a
        # finding. The window is stated in the report.
        lo, hi = min(cand), max(cand)
        can = {d: r for d, r in can.items() if lo <= d <= hi}
    findings: list[dict] = []
    counts: Counter = Counter()

    for d in sorted(set(can) | set(cand)):
        a, b = can.get(d), cand.get(d)
        if a and not b:
            findings.append({"date": d, "kind": "missing_in_candidate",
                             "class": 10, "detail": "canonical trade not produced",
                             "fields": ""})
            counts[10] += 1
            continue
        if b and not a:
            findings.append({"date": d, "kind": "extra_in_candidate",
                             "class": 10, "detail": "candidate trade not in canonical",
                             "fields": ""})
            counts[10] += 1
            continue

        diffs = []
        for f in COMPARED_FIELDS:
            av, bv = a.get(f, ""), b.get(f, "")
            if f in ("entry", "stop", "exit"):
                an, bn = fnum(av), fnum(bv)
                if an is None or bn is None:
                    if an != bn:
                        diffs.append(f)
                elif abs(an - bn) > price_tol + 1e-9:
                    diffs.append(f)
            elif f in ("signal_ts", "entry_ts", "exit_ts"):
                if ts(av) != ts(bv):
                    diffs.append(f)
            elif av.strip().lower() != bv.strip().lower():
                diffs.append(f)

        if not diffs:
            counts["match"] += 1
            continue
        cls, why = classify(a, b, diffs)
        counts[cls] += 1
        findings.append({"date": d, "kind": "field_mismatch", "class": cls,
                         "detail": why, "fields": ",".join(diffs),
                         "canonical": {f: a.get(f) for f in diffs},
                         "candidate": {f: b.get(f) for f in diffs}})
    return findings, counts


def write_report(path: str, findings: list[dict], counts: Counter,
                 n_can: int, n_cand: int, source: str) -> None:
    matched = counts.get("match", 0)
    unknown = counts.get(10, 0)
    lines = [
        "# ORB PARITY REPORT",
        "",
        f"- candidate source: `{source}`",
        f"- canonical trades: {n_can}",
        f"- candidate trades: {n_cand}",
        f"- exact matches: **{matched}** "
        f"({100.0 * matched / n_can:.2f}% of canonical)" if n_can else "",
        f"- mismatches: **{sum(v for k, v in counts.items() if k != 'match')}**",
        f"- unknown (blocking) mismatches: **{unknown}**",
        "",
        "## Mismatch classification (spec/PARITY_ACCEPTANCE_GATES.md)",
        "",
        "| # | class | count |",
        "|---|---|---|",
    ]
    for k in sorted(CLASSES):
        lines.append(f"| {k} | {CLASSES[k]} | {counts.get(k, 0)} |")
    lines += ["", "## Gate verdict", "",
              ("**PASS** — no unknown mismatches remain."
               if unknown == 0 else
               f"**BLOCKED** — {unknown} unknown mismatches. "
               "spec/PARITY_ACCEPTANCE_GATES.md: \"Unknown mismatches are blockers.\""),
              "", "## Findings", ""]
    if not findings:
        lines.append("None.")
    else:
        lines += ["| date | class | fields | detail |", "|---|---|---|---|"]
        for f in findings[:500]:
            lines.append(f"| {f['date']} | {f['class']} {CLASSES[f['class']]} | "
                         f"{f['fields']} | {f['detail']} |")
        if len(findings) > 500:
            lines.append(f"| ... | | | {len(findings) - 500} more |")
    lines.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--candidate", help="trade list in reference_engine schema")
    src.add_argument("--tv-export", dest="tv_export",
                     help="TradingView 'Export chart data' CSV")
    ap.add_argument("--canonical", default=CANONICAL)
    ap.add_argument("--price-tolerance", type=float, default=0.0,
                    help="price tolerance in points (default 0 = exact)")
    ap.add_argument("--auto-window", action="store_true",
                    help="compare only over the date span the candidate covers "
                         "(use when the raw data is a subset of 2008-2026)")
    ap.add_argument("--report", default=os.path.join(REPO, "tests", "parity_report.md"))
    a = ap.parse_args(argv)

    canonical = read(a.canonical)
    if a.candidate:
        candidate = read(a.candidate)
        source = a.candidate
    else:
        candidate = from_tv_export(a.tv_export)
        source = a.tv_export

    findings, counts = compare(canonical, candidate, a.price_tolerance,
                               a.auto_window)
    if a.auto_window and candidate:
        dates = [r['date'] for r in candidate]
        source += f"  [window {min(dates)} .. {max(dates)}]"
    write_report(a.report, findings, counts, len(canonical), len(candidate), source)

    print(f"canonical {len(canonical)}  candidate {len(candidate)}  "
          f"matched {counts.get('match', 0)}  unknown {counts.get(10, 0)}")
    print(f"report -> {a.report}")
    return 1 if counts.get(10, 0) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
