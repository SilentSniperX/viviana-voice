# PAPER PIPELINE — Phase 1 runs inside TradingView

```
                    ┌─ signal alert  (bar close)   ─┐
Pine strategy  ─────┤                               ├──► receiver ──► ledger ──┐
(broker emulator)   └─ fill alert    (order fills)  ─┘              └► fills ──┤
                                                                               ▼
                                                                        daily audit
                                                                     (clean / not clean)
```

**Execution happens entirely inside TradingView's Pine strategy and its broker
emulator.** There is no external broker in Phase 1. The Tradovate adapter is
built and its gates pass, but it is **DEFERRED** — `make_broker("tradovate")`
refuses to start (`executor/brokers/README.md`). Wiring it is a live-capital
decision after paper validation, not a runtime flag.

That choice is what makes the two alert channels necessary. With no broker to
query, TradingView's own fill reports are the **only independent observation**
of what was executed, and the daily audit is what turns them into a verdict.

| channel | fires on | carries | goes to |
|---|---|---|---|
| **signal** | bar close, via `alert()` | what the strategy DECIDED — direction, entry, stop, intended exit, S5b state, **and an end-of-session heartbeat** | `events.jsonl`, drives the position ledger |
| **fill** | order fill, via `alert_message` | what the emulator DID — fill price, quantity, resulting position | `fills.jsonl`, never touches the ledger |

Keeping them apart is the whole point. If fills fed the ledger, the audit would
be comparing the ledger against itself.

---

## 1. Two TradingView alerts

Both are created on the **same chart** running `pine/nq_orb_s5b_v1.pine`.
Chart must be **5-minute, regular trading hours, America/New York**
(`docs/PARITY_PROCEDURE.md`).

**Alert A — signal channel**

- Condition: **NQ ORB + S5b v1** → **Any alert() function call**
- Message: leave the default — the script supplies the whole JSON body
- Expiration: **Open-ended**
- Notifications → **Webhook URL**: your receiver's public HTTPS URL

**Alert B — fill channel**

- Condition: **NQ ORB + S5b v1** → **Order fills only**
- Message: `{{strategy.order.alert_message}}` — this is required; the per-order
  payload is built by `f_fill()` in the Pine
- Expiration: **Open-ended**
- Notifications → **Webhook URL**: the same receiver URL

If the message field of Alert B is left as anything else, TradingView posts an
unexpanded `{{...}}` template. The receiver **rejects that loudly** rather than
recording an empty fill — it is the single most likely setup mistake and it must
never be mistaken for a quiet session.

Alerts run on TradingView's servers, so they keep firing with the browser closed.

## 2. Payloads

Signal channel, from `f_payload()`, validated against `spec/alert_schema.json`:

```json
{"strategy_version":"nq_orb_s5b_v1",
 "signal_id":"nq_orb_s5b_v1|NQ1!|20260817|ORB_LONG_ENTRY|1755432000000",
 "event":"ORB_LONG_ENTRY","symbol":"NQ1!",
 "event_time":"2026-08-17T09:50:00-0400","session_date":"2026-08-17",
 "direction":"LONG","orb_direction":"LONG",
 "s5b_state":"WAITING_FOR_LATCH","s5b_direction":"NONE","alignment":"UNRESOLVED",
 "s5b_entry_eligible":false,
 "entry":23100.25,"stop":23050.00,"exit":null,
 "or_high":23105.00,"or_low":23050.00}
```

`signal_id` is deterministic — version, symbol, session, event and bar time — so
a replayed alert is recognisably the same signal rather than a new one.

`exit` carries the strategy's **intended** exit price: the stop on a stop exit,
the RTH close price on a hold-to-close exit. Without it a close exit has nothing
to measure the emulator's fill against, and slippage per side — the kill
criterion — would only ever be observable on stop exits.

Fill channel, from `f_fill()`, placeholders expanded by TradingView at fill time:

