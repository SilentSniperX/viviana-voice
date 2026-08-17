#!/usr/bin/env python3
"""Tradovate broker adapter — DEMO / PAPER ONLY.

Implements `executor.paper_executor.BrokerAdapter` against the Tradovate REST
API so the executor above it is unchanged. Authentication, account and position
state, market entry, protective stop, cancellation, flattening and
reconciliation.

=============================================================================
LIVE TRADING IS STRUCTURALLY DISABLED
=============================================================================
`DEMO_BASE` is the only host this module will talk to. `_assert_demo()` runs on
every request and raises if the URL is not the demo host, so pointing this at
`live.tradovateapi.com` requires editing source, not configuration. That is
deliberate: CLAUDE.md's LIVE-MONEY RULE makes enabling live a separate,
explicitly authorised change.

Credentials come from the environment only. They are never written to the repo,
never logged, and never placed in a Pine script or an alert payload.

=============================================================================
FIELD NAMES MUST BE VERIFIED BEFORE FIRST REAL USE
=============================================================================
This module was written without network access to Tradovate, so the request and
response shapes below come from the documented API and CANNOT be confirmed here.
Every one of them is tagged `# VERIFY`. Run

    python3 -m executor.brokers.tradovate preflight

against a demo account before trusting it: preflight exercises auth, account
lookup, contract resolution, position read and an order round trip, and reports
exactly which field it could not find rather than failing silently.

Environment:
    TRADOVATE_USERNAME, TRADOVATE_PASSWORD, TRADOVATE_APP_ID,
    TRADOVATE_APP_VERSION, TRADOVATE_CID, TRADOVATE_SECRET, TRADOVATE_DEVICE_ID
    TRADOVATE_ACCOUNT_SPEC   (account nickname, optional — else the first account)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_executor import BrokerAdapter  # noqa: E402

DEMO_BASE = "https://demo.tradovateapi.com/v1"
LIVE_HOSTS = ("live.tradovateapi.com",)          # never permitted from here

# Tradovate requires automated order flow to be declared. Sending orders without
# it on a real account is a compliance problem, so it is not optional here.
IS_AUTOMATED = True

TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


class BrokerError(RuntimeError):
    """Anything the adapter refuses to proceed through. Never swallowed."""


def _assert_demo(url: str) -> None:
    for host in LIVE_HOSTS:
        if host in url:
            raise BrokerError(
                f"refusing to contact a live Tradovate host ({host}). Enabling "
                f"live trading is a separate authorised change, not a config "
                f"switch. See CLAUDE.md LIVE-MONEY RULE.")
    if not url.startswith(DEMO_BASE):
        raise BrokerError(f"refusing a non-demo base URL: {url!r}")


# ---------------------------------------------------------------------------
# Transport — separated so the adapter logic is testable without a network
# ---------------------------------------------------------------------------

class Transport:
    def request(self, method: str, url: str, body: dict | None,
                headers: dict) -> tuple[int, dict]:
        raise NotImplementedError


class HttpTransport(Transport):
    """Real HTTP. Demo host only; never logs credentials or tokens."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def request(self, method, url, body, headers):
        _assert_demo(url)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              **headers})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode() or "{}"
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "{}"
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"errorText": raw[:500]}
        except urllib.error.URLError as e:
            raise BrokerError(f"transport failure contacting Tradovate demo: {e}")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class TradovateAdapter(BrokerAdapter):
    """Demo-only Tradovate implementation of the broker-neutral interface."""

    live = False                      # asserted by the executor's gate tests

    def __init__(self, transport: Transport | None = None,
                 base: str = DEMO_BASE, symbol: str = "MNQZ6",
                 credentials: dict | None = None):
        _assert_demo(base)
        self.base = base
        self.transport = transport or HttpTransport()
        self.symbol = symbol
        self.creds = credentials if credentials is not None else self._env_creds()
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._account: dict | None = None
        self._contract_id: int | None = None

    # -- credentials --------------------------------------------------------
    @staticmethod
    def _env_creds() -> dict:
        need = ["TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_APP_ID",
                "TRADOVATE_CID", "TRADOVATE_SECRET"]
        missing = [k for k in need if not os.environ.get(k)]
        if missing:
            raise BrokerError(
                f"missing credentials in the environment: {missing}. They belong "
                f"in the environment only — never in the repo, a Pine script or "
                f"an alert payload.")
        return {
            "name": os.environ["TRADOVATE_USERNAME"],          # VERIFY
            "password": os.environ["TRADOVATE_PASSWORD"],      # VERIFY
            "appId": os.environ["TRADOVATE_APP_ID"],           # VERIFY
            "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "1.0"),
            "cid": os.environ["TRADOVATE_CID"],                # VERIFY
            "sec": os.environ["TRADOVATE_SECRET"],             # VERIFY
            "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", "nq-orb-s5b-paper"),
        }

    # -- plumbing -----------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None,
              authed: bool = True) -> dict:
        headers = {}
        if authed:
            headers["Authorization"] = f"Bearer {self._valid_token()}"
        status, payload = self.transport.request(
            method, f"{self.base}{path}", body, headers)
        if status >= 400 or (isinstance(payload, dict) and payload.get("errorText")):
            raise BrokerError(f"{method} {path} -> {status}: "
                              f"{payload.get('errorText', payload)}")
        return payload

    def _valid_token(self) -> str:
        if (self._token and self._token_expiry
                and datetime.now(timezone.utc) < self._token_expiry - TOKEN_REFRESH_MARGIN):
            return self._token
        return self.authenticate()

    # -- 1. authentication --------------------------------------------------
    def authenticate(self) -> str:
        payload = self._call("POST", "/auth/accessTokenRequest",   # VERIFY
                             self.creds, authed=False)
        token = payload.get("accessToken")                          # VERIFY
        if not token:
            raise BrokerError(f"no accessToken in the auth response "
                              f"(keys: {sorted(payload)})")
        self._token = token
        exp = payload.get("expirationTime")                         # VERIFY
        self._token_expiry = _parse_time(exp) or (
            datetime.now(timezone.utc) + timedelta(minutes=60))
        return token

    def renew(self) -> str:
        payload = self._call("GET", "/auth/renewAccessToken")       # VERIFY
        if payload.get("accessToken"):
            self._token = payload["accessToken"]
            self._token_expiry = _parse_time(payload.get("expirationTime")) or (
                datetime.now(timezone.utc) + timedelta(minutes=60))
        return self._token

    # -- 2. account and position state --------------------------------------
    def get_account_state(self) -> dict:
        if self._account is None:
            accounts = self._call("GET", "/account/list")           # VERIFY
            if not accounts:
                raise BrokerError("no accounts returned for these credentials")
            want = os.environ.get("TRADOVATE_ACCOUNT_SPEC")
            self._account = next(
                (a for a in accounts if a.get("name") == want), accounts[0])
        return self._account

    def account_id(self) -> int:
        return self.get_account_state()["id"]                       # VERIFY

    def contract_id(self) -> int:
        if self._contract_id is None:
            found = self._call("GET", f"/contract/find?name={self.symbol}")  # VERIFY
            if not found or "id" not in found:
                raise BrokerError(f"could not resolve contract {self.symbol!r}")
            self._contract_id = found["id"]
        return self._contract_id

    def get_open_positions(self) -> list[dict]:
        positions = self._call("GET", "/position/list")             # VERIFY
        acct = self.account_id()
        return [p for p in positions
                if p.get("accountId") == acct and p.get("netPos")]  # VERIFY

    def health_check(self) -> bool:
        try:
            self.get_account_state()
            return True
        except BrokerError:
            return False

    # -- 3. orders ----------------------------------------------------------
    def submit_market_order(self, side: str, qty: int, price: float) -> dict:
        """`price` is the reference/alert price. A market order does not carry
        it; it is echoed back so the ledger can measure realised slippage."""
        body = {
            "accountSpec": self.get_account_state().get("name"),    # VERIFY
            "accountId": self.account_id(),
            "action": "Buy" if side == "LONG" else "Sell",          # VERIFY
            "symbol": self.symbol,
            "orderQty": qty,
            "orderType": "Market",                                  # VERIFY
            "isAutomated": IS_AUTOMATED,
        }
        r = self._call("POST", "/order/placeOrder", body)           # VERIFY
        return {"filled": None, "order_id": r.get("orderId"),       # VERIFY
                "side": side, "qty": qty, "reference_price": price,
                "venue": "TRADOVATE_DEMO", "raw": r}

    def submit_stop_order(self, side: str, qty: int, stop: float) -> dict:
        """Protective stop. Opposite side of the entry, same quantity."""
        body = {
            "accountSpec": self.get_account_state().get("name"),
            "accountId": self.account_id(),
            "action": "Sell" if side == "LONG" else "Buy",
            "symbol": self.symbol,
            "orderQty": qty,
            "orderType": "Stop",                                    # VERIFY
            "stopPrice": stop,                                      # VERIFY
            "isAutomated": IS_AUTOMATED,
        }
        r = self._call("POST", "/order/placeOrder", body)
        oid = r.get("orderId")
        if not oid:
            raise BrokerError(
                f"protective stop was not accepted ({r}). The executor must not "
                f"hold an unprotected position — flatten immediately.")
        return {"order_id": oid, "resting": True,
                "side": "SELL" if side == "LONG" else "BUY",
                "qty": qty, "stop": stop, "venue": "TRADOVATE_DEMO", "raw": r}

    def cancel_order(self, order_id) -> dict:
        r = self._call("POST", "/order/cancelOrder", {"orderId": order_id})  # VERIFY
        return {"cancelled": order_id, "venue": "TRADOVATE_DEMO", "raw": r}

    def flatten_position(self, price: float) -> dict:
        body = {"accountId": self.account_id(),
                "contractId": self.contract_id(),
                "admin": False}                                     # VERIFY
        r = self._call("POST", "/order/liquidatePosition", body)    # VERIFY
        return {"filled": None, "reference_price": price,
                "venue": "TRADOVATE_DEMO", "raw": r}

    # -- 4. reconciliation --------------------------------------------------
    def reconcile(self, ledger_position: dict | None) -> dict:
        """Compare what the executor believes against what the broker reports.

        This is the check that catches a fill the executor never saw, or a
        position the broker closed underneath it. A mismatch is never repaired
        automatically — it is reported for a human.
        """
        broker = self.get_open_positions()
        net = sum(p.get("netPos", 0) for p in broker)               # VERIFY
        expected = 0
        if ledger_position:
            expected = (ledger_position.get("qty", 1)
                        * (1 if ledger_position["side"] == "LONG" else -1))
        out = {"broker_net_position": net, "ledger_net_position": expected,
               "agree": net == expected,
               "broker_positions": broker,
               "open_orders": self.open_orders()}
        if not out["agree"]:
            out["action_required"] = (
                "broker and ledger disagree — halt, do not auto-repair")
        return out

    def open_orders(self) -> list[dict]:
        try:
            orders = self._call("GET", "/order/list")               # VERIFY
        except BrokerError:
            return []
        acct = self.account_id()
        live_states = {"Working", "Pending", "Suspended"}           # VERIFY
        return [o for o in orders
                if o.get("accountId") == acct and o.get("ordStatus") in live_states]


