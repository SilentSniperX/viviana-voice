#!/usr/bin/env python3
"""Gates for the Tradovate demo adapter, run against a scripted fake transport.

There is no network in this environment, so these prove the ADAPTER LOGIC:
auth and token reuse, account and contract resolution, order construction,
protective-stop refusal, cancellation, flattening, reconciliation, and the
live-endpoint refusal.

What they cannot prove is that Tradovate's real field names match — that is what
`python3 -m executor.brokers.tradovate preflight` is for, and it must be run
against a demo account before the adapter is trusted.

Run:  python3 tests/test_tradovate_adapter.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "executor"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "executor", "brokers"))

from tradovate import (BrokerError, DEMO_BASE, Transport,  # noqa: E402
                       TradovateAdapter, _assert_demo)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class FakeTradovate(Transport):
    """Mimics the documented response shapes and records every request."""

    def __init__(self, *, stop_accepted=True, net_pos=0):
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.stop_accepted = stop_accepted
        self.net_pos = net_pos
        self.auth_count = 0
        self.cancelled: list = []

    def request(self, method, url, body, headers):
        _assert_demo(url)
        path = url[len(DEMO_BASE):]
        self.calls.append((method, path, body, headers))
        if path == "/auth/accessTokenRequest":
            self.auth_count += 1
            return 200, {"accessToken": f"TOKEN{self.auth_count}",
                         "expirationTime": "2099-01-01T00:00:00Z", "userId": 1}
        if path == "/account/list":
            return 200, [{"id": 77, "name": "DEMO123", "active": True}]
        if path.startswith("/contract/find"):
            return 200, {"id": 4242, "name": "MNQZ6"}
        if path == "/position/list":
            return 200, [{"accountId": 77, "contractId": 4242, "netPos": self.net_pos}]
        if path == "/order/list":
            return 200, [{"id": 9, "accountId": 77, "ordStatus": "Working"},
                         {"id": 10, "accountId": 77, "ordStatus": "Filled"}]
        if path == "/order/placeOrder":
            if body.get("orderType") == "Stop" and not self.stop_accepted:
                return 200, {"failureReason": "RejectedByServer"}
            return 200, {"orderId": 555 if body.get("orderType") == "Market" else 556}
        if path == "/order/cancelOrder":
            self.cancelled.append(body["orderId"])
            return 200, {"commandId": 1}
        if path == "/order/liquidatePosition":
            return 200, {"commandId": 2}
        return 404, {"errorText": f"unmapped {path}"}


CREDS = {"name": "u", "password": "p", "appId": "a", "appVersion": "1.0",
         "cid": "c", "sec": "s", "deviceId": "d"}


def adapter(**kw) -> tuple[TradovateAdapter, FakeTradovate]:
    fake = FakeTradovate(**kw)
    return TradovateAdapter(transport=fake, credentials=CREDS), fake


def main() -> int:
    print("live-endpoint refusal")
    for bad in ("https://live.tradovateapi.com/v1",
                "https://demo.tradovateapi.com.evil.test/v1",
                "http://localhost/v1"):
        try:
            _assert_demo(bad); ok = False
        except BrokerError:
            ok = True
        check(f"refuses {bad}", ok)
    try:
        TradovateAdapter(transport=FakeTradovate(),
                         base="https://live.tradovateapi.com/v1",
                         credentials=CREDS)
        ok = False
    except BrokerError:
        ok = True
    check("constructor refuses a live base URL", ok)
    check("adapter declares itself non-live", TradovateAdapter.live is False)

    print("\nauthentication")
    a, fake = adapter()
    tok = a.authenticate()
    check("obtains an access token", tok == "TOKEN1")
    a.get_account_state(); a.get_account_state()
    check("token is reused, not re-requested", fake.auth_count == 1,
          f"auth calls={fake.auth_count}")
    auth_hdr = [h for m, p, b, h in fake.calls if p == "/account/list"][0]
    check("bearer token is sent", auth_hdr.get("Authorization") == "Bearer TOKEN1")
    creds_logged = any("password" in str(h) for _, _, _, h in fake.calls)
    check("credentials never appear in headers", not creds_logged)

    print("\naccount and position state")
    a, fake = adapter(net_pos=1)
    check("resolves the account", a.account_id() == 77)
    check("resolves the contract", a.contract_id() == 4242)
    check("caches the contract lookup",
          sum(1 for _, p, _, _ in fake.calls if p.startswith("/contract/find")) == 1
          if a.contract_id() == 4242 else False)
    check("reads open positions", len(a.get_open_positions()) == 1)
    a2, _ = adapter(net_pos=0)
    check("a flat account reports no positions", a2.get_open_positions() == [])
    check("health check passes", a.health_check() is True)

    print("\nmarket entry")
    a, fake = adapter()
    r = a.submit_market_order("LONG", 1, 23100.25)
    body = [b for m, p, b, h in fake.calls if p == "/order/placeOrder"][0]
    check("order id returned", r["order_id"] == 555)
    check("action Buy for LONG", body["action"] == "Buy")
    check("order type Market", body["orderType"] == "Market")
    check("quantity honoured", body["orderQty"] == 1)
    check("isAutomated declared", body["isAutomated"] is True)
    check("reference price echoed for slippage measurement",
          r["reference_price"] == 23100.25)
    a, fake = adapter()
    a.submit_market_order("SHORT", 2, 1.0)
    body = [b for m, p, b, h in fake.calls if p == "/order/placeOrder"][0]
    check("action Sell for SHORT", body["action"] == "Sell" and body["orderQty"] == 2)

    print("\nprotective stop")
    a, fake = adapter()
    s = a.submit_stop_order("LONG", 1, 23050.0)
    body = [b for m, p, b, h in fake.calls if p == "/order/placeOrder"][0]
    check("stop is the opposite side", body["action"] == "Sell")
    check("stop type and price", body["orderType"] == "Stop"
          and body["stopPrice"] == 23050.0)
    check("stop reports as resting", s["resting"] is True and s["order_id"] == 556)
    a, fake = adapter(stop_accepted=False)
    try:
        a.submit_stop_order("LONG", 1, 1.0); ok = False
    except BrokerError as e:
        ok = "unprotected" in str(e)
    check("a rejected stop raises rather than leaving a naked position", ok)

    print("\ncancellation and flattening")
    a, fake = adapter()
    a.cancel_order(556)
    check("cancel sends the order id", fake.cancelled == [556])
    a, fake = adapter(net_pos=1)
    a.flatten_position(23050.0)
    body = [b for m, p, b, h in fake.calls if p == "/order/liquidatePosition"][0]
    check("flatten targets account and contract",
          body["accountId"] == 77 and body["contractId"] == 4242)

    print("\nreconciliation")
    a, _ = adapter(net_pos=1)
    rec = a.reconcile({"side": "LONG", "qty": 1})
    check("agrees when broker and ledger match", rec["agree"] is True)
    a, _ = adapter(net_pos=1)
    rec = a.reconcile(None)
    check("disagrees when the broker holds a position the ledger does not",
          rec["agree"] is False and "action_required" in rec,
          f"broker={rec['broker_net_position']} ledger={rec['ledger_net_position']}")
    a, _ = adapter(net_pos=0)
    rec = a.reconcile({"side": "SHORT", "qty": 1})
    check("disagrees when the ledger holds a position the broker does not",
          rec["agree"] is False)
    check("never auto-repairs a mismatch",
          "action_required" in rec and "halt" in rec["action_required"])
    a, _ = adapter()
    check("reports only working orders", len(a.open_orders()) == 1)

    print("\nerror surfacing")
    a, _ = adapter()
    try:
        a._call("GET", "/nope"); ok = False
    except BrokerError as e:
        ok = "unmapped" in str(e)
    check("an unexpected response raises, never silently returns", ok)

    print("\ncredential handling")
    keep = {k: os.environ.pop(k, None) for k in
            ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_APP_ID",
             "TRADOVATE_CID", "TRADOVATE_SECRET")}
    try:
        TradovateAdapter(transport=FakeTradovate()); ok = False
    except BrokerError as e:
        ok = "missing credentials" in str(e)
    check("refuses to start without credentials in the environment", ok)
    for k, v in keep.items():
        if v is not None:
            os.environ[k] = v
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "executor", "brokers", "tradovate.py")).read()
    check("no credential literals in the adapter source",
          not any(t in src for t in ("password=", "sec=\"", "secret=\"")))

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("ALL TRADOVATE ADAPTER GATES PASS (against a scripted fake)")
    print("Field names are NOT confirmed here — run `python3 -m "
          "executor.brokers.tradovate preflight` against demo before trusting it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
