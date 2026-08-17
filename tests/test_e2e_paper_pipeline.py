#!/usr/bin/env python3
"""End-to-end proof: TradingView alert -> webhook -> paper order -> stop/close
-> audit ledger.

Runs the real receiver over real HTTP on a temporary state directory, posts the
exact JSON payloads `pine/nq_orb_s5b_v1.pine` emits, and prints the resulting
audit trail. Nothing is mocked except the market itself.

The session replayed is a real one from the canonical reference, so the payloads
carry prices the strategy actually traded.

Run:  python3 tests/test_e2e_paper_pipeline.py
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXEC = os.path.join(REPO, "executor", "paper_executor.py")
PORT = 8801
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def post(payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def state() -> dict:
    return json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{PORT}/", timeout=5).read())


def real_session() -> dict:
    """A real losing session from the reference: entry, stop, and a stop-out."""
    path = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
    for r in csv.DictReader(open(path)):
        if r["date"] >= "2026-06-01" and r["exit_reason"] == "stop":
            return r
    raise SystemExit("no suitable session found")


def main() -> int:
    trade = real_session()
    print(f"replaying a real reference session: {trade['date']} {trade['direction']}"
          f"  entry {trade['entry']}  stop {trade['stop']}  "
          f"exit {trade['exit']} ({trade['exit_reason']})\n")

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "NQ_PAPER_DIR": tmp,
               "NQ_PAPER_CONTRACT": "MNQ", "NQ_PAPER_QTY": "1"}
        proc = subprocess.Popen([sys.executable, EXEC, "serve", "--port", str(PORT)],
                                env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    state(); break
                except Exception:
                    time.sleep(0.1)
            else:
                raise SystemExit("receiver did not start")

            now = datetime.now(timezone.utc)
            stamp = lambda off: (now + timedelta(minutes=off)).strftime(
                "%Y-%m-%dT%H:%M:%S+0000")
            side = trade["direction"]
            sess = trade["date"]
            base = dict(strategy_version="nq_orb_s5b_v1", symbol="NQ1!",
                        orb_direction=side, s5b_state="WAITING_FOR_LATCH",
                        alignment="UNRESOLVED", session_date=sess,
                        or_high=None, or_low=None)

            entry_alert = {**base,
                           "signal_id": f"nq_orb_s5b_v1|NQ1!|{sess}|ENTRY",
                           "event": f"ORB_{side}_ENTRY", "direction": side,
                           "event_time": stamp(-10),
                           "entry": float(trade["entry"]),
                           "stop": float(trade["stop"])}
            s5b_alert = {**base,
                         "signal_id": f"nq_orb_s5b_v1|NQ1!|{sess}|S5B",
                         "event": f"S5B_{side}_CONFIRMED", "direction": side,
                         "event_time": stamp(-5), "s5b_state": "CONFIRMED",
                         "alignment": "ALIGNED", "entry": None, "stop": None}
            stop_alert = {**base,
                          "signal_id": f"nq_orb_s5b_v1|NQ1!|{sess}|STOP",
                          "event": "ORB_STOP", "direction": side,
                          "event_time": stamp(-1),
                          "entry": float(trade["entry"]),
                          "stop": float(trade["stop"])}

            print("STEP 1 — entry alert fires")
            code, body = post(entry_alert)
            check("entry accepted", code == 200 and body["action"] == "opened")
            check("protective stop resting immediately",
                  body.get("protective_stop", {}).get("resting") is True,
                  json.dumps(body.get("protective_stop")))

            print("STEP 2 — S5b confirmation fires (classifier, must not trade)")
            code, body = post(s5b_alert)
            st = state()
            check("S5b recorded as state only",
                  code == 200 and body["action"] == "state_recorded"
                  and st["closed_trades"] == 0
                  and st["open_position"]["entry"] == float(trade["entry"]))

            print("STEP 3 — TradingView re-fires the entry (duplicate)")
            code, body = post(entry_alert)
            check("duplicate rejected", code == 400 and "duplicate" in body["rejected"])

            print("STEP 4 — a stale alert arrives")
            code, body = post({**stop_alert, "signal_id": "stale-1",
                               "event_time": "2020-01-01T10:00:00+0000"})
            check("stale rejected", code == 400 and "stale" in body["rejected"])

            print("STEP 5 — malformed payload")
            code, body = post({"event": "ORB_STOP"})
            check("malformed rejected",
                  code == 400 and "missing required field" in body["rejected"])

            print("STEP 6 — stop is hit")
            code, body = post(stop_alert)
            # net of the same 0.75-point round-turn cost the reference applies
            expected = round(float(trade["net_points"]), 2)
            check("position flattened", code == 200 and body["action"] == "closed")
            check("points match the reference trade exactly",
                  abs(body["points"] - expected) < 0.01,
                  f"ledger {body['points']} vs reference {expected}")
            check("dollars sized at 1 MNQ ($2/pt)",
                  abs(body["dollars"] - body["points"] * 2) < 0.01,
                  f"${body['dollars']}")

            print("STEP 6b — end-of-session heartbeat")
            code, body = post({**base,
                               "signal_id": f"nq_orb_s5b_v1|NQ1!|{sess}|SUMMARY",
                               "event": "SESSION_SUMMARY", "direction": side,
                               "event_time": stamp(0), "traded": True,
                               "entry": float(trade["entry"]),
                               "stop": float(trade["stop"]),
                               "exit": float(trade["exit"])})
            check("heartbeat recorded as state, reporting the session traded",
                  code == 200 and body["action"] == "state_recorded"
                  and body["traded"] is True, json.dumps(body))

            print("STEP 7 — a second entry arrives the same session")
            code, body = post({**entry_alert, "signal_id": "second-entry",
                               "event_time": stamp(0)})
            check("one-trade-per-day enforced",
                  code == 400 and "already traded" in body["rejected"])

            print("STEP 8 — restart recovery")
            proc.terminate(); proc.wait(timeout=5)
            out = subprocess.run([sys.executable, EXEC, "replay"], env=env,
                                 capture_output=True, text=True)
            rebuilt = json.loads(out.stdout)
            check("state rebuilt from the durable log",
                  rebuilt["closed_trades"] == 1
                  and abs(rebuilt["net_points"] - expected) < 0.01
                  and rebuilt["open_position"] is None,
                  json.dumps({k: rebuilt[k] for k in
                              ("closed_trades", "net_points", "sessions_traded")}))

            print("\nSTEP 9 — audit ledger")
            for name in ("events.jsonl", "ledger.jsonl", "rejected.jsonl"):
                path = os.path.join(tmp, name)
                n = sum(1 for _ in open(path)) if os.path.exists(path) else 0
                print(f"   {name:16s} {n} record(s)")
            check("every accepted event is on the audit log",
                  sum(1 for _ in open(os.path.join(tmp, "events.jsonl"))) == 4)
            check("every rejection is logged with a reason",
                  sum(1 for _ in open(os.path.join(tmp, "rejected.jsonl"))) == 4)
            print("\n   rejection reasons recorded:")
            for line in open(os.path.join(tmp, "rejected.jsonl")):
                print(f"     - {json.loads(line)['reason']}")

            print("\nSTEP 10 — daily reconciliation")
            out = subprocess.run([sys.executable, EXEC, "reconcile", "--date", sess],
                                 env=env, capture_output=True, text=True)
            rec = json.loads(out.stdout)
            print("   " + json.dumps(rec, indent=2).replace("\n", "\n   "))
            check("reconciliation sees exactly one paper trade",
                  rec["paper_trades"] == 1)
            check("reconciliation matches the reference direction",
                  rec.get("direction_match") is True)
            check("reconciliation reports no open position",
                  rec["open_position_at_report"] is False)

            print("\nSTEP 11 — TradingView's own fill reports, and the daily audit")
            proc = subprocess.Popen(
                [sys.executable, EXEC, "serve", "--port", str(PORT)], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                try:
                    state(); break
                except Exception:
                    time.sleep(0.1)
            fill = lambda evt, px, pos: {
                "strategy_version": "nq_orb_s5b_v1", "channel": "fill",
                "event": evt, "symbol": "NQ1!", "direction": side,
                "fill_price": px, "fill_qty": 1, "position_after": pos,
                "order_comment": evt, "stop": float(trade["stop"]),
                "session_date": sess, "bar_time": stamp(0),
                "signal_id": f"nq_orb_s5b_v1|NQ1!|{sess}|{evt}"}
            code, body = post(fill(f"ORB_{side}_ENTRY", float(trade["entry"]),
                                   1 if side == "LONG" else -1))
            check("entry fill recorded",
                  code == 200 and body["action"] == "fill_recorded")
            code, body = post(fill("ORB_STOP", float(trade["stop"]), 0))
            check("stop fill recorded",
                  code == 200 and body["action"] == "fill_recorded")
            st = state()
            check("fills did not disturb the position ledger",
                  st["closed_trades"] == 1 and st["open_position"] is None
                  and st["fills_recorded"] == 2, json.dumps(
                      {k: st[k] for k in ("closed_trades", "fills_recorded")}))
            out = subprocess.run(
                [sys.executable, os.path.join(REPO, "executor", "daily_audit.py"),
                 "--date", sess, "--json"], env=env, capture_output=True, text=True)
            verdict = json.loads(out.stdout)
            # This session had four faults injected into it in steps 3-7, so the
            # audit MUST refuse to call it clean. A tool that passed here would
            # be manufacturing the streak that gates live capital.
            check("audit refuses to call a fault-injected session clean",
                  out.returncode == 1 and verdict["clean"] is False,
                  ", ".join(verdict.get("failures", [])))
            check("the injected faults are named, the duplicate is not",
                  verdict.get("duplicate_alerts_ignored") == 1
                  and any("rejections" in f for f in verdict["failures"]),
                  json.dumps({"failures": verdict["failures"],
                              "rejections": verdict.get("rejections")}))
            check("execution itself reconciled: direction, position and both legs",
                  all(c["result"] != "FAIL" for c in verdict["checks"]
                      if "reject" not in c["check"]),
                  json.dumps([c["check"] for c in verdict["checks"]
                              if c["result"] == "FAIL"]))
            check("audit measured slippage against the emulator's fills",
                  verdict.get("slippage_points") == {"entry": 0.0, "exit": 0.0},
                  json.dumps(verdict.get("slippage_points")))
            out = subprocess.run(
                [sys.executable, os.path.join(REPO, "executor", "daily_audit.py"),
                 "--streak"], env=env, capture_output=True, text=True)
            s = json.loads(out.stdout)
            check("a session with failures does not count toward the streak",
                  s["consecutive_clean"] == 0
                  and s["sessions_with_failures"] == [sess], json.dumps(s))
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("END-TO-END PASS — alert -> webhook -> paper order -> stop -> audit ledger")
    print("PAPER ONLY: no broker transport, no credentials, live=False.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
