#!/usr/bin/env python3
"""Gates for the daily TradingView-emulator audit.

Every gate here is a break the audit MUST catch. A reconciliation tool that
reports clean when execution diverged is worse than no tool at all — it
manufactures the 20 consecutive clean sessions that gate live capital.

The last block runs the real receiver over real HTTP to prove the fill channel
is recorded and does not touch the position ledger.

Run:  python3 tests/test_daily_audit.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="nq_audit_")
os.environ["NQ_PAPER_DIR"] = TMP          # must precede the imports below
os.environ.setdefault("NQ_PAPER_CONTRACT", "MNQ")
os.environ.setdefault("NQ_PAPER_QTY", "1")

sys.path.insert(0, os.path.join(REPO, "executor"))

import daily_audit as DA                                        # noqa: E402
import paper_executor as PE                                     # noqa: E402

BUILD = PE.expected_build_id()

FAILURES: list[str] = []
DATE = "2026-08-17"                       # outside the canonical window
REF_DATE = None                           # filled in from the reference below


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def reset(pin: bool = True) -> None:
    for f in ("events.jsonl", "fills.jsonl", "ledger.jsonl", "rejected.jsonl",
              "sessions.jsonl", "chart_pin.json"):
        p = os.path.join(TMP, f)
        if os.path.exists(p):
            os.remove(p)
    if pin:
        # The pin is a deliberate operator act; every gate that is not ABOUT
        # pinning assumes it has been done.
        json.dump({"tickerid": "CME_MINI:NQ1!", "session_type": "regular",
                   "timeframe": "5", "chart_standard": True,
                   "pinned_from": "setup"}, open(DA.CHART_PIN, "w"))


def signal(event, direction, date=DATE, **kw) -> dict:
    p = {"strategy_version": "nq_orb_s5b_v1", "build_id": BUILD,
         "signal_id": f"sig|{date}|{event}", "event": event, "symbol": "NQ1!",
         "event_time": f"{date}T09:50:00-0400", "session_date": date,
         "direction": direction, "orb_direction": direction,
         "s5b_state": "WAITING_FOR_LATCH", "alignment": "UNRESOLVED"}
    if event == "SESSION_SUMMARY":
        p.update({"timeframe": "5", "eth_bars": 0, "chart_standard": True,
                  "session_type": "regular", "tickerid": "CME_MINI:NQ1!",
                  "intrabar_calcs": 0, "initial_capital": 1000000.0,
                  "chart_config_ok": True})
    p.update(kw)
    return p


def fill(event, direction, price, pos_after, date=DATE, qty=1) -> dict:
    return {"strategy_version": "nq_orb_s5b_v1", "build_id": BUILD,
            "channel": "fill", "event": event, "symbol": "NQ1!", "direction": direction,
            "fill_price": price, "fill_qty": qty, "position_after": pos_after,
            "order_comment": event, "stop": None, "session_date": date,
            "bar_time": NOW,
            "signal_id": f"fill|{date}|{event}"}


def write(name: str, payloads: list[dict]) -> None:
    with open(os.path.join(TMP, name), "a") as fh:
        for p in payloads:
            fh.write(json.dumps({"received": "2026-08-17T14:00:00Z",
                                 "payload": p}) + "\n")


def feed(signals: list[dict]) -> PE.Ledger:
    """Push signals through the real ledger, which writes events.jsonl itself —
    exactly the production path. Writing that log by hand would double-count."""
    led = PE.Ledger()
    for p in signals:
        led.apply(p, persist=True)
    return led


def clean_session(date=DATE, entry=23100.0, stop=23050.0, exit_px=23180.0,
                  direction="LONG", exit_event="SESSION_CLOSE_EXIT") -> None:
    """A session where the emulator did exactly what the strategy asked."""
    entry_evt = "ORB_LONG_ENTRY" if direction == "LONG" else "ORB_SHORT_ENTRY"
    feed([signal(entry_evt, direction, date, entry=entry, stop=stop),
          signal(exit_event, direction, date, entry=entry, stop=stop,
                 exit=exit_px),
          signal("SESSION_SUMMARY", direction, date, entry=entry, stop=stop,
                 exit=exit_px, traded=True)])
    write("fills.jsonl", [
        fill(entry_evt, direction, entry, 1 if direction == "LONG" else -1, date),
        fill(exit_event, direction, exit_px, 0, date)])


def audit(date=DATE, **kw):
    return DA.audit(date, **kw)


def names(rep) -> list[str]:
    return [c["check"] for c in rep.failures]


def main() -> int:
    global REF_DATE
    print("fill payload validation")
    ok = lambda p: (PE.validate_fill(p), True)[1]
    try:
        ok(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)); good = True
    except PE.Rejected as e:
        good, err = False, str(e)
    check("a well-formed fill validates", good)

    def rejects(p, fragment):
        try:
            PE.validate_fill(p)
            return False
        except PE.Rejected as e:
            return fragment in str(e)

    bad = fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)
    check("an unexpanded TradingView placeholder is rejected",
          rejects({**bad, "order_comment": "{{strategy.order.comment}}"},
                  "unexpanded"))
    check("a fill for S5b — which places no order — is rejected",
          rejects({**bad, "event": "S5B_LONG_CONFIRMED"}, "places no order"))
    check("a non-numeric fill price is rejected",
          rejects({**bad, "fill_price": "23100"}, "not a number"))
    check("a zero fill quantity is rejected",
          rejects({**bad, "fill_qty": 0}, "positive"))
    check("a missing field is rejected",
          rejects({k: v for k, v in bad.items() if k != "position_after"},
                  "missing required field"))
    check("a fill from another strategy is rejected",
          rejects({**bad, "strategy_version": "something_else"},
                  "unknown strategy"))
    seen: dict[str, str] = {}
    PE.record_fill(fill("ORB_LONG_ENTRY", "LONG", 1.0, 1), seen)
    try:
        PE.record_fill(fill("ORB_LONG_ENTRY", "LONG", 1.0, 1), seen)
        dup = False
    except PE.Rejected as e:
        dup = "duplicate" in str(e)
    check("a re-fired fill alert is rejected as a duplicate", dup)

    print("\nclean session")
    reset()
    clean_session()
    rep = audit()
    check("a session the emulator executed as instructed is clean",
          rep.clean, ", ".join(names(rep)))
    check("the audit sees the trade", rep.facts.get("traded") is True
          and rep.facts.get("ledger_trades") == 1)
    check("slippage measured on both legs",
          rep.facts.get("slippage_points", {}) == {"entry": 0.0, "exit": 0.0},
          json.dumps(rep.facts.get("slippage_points")))
    check("a session outside the canonical window says so",
          rep.facts.get("reference") == "outside the canonical window")

    print("\nbreaks the audit must catch")
    reset()
    clean_session()
    # drop the exit fill: TradingView never closed the position
    lines = [l for l in open(os.path.join(TMP, "fills.jsonl"))
             if "SESSION_CLOSE_EXIT" not in l]
    open(os.path.join(TMP, "fills.jsonl"), "w").writelines(lines)
    rep = audit()
    check("a position TradingView never closed fails", not rep.clean,
          ", ".join(names(rep)))
    check("and the failure names the unclosed position",
          any("closed" in n for n in names(rep)))

    reset()
    feed([signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0)])
    write("fills.jsonl", [fill("ORB_SHORT_ENTRY", "SHORT", 23100.0, -1)])
    rep = audit()
    check("the emulator filling the opposite direction fails", not rep.clean)
    check("and the failure names the direction",
          any("direction" in n for n in names(rep)), ", ".join(names(rep)))

    reset()
    clean_session(entry=23100.0, exit_px=23180.0)
    lines = open(os.path.join(TMP, "fills.jsonl")).read().replace(
        '"fill_price": 23100.0', '"fill_price": 23102.0')
    open(os.path.join(TMP, "fills.jsonl"), "w").write(lines)
    rep = audit()
    check("2 points of entry slippage fails the 1.5-point kill threshold",
          not rep.clean and any("slippage" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    clean_session(entry=23100.0, exit_px=23180.0)
    lines = open(os.path.join(TMP, "fills.jsonl")).read().replace(
        '"fill_price": 23100.0', '"fill_price": 23100.5')
    open(os.path.join(TMP, "fills.jsonl"), "w").write(lines)
    rep = audit()
    check("0.5 points of slippage is inside the modelled cost and stays clean",
          rep.clean, ", ".join(names(rep)))

    reset()
    clean_session()
    lines = open(os.path.join(TMP, "fills.jsonl")).read().replace(
        '"position_after": 1', '"position_after": 2')
    open(os.path.join(TMP, "fills.jsonl"), "w").write(lines)
    rep = audit()
    check("TradingView holding two contracts fails",
          not rep.clean and any("position after entry" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    clean_session()
    write("fills.jsonl", [{**fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1),
                           "signal_id": "fill|dup|2"}])
    rep = audit()
    check("a second entry fill in one session fails",
          not rep.clean and any("one entry fill" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)])
    rep = audit()
    check("a fill with no signal behind it fails",
          not rep.clean and any("orphan" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    # a stop that filled BETTER than its trigger — not a real market event
    feed([signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0),
          signal("ORB_STOP", "LONG", entry=23100.0, stop=23050.0, exit=23050.0)])
    write("fills.jsonl", [
        fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1),
        fill("ORB_STOP", "LONG", 23055.0, 0)])
    rep = audit()
    check("a stop filling better than its trigger fails",
          not rep.clean and any("better than" in n for n in names(rep)),
          ", ".join(names(rep)))

    print("\nno-trade sessions and the silence hole")
    reset()
    feed([signal("SESSION_SUMMARY", "NONE", traded=False)])
    rep = audit()
    check("a session the strategy declined, with its heartbeat, is clean",
          rep.clean and rep.facts.get("traded") is False, ", ".join(names(rep)))

    reset()
    rep = audit()
    check("TOTAL SILENCE is not a clean session",
          not rep.clean and any("reported in" in n for n in names(rep)),
          "a dead receiver or an expired alert must never bank a clean day")
    check("and the verdict says so in the detail",
          any("NOT a quiet session" in c["detail"] for c in rep.failures))

    reset()
    feed([signal("SESSION_SUMMARY", "LONG", traded=True)])
    rep = audit()
    check("a heartbeat claiming a trade that never arrived fails",
          not rep.clean and any("heartbeat agrees" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    clean_session()
    write("events.jsonl", [signal("SESSION_SUMMARY", "LONG", traded=True,
                                  signal_id="sig|dup|summary")])
    rep = audit()
    check("two heartbeats in one session fails",
          not rep.clean and any("reported in" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()
    feed([signal("SESSION_SUMMARY", "NONE", traded=False)])
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)])
    rep = audit()
    check("an orphan fill with no signal behind it fails",
          not rep.clean and any("orphan" in n for n in names(rep)),
          ", ".join(names(rep)))

    print("\nreference cross-check inside the canonical window")
    import csv as _csv
    with open(DA.CANONICAL, encoding="utf-8-sig") as fh:
        rows = list(_csv.DictReader(fh))
    ref = rows[-1]
    REF_DATE = ref["date"]
    reset()
    opp = "SHORT" if ref["direction"] == "LONG" else "LONG"
    clean_session(date=REF_DATE, entry=float(ref["entry"]),
                  stop=float(ref["stop"]), exit_px=float(ref["exit"]),
                  direction=ref["direction"],
                  exit_event=("ORB_STOP" if ref["exit_reason"] == "stop"
                              else "SESSION_CLOSE_EXIT"))
    rep = audit(REF_DATE)
    check("a session matching the reference is clean", rep.clean,
          ", ".join(names(rep)))
    check("the reference comparison actually ran",
          "difference_vs_reference_points" in rep.facts,
          json.dumps(rep.facts.get("difference_vs_reference_points")))
    reset()
    clean_session(date=REF_DATE, entry=float(ref["entry"]),
                  stop=float(ref["stop"]), exit_px=float(ref["exit"]),
                  direction=opp)
    rep = audit(REF_DATE)
    check("trading the opposite direction to the reference fails",
          not rep.clean and any("reference" in n for n in names(rep)),
          ", ".join(names(rep)))
    reset()
    feed([signal("SESSION_SUMMARY", "NONE", REF_DATE, traded=False)])
    rep = audit(REF_DATE)
    check("not trading on a day the reference traded fails",
          not rep.clean and any("reference did" in n for n in names(rep)),
          ", ".join(names(rep)))

    print("\npartial-silence paths (S1-S8)")
    # One stream goes quiet while the others look healthy. NOTE: these are NOT
    # the round-1 red-team scenarios — conflating the two is how R1/R2/R5 below
    # survived a whole review round. Those are gated separately.

    reset()   # 1. heartbeat and signal arrive, the fill never does
    feed([signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0),
          signal("SESSION_CLOSE_EXIT", "LONG", entry=23100.0, stop=23050.0,
                 exit=23180.0),
          signal("SESSION_SUMMARY", "LONG", traded=True)])
    rep = audit()
    check("S1 signal and heartbeat arrive but no fill does",
          not rep.clean and any("reached the emulator" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()   # 2. the emulator acted, the decision stream did not arrive
    feed([signal("SESSION_SUMMARY", "NONE", traded=False)])
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1),
                          fill("SESSION_CLOSE_EXIT", "LONG", 23180.0, 0)])
    rep = audit()
    check("S2 fills arrive with no signal channel behind them",
          not rep.clean and any("orphan" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()   # 3. entry filled, exit fill never came, heartbeat says day ended
    feed([signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0),
          signal("SESSION_SUMMARY", "LONG", traded=True)])
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)])
    rep = audit()
    check("S3 a heartbeat does NOT imply the position was closed",
          not rep.clean and any("position was closed" in n for n in names(rep)),
          ", ".join(names(rep)))

    reset()   # 4. a stale heartbeat, rejected by the receiver's freshness gate
    led = PE.Ledger()
    try:
        led.apply(signal("SESSION_SUMMARY", "NONE", traded=False,
                         event_time="2020-01-01T10:00:00+0000"),
                  persist=True, enforce_freshness=True)
        stale_ok = False
    except PE.Rejected as e:
        stale_ok = "stale" in str(e)
    rep = audit()
    check("S4 a stale heartbeat is refused and cannot rescue the session",
          stale_ok and not rep.clean
          and any("reported in" in n for n in names(rep)))

    reset()   # 5. a fill attributed to the wrong New York date
    clean_session(date="2026-08-18")
    lines = open(os.path.join(TMP, "fills.jsonl")).read().replace(
        '"session_date": "2026-08-18"', '"session_date": "2026-08-17"', 1)
    open(os.path.join(TMP, "fills.jsonl"), "w").write(lines)
    a, b = audit("2026-08-18"), audit("2026-08-17")
    check("S5 a misdated fill fails BOTH days, never silently one",
          not a.clean and not b.clean,
          f"2026-08-18={names(a)}  2026-08-17={names(b)}")

    reset()   # 6. TradingView disabled the alert mid-session (rate limit)
    feed([signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0)])
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)])
    rep = audit()
    check("S6 an alert disabled mid-session loses the heartbeat and fails",
          not rep.clean and any("reported in" in n for n in names(rep)),
          "TradingView does not auto-re-enable, so no heartbeat at close "
          "means it was down at close")

    reset()   # 8. heartbeat claims no trade while a fill says otherwise
    feed([signal("SESSION_SUMMARY", "NONE", traded=False)])
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1),
                          fill("ORB_STOP", "LONG", 23050.0, 0)])
    rep = audit()
    check("S8 a traded=false heartbeat cannot outvote an observed fill",
          not rep.clean and any("heartbeat agrees" in n for n in names(rep)),
          ", ".join(names(rep)))

    print("\nround-2 red-team: gate integrity (R1, R2, R5, R6)")
    reset()
    sl = os.path.join(TMP, "sessions.jsonl")

    # R1 — a holiday marker must not be able to erase a failed live session
    PE.append(sl, {"date": "2026-08-20", "mode": "live", "clean": False,
                   "traded": True, "failures": ["the session's position was closed"]})
    try:
        DA.mark_holiday("2026-08-20"); refused = False
    except SystemExit as e:
        refused = "REFUSED" in str(e)
    check("R1 a date with a live verdict cannot be marked a holiday", refused)
    PE.append(sl, {"date": "2026-08-20", "mode": "holiday", "clean": True,
                   "traded": False})
    s2 = DA.streak(sl)
    check("R1 even a forged holiday record does not clear the failure",
          s2["sessions_with_failures"] == ["2026-08-20"]
          and s2.get("holiday_markers_ignored") == ["2026-08-20"], json.dumps(s2))

    # R1b — a genuine holiday, with no pipeline traffic at all, is still allowed
    reset()
    h = DA.mark_holiday("2026-11-26")
    check("R1b a date with no verdict, signal or fill can still be a holiday",
          h["mode"] == "holiday")
    reset()
    feed([signal("SESSION_SUMMARY", "NONE", "2026-11-26", traded=False)])
    try:
        DA.mark_holiday("2026-11-26"); refused = False
    except SystemExit as e:
        refused = "signal alert" in str(e)
    check("R1c a date the strategy sent a heartbeat on is not a holiday", refused)

    # R2 — a later CLEAN must not rehabilitate a historical BREAK
    reset()
    PE.append(sl, {"date": "2026-08-19", "mode": "live", "clean": True,
                   "traded": True})
    PE.append(sl, {"date": "2026-08-20", "mode": "live", "clean": False,
                   "traded": True, "failures": ["fill missing"]})
    PE.append(sl, {"date": "2026-08-20", "mode": "live", "clean": True,
                   "traded": True, "failures": []})
    s2 = DA.streak(sl)
    check("R2 re-auditing a failed session cannot un-break it",
          s2["consecutive_clean"] == 0
          and s2["sessions_with_failures"] == ["2026-08-20"], json.dumps(s2))
    r = DA.reset_streak("logs rebuilt after disk failure")
    check("R2b an explicit reset is the only way out, and starts at zero",
          r["mode"] == "reset" and DA.streak(sl)["consecutive_clean"] == 0
          and DA.streak(sl)["streak_reset_at"] is not None)

    # R5 — a conflicting body must not hide behind the idempotency gate
    seen2: dict[str, str] = {}
    PE.record_fill(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1), seen2)
    try:
        PE.record_fill(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1), seen2)
        benign = False
    except PE.Rejected as e:
        benign = str(e).startswith("duplicate")
    check("R5 an identical retry is a benign duplicate", benign)
    try:
        PE.record_fill(fill("ORB_LONG_ENTRY", "LONG", 23999.0, 1), seen2)
        conflict = False
    except PE.Rejected as e:
        conflict = "CONFLICTING" in str(e)
    check("R5 the same id with a different body is corruption, not a retry",
          conflict)
    check("R5 and a conflict is NOT on the benign list the audit forgives",
          not any(b in "CONFLICTING fill signal_id x: same id, different body"
                  for b in DA.BENIGN_REJECTIONS))

    # R6 — clock skew must fail safe, never produce a false clean
    reset()
    clean_session(date=DATE)
    skewed = audit("2026-08-19")          # as if the box's clock were 2 days out
    check("R6 auditing the wrong date under clock skew fails, never passes",
          not skewed.clean and any("reported in" in n for n in names(skewed)),
          ", ".join(names(skewed)))

    print("\nround-3 red-team: identity and chart configuration (R7-R9)")

    # R7 — a complete, internally consistent trade on the WRONG SYMBOL
    reset()
    wrong = lambda p: {**p, "symbol": "WRONG1!"}
    led = PE.Ledger()
    rejected_at_door = 0
    for p in [signal("ORB_LONG_ENTRY", "LONG", entry=23100.0, stop=23050.0),
              signal("SESSION_CLOSE_EXIT", "LONG", entry=23100.0, stop=23050.0,
                     exit=23180.0),
              signal("SESSION_SUMMARY", "LONG", traded=True)]:
        try:
            led.apply(wrong(p), persist=True)
        except PE.Rejected as e:
            rejected_at_door += "not the configured signal source" in str(e)
    check("R7 the receiver refuses payloads from another chart",
          rejected_at_door == 3, f"{rejected_at_door}/3 refused")
    # and if they reach the log anyway (edited file, older receiver), the audit
    # must still refuse to call the session clean
    reset()
    write("events.jsonl", [wrong(signal("ORB_LONG_ENTRY", "LONG", entry=23100.0,
                                        stop=23050.0)),
                           wrong(signal("SESSION_CLOSE_EXIT", "LONG",
                                        entry=23100.0, stop=23050.0,
                                        exit=23180.0)),
                           wrong(signal("SESSION_SUMMARY", "LONG", traded=True))])
    write("fills.jsonl", [wrong(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)),
                          wrong(fill("SESSION_CLOSE_EXIT", "LONG", 23180.0, 0))])
    rep = audit()
    check("R7 an internally consistent trade on WRONG1! is NOT clean",
          not rep.clean and any("signal source" in n for n in names(rep)),
          ", ".join(names(rep)))
    check("R7 and the verdict records which symbols were seen",
          rep.facts.get("symbols") == ["WRONG1!"], json.dumps(rep.facts.get("symbols")))

    # R8 — a stale TradingView alert snapshot from an older Pine build
    reset()
    stale = lambda p: {**p, "build_id": "r2-0000deadbeef"}
    led = PE.Ledger()
    try:
        led.apply(stale(signal("ORB_LONG_ENTRY", "LONG", entry=23100.0,
                               stop=23050.0)), persist=True)
        refused = False
    except PE.Rejected as e:
        refused = "not the deployed build" in str(e)
    check("R8 the receiver refuses an alert from a different Pine build", refused)
    reset()
    write("events.jsonl", [stale(signal("ORB_LONG_ENTRY", "LONG", entry=23100.0,
                                        stop=23050.0)),
                           stale(signal("SESSION_CLOSE_EXIT", "LONG",
                                        entry=23100.0, stop=23050.0,
                                        exit=23180.0)),
                           stale(signal("SESSION_SUMMARY", "LONG", traded=True))])
    write("fills.jsonl", [stale(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)),
                          stale(fill("SESSION_CLOSE_EXIT", "LONG", 23180.0, 0))])
    rep = audit()
    check("R8 a stale build's session is NOT clean",
          not rep.clean and any("deployed Pine build" in n for n in names(rep)),
          ", ".join(names(rep)))
    check("R8 the build stamp tracks the file, so an unstamped edit is caught",
          subprocess.run([sys.executable,
                          os.path.join(REPO, "tests", "stamp_build_id.py"),
                          "--check"], capture_output=True).returncode == 0)

    # R9 — an ETH chart on an ordinary full session
    reset()
    clean_session()
    lines = open(os.path.join(TMP, "events.jsonl")).read().replace(
        '"eth_bars": 0', '"eth_bars": 96').replace(
        '"chart_config_ok": true', '"chart_config_ok": false')
    open(os.path.join(TMP, "events.jsonl"), "w").write(lines)
    rep = audit()
    check("R9 an ETH chart fails even on an ordinary full session",
          not rep.clean and any("regular trading hours" in n for n in names(rep)),
          ", ".join(names(rep)))
    reset()
    clean_session()
    lines = open(os.path.join(TMP, "events.jsonl")).read().replace(
        '"timeframe": "5"', '"timeframe": "15"')
    open(os.path.join(TMP, "events.jsonl"), "w").write(lines)
    rep = audit()
    check("R9 a wrong timeframe fails",
          not rep.clean and any("5-minute" in n for n in names(rep)),
          ", ".join(names(rep)))
    reset()
    clean_session()
    lines = [l for l in open(os.path.join(TMP, "events.jsonl"))]
    import re as _re
    lines = [_re.sub(r'"eth_bars": \d+, ', "", l) for l in lines]
    open(os.path.join(TMP, "events.jsonl"), "w").writelines(lines)
    rep = audit()
    check("R9 a heartbeat that cannot answer the question is not a pass",
          not rep.clean, ", ".join(names(rep)))

    # N/A semantics — a required check that could not run is a BREAK
    r = DA.Report("x")
    r.check("required but unanswerable", None)
    check("a required N/A makes the session unclean", not r.clean)
    r2 = DA.Report("x")
    r2.check("optional diagnostic", None, required=False)
    check("an optional N/A does not", r2.clean)

    print("\nround-4 red-team: chart identity (R10-R13)")

    def with_hb(**over):
        """A clean session whose heartbeat carries a doctored chart identity."""
        reset()
        clean_session()
        txt = open(os.path.join(TMP, "events.jsonl")).read()
        for k, v in over.items():
            txt = re.sub(rf'"{k}": [^,}}]+', f'"{k}": {json.dumps(v)}', txt)
        open(os.path.join(TMP, "events.jsonl"), "w").write(txt)
        return audit()

    # R10 — synthetic OHLC from a non-standard chart type
    rep = with_hb(chart_standard=False)
    check("R10 a Heikin-Ashi / Renko / Range chart fails",
          not rep.clean and any("standard candlestick" in n for n in names(rep)),
          ", ".join(names(rep)))
    check("R10 and the reason names synthetic OHLC",
          any("SYNTHETIC OHLC" in c["detail"] for c in rep.failures))

    # R11 — declared session identity, not inferred from absent bars
    rep = with_hb(session_type="extended")
    check("R11 a chart declaring the extended session fails",
          not rep.clean and any("expected session" in n for n in names(rep)),
          ", ".join(names(rep)))
    rep = with_hb(session_type="extended", eth_bars=0)
    check("R11 and it fails even with ZERO overnight bars seen",
          not rep.clean and any("expected session" in n for n in names(rep)),
          "absence of ETH bars is not evidence of a regular-session chart")

    # R12 — ticker construction, pinned
    rep = with_hb(tickerid="CME_MINI:NQ1!_UNADJUSTED")
    check("R12 a changed ticker construction fails against the pin",
          not rep.clean and any("matches the pin" in n for n in names(rep)),
          ", ".join(names(rep)))
    reset(pin=False)
    clean_session()
    rep = audit()
    check("R12 an UNPINNED chart is not clean",
          not rep.clean and any("is pinned" in n for n in names(rep)),
          "the audit cannot tell back-adjusted from unadjusted, and forward "
          "sessions have no reference to catch it")
    check("R12 and the failure tells the operator exactly what to do",
          any("--pin-chart" in c["detail"] for c in rep.failures))
    pin = DA.pin_chart(DATE)
    check("R12 pinning records the construction the session reported",
          pin["tickerid"] == "CME_MINI:NQ1!" and pin["pinned_from"] == DATE,
          json.dumps(pin))
    check("R12 and the session is clean once pinned", audit().clean)
    try:
        reset(pin=False); DA.pin_chart(DATE); pinned_nothing = False
    except SystemExit as e:
        pinned_nothing = "nothing to pin" in str(e)
    check("R12 pinning refuses when there is no heartbeat to pin from",
          pinned_nothing)

    # R13 — Strategy Properties are snapshot with the alert; BUILD_ID is not enough
    rep = with_hb(intrabar_calcs=417)
    check("R13 a calc_on_every_tick override fails",
          not rep.clean and any("calc_on_every_tick" in n for n in names(rep)),
          ", ".join(names(rep)))
    rep = with_hb(initial_capital=25000.0)
    check("R13 an initial-capital override fails",
          not rep.clean and any("initial capital" in n for n in names(rep)),
          ", ".join(names(rep)))
    reset()
    clean_session()
    txt = open(os.path.join(TMP, "events.jsonl")).read()
    txt = re.sub(r'"(chart_standard|session_type|tickerid|intrabar_calcs|'
                 r'initial_capital)": [^,}]+, ', "", txt)
    open(os.path.join(TMP, "events.jsonl"), "w").write(txt)
    rep = audit()
    check("R13 a heartbeat that omits the chart identity is not clean",
          not rep.clean, ", ".join(names(rep))[:120])

    print("\nStrategy Tester export cross-check")
    reset()
    clean_session(entry=23100.0, exit_px=23180.0)
    export = os.path.join(TMP, "list_of_trades.csv")

    def write_export(entry_px, exit_px, direction="Entry long"):
        with open(export, "w", newline="") as fh:
            fh.write("Trade #,Type,Date/Time,Price USD,Size (value)\n")
            fh.write(f"1,{direction},{DATE} 09:50,{entry_px},{entry_px * 2}\n")
            fh.write(f"1,Exit long,{DATE} 15:55,{exit_px},{exit_px * 2}\n")

    write_export(23100.0, 23180.0)
    rep = audit(tv_export=export)
    check("an export agreeing with the fill alerts is clean", rep.clean,
          ", ".join(names(rep)))
    check("the export comparison actually ran",
          rep.facts.get("tv_export_trades") == 1)
    write_export(23100.0, 23195.0)
    rep = audit(tv_export=export)
    check("an export whose exit price disagrees with the fill alert fails",
          not rep.clean and any("export exit price" in n for n in names(rep)),
          ", ".join(names(rep)))
    with open(export, "w", newline="") as fh:
        fh.write("Trade #,Type,Date/Time,Price USD,Size (value)\n")
    rep = audit(tv_export=export)
    check("an export showing no trade for a session that traded fails",
          not rep.clean and any("exactly one trade" in n for n in names(rep)),
          ", ".join(names(rep)))

    print("\nstreak accounting")
    reset()
    sl = os.path.join(TMP, "sessions.jsonl")
    for i, (d, clean, traded) in enumerate([
            ("2026-08-03", True, True), ("2026-08-04", False, True),
            ("2026-08-05", True, True), ("2026-08-06", True, False),
            ("2026-08-07", True, True)]):
        PE.append(sl, {"date": d, "clean": clean, "traded": traded,
                       "mode": "live"})
    s = DA.streak(sl)
    check("the streak stops at the last failure", s["consecutive_clean"] == 3,
          json.dumps(s))
    check("untraded sessions are counted separately",
          s["of_which_traded"] == 2, json.dumps(s))
    check("three clean sessions do not validate the pipeline",
          s["pipeline_validated"] is False)
    check("nor does it meet the live-capital gate",
          s["live_capital_gate_met"] is False)
    check("the failing session is named",
          s["sessions_with_failures"] == ["2026-08-04"])
    PE.append(sl, {"date": "2026-08-07", "clean": False, "traded": True,
                   "mode": "live"})
    check("re-auditing a session overwrites the earlier verdict",
          DA.streak(sl)["consecutive_clean"] == 0)
    os.remove(sl)
    for i in range(30):
        PE.append(sl, {"date": f"2026-09-{i + 1:02d}", "clean": True,
                       "traded": True, "mode": "live"})
    s = DA.streak(sl)
    check("30 clean traded sessions validate the pipeline",
          s["pipeline_validated"] is True and s["consecutive_clean"] == 30)
    check("30 sessions do not open the live-capital gate",
          s["live_capital_gate_met"] is False)
    os.remove(sl)
    for i in range(25):
        PE.append(sl, {"date": f"2026-10-{i + 1:02d}", "clean": True,
                       "traded": False, "mode": "live"})
    check("25 clean but untraded sessions do NOT validate the pipeline",
          DA.streak(sl)["pipeline_validated"] is False,
          "quiet days prove nothing about execution")

    print("\nexit code and session log")
    reset()
    clean_session()
    env = {**os.environ, "NQ_PAPER_DIR": TMP}
    r = subprocess.run([sys.executable,
                        os.path.join(REPO, "executor", "daily_audit.py"),
                        "--date", DATE], env=env, capture_output=True, text=True)
    check("a clean session exits 0", r.returncode == 0, r.stdout[-300:])
    check("the verdict is written to the session log",
          os.path.exists(os.path.join(TMP, "sessions.jsonl"))
          and DA.read_sessions(os.path.join(TMP, "sessions.jsonl"))[-1]["clean"]
          is True)
    reset()
    write("fills.jsonl", [fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1)])
    r = subprocess.run([sys.executable,
                        os.path.join(REPO, "executor", "daily_audit.py"),
                        "--date", DATE], env=env, capture_output=True, text=True)
    check("a broken session exits 1", r.returncode == 1)

    print("\nreceiver routes the fill channel without moving the ledger")
    reset()
    port = 8803
    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "executor", "paper_executor.py"),
         "serve", "--port", str(port)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def post(p):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/",
                                         data=json.dumps(p).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                r = urllib.request.urlopen(req, timeout=5)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
                break
            except Exception:
                time.sleep(0.1)
        code, body = post(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1))
        check("a fill payload is accepted",
              code == 200 and body["action"] == "fill_recorded", json.dumps(body))
        snap = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=5).read())
        check("the fill did NOT open a paper position",
              snap["open_position"] is None and snap["closed_trades"] == 0
              and snap["fills_recorded"] == 1, json.dumps(snap))
        code, body = post(fill("ORB_LONG_ENTRY", "LONG", 23100.0, 1))
        check("a duplicate fill is rejected over HTTP",
              code == 400 and "duplicate" in body["rejected"])
        code, body = post({**fill("ORB_STOP", "LONG", 23050.0, 0),
                           "order_comment": "{{strategy.order.comment}}"})
        check("a mis-configured alert message is rejected over HTTP",
              code == 400 and "unexpanded" in body["rejected"])
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("ALL DAILY-AUDIT GATES PASS")
    print(f"state dir: {TMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
