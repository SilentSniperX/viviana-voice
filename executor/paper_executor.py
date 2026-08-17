#!/usr/bin/env python3
"""PAPER execution pipeline — TradingView alert -> webhook -> ledger -> audit.

    TradingView alert  ->  receiver  ->  append-only event log
                                      ->  paper position ledger
                                      ->  daily reconciliation

PAPER ONLY. There is no broker adapter and no order transport. `BrokerAdapter`
is an interface with a single paper implementation; wiring a live one is a
separate, explicitly authorised change (CLAUDE.md LIVE-MONEY RULE).

Gates implemented, each from spec/PARITY_ACCEPTANCE_GATES.md:
  * JSON schema validation against spec/alert_schema.json
  * deterministic signal_id idempotency — a duplicate is rejected, not replayed
  * append-only raw event log; the ledger is rebuilt from it on restart
  * at most one open position, ever
  * a stop or session-close event can only reduce or flatten, never reverse
  * events older than a tolerance are rejected when running realtime
  * every rejection is logged with a reason; nothing fails silently

Run the receiver:      python3 executor/paper_executor.py serve --port 8787
Replay a log:          python3 executor/paper_executor.py replay --log events.jsonl
Daily reconciliation:  python3 executor/paper_executor.py reconcile --date 2026-08-17
Self-test the gates:   python3 executor/paper_executor.py selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("NQ_PAPER_DIR", os.path.join(REPO, "executor", "_state"))
EVENT_LOG = os.path.join(STATE_DIR, "events.jsonl")
LEDGER_LOG = os.path.join(STATE_DIR, "ledger.jsonl")
REJECT_LOG = os.path.join(STATE_DIR, "rejected.jsonl")

ENTRY_EVENTS = {"ORB_LONG_ENTRY", "ORB_SHORT_ENTRY"}
EXIT_EVENTS = {"ORB_STOP", "SESSION_CLOSE_EXIT"}
STATE_EVENTS = {"S5B_LONG_CONFIRMED", "S5B_SHORT_CONFIRMED"}
ALL_EVENTS = ENTRY_EVENTS | EXIT_EVENTS | STATE_EVENTS

STALE_TOLERANCE = timedelta(minutes=15)


class Rejected(Exception):
    """A payload that must not reach the ledger. The reason is always logged."""


# ---------------------------------------------------------------------------
# Broker interface — paper only
# ---------------------------------------------------------------------------

class BrokerAdapter:
    """Interface only. A live implementation requires explicit authorisation."""

    def submit_market_order(self, side: str, qty: int, price: float) -> dict:
        raise NotImplementedError

    def flatten_position(self, price: float) -> dict:
        raise NotImplementedError

    def health_check(self) -> bool:
        raise NotImplementedError


class PaperBroker(BrokerAdapter):
    """Fills at the price carried in the alert. No transport, no credentials."""

    live = False

    def submit_market_order(self, side, qty, price):
        return {"filled": True, "side": side, "qty": qty, "price": price,
                "venue": "PAPER"}

    def flatten_position(self, price):
        return {"filled": True, "price": price, "venue": "PAPER"}

    def health_check(self):
        return True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def load_schema() -> dict:
    with open(os.path.join(REPO, "spec", "alert_schema.json")) as fh:
        return json.load(fh)


def validate(payload: dict, schema: dict, *, now: datetime | None = None,
             enforce_freshness: bool = False) -> None:
    for field in schema["required"]:
        if field not in payload:
            raise Rejected(f"missing required field {field!r}")
    props = schema["properties"]
    for key, spec in props.items():
        if key not in payload:
            continue
        val = payload[key]
        if "const" in spec and val != spec["const"]:
            raise Rejected(f"{key} must be {spec['const']!r}, got {val!r}")
        if "enum" in spec and val not in spec["enum"]:
            raise Rejected(f"{key}={val!r} not one of {spec['enum']}")
        types = spec.get("type")
        if types:
            types = [types] if isinstance(types, str) else types
            ok = any((t == "string" and isinstance(val, str))
                     or (t == "number" and isinstance(val, (int, float))
                         and not isinstance(val, bool))
                     or (t == "null" and val is None)
                     or (t == "object" and isinstance(val, dict))
                     for t in types)
            if not ok:
                raise Rejected(f"{key} has wrong type: {val!r}")
    if payload["event"] not in ALL_EVENTS:
        raise Rejected(f"unknown event {payload['event']!r}")
    if enforce_freshness:
        ts = parse_event_time(payload["event_time"])
        now = now or datetime.now(timezone.utc)
        if ts is None:
            raise Rejected("unparsable event_time")
        if abs(now - ts) > STALE_TOLERANCE:
            raise Rejected(f"stale event_time {payload['event_time']}")


def parse_event_time(raw: str) -> datetime | None:
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class Ledger:
    """Paper position state, rebuildable from the append-only event log alone."""

    def __init__(self, broker: BrokerAdapter | None = None):
        self.broker = broker or PaperBroker()
        self.seen: set[str] = set()
        self.position: dict | None = None
        self.closed: list[dict] = []
        self.rejections: list[dict] = []
        self.skipped = 0          # unusable log lines seen during recovery

    # -- restart recovery ---------------------------------------------------
    @classmethod
    def rebuild(cls, path: str = EVENT_LOG) -> "Ledger":
        led = cls()
        if not os.path.exists(path):
            return led
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # A crash mid-append can leave a truncated final line, and an
                # operator can put anything in this file. Recovery must survive
                # both: skip what it cannot use, count it, never abort. A
                # recovery path that crashes on one bad line is not restart-safe.
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    led.skipped += 1
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("payload"), dict):
                    led.skipped += 1
                    continue
                try:
                    led.apply(rec["payload"], persist=False, enforce_freshness=False)
                except Rejected:
                    pass          # already adjudicated when first received
        return led

    def apply(self, payload: dict, *, persist: bool = True,
              enforce_freshness: bool = False, now: datetime | None = None) -> dict:
        schema = load_schema()
        validate(payload, schema, now=now, enforce_freshness=enforce_freshness)

        sid = payload["signal_id"]
        if sid in self.seen:
            raise Rejected(f"duplicate signal_id {sid}")

        event = payload["event"]
        result: dict

        if event in STATE_EVENTS:
            # S5b is a classifier. It is recorded and never trades.
            result = {"action": "state_recorded", "s5b_state": payload["s5b_state"]}

        elif event in ENTRY_EVENTS:
            if self.position is not None:
                raise Rejected("an entry arrived while a position was already open")
            side = "LONG" if event == "ORB_LONG_ENTRY" else "SHORT"
            if payload.get("direction") != side:
                raise Rejected(f"direction {payload.get('direction')!r} contradicts "
                               f"event {event}")
            entry = payload.get("entry")
            if entry is None:
                raise Rejected("entry event carries no entry price")
            fill = self.broker.submit_market_order(side, 1, float(entry))
            self.position = {"side": side, "entry": float(entry),
                             "stop": payload.get("stop"),
                             "opened": payload["event_time"],
                             "signal_id": sid, "session": payload.get("session_date")}
            result = {"action": "opened", "fill": fill}

        else:                                            # EXIT_EVENTS
            if self.position is None:
                raise Rejected(f"{event} with no open position")
            px = payload.get("stop") if event == "ORB_STOP" else payload.get("entry")
            px = float(px) if px is not None else self.position["entry"]
            fill = self.broker.flatten_position(px)
            pos = self.position
            pts = (px - pos["entry"]) * (1 if pos["side"] == "LONG" else -1)
            rec = {**pos, "exit": px, "exit_event": event,
                   "closed": payload["event_time"], "points": round(pts, 2)}
            self.closed.append(rec)
            self.position = None                          # can only flatten
            result = {"action": "closed", "fill": fill, "points": rec["points"]}

        self.seen.add(sid)
        if persist:
            append(EVENT_LOG, {"received": utcnow(), "payload": payload})
            append(LEDGER_LOG, {"at": utcnow(), "signal_id": sid, **result})
        return result

    def snapshot(self) -> dict:
        return {"open_position": self.position, "closed_trades": len(self.closed),
                "net_points": round(sum(c["points"] for c in self.closed), 2),
                "signals_seen": len(self.seen), "unusable_log_lines": self.skipped}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------

def make_handler(ledger: Ledger):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                return self.reject(f"malformed JSON: {exc}", raw)
            try:
                result = ledger.apply(payload, enforce_freshness=True)
            except Rejected as exc:
                return self.reject(str(exc), raw)
            self.respond(200, {"ok": True, **result})

        def do_GET(self):
            self.respond(200, ledger.snapshot())

        def reject(self, reason, raw):
            append(REJECT_LOG, {"at": utcnow(), "reason": reason, "raw": raw[:2000]})
            ledger.rejections.append({"reason": reason})
            self.respond(400, {"ok": False, "rejected": reason})

        def respond(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile(date: str) -> dict:
    """Signal -> ledger for one session. The reference column is filled by
    tests/parity_orb.py output when a session has a canonical trade."""
    led = Ledger.rebuild()
    trades = [c for c in led.closed if str(c.get("session") or c["opened"])[:10] == date]
    canon = {}
    path = os.path.join(REPO, "reference", "canonical_orb_trades.csv")
    if os.path.exists(path):
        import csv
        for r in csv.DictReader(open(path)):
            if r["date"] == date:
                canon = r
    report = {"date": date, "paper_trades": len(trades),
              "paper_points": round(sum(t["points"] for t in trades), 2),
              "reference_trade": bool(canon),
              "reference_points": float(canon["net_points"]) if canon else None,
              "open_position_at_report": led.position is not None}
    if canon and len(trades) == 1:
        report["direction_match"] = trades[0]["side"] == canon["direction"]
        report["difference_points"] = round(
            trades[0]["points"] - float(canon["net_points"]), 2)
    report["clean"] = (report["reference_trade"] == (len(trades) == 1)
                       and not report["open_position_at_report"])
    return report


# ---------------------------------------------------------------------------
# Gate self-test
# ---------------------------------------------------------------------------

def selftest() -> int:
    base = dict(strategy_version="nq_orb_s5b_v1", symbol="NQ1!",
                event_time="2026-08-17T09:50:00+0000", orb_direction="LONG",
                s5b_state="WAITING_FOR_LATCH", alignment="UNRESOLVED",
                session_date="2026-08-17")
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(name)

    def entry(sid, direction="LONG", entry=100.0, stop=90.0):
        ev = "ORB_LONG_ENTRY" if direction == "LONG" else "ORB_SHORT_ENTRY"
        return {**base, "signal_id": sid, "event": ev, "direction": direction,
                "entry": entry, "stop": stop}

    led = Ledger()
    led.apply(entry("a"), persist=False)
    check("entry opens exactly one position", led.position is not None)

    try:
        led.apply(entry("a"), persist=False); ok = False
    except Rejected as e:
        ok = "duplicate" in str(e)
    check("duplicate signal_id is rejected", ok)

    try:
        led.apply(entry("b"), persist=False); ok = False
    except Rejected as e:
        ok = "already open" in str(e)
    check("second entry cannot open a second position", ok)

    led.apply({**base, "signal_id": "c", "event": "ORB_STOP", "direction": "LONG",
               "entry": 100.0, "stop": 90.0}, persist=False)
    check("stop flattens and cannot reverse",
          led.position is None and len(led.closed) == 1,
          f"points={led.closed[0]['points']}")

    try:
        led.apply({**base, "signal_id": "d", "event": "SESSION_CLOSE_EXIT",
                   "direction": "LONG", "entry": 100.0, "stop": 90.0}, persist=False)
        ok = False
    except Rejected as e:
        ok = "no open position" in str(e)
    check("exit with no position is rejected", ok)

    try:
        led.apply({**base, "signal_id": "e", "event": "NOT_A_REAL_EVENT",
                   "direction": "LONG"}, persist=False); ok = False
    except Rejected:
        ok = True
    check("unknown event is rejected", ok)

    try:
        led.apply({"signal_id": "f", "event": "ORB_STOP"}, persist=False); ok = False
    except Rejected as e:
        ok = "missing required field" in str(e)
    check("malformed payload is rejected", ok)

    try:
        led.apply({**entry("g"), "event_time": "1999-01-01T00:00:00+0000"},
                  persist=False, enforce_freshness=True); ok = False
    except Rejected as e:
        ok = "stale" in str(e)
    check("stale event is rejected when realtime", ok)

    led2 = Ledger()
    led2.apply({**base, "signal_id": "s1", "event": "S5B_LONG_CONFIRMED",
                "direction": "LONG", "s5b_state": "CONFIRMED",
                "alignment": "ALIGNED"}, persist=False)
    check("S5b confirmation records state and never trades",
          led2.position is None and not led2.closed)

    # restart recovery from the durable log
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        global EVENT_LOG, LEDGER_LOG
        keep = (EVENT_LOG, LEDGER_LOG)
        EVENT_LOG = os.path.join(tmp, "events.jsonl")
        LEDGER_LOG = os.path.join(tmp, "ledger.jsonl")
        live = Ledger()
        live.apply(entry("r1"))
        rebuilt = Ledger.rebuild(EVENT_LOG)
        check("restart rebuilds the open position from the log",
              rebuilt.position is not None
              and rebuilt.position["entry"] == live.position["entry"])
        try:
            rebuilt.apply(entry("r1"), persist=False); ok = False
        except Rejected:
            ok = True
        check("idempotency survives a restart", ok)
        EVENT_LOG, LEDGER_LOG = keep

    with tempfile.TemporaryDirectory() as tmp:
        bad = os.path.join(tmp, "corrupt.jsonl")
        with open(bad, "w") as fh:
            fh.write(json.dumps({"received": "x", "payload": entry("k1")}) + "\n")
            fh.write("{not json at all\n")
            fh.write(json.dumps({"no_payload_key": True}) + "\n")
            fh.write('{"received":"x","payload":{"trunc')          # crash mid-write
        rec = Ledger.rebuild(bad)
        check("recovery survives corrupt and truncated log lines",
              rec.position is not None and rec.skipped == 3,
              f"recovered position, skipped {rec.skipped}")

    check("broker is paper, never live", PaperBroker().live is False)
    print(f"\n{'ALL GATES PASS' if not fails else 'FAILED: ' + str(fails)}")
    return 1 if fails else 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8787)
    r = sub.add_parser("replay"); r.add_argument("--log", default=EVENT_LOG)
    c = sub.add_parser("reconcile"); c.add_argument("--date", required=True)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "reconcile":
        print(json.dumps(reconcile(a.date), indent=2))
        return 0
    if a.cmd == "replay":
        led = Ledger.rebuild(a.log)
        print(json.dumps(led.snapshot(), indent=2))
        return 0

    led = Ledger.rebuild()
    print(f"PAPER executor on :{a.port} — broker={type(led.broker).__name__}, "
          f"live=False")
    print(f"  state {STATE_DIR}\n  recovered {json.dumps(led.snapshot())}")
    HTTPServer(("", a.port), make_handler(led)).serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