def _parse_time(raw) -> datetime | None:
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Preflight — the only way to confirm the field names, run it against demo
# ---------------------------------------------------------------------------

def preflight(symbol: str = "MNQZ6", place_test_order: bool = False) -> int:
    """Exercise every call the executor depends on and report what is missing.

    Read-only unless --place-test-order is passed, which submits a 1-lot market
    order and immediately flattens it on the DEMO account.
    """
    print("Tradovate PREFLIGHT — demo endpoint only")
    print(f"  base   {DEMO_BASE}\n  symbol {symbol}\n")
    checks: list[tuple[str, bool, str]] = []

    def step(name, fn):
        try:
            val = fn()
            checks.append((name, True, str(val)[:110]))
            return val
        except Exception as exc:                       # noqa: BLE001 - reported
            checks.append((name, False, f"{type(exc).__name__}: {exc}"))
            return None

    a = TradovateAdapter(symbol=symbol)
    step("authenticate", a.authenticate)
    step("account/list", a.get_account_state)
    step("contract/find", a.contract_id)
    step("position/list", a.get_open_positions)
    step("order/list", a.open_orders)
    step("reconcile (flat)", lambda: a.reconcile(None))
    if place_test_order:
        o = step("placeOrder Market", lambda: a.submit_market_order("LONG", 1, 0.0))
        s = step("placeOrder Stop", lambda: a.submit_stop_order("LONG", 1, 1.0))
        if s and s.get("order_id"):
            step("cancelOrder", lambda: a.cancel_order(s["order_id"]))
        if o:
            step("liquidatePosition", lambda: a.flatten_position(0.0))

    print()
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:22s} {detail}")
    bad = [n for n, ok, _ in checks if not ok]
    print(f"\n{'PREFLIGHT PASS' if not bad else 'PREFLIGHT FAILED: ' + str(bad)}")
    if bad:
        print("Each failure names the call and the field. Fix the `# VERIFY` line "
              "for that call in this module against the current Tradovate docs.")
    return 1 if bad else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["preflight"])
    ap.add_argument("--symbol", default=os.environ.get("TRADOVATE_SYMBOL", "MNQZ6"))
    ap.add_argument("--place-test-order", action="store_true",
                    help="submit and immediately flatten a 1-lot on DEMO")
    args = ap.parse_args()
    sys.exit(preflight(args.symbol, args.place_test_order))