```json
{"strategy_version":"nq_orb_s5b_v1","channel":"fill",
 "event":"ORB_LONG_ENTRY","symbol":"NQ1!","direction":"LONG",
 "fill_price":23100.25,"fill_qty":1,"position_after":1,
 "order_comment":"ORB_LONG_ENTRY","stop":23050.0,
 "session_date":"2026-08-17","bar_time":"2026-08-17T13:50:00Z",
 "signal_id":"nq_orb_s5b_v1|NQ1!|20260817|ORB_LONG_ENTRY"}
```

Seven signal events: `ORB_LONG_ENTRY`, `ORB_SHORT_ENTRY`, `ORB_STOP`,
`SESSION_CLOSE_EXIT`, `S5B_LONG_CONFIRMED`, `S5B_SHORT_CONFIRMED`,
`SESSION_SUMMARY`. Only the first four can produce a fill — S5b is a classifier
and places no orders, so a fill claiming to be an S5b event is rejected.

### SESSION_SUMMARY — why silence is not clean

`SESSION_SUMMARY` fires at the close of **every** regular session, traded or
not, carrying `"traded": true|false` and the day's final state.

It exists because without it a session where nothing arrives is
indistinguishable from a session the strategy declined. A dead receiver, an
expired alert, a paused alert or a wrong webhook URL would all have been
recorded as clean no-trade days and counted toward the 20 sessions that gate
live capital — a pipeline validating itself by being broken.

The audit therefore **fails any session with no heartbeat**, and fails a
heartbeat that disagrees with what was observed.

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
| idempotency | duplicate `signal_id` rejected, never replayed — on both channels |
| one position | an entry while a position is open is rejected |
| **one trade per day** | a second entry in the same `session_date` is rejected |
| protective stop | resting stop submitted in the same handler as the entry |
| no unprotected entry | an entry without a stop price is rejected |
| **no invented prices** | an exit event with no exit price is rejected, never booked at the entry price |
| exits only flatten | a stop or close can never reverse into a new position |
| stale events | outside a 15-minute tolerance, rejected |
| fills never trade | a fill payload is recorded and reconciled, never acted on |
| unexpanded placeholders | a mis-configured alert message is rejected, not recorded |
| kill switch | `touch $NQ_PAPER_DIR/KILL` refuses new entries; exits still honoured |
| restart recovery | state rebuilt from the append-only log, corrupt lines skipped and counted |
| audit | every accepted event and every rejection logged with a reason |

The one-trade-per-day and one-position rules are enforced here *as well as* in
the Pine. A duplicated or erroneous alert must not be able to re-arm the day, so
the executor does not trust the signal source.

## 5. The daily audit — the Phase 1 deliverable

Run once after each session closes:

```bash
python3 executor/daily_audit.py --date today        # or `yesterday`, or 2026-08-17
```

`today` and `yesterday` resolve to **New York** dates. Running from a UTC box
after 20:00 New York would otherwise audit tomorrow, find nothing, and bank a
spurious clean no-trade session toward the streak.

Optionally cross-check against the Strategy Tester's own record:

```bash
python3 executor/daily_audit.py --date 2026-08-17 \
        --tv-export "NQ ORB + S5b v1 List of Trades.csv"
```

It reconciles three independently-produced streams — plus the canonical
reference when the date falls inside its window — and prints PASS/FAIL per named
check. Exit code is 0 only when the session is clean.

What it checks:

| group | check |
|---|---|
| structure | at most one entry signal and one entry fill; every signal reached the emulator; no orphan fill without a signal behind it |
| direction | signal, fill and ledger all agree |
| position | TradingView holds exactly one contract on the correct side after entry, and is flat after the exit |
| **the position was closed** | no exit fill is a failure — hold-to-close is a frozen rule |
| slippage | entry and exit, signed so positive is always adverse; **fails above 1.5 points**, the kill threshold from `docs/DEPLOYMENT_ASSESSMENT.md` |
| stop discipline | a stop may fill worse than its trigger, never better |
| ledger | one closed trade, prices agreeing with the emulator's fills, executor flat |
| **the heartbeat** | exactly one `SESSION_SUMMARY`, agreeing on whether the session traded — no heartbeat is a failure, never a quiet day |
| rejections | anything beyond a routine duplicate alert fails the session |
| reference | inside the canonical window: traded exactly when the reference did, same direction, net points within the known price-series tolerance |
| export | with `--tv-export`: one trade for the date, direction and both prices agreeing with the fill alerts |

