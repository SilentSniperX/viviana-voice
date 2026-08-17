# Broker adapters — DEFERRED

## Status: built, tested, deliberately unwired

Phase 1 paper validation runs **entirely inside TradingView's strategy/broker
emulator**. No external broker is connected, and `paper_executor.py` refuses to
select one:

```
$ python3 executor/paper_executor.py serve --broker tradovate
the Tradovate adapter is DEFERRED and will not be connected.
```

`tradovate.py` implements the broker-neutral interface — authentication with
token reuse, account and contract resolution, position state, market entry,
protective stop, cancellation, flattening and reconciliation — against the
**demo endpoint only**. `_assert_demo()` runs on every request, so pointing it at
a live host requires editing source, not configuration.

`tests/test_tradovate_adapter.py` proves the logic against a scripted fake
transport (34 gates), including that the executor's ledger behaves identically
with Tradovate substituted for `PaperBroker` — the interface is genuinely
neutral.

## What is NOT proven

There was no network access to Tradovate when this was written, so the request
and response **field names are unverified**. Every one is tagged `# VERIFY`.
Before this is ever trusted:

```bash
python3 -m executor.brokers.tradovate preflight
```

It exercises auth, account lookup, contract resolution, position read and an
order round trip against a demo account, and names the exact field it could not
find rather than failing silently.

## Reactivating it

A live-capital decision, not a runtime flag. It requires, in order: the Phase-1
TradingView paper validation gate met (`docs/DEPLOYMENT_ASSESSMENT.md` Task 5),
a clean preflight against demo, and explicit authorisation.
