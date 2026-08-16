#!/usr/bin/env python3
"""S5b state parity comparator (Phase 1 step 1.8).

`spec/PARITY_ACCEPTANCE_GATES.md` asks for a spot check of, at minimum:
latch timestamp/direction, pullback activation, failure, confirmation and
invalidation timestamps, and ORB/S5b alignment.

References used (validated by tests/verify_s5b_reference.py):
  DAY FLAGS   research/master_handoff/01_CLAUDE_ROUND3_PART1/s5b_day_flags_allmult.csv
              4,768 sessions: latch side, band reached, reassertion at the frozen
              1.2x threshold with its side, invalidation. Day-level, no timestamps.
  TIMESTAMPS  research/master_handoff/01_CLAUDE_ROUND3_PART1/claude_s5b_hist_2008_2023.csv
              763 standalone S5b trades. `entry_ts` is the confirmation bar plus
              five minutes, so it pins the confirmation timestamp exactly.

NOT a reference: the `Failed_Counterattack_*` logs implement a different, earlier
construction (see spec/SPEC_SEAMS.md S-5). This script refuses to read them.

Coverage note: the day flags carry no pullback-activation or failure timestamps,
so those two transitions can only be checked for internal consistency (ordering
and reachability), not against a published value. That limit is reported rather
than hidden.

Usage:
    python3 tests/parity_s5b.py --candidate /tmp/engine_s5b.csv
    python3 tests/parity_s5b.py --tv-export /path/to/chart_data.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGS = os.path.join(REPO, "research", "master_handoff", "01_CLAUDE_ROUND3_PART1",
                     "s5b_day_flags_allmult.csv")
HIST = os.path.join(REPO, "research", "master_handoff", "01_CLAUDE_ROUND3_PART1",
                    "claude_s5b_hist_2008_2023.csv")

STATE_NAMES = {0: "WAITING_FOR_LATCH", 1: "LATCHED", 2: "PULLBACK_ACTIVE",
               3: "COUNTERATTACK_FAILURE", 4: "CONFIRMED", 5: "INVALIDATED"}


def read(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def parse_ts(v: str) -> datetime | None:
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(v.split("+")[0].rstrip("Z"), fmt)
        except ValueError:
            pass
    raise SystemExit(f"unparsable timestamp {v!r}")


def side_of(direction: str) -> int:
    d = (direction or "").strip().upper()
    return 1 if d == "LONG" else -1 if d == "SHORT" else 0


def from_tv_export(path: str) -> list[dict]:
    """Rebuild per-session S5b timelines from the Pine px_s5b_* series."""
    rows = read(path)
    if not rows:
        return []
    tcol = next((c for c in rows[0] if c.strip().lower() in
                 ("time", "date", "datetime")), None)
    if tcol is None:
        raise SystemExit("tv-export: no time column found")

    sessions: dict[str, dict] = {}
    for r in rows:
        t = parse_ts(r[tcol].replace("T", " "))
        if t is None:
            continue
        day = str(t.date())
        s = sessions.setdefault(day, {
            "date": day, "direction": "NONE", "state": "WAITING_FOR_LATCH",
            "latch_ts": "", "pullback_ts": "", "failure_ts": "",
            "confirmed_ts": "", "invalidated_ts": "", "entry_eligible": "False"})
        try:
            st = int(float(r.get("px_s5b_state", "") or 0))
            sd = int(float(r.get("px_s5b_side", "") or 0))
        except ValueError:
            continue
        prev = {v: k for k, v in STATE_NAMES.items()}[s["state"]]
        if st != prev:
            stamp = t.strftime("%Y-%m-%d %H:%M:%S")
            if st == 1 and not s["latch_ts"]:
                s["latch_ts"] = stamp
            elif st == 2 and not s["pullback_ts"]:
                s["pullback_ts"] = stamp
            elif st == 3 and not s["failure_ts"]:
                s["failure_ts"] = stamp
            elif st == 4:
                s["confirmed_ts"] = stamp
                s["entry_eligible"] = str(t.time() <= datetime.strptime(
                    "11:30", "%H:%M").time())
            elif st == 5:
                s["invalidated_ts"] = stamp
            s["state"] = STATE_NAMES[st]
        if sd != 0:
            s["direction"] = "LONG" if sd > 0 else "SHORT"
    return [sessions[k] for k in sorted(sessions)]


def compare(candidate: list[dict]) -> tuple[list[dict], Counter, dict]:
    flags = {r["date"]: r for r in read(FLAGS)}
    hist = {r["date"]: r for r in read(HIST)}
    findings: list[dict] = []
    c: Counter = Counter()

    for cand in candidate:
        d = cand["date"]
        f = flags.get(d)
        if f is None:
            c["session_not_in_reference"] += 1
            findings.append({"date": d, "field": "session",
                             "detail": "no reference row for this session"})
            continue
        c["sessions_compared"] += 1

        # --- latch direction -------------------------------------------------
        ref_latch = int(f["latch"])
        cand_latch = side_of(cand.get("direction", ""))
        if ref_latch == cand_latch:
            c["latch_direction_match"] += 1
        else:
            c["latch_direction_mismatch"] += 1
            findings.append({"date": d, "field": "latch_direction",
                             "detail": f"reference {ref_latch} vs candidate {cand_latch}"})

        # --- confirmation flag and side (frozen 1.2x threshold) --------------
        ref_conf = f["re12"] == "1"
        cand_conf = cand.get("state") == "CONFIRMED"
        if ref_conf == cand_conf:
            c["confirmation_flag_match"] += 1
        else:
            c["confirmation_flag_mismatch"] += 1
            findings.append({"date": d, "field": "confirmed",
                             "detail": f"reference {ref_conf} vs candidate {cand_conf}"})
        if ref_conf and cand_conf:
            if int(f["side12"]) == cand_latch:
                c["confirmation_side_match"] += 1
            else:
                c["confirmation_side_mismatch"] += 1
                findings.append({"date": d, "field": "confirmed_side",
                                 "detail": f"reference {f['side12']} vs {cand_latch}"})

        # --- pullback band reached -------------------------------------------
        ref_band = f["band"] == "True"
        cand_band = bool(cand.get("pullback_ts")) or cand.get("state") in (
            "PULLBACK_ACTIVE", "COUNTERATTACK_FAILURE", "CONFIRMED")
        if ref_band == cand_band:
            c["band_match"] += 1
        else:
            c["band_mismatch"] += 1
            findings.append({"date": d, "field": "band",
                             "detail": f"reference {ref_band} vs candidate {cand_band}"})

        # --- invalidation ------------------------------------------------------
        ref_inv = f["invalid"] == "1"
        cand_inv = cand.get("state") == "INVALIDATED" or bool(cand.get("invalidated_ts"))
        if ref_inv == cand_inv:
            c["invalidation_match"] += 1
        else:
            c["invalidation_mismatch"] += 1
            findings.append({"date": d, "field": "invalidated",
                             "detail": f"reference {ref_inv} vs candidate {cand_inv}"})

        # --- confirmation timestamp (only where the standalone log covers it) --
        h = hist.get(d)
        if h and cand.get("confirmed_ts"):
            want = parse_ts(h["entry_ts"]) - timedelta(minutes=5)
            got = parse_ts(cand["confirmed_ts"])
            if want == got:
                c["confirmation_timestamp_match"] += 1
            else:
                c["confirmation_timestamp_mismatch"] += 1
                findings.append({"date": d, "field": "confirmed_ts",
                                 "detail": f"reference {want} vs candidate {got}"})
        elif h and not cand.get("confirmed_ts"):
            c["confirmation_missing_vs_standalone_log"] += 1
            findings.append({"date": d, "field": "confirmed_ts",
                             "detail": "standalone log has a trade, candidate never confirmed"})

        # --- internal ordering consistency (no published reference exists) -----
        order = [cand.get(k) for k in ("latch_ts", "pullback_ts", "failure_ts",
                                       "confirmed_ts")]
        stamps = [parse_ts(x) for x in order if x]
        if stamps != sorted(stamps):
            c["state_order_violation"] += 1
            findings.append({"date": d, "field": "ordering",
                             "detail": "state timestamps are not monotonic"})

    coverage = {
        "sessions_in_candidate": len(candidate),
        "sessions_in_day_flags": len(flags),
        "sessions_with_published_confirmation_timestamp": len(hist),
        "note": ("pullback-activation and failure timestamps have no published "
                 "reference value; they are checked for ordering only"),
    }
    return findings, c, coverage


def write_report(path: str, findings: list[dict], c: Counter, coverage: dict,
                 source: str) -> None:
    mism = sum(v for k, v in c.items() if k.endswith("mismatch")
               or k.endswith("violation") or k.startswith("session_not"))
    lines = [
        "# S5b STATE PARITY REPORT",
        "",
        f"- candidate source: `{source}`",
        f"- sessions compared: {c.get('sessions_compared', 0)}",
        f"- total mismatches: **{mism}**",
        "",
        "## Counters",
        "",
        "| check | count |",
        "|---|---|",
    ]
    for k in sorted(c):
        lines.append(f"| {k} | {c[k]} |")
    lines += ["", "## Coverage", ""]
    for k, v in coverage.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Findings", ""]
    if not findings:
        lines.append("None.")
    else:
        lines += ["| date | field | detail |", "|---|---|---|"]
        for f in findings[:400]:
            lines.append(f"| {f['date']} | {f['field']} | {f['detail']} |")
        if len(findings) > 400:
            lines.append(f"| ... | | {len(findings) - 400} more |")
    lines.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--candidate", help="S5b timeline CSV (reference_engine --s5b schema)")
    src.add_argument("--tv-export", dest="tv_export",
                     help="TradingView 'Export chart data' CSV")
    ap.add_argument("--report", default=os.path.join(REPO, "tests",
                                                     "parity_report_s5b.md"))
    a = ap.parse_args(argv)

    if a.candidate:
        candidate, source = read(a.candidate), a.candidate
    else:
        candidate, source = from_tv_export(a.tv_export), a.tv_export

    findings, counters, coverage = compare(candidate)
    write_report(a.report, findings, counters, coverage, source)

    mism = sum(v for k, v in counters.items() if k.endswith("mismatch")
               or k.endswith("violation") or k.startswith("session_not"))
    print(f"sessions compared {counters.get('sessions_compared', 0)}  "
          f"mismatches {mism}")
    print(f"report -> {a.report}")
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
