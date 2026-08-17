# PAPER PIPELINE — setup and operation

```
TradingView Pine  ->  strategy alert  ->  webhook  ->  executor  ->  audit ledger
```

PAPER ONLY. `BrokerAdapter` has exactly one implementation, `PaperBroker`, with
`live = False`, no transport and no credentials. Wiring a live broker is a
separate, explicitly authorised change.

## 1. TradingView alert

One alert covers everything. `pine/nq_orb_s5b_v1.pine` calls `alert()` itself
with a fully-formed JSON payload, so the alert message must be left as the
default placeholder.

- Right-click the chart -> **Add alert**
- Condition: **NQ ORB + S5b v1** -> **Any alert() function call**
- Expiration: **Open-ended**
- Notifications -> **Webhook URL**: your receiver's public HTTPS URL
- Message: leave as `{{strategy.order.alert_message}}` — the script supplies the body

Alerts run on TradingView's servers, so they keep firing with the browser closed.

Chart must be **5-minute, regular trading hours** (`docs/PARITY_PROCEDURE.md`).

## 2. Payload

Emitted by `f_payload()` in the Pine, validated against `spec/alert_schema.json`:

```json
{"strategy_version":"nq_orb_s5b_v1",
 "signal_id":"nq_orb_s5b_v1|NQ1!|20260817|ORB_LONG_ENTRY|1755432000000",
 "event":"ORB_LONG_ENTRY","symbol":"NQ1!",
 "event_time":"2026-08-17T09:50:00-0400","session_date":"2026-08-17",
 "direction":"LONG","orb_direction":"LONG",
 "s5b_state":"WAITING_FOR_LATCH","s5b_direction":"NONE","alignment":"UNRESOLVED",
 "s5b_entry_eligible":false,
 "entry":23100.25,"stop":23050.00,"or_high":23105.00,"or_low":23050.00}
```

`signal_id` is deterministic — version, symbol, session, event and bar time — so
a replayed alert is recognisably the same signal rather than a new one.

Six events: `ORB_LONG_ENTRY`, `ORB_SHORT_ENTRY`, `ORB_STOP`,
`SESSION_CLOSE_EXIT`, `S5B_LONG_CONFIRMED`, `S5B_SHORT_CONFIRMED`.

## 3. Run the receiver

```bash
python3 executor/paper_executor.py serve --port 8787
```

Environment: `NQ_PAPER_DIR` (state directory), `NQ_PAPER_CONTRACT` (`MNQ`
default, or `NQ`), `NQ_PAPER_QTY` (default 1).

The default is deliberately the smallest tradeable size on the smallest
contract. `docs/DEPLOYMENT_ASSESSMENT.md` puts the defensible self-funded
envelope at ~$20,000-25,000 per MNQ; raising either value is a capital decision.

TradingView posts to a public HTTPS endpoint, so terminate TLS in front of this
(reverse proxy or tunnel). The receiver speaks plain HTTP by design and holds no
secrets.

## 4. Safety rules, all enforced in the receiver

| rule | behaviour |
|---|---|
| schema validation | rejected with the offending field named |
| idempotency | duplicate `signal_id` rejected, never replayed |
| one position | an entry while a position is open is rejected |
| **one trade per day** | a second entry in the same `session_date` is rejected |
| protective stop | resting stop submitted in the same handler as the entry |
| no unprotected entry | an entry without a stop price is rejected |
| exits only flatten | a stop or close can never reverse into a new position |
| stale events | outside a 15-minute tolerance, rejected |
| kill switch | `touch $NQ_PAPER_DIR/KILL` refuses new entries; exits still honoured |
| restart recovery | state rebuilt from the append-only log, corrupt lines skipped and counted |
| audit | every accepted event and every rejection logged with a reason |

The one-trade-per-day and one-position rules are enforced here *as well as* in
the Pine. A duplicated or erroneous alert must not be able to re-arm the day, so
the executor does not trust the signal source.

## 5. Daily reconciliation

```bash
python3 executor/paper_executor.py reconcile --date 2026-08-17
```

Compares the paper ledger against `reference/canonical_orb_trades.csv` for that
session and reports `clean: true/false`. The paper ledger applies the same
0.75-point round-turn cost as the reference, so a matching trade reconciles to
`difference_points: 0.0` and any non-zero value is a real break.

## 6. Prove the chain

```bash
python3 executor/paper_executor.py selftest        # 16 gates
python3 tests/test_e2e_paper_pipeline.py           # full chain over real HTTP
```

The end-to-end test replays a real reference session: entry accepted with a
resting stop, S5b recorded without trading, duplicate rejected, stale rejected,
malformed rejected, stop flattens to the reference's exact net points, second
same-day entry refused, state rebuilt after a restart, and reconciliation clean.

## 7. Gate before live capital

`docs/DEPLOYMENT_ASSESSMENT.md` Task 5. In short: 20 consecutive clean sessions
before the pipeline is considered validated, 60 before real money, self-funded
or non-trailing drawdown only, and 1 MNQ per $20,000.