Outside the canonical window the audit says so explicitly rather than reading
"no reference trade" as "no trade" — the reference ends 2026-06-30 and its
silence after that date means nothing.

Every run appends one verdict record to `sessions.jsonl`. Re-auditing a session
overwrites the earlier verdict, so a break can be corrected once it is
understood, with both records left on the log.

### Never miss a session

```bash
python3 executor/daily_audit.py --catch-up
```

Audits every weekday since the last audited session. Running the audit only on
the days you remember to is not unattended operation: a week of dead receiver
would otherwise leave no record at all, and the streak counts audited sessions.
Catch-up turns a gap into N failing sessions instead of nothing.

A real market holiday and a dead receiver both look like silence, and no
calendar shipped in this repo would stay correct. So the default stays safe and
the exception is an explicit, logged operator assertion:

```bash
python3 executor/daily_audit.py --mark-holiday 2026-11-26
```

Holiday and replay verdicts are excluded from the streak in both directions.

## 6. The streak that gates deployment

```bash
python3 executor/daily_audit.py --streak
```

```json
{"sessions_audited": 22, "consecutive_clean": 22, "of_which_traded": 14,
 "pipeline_validated": true, "live_capital_gate_met": false,
 "sessions_with_failures": []}
```

`docs/DEPLOYMENT_ASSESSMENT.md` Task 5 requires **20 consecutive clean sessions**
to consider the pipeline validated and **60** before live capital.

`of_which_traded` exists because the strategy declines a minority of sessions
outright (2,295 trades across roughly 2,630 trading days in the reference
window). A declined session is a legitimate outcome and counts as
clean, but it proves nothing about execution — so neither gate opens unless at
least half the streak actually traded. Quiet days cannot manufacture a validated
pipeline.

## 7. Prove the chain

```bash
python3 executor/paper_executor.py selftest        # 22 gates
python3 tests/test_daily_audit.py                  # audit gates, incl. real HTTP
python3 tests/test_e2e_paper_pipeline.py           # full chain over real HTTP
python3 tests/replay_reference_sessions.py         # 4,022 real sessions
```

`replay_reference_sessions.py` exists to prove the property the gate suite
cannot: that the audit does **not** raise FALSE failures on ordinary sessions.
A single false-failure mode would stall the live count indefinitely and be
blamed on the market rather than on the tool. It replays every canonical
session end to end — signals through the ledger, fills through the fill
channel, then the audit — and currently reports **4,022 / 4,022 clean, 0
payloads rejected**. It found a real defect on its first run: the schema
validator had no `boolean` branch, so every heartbeat was being rejected.

Every replayed verdict is written with `mode: "replay"` and the run asserts
that none of them reached the streak.

The end-to-end test replays a real reference session with four faults injected —
duplicate alert, stale alert, malformed payload, second same-day entry — then
posts TradingView's fill reports and runs the audit. It asserts that the audit
**refuses to call that session clean**, and that it does so without flagging the
routine duplicate. A reconciliation tool that passed there would be
manufacturing the streak that gates live capital.

`tests/test_daily_audit.py` covers the clean path and every break the audit must
catch: an unclosed position, a fill in the wrong direction, excessive slippage,
a stop filling better than its trigger, two contracts instead of one, an orphan
fill, a direction disagreeing with the reference, and a Strategy Tester export
that disagrees with the fill alerts.

## 8. Before live capital

`docs/DEPLOYMENT_ASSESSMENT.md` Task 5. In short: 20 consecutive clean sessions
before the pipeline is considered validated, 60 before real money, measured
slippage at or below 0.75 points per side, self-funded or non-trailing drawdown
only, and 1 MNQ per $20,000.

Only then does the broker question reopen. Nothing in this document connects one.
