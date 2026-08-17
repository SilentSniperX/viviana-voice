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
# TradingView's broker emulator reports its own fills on a second alert
# ("Order fills only"). Those land here and NEVER touch the position ledger —
# they are the independent observation that the daily audit reconciles the
# ledger against. Merging the two would destroy the only thing they are for.
FILL_LOG = os.path.join(STATE_DIR, "fills.jsonl")

ENTRY_EVENTS = {"ORB_LONG_ENTRY", "ORB_SHORT_ENTRY"}
EXIT_EVENTS = {"ORB_STOP", "SESSION_CLOSE_EXIT"}
# SESSION_SUMMARY is the end-of-session heartbeat. It fires on every regular
# session whether or not the strategy traded, and it exists so that SILENCE is
# detectable: without it, a dead receiver or an expired TradingView alert looks
# exactly like a session the strategy declined, and the daily audit would bank
# it as clean toward the 20 sessions that gate live capital.
SUMMARY_EVENT = "SESSION_SUMMARY"
STATE_EVENTS = {"S5B_LONG_CONFIRMED", "S5B_SHORT_CONFIRMED", SUMMARY_EVENT}
ALL_EVENTS = ENTRY_EVENTS | EXIT_EVENTS | STATE_EVENTS
# S5b is a classifier and places no orders, so it can never produce a fill.
FILL_EVENTS = ENTRY_EVENTS | EXIT_EVENTS

STALE_TOLERANCE = timedelta(minutes=15)

# Position sizing. The deployment assessment puts the defensible self-funded
# envelope at roughly $20,000-25,000 per MNQ, so the default is the smallest
# tradeable size on the smallest contract. Raising either of these is a capital
# decision, not a code decision.
CONTRACT = os.environ.get("NQ_PAPER_CONTRACT", "MNQ")     # MNQ ($2/pt) or NQ ($20/pt)
QTY = int(os.environ.get("NQ_PAPER_QTY", "1"))
POINT_VALUE = {"MNQ": 2.0, "NQ": 20.0}
# The reference subtracts a 0.75-point round-turn cost. The paper ledger uses the
# same convention so daily reconciliation compares like with like — otherwise
# every trade shows a constant 0.75-point difference and operators learn to
# ignore the column that is supposed to surface real breaks.
COST_POINTS = 0.75

# Kill switch. Touch this file and the receiver accepts no new ENTRIES; exits
# are still honoured so an open position can always be closed.
KILL_FILE = os.path.join(STATE_DIR, "KILL")


class Rejected(Exception):
    """A payload that must not reach the ledger. The reason is always logged."""


class NotDurable(Exception):
    """The event could not be committed to the append-only log.

    Distinct from Rejected: the payload was VALID and would have been accepted.
    In-memory state is rolled back and the caller answers 503, because the
    alternative — a running process holding a position the durable log has no
    record of — survives until the next restart and then silently vanishes.
    """


# ---------------------------------------------------------------------------
# Broker interface — paper only
# ---------------------------------------------------------------------------

class BrokerAdapter:
    """Interface only. A live implementation requires explicit authorisation."""

    def submit_market_order(self, side: str, qty: int, price: float) -> dict:
        raise NotImplementedError

    def submit_stop_order(self, side: str, qty: int, stop: float) -> dict:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> dict:
        raise NotImplementedError

    def flatten_position(self, price: float) -> dict:
        raise NotImplementedError

    def health_check(self) -> bool:
        raise NotImplementedError


class PaperBroker(BrokerAdapter):
    """Fills at the price carried in the alert. No transport, no credentials."""

    live = False
    _seq = 0

    def submit_market_order(self, side, qty, price):
        return {"filled": True, "side": side, "qty": qty, "price": price,
                "venue": "PAPER"}

    def submit_stop_order(self, side, qty, stop):
        self._seq += 1
        return {"order_id": f"PAPER-STOP-{self._seq}", "resting": True,
                "side": "SELL" if side == "LONG" else "BUY", "qty": qty,
                "stop": stop, "venue": "PAPER"}

    def cancel_order(self, order_id):
        return {"cancelled": order_id, "venue": "PAPER"}

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
                     # `boolean` was missing here, so any field the schema
                     # declared boolean was rejected as the wrong type. It went
                     # unnoticed until a field of that type became required by a
                     # check: the SESSION_SUMMARY heartbeat.
                     or (t == "boolean" and isinstance(val, bool))
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


