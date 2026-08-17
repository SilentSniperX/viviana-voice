#!/usr/bin/env python3
"""DAILY AUDIT — TradingView's simulated orders vs the expected state.

Phase 1 runs execution entirely inside TradingView's Pine strategy / broker
emulator. There is no external broker. That makes the emulator's own fill
reports the ONLY independent observation of what was executed, and this tool is
what turns them into a per-session verdict.

Three streams, three different origins, reconciled here:

    EXPECTED   signal-channel alerts  (`alert()`, bar close)   -> events.jsonl
               what the frozen strategy decided
    ACTUAL     fill-channel alerts    ("Order fills only")     -> fills.jsonl
               what TradingView's broker emulator actually did
    LEDGER     the paper position ledger built from EXPECTED   -> ledger.jsonl
               what the executor believes it is holding

plus, when the session falls inside the canonical window, the reference trade
from `reference/canonical_orb_trades.csv`, and optionally the Strategy Tester
"List of Trades" export for the same date (`--tv-export`).

The audit never repairs a difference and never trades. It emits PASS/FAIL per
named check, writes one verdict record per session to `sessions.jsonl`, and
`--streak` counts the consecutive clean sessions that
docs/DEPLOYMENT_ASSESSMENT.md Task 5 requires (20 to validate the pipeline, 60
before live capital).

Run:
    python3 executor/daily_audit.py --date 2026-08-17
    python3 executor/daily_audit.py --date 2026-08-17 --tv-export "List of Trades.csv"
    python3 executor/daily_audit.py --streak
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paper_executor import (COST_POINTS, ENTRY_EVENTS, EXIT_EVENTS,  # noqa: E402
                            EVENT_LOG, QTY, REJECT_LOG, STATE_DIR, Ledger,
                            append, load_fills, utcnow)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
SESSION_LOG = os.path.join(STATE_DIR, "sessions.jsonl")

TICK = 0.25
# The reference already charges 0.75 points per round turn, so slippage up to
# that is inside the modelled cost. Beyond 1.5 points per side the edge is gone:
# expectancy is 3.65 points per trade (docs/DEPLOYMENT_ASSESSMENT.md, kill
# criterion 3). These are not tunable knobs; they are the published thresholds.
SLIP_WARN = COST_POINTS
SLIP_FAIL = 1.5
# TradingView's NQ1! continuous is not FirstRate's back-adjusted series. The
# measured difference is about 0.75 points per trade over 2,265 bar-matched
# sessions, so a price comparison against the reference is only meaningful at a
# coarser tolerance. Bar/direction comparisons carry no such excuse.
REF_PRICE_TOL = 5.0

VALIDATE_SESSIONS = 20
LIVE_SESSIONS = 60


class Report:
    """A session verdict. Any FAIL makes the session not clean; nothing else does."""

    def __init__(self, date: str):
        self.date = date
        self.checks: list[dict] = []
        self.facts: dict = {}

    def check(self, name: str, ok: bool | None, detail: str = "") -> bool | None:
        """ok=None means the check could not run (source absent) — never a pass."""
        self.checks.append({"check": name,
                            "result": "N/A" if ok is None else ("PASS" if ok else "FAIL"),
                            "detail": detail})
        return ok

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.checks if c["result"] == "FAIL"]

    @property
    def clean(self) -> bool:
        return not self.failures

    def to_record(self) -> dict:
        return {"date": self.date, "audited_at": utcnow(), "clean": self.clean,
                "failures": [c["check"] for c in self.failures],
                **self.facts,
                "checks": self.checks}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def load_signals(date: str, path: str = EVENT_LOG) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = rec.get("payload") if isinstance(rec, dict) else None
            if not isinstance(p, dict):
                continue
            sess = p.get("session_date") or str(p.get("event_time", ""))[:10]
            if sess == date:
                out.append(p)
    return out


def load_rejections(date: str, path: str = REJECT_LOG) -> list[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if date in str(rec.get("raw", "")) or str(rec.get("at", ""))[:10] == date:
                out.append(rec)
    return out


def reference_trade(date: str) -> dict | None:
    if not os.path.exists(CANONICAL):
        return None
    with open(CANONICAL, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r["date"] == date:
                return r
    return None


def reference_covers(date: str) -> bool:
    """Is this date inside the canonical window at all? Outside it, the absence
    of a reference trade says nothing and must not be read as 'no trade'."""
    if not os.path.exists(CANONICAL):
        return False
    with open(CANONICAL, encoding="utf-8-sig") as fh:
        rows = [r["date"] for r in csv.DictReader(fh)]
    return bool(rows) and rows[0] <= date <= rows[-1]


def tv_export_trades(path: str, date: str) -> list[dict]:
    sys.path.insert(0, os.path.join(REPO, "tests"))
    from parity_tv_list_of_trades import parse_tv_export     # noqa: E402
    return [t for t in parse_tv_export(path) if t["date"] == date]


# ---------------------------------------------------------------------------
# Slippage, signed so that positive is always adverse
# ---------------------------------------------------------------------------

def slippage(direction: str, leg: str, expected: float, actual: float) -> float:
    if leg == "entry":
        return actual - expected if direction == "LONG" else expected - actual
    return expected - actual if direction == "LONG" else actual - expected


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def audit(date: str, tv_export: str | None = None) -> Report:
    rep = Report(date)
    signals = load_signals(date)
    fills = load_fills(date)
    led = Ledger.rebuild()
    ledger_trades = [c for c in led.closed
                     if str(c.get("session") or c.get("opened"))[:10] == date]

    sig_entries = [s for s in signals if s["event"] in ENTRY_EVENTS]
    sig_exits = [s for s in signals if s["event"] in EXIT_EVENTS]
    fill_entries = [f for f in fills if f["event"] in ENTRY_EVENTS]
    fill_exits = [f for f in fills if f["event"] in EXIT_EVENTS]

    traded = bool(sig_entries or fill_entries)
    rep.facts = {"traded": traded, "signals": len(signals), "fills": len(fills),
                 "signal_entries": len(sig_entries), "fill_entries": len(fill_entries),
                 "ledger_trades": len(ledger_trades)}

    # -- A. structure --------------------------------------------------------
    rep.check("one entry signal at most (frozen spec B: one trade/day)",
              len(sig_entries) <= 1, f"{len(sig_entries)} entry signals")
    rep.check("one entry fill at most",
              len(fill_entries) <= 1, f"{len(fill_entries)} entry fills")
    rep.check("every entry signal reached the emulator",
              len(sig_entries) <= len(fill_entries),
              f"signals={len(sig_entries)} fills={len(fill_entries)}")
    rep.check("no orphan entry fill without a signal behind it",
              len(fill_entries) <= len(sig_entries),
              f"signals={len(sig_entries)} fills={len(fill_entries)}")
    rep.check("every exit signal reached the emulator",
              len(sig_exits) <= len(fill_exits),
              f"signals={len(sig_exits)} fills={len(fill_exits)}")
    rep.check("no orphan exit fill without a signal behind it",
              len(fill_exits) <= len(sig_exits),
              f"signals={len(sig_exits)} fills={len(fill_exits)}")

    if not traded:
        # A day the strategy declined is a legitimate outcome, not a break — but
        # it proves nothing about execution, which is why the streak counter
        # tracks traded sessions separately.
        rep.check("no orphan fills on a no-trade session", not fills,
                  f"{len(fills)} fills with no entry signal")
        rep.check("ledger holds no trade for a no-trade session",
                  not ledger_trades, f"{len(ledger_trades)} ledger trades")
        rep.check("no open position carried out of the session",
                  led.position is None or
                  str(led.position.get("session")) != date,
                  json.dumps(led.position) if led.position else "flat")
        _reference_checks(rep, date, None, None)
        return rep

    # -- B. the traded session ----------------------------------------------
    sig_entry = sig_entries[0] if sig_entries else None
    fill_entry = fill_entries[0] if fill_entries else None
    sig_exit = sig_exits[0] if sig_exits else None
    fill_exit = fill_exits[0] if fill_exits else None

    direction = (sig_entry or fill_entry).get("direction")
    rep.facts["direction"] = direction

    if sig_entry and fill_entry:
        rep.check("fill direction agrees with the signal",
                  fill_entry["direction"] == sig_entry["direction"],
                  f"signal={sig_entry['direction']} fill={fill_entry['direction']}")
        rep.check("fill event agrees with the signal event",
                  fill_entry["event"] == sig_entry["event"],
                  f"signal={sig_entry['event']} fill={fill_entry['event']}")
    else:
        rep.check("entry signal and entry fill both present", None,
                  "one side is missing — nothing to compare")

    # position accounting straight out of TradingView
    if fill_entry:
        expected_sign = 1 if fill_entry["direction"] == "LONG" else -1
        rep.check("TradingView position after entry is one contract, correct side",
                  fill_entry["position_after"] == expected_sign * QTY,
                  f"position_after={fill_entry['position_after']} expected="
                  f"{expected_sign * QTY}")
        rep.check("entry fill quantity matches the configured size",
                  fill_entry["fill_qty"] == QTY,
                  f"fill_qty={fill_entry['fill_qty']} configured={QTY}")
    if fill_exit:
        rep.check("TradingView is flat after the exit fill",
                  fill_exit["position_after"] == 0,
                  f"position_after={fill_exit['position_after']}")
        rep.check("exit fill quantity matches the entry",
                  fill_exit["fill_qty"] == QTY,
                  f"fill_qty={fill_exit['fill_qty']} configured={QTY}")
    else:
        rep.check("the session's position was closed", False,
                  "no exit fill — hold-to-close is a frozen rule, an open "
                  "position at the end of a session is a break")

    if sig_exit and fill_exit:
        rep.check("exit reason agrees between signal and fill",
                  fill_exit["event"] == sig_exit["event"],
                  f"signal={sig_exit['event']} fill={fill_exit['event']}")

    # -- C. price: slippage is the number that decides whether this is tradeable
    slips = {}
    if sig_entry and fill_entry and sig_entry.get("entry") is not None:
        s = round(slippage(direction, "entry", float(sig_entry["entry"]),
                           float(fill_entry["fill_price"])), 2)
        slips["entry"] = s
        rep.check("entry slippage within the kill threshold", s <= SLIP_FAIL,
                  f"{s:+.2f} pts adverse (warn>{SLIP_WARN}, fail>{SLIP_FAIL})")
    if sig_exit and fill_exit:
        # `exit` is the strategy's INTENDED exit price: the stop for a stop
        # exit, the RTH-close price for a hold-to-close exit. Older payloads
        # predate the field, so fall back to the stop and say when neither is
        # available rather than silently reporting zero slippage.
        expected_exit = sig_exit.get("exit")
        if expected_exit is None and sig_exit["event"] == "ORB_STOP":
            expected_exit = sig_exit.get("stop")
        if expected_exit is None:
            rep.check("exit slippage measurable", None,
                      "signal payload carries no intended exit price "
                      "(pre-v1.6 alert) — slippage not measured")
        else:
            s = round(slippage(direction, "exit", float(expected_exit),
                               float(fill_exit["fill_price"])), 2)
            slips["exit"] = s
            rep.check("exit slippage within the kill threshold", s <= SLIP_FAIL,
                      f"{s:+.2f} pts adverse (warn>{SLIP_WARN}, fail>{SLIP_FAIL})")
            if sig_exit["event"] == "ORB_STOP":
                # A stop that fills BETTER than its trigger did not happen in a
                # real market; it means the comparison is wrong somewhere.
                rep.check("stop did not fill better than its trigger price",
                          s >= -TICK, f"{s:+.2f} pts")
    if slips:
        rep.facts["slippage_points"] = slips

    # -- D. ledger -----------------------------------------------------------
    rep.check("ledger records exactly one closed trade for the session",
              len(ledger_trades) == 1, f"{len(ledger_trades)} closed trades")
    if ledger_trades:
        lt = ledger_trades[0]
        rep.facts["ledger_points"] = lt["points"]
        rep.check("ledger direction agrees with the fills",
                  lt["side"] == direction, f"ledger={lt['side']} fills={direction}")
        if fill_entry:
            rep.check("ledger entry price agrees with the TradingView fill",
                      abs(lt["entry"] - float(fill_entry["fill_price"])) <= SLIP_FAIL,
                      f"ledger={lt['entry']} fill={fill_entry['fill_price']}")
        if fill_exit:
            rep.check("ledger exit price agrees with the TradingView fill",
                      abs(lt["exit"] - float(fill_exit["fill_price"])) <= SLIP_FAIL,
                      f"ledger={lt['exit']} fill={fill_exit['fill_price']}")
    rep.check("executor is flat after the session",
              led.position is None, json.dumps(led.position) if led.position else "flat")

    _rejection_checks(rep, date)

    # -- E. reference and the Strategy Tester export -------------------------
    _reference_checks(rep, date, direction,
                      ledger_trades[0] if ledger_trades else None)
    if tv_export:
        _export_checks(rep, date, tv_export, direction, fill_entry, fill_exit)
    return rep


# TradingView re-fires an alert now and then; the receiver's idempotency catches
# it and that is the system working, not a break. Every OTHER rejection means a
# payload arrived that the strategy should never have sent, or arrived too late
# to act on, and the session is not clean.
BENIGN_REJECTIONS = ("duplicate signal_id", "duplicate fill signal_id")


def _rejection_checks(rep: Report, date: str) -> None:
    rejects = load_rejections(date)
    benign = [r for r in rejects
              if any(b in r.get("reason", "") for b in BENIGN_REJECTIONS)]
    serious = [r for r in rejects if r not in benign]
    rep.facts["rejections"] = len(rejects)
    if benign:
        rep.facts["duplicate_alerts_ignored"] = len(benign)
    rep.check("no alert rejections beyond routine duplicates", not serious,
              "; ".join(r.get("reason", "?") for r in serious[:3]))


def _reference_checks(rep: Report, date: str, direction: str | None,
                      ledger_trade: dict | None) -> None:
    """Only meaningful inside the canonical window. Outside it, say so."""
    if not reference_covers(date):
        rep.facts["reference"] = "outside the canonical window"
        return
    ref = reference_trade(date)
    rep.facts["reference"] = ref["direction"] if ref else "no reference trade"
    rep.check("session traded exactly when the reference did",
              bool(ref) == bool(direction),
              f"reference={'trade' if ref else 'none'} "
              f"live={'trade' if direction else 'none'}")
    if ref and direction:
        rep.check("direction matches the reference",
                  ref["direction"] == direction,
                  f"reference={ref['direction']} live={direction}")
        if ledger_trade:
            d = round(ledger_trade["points"] - float(ref["net_points"]), 2)
            rep.facts["difference_vs_reference_points"] = d
            rep.check("net points within the known price-series tolerance",
                      abs(d) <= REF_PRICE_TOL,
                      f"{d:+.2f} pts (tolerance {REF_PRICE_TOL}, "
                      f"NQ1! continuous vs back-adjusted)")


def _export_checks(rep: Report, date: str, path: str, direction: str,
                   fill_entry: dict | None, fill_exit: dict | None) -> None:
    trades = tv_export_trades(path, date)
    rep.facts["tv_export_trades"] = len(trades)
    if not rep.check("Strategy Tester export shows exactly one trade for the session",
                     len(trades) == 1, f"{len(trades)} trades"):
        return
    t = trades[0]
    rep.check("export direction agrees with the fill alerts",
              t["direction"] == direction,
              f"export={t['direction']} fills={direction}")
    if fill_entry:
        rep.check("export entry price agrees with the fill alert",
                  abs(t["entry"] - float(fill_entry["fill_price"])) <= TICK,
                  f"export={t['entry']} fill={fill_entry['fill_price']}")
    if fill_exit and t["exit"] is not None:
        rep.check("export exit price agrees with the fill alert",
                  abs(t["exit"] - float(fill_exit["fill_price"])) <= TICK,
                  f"export={t['exit']} fill={fill_exit['fill_price']}")


# ---------------------------------------------------------------------------
# Session log and the streak that gates deployment
# ---------------------------------------------------------------------------

def record_session(rep: Report) -> None:
    append(SESSION_LOG, rep.to_record())


def read_sessions(path: str = SESSION_LOG) -> list[dict]:
    """Latest verdict wins per date, so re-auditing a session corrects it."""
    latest: dict[str, dict] = {}
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("date"):
                latest[rec["date"]] = rec
    return [latest[d] for d in sorted(latest)]


def streak(path: str = SESSION_LOG) -> dict:
    sessions = read_sessions(path)
    run: list[dict] = []
    for rec in reversed(sessions):
        if not rec.get("clean"):
            break
        run.append(rec)
    run.reverse()
    traded = sum(1 for r in run if r.get("traded"))
    out = {"sessions_audited": len(sessions),
           "consecutive_clean": len(run),
           "of_which_traded": traded,
           "first_clean": run[0]["date"] if run else None,
           "last_audited": sessions[-1]["date"] if sessions else None,
           "pipeline_validated": len(run) >= VALIDATE_SESSIONS
                                 and traded >= VALIDATE_SESSIONS // 2,
           "live_capital_gate_met": len(run) >= LIVE_SESSIONS
                                    and traded >= LIVE_SESSIONS // 2}
    broken = [r["date"] for r in sessions if not r.get("clean")]
    out["sessions_with_failures"] = broken[-5:]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_report(rep: Report) -> None:
    print(f"DAILY AUDIT — {rep.date}")
    print("=" * 72)
    for k, v in rep.facts.items():
        print(f"  {k:34s} {v}")
    print("-" * 72)
    for c in rep.checks:
        mark = {"PASS": "PASS", "FAIL": "FAIL", "N/A": " -- "}[c["result"]]
        print(f"  {mark}  {c['check']}"
              + (f"\n          {c['detail']}" if c["detail"] else ""))
    print("=" * 72)
    print("SESSION CLEAN" if rep.clean else
          f"SESSION NOT CLEAN — {len(rep.failures)} failure(s): "
          + ", ".join(c["check"] for c in rep.failures))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="session date, YYYY-MM-DD")
    ap.add_argument("--tv-export", help="Strategy Tester 'List of Trades' CSV")
    ap.add_argument("--streak", action="store_true",
                    help="report consecutive clean sessions and the deployment gates")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-record", action="store_true",
                    help="audit without appending to the session log")
    args = ap.parse_args(argv)

    if args.streak and not args.date:
        print(json.dumps(streak(), indent=2))
        return 0
    if not args.date:
        ap.error("--date is required (or use --streak)")

    rep = audit(args.date, args.tv_export)
    if not args.no_record:
        record_session(rep)
    if args.json:
        print(json.dumps(rep.to_record(), indent=2))
    else:
        print_report(rep)
        if args.streak:
            print("\nSTREAK")
            print(json.dumps(streak(), indent=2))
    return 0 if rep.clean else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
