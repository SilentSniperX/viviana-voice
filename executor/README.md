# PAPER EXECUTOR SPEC

TradingView/Pine is the deterministic signal source.
Claude/LLM judgment must NOT be in the order-decision path.

## v1 scope
Paper execution only.

## Receiver responsibilities
- HTTPS-capable webhook endpoint for eventual deployment.
- JSON schema validation.
- deterministic signal_id idempotency.
- append-only raw event log.
- normalized event table.
- paper position ledger.
- reject duplicates.
- reject malformed events.
- reject event timestamps older than configured tolerance when running realtime.
- do not create a second position if one is already active.
- stop/close events can only reduce/flatten the current position.
- restart-safe state recovery from durable events.

## Broker interface
Define a broker-adapter interface, but v1 implementation should be PAPER.
Do not hardcode a specific broker until selected by the user.

Suggested interface:
- get_account_state()
- get_open_positions()
- submit_market_order(...)
- submit_stop_order(...)
- cancel_order(...)
- flatten_position(...)
- health_check()

## Credentials
No credentials in:
- Pine source
- TradingView alert JSON
- repository
Use environment/secret management only when a broker adapter is later authorized.

## Audit
Every event should connect:
TradingView signal_id -> receiver record -> paper order -> position change -> reconciliation result.