FILL_REQUIRED = ("strategy_version", "channel", "event", "symbol", "direction",
                 "fill_price", "fill_qty", "position_after", "signal_id",
                 "session_date")


def validate_fill(payload: dict) -> None:
    """Gate a TradingView 'Order fills only' payload.

    This is an OBSERVATION, not an instruction: it is recorded and reconciled,
    never traded on. It still has to be well-formed, because the failure mode
    that matters is silent — a mis-configured alert message posts the literal
    template and an unvalidated audit would then compare nothing against nothing
    and report a clean session.
    """
    for field in FILL_REQUIRED:
        if field not in payload:
            raise Rejected(f"fill payload missing required field {field!r}")
    if payload["strategy_version"] != "nq_orb_s5b_v1":
        raise Rejected(f"fill from an unknown strategy "
                       f"{payload['strategy_version']!r}")
    if payload["event"] not in FILL_EVENTS:
        raise Rejected(f"fill for an event that places no order "
                       f"{payload['event']!r}")
    if payload["direction"] not in ("LONG", "SHORT"):
        raise Rejected(f"fill direction {payload['direction']!r} is not LONG/SHORT")
    for key in ("fill_price", "fill_qty", "position_after"):
        val = payload[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise Rejected(f"fill {key} is not a number: {val!r}")
    if payload["fill_price"] <= 0:
        raise Rejected(f"fill_price {payload['fill_price']!r} is not a price")
    if payload["fill_qty"] <= 0:
        raise Rejected(f"fill_qty {payload['fill_qty']!r} is not positive")
    # An unexpanded placeholder means the alert's message field was not left as
    # {{strategy.order.alert_message}}. Reject loudly: this is the single most
    # likely setup mistake and it must not be mistaken for a quiet session.
    for key, val in payload.items():
        if isinstance(val, str) and "{{" in val:
            raise Rejected(f"fill field {key!r} still contains an unexpanded "
                           f"TradingView placeholder: {val!r}")


def record_fill(payload: dict, seen: set[str] | None = None) -> dict:
    validate_fill(payload)
    sid = payload["signal_id"]
    if seen is not None:
        if sid in seen:
            raise Rejected(f"duplicate fill signal_id {sid}")
        seen.add(sid)
    append(FILL_LOG, {"received": utcnow(), "payload": payload})
    return {"action": "fill_recorded", "event": payload["event"],
            "fill_price": payload["fill_price"],
            "position_after": payload["position_after"]}


def load_fills(date: str | None = None, path: str = FILL_LOG) -> list[dict]:
    """Fill payloads, optionally for one session. Unusable lines are skipped."""
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
            if date is None or str(p.get("session_date")) == date:
                out.append(p)
    return out


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
        self.ledger_log_failures = 0   # durable event written, summary line not
        self.sessions_traded: set[str] = set()   # frozen spec B: one trade/day
        self.resting_stop: dict | None = None

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

        # Everything below MUTATES. If the append-only log cannot then be
        # written — a full disk is the realistic case — this snapshot is what
        # keeps memory and disk from diverging. Without it the process goes on
        # holding a position that no durable record contains, and a restart
        # silently flattens it.
        undo = (self.position, list(self.closed), set(self.sessions_traded),
                set(self.seen), self.resting_stop)

        event = payload["event"]
        result: dict

        if event in STATE_EVENTS:
            # S5b is a classifier and the summary is a heartbeat. Both are
            # recorded; neither ever trades.
            result = {"action": "state_recorded", "s5b_state": payload["s5b_state"]}
            if event == SUMMARY_EVENT:
                result["traded"] = bool(payload.get("traded"))

        elif event in ENTRY_EVENTS:
            if kill_engaged():
                raise Rejected("kill switch engaged — new entries refused")
            if self.position is not None:
                raise Rejected("an entry arrived while a position was already open")
            # Frozen spec B: ONE TRADE PER DAY. The Pine enforces this too, but a
            # duplicated or replayed alert must not be able to re-arm the day, so
            # the executor refuses independently rather than trusting the source.
            session = payload.get("session_date") or payload["event_time"][:10]
            if session in self.sessions_traded:
                raise Rejected(f"session {session} has already traded "
                               f"(frozen spec B: one trade per day)")
            side = "LONG" if event == "ORB_LONG_ENTRY" else "SHORT"
            if payload.get("direction") != side:
                raise Rejected(f"direction {payload.get('direction')!r} contradicts "
                               f"event {event}")
            entry = payload.get("entry")
            if entry is None:
                raise Rejected("entry event carries no entry price")
            stop = payload.get("stop")
            if stop is None:
                raise Rejected("entry event carries no stop price — refusing an "
                               "unprotected position")
            fill = self.broker.submit_market_order(side, QTY, float(entry))
            # The protective stop goes on immediately, in the same handler, so a
            # position can never exist without one even if the process dies here.
            self.resting_stop = self.broker.submit_stop_order(side, QTY, float(stop))
            self.position = {"side": side, "entry": float(entry), "stop": float(stop),
                             "qty": QTY, "contract": CONTRACT,
                             "opened": payload["event_time"],
                             "signal_id": sid, "session": session}
            self.sessions_traded.add(session)
            result = {"action": "opened", "fill": fill,
                      "protective_stop": self.resting_stop}

        else:                                            # EXIT_EVENTS
            if self.position is None:
                raise Rejected(f"{event} with no open position")
            # The exit price, in order of authority: the strategy's stated exit
            # price, then the stop for a stop exit. There is no third option —
            # falling back to the ENTRY price (as this did) books every
            # hold-to-close trade at zero and hides the entire P&L.
            px = payload.get("exit")
            if px is None and event == "ORB_STOP":
                px = payload.get("stop")
            if px is None:
                raise Rejected(f"{event} carries no exit price — refusing to "
                               f"book a trade at a price the strategy never sent")
            px = float(px)
            fill = self.broker.flatten_position(px)
            if self.resting_stop is not None:
                self.broker.cancel_order(self.resting_stop["order_id"])
                self.resting_stop = None
            pos = self.position
            gross = (px - pos["entry"]) * (1 if pos["side"] == "LONG" else -1)
            pts = gross - COST_POINTS
            pv = POINT_VALUE[pos.get("contract", CONTRACT)]
            rec = {**pos, "exit": px, "exit_event": event,
                   "closed": payload["event_time"],
                   "gross_points": round(gross, 2), "points": round(pts, 2),
                   "dollars": round(pts * pv * pos.get("qty", QTY), 2)}
            self.closed.append(rec)
            self.position = None                          # can only flatten
            result = {"action": "closed", "fill": fill,
                      "gross_points": rec["gross_points"], "points": rec["points"],
                      "dollars": rec["dollars"]}

        self.seen.add(sid)
        if persist:
            try:
                # The EVENT log is the one that must survive: `rebuild` replays
                # it and nothing else. It is written first, so a failure here
                # leaves no trace to roll back from.
                append(EVENT_LOG, {"received": utcnow(), "payload": payload})
            except OSError as exc:
                (self.position, self.closed, self.sessions_traded,
                 self.seen, self.resting_stop) = undo
                raise NotDurable(f"could not commit {sid} to the event log: "
                                 f"{exc}") from exc
            try:
                append(LEDGER_LOG, {"at": utcnow(), "signal_id": sid, **result})
            except OSError as exc:
                # The event log already has it, so a rebuild recovers this
                # state correctly. Rolling back here would be the wrong move —
                # it would contradict the durable record. Surface it instead.
                self.ledger_log_failures += 1
                print(f"WARNING: ledger log write failed for {sid}: {exc}",
                      file=sys.stderr)
        return result

    def snapshot(self) -> dict:
        return {"open_position": self.position, "closed_trades": len(self.closed),
                "net_points": round(sum(c["points"] for c in self.closed), 2),
                "net_dollars": round(sum(c.get("dollars", 0) for c in self.closed), 2),
                "signals_seen": len(self.seen), "fills_recorded": len(load_fills()),
                "unusable_log_lines": self.skipped,
                "ledger_log_failures": self.ledger_log_failures,
                "state_dir_writable": state_dir_writable(),
                "sessions_traded": len(self.sessions_traded),
                "protective_stop": self.resting_stop,
                "kill_switch": kill_engaged(),
                "contract": CONTRACT, "qty": QTY}


def make_broker(name: str) -> BrokerAdapter:
    """Broker selection lives here and nowhere else. The Ledger only ever sees
    the BrokerAdapter interface, so adding a venue never touches trading logic."""
    name = (name or "paper").lower()
    if name == "paper":
        return PaperBroker()
    if name == "tradovate":
        # DEFERRED. Phase 1 paper validation runs entirely inside TradingView's
        # strategy/broker emulator; no external broker is connected. The adapter
        # is built and its gates pass (tests/test_tradovate_adapter.py) but it
        # stays unwired until live-capital deployment is authorised.
        raise SystemExit(
            "the Tradovate adapter is DEFERRED and will not be connected.\n"
            "Phase 1 paper validation runs inside TradingView's broker emulator.\n"
            "The adapter exists and is tested; wiring it is a live-capital "
            "decision, not a runtime flag.")
    raise SystemExit(f"unknown broker {name!r} (paper|tradovate)")


def state_dir_writable() -> bool:
    """Can the append-only log actually be written right now?

    Exposed on the status endpoint so a full or read-only disk is visible
    BEFORE a session rather than at the moment an entry needs committing.
    """
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        probe = os.path.join(STATE_DIR, ".writable")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def kill_engaged() -> bool:
    return os.path.exists(KILL_FILE)


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
    # Fill ids already recorded, so a re-fired fill alert is rejected rather than
    # double-counted. Seeded from the log so a restart does not forget.
    fills_seen = {p["signal_id"] for p in load_fills() if p.get("signal_id")}

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
            if not isinstance(payload, dict):
                return self.reject("payload is not a JSON object", raw)
            try:
                if payload.get("channel") == "fill":
                    # TradingView's own fill report. Recorded for the daily
                    # audit; it must not move the ledger, or the audit would be
                    # comparing the ledger against itself.
                    result = record_fill(payload, fills_seen)
                else:
                    result = ledger.apply(payload, enforce_freshness=True)
            except Rejected as exc:
                return self.reject(str(exc), raw)
            except NotDurable as exc:
                # 503, not 400: the payload was valid and TradingView should be
                # free to retry. Nothing is written here — the reject log lives
                # on the same disk that just failed.
                print(f"NOT DURABLE: {exc}", file=sys.stderr)
                return self.respond(503, {"ok": False, "not_durable": str(exc)})
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

    # one trade per day, enforced independently of the signal source
    led3 = Ledger()
    led3.apply(entry("d1"), persist=False)
    led3.apply({**base, "signal_id": "d2", "event": "ORB_STOP", "direction": "LONG",
                "entry": 100.0, "stop": 90.0}, persist=False)
    try:
        led3.apply(entry("d3"), persist=False); ok = False
    except Rejected as e:
        ok = "already traded" in str(e)
    check("a second entry in the same session is refused", ok)
    led3.apply({**entry("d4"), "session_date": "2026-08-18"}, persist=False)
    check("a new session re-arms", led3.position is not None)

    led4 = Ledger()
    led4.apply(entry("p1"), persist=False)
    check("protective stop is resting immediately after entry",
          led4.resting_stop is not None and led4.resting_stop["stop"] == 90.0,
          str(led4.resting_stop))
    try:
        led4.apply({**base, "signal_id": "p2", "event": "ORB_SHORT_ENTRY",
                    "direction": "SHORT", "entry": 100.0}, persist=False); ok = False
    except Rejected:
        ok = True
    check("entry without a stop price is refused", ok)

    with tempfile.TemporaryDirectory() as tmp:
        global KILL_FILE
        keep_kill = KILL_FILE
        KILL_FILE = os.path.join(tmp, "KILL")
        led5 = Ledger()
        open(KILL_FILE, "w").close()
        try:
            led5.apply(entry("k9"), persist=False); ok = False
        except Rejected as e:
            ok = "kill switch" in str(e)
        check("kill switch refuses new entries", ok)
        os.remove(KILL_FILE)
        led5.apply(entry("k10"), persist=False)
        led5_pos = led5.position is not None
        open(KILL_FILE, "w").close()
        # A close exit with no exit price cannot be booked: the old code fell
        # back to the ENTRY price, which silently recorded every hold-to-close
        # trade as flat and erased the strategy's entire P&L.
        try:
            led5.apply({**base, "signal_id": "k11b",
                        "event": "SESSION_CLOSE_EXIT", "direction": "LONG",
                        "entry": 100.0, "stop": 90.0}, persist=False)
            ok = False
        except Rejected as e:
            ok = "no exit price" in str(e)
        check("a close exit without an exit price is refused, never booked at "
              "the entry price", ok)
        led5.apply({**base, "signal_id": "k11", "event": "SESSION_CLOSE_EXIT",
                    "direction": "LONG", "entry": 100.0, "stop": 90.0,
                    "exit": 101.0}, persist=False)
        check("kill switch still allows an open position to be closed",
              led5_pos and led5.position is None)
        check("the close exit is booked at the strategy's exit price",
              led5.closed[-1]["exit"] == 101.0
              and led5.closed[-1]["gross_points"] == 1.0,
              json.dumps({k: led5.closed[-1][k]
                          for k in ("exit", "gross_points", "points")}))
        KILL_FILE = keep_kill

    # A full disk must not leave the process holding a position the durable log
    # has no record of: that survives until the next restart and then silently
    # vanishes. Red-team finding 7.
    import builtins
    led6 = Ledger()
    real_open = builtins.open
    builtins.open = lambda *a, **k: (
        (_ for _ in ()).throw(OSError(28, "No space left on device"))
        if a and str(a[0]).endswith("events.jsonl") else real_open(*a, **k))
    try:
        led6.apply(entry("d1"), persist=True)
        ok = False
    except NotDurable as exc:
        ok = "could not commit" in str(exc)
    except OSError:
        ok = False
    finally:
        builtins.open = real_open
    check("a failed durable write raises NotDurable, not OSError", ok)
    check("and in-memory state is rolled back, not left ahead of the log",
          led6.position is None and not led6.sessions_traded and not led6.seen,
          f"position={led6.position} sessions={led6.sessions_traded}")
    led6.apply(entry("d1"), persist=False)
    check("the same alert can be retried once the disk recovers",
          led6.position is not None)

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
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--broker", default=os.environ.get("BROKER_ADAPTER", "paper"),
                   choices=["paper", "tradovate"],
                   help="paper only; tradovate is built but DEFERRED and refuses "
                        "to connect")
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
    led.broker = make_broker(a.broker)
    print(f"executor on :{a.port} — broker={type(led.broker).__name__}, "
          f"live={getattr(led.broker, 'live', 'unknown')}, "
          f"contract={CONTRACT} x{QTY}")
    print(f"  state {STATE_DIR}\n  recovered {json.dumps(led.snapshot())}")
    HTTPServer(("", a.port), make_handler(led)).serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
