#!/usr/bin/env python3
"""Drive the whole paper pipeline over every session in the canonical reference.

`tests/test_daily_audit.py` proves the audit catches a dozen hand-built breaks.
It cannot prove the opposite and more dangerous property: that the audit does
NOT raise FALSE failures on ordinary sessions. A single false-failure mode —
same-bar stops, zero-length trades, a direction the reference records
differently — would stall the live count indefinitely and be blamed on the
market rather than on the tool.

So this replays real sessions end to end: signal alerts through the ledger,
matching fill alerts through the fill channel, then the daily audit, and reports
the distribution of verdicts.

Every verdict is written with `mode: "replay"`. `streak()` counts only `live`
verdicts, so nothing here can shorten the 20 sessions that gate live capital.
That exclusion is asserted below, not assumed.

Run:  python3 tests/replay_reference_sessions.py [--limit N] [--from DATE]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="nq_replay_")
os.environ["NQ_PAPER_DIR"] = TMP          # must precede the imports below
os.environ["NQ_PAPER_CONTRACT"] = "MNQ"
os.environ["NQ_PAPER_QTY"] = "1"

sys.path.insert(0, os.path.join(REPO, "executor"))

import daily_audit as DA                                          # noqa: E402
import paper_executor as PE                                       # noqa: E402

CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")


def payloads(t: dict) -> tuple[list[dict], list[dict]]:
    """The signal and fill alerts the Pine would have emitted for one trade."""
    date = t["date"]
    side = t["direction"]
    entry_evt = f"ORB_{side}_ENTRY"
    exit_evt = "ORB_STOP" if t["exit_reason"] == "stop" else "SESSION_CLOSE_EXIT"
    entry, stop, exit_px = float(t["entry"]), float(t["stop"]), float(t["exit"])

    base = {"strategy_version": "nq_orb_s5b_v1", "symbol": "NQ1!",
            "session_date": date, "orb_direction": side,
            "s5b_state": "WAITING_FOR_LATCH", "alignment": "UNRESOLVED"}
    sig = lambda evt, **kw: {**base, "signal_id": f"nq_orb_s5b_v1|NQ1!|{date}|{evt}",
                             "event": evt, "direction": side,
                             "event_time": f"{date}T09:50:00-0400", **kw}
    signals = [
        sig(entry_evt, entry=entry, stop=stop),
        sig(exit_evt, entry=entry, stop=stop, exit=exit_px),
        sig("SESSION_SUMMARY", entry=entry, stop=stop, exit=exit_px, traded=True),
    ]
    fill = lambda evt, px, pos: {
        "strategy_version": "nq_orb_s5b_v1", "channel": "fill", "event": evt,
        "symbol": "NQ1!", "direction": side, "fill_price": px, "fill_qty": 1,
        "position_after": pos, "order_comment": evt, "stop": stop,
        "session_date": date, "bar_time": f"{date}T20:00:00Z",
        "signal_id": f"nq_orb_s5b_v1|NQ1!|{date}|{evt}|fill"}
    fills = [fill(entry_evt, entry, 1 if side == "LONG" else -1),
             fill(exit_evt, exit_px, 0)]
    return signals, fills


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = every session")
    ap.add_argument("--from", dest="start", default="", help="skip earlier dates")
    args = ap.parse_args(argv)

    with open(CANONICAL, encoding="utf-8-sig") as fh:
        trades = [r for r in csv.DictReader(fh) if r["date"] >= args.start]
    if args.limit:
        trades = trades[-args.limit:]
    print(f"replaying {len(trades)} canonical sessions "
          f"({trades[0]['date']} -> {trades[-1]['date']})\n")

    led = PE.Ledger()
    fills_seen: dict[str, str] = {}
    verdicts: list[dict] = []
    reasons: Counter = Counter()
    rejected = 0

    for i, t in enumerate(trades, 1):
        signals, fills = payloads(t)
        for p in signals:
            try:
                led.apply(p, persist=True)
            except PE.Rejected as exc:
                reasons[f"SIGNAL REJECTED: {exc}"] += 1
                rejected += 1
        for f in fills:
            try:
                PE.record_fill(f, fills_seen)
            except PE.Rejected as exc:
                reasons[f"FILL REJECTED: {exc}"] += 1
                rejected += 1
        # The ledger is passed in rather than rebuilt: rebuilding per session
        # would re-parse the whole event log every time and make this quadratic.
        rep = DA.audit(t["date"], ledger=led, mode="replay")
        DA.record_session(rep)
        verdicts.append(rep.to_record())
        for c in rep.failures:
            reasons[c["check"]] += 1
        if i % 500 == 0:
            print(f"  {i}/{len(trades)} sessions, "
                  f"{sum(1 for v in verdicts if v['clean'])} clean")

    clean = sum(1 for v in verdicts if v["clean"])
    print(f"\n{'=' * 72}")
    print(f"sessions replayed        {len(verdicts)}")
    print(f"clean                    {clean}  ({100 * clean / len(verdicts):.2f}%)")
    print(f"payloads rejected        {rejected}")
    if reasons:
        print("\nfailure reasons, most common first:")
        for name, n in reasons.most_common(15):
            print(f"  {n:6d}  {name}")
        bad = [v for v in verdicts if not v["clean"]]
        print(f"\nfirst 5 failing sessions:")
        for v in bad[:5]:
            print(f"  {v['date']}  {v['failures']}")

    # The exclusion is the point, so prove it rather than trusting the flag.
    s = DA.streak()
    ok = s["consecutive_clean"] == 0 and s["sessions_audited"] == 0
    print(f"\n{'=' * 72}")
    print(f"streak after {len(verdicts)} replayed sessions: "
          f"audited={s['sessions_audited']} consecutive_clean="
          f"{s['consecutive_clean']} excluded={s.get('replayed_sessions_excluded')}")
    print("PASS  replayed sessions cannot count toward the deployment gate"
          if ok else
          "FAIL  replayed sessions leaked into the deployment streak")

    print(f"\nstate dir: {TMP}")
    if not ok:
        return 1
    return 0 if clean == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
