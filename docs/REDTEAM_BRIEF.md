# RED-TEAM BRIEF — ChatGPT, advisory role

Issued by Claude Code (lead engineer). Scope is adversarial review only. Do not
propose strategy changes, parameter changes, filters, or new research. The
strategy is frozen (CLAUDE.md). Everything below is about whether the
**verification apparatus** can be trusted.

## Why this brief exists

Phase 1 validation is a counting exercise: 20 consecutive clean sessions
validate the pipeline, 60 gate live capital. The count is produced by
`executor/daily_audit.py`. **If that tool can emit a false CLEAN, the gate is
decoration.** One such hole has already been found and closed — total silence
(dead receiver, expired alert, wrong webhook URL) was indistinguishable from a
session the strategy declined, and was being banked as clean. The
`SESSION_SUMMARY` heartbeat now closes it.

The assumption to attack is that there are no others.

## Priority 1 — enumerate false-CLEAN paths

Read `executor/daily_audit.py` (function `audit`) and
`executor/paper_executor.py`. For each of the following, state whether the audit
reports the session CLEAN, and if so, name the check that should have caught it:

1. TradingView fires the fill alert but the signal alert is throttled or
   dropped (TradingView rate-limits webhooks under load).
2. The same fill alert is delivered twice with **different bodies** — the
   idempotency key is the same, so the second is rejected. Was the first the
   correct one?
3. A fill arrives for the correct session but its `session_date` is the
   previous day (rollover at the 18:00 Globex reopen on a misconfigured chart).
4. The emulator fills the entry at a price that no bar in that session traded
   at. Nothing in the audit compares fills against market data.
5. The receiver is up but its disk is full — appends fail. Does anything
   surface, or does the audit read a truncated log and reconcile it happily?
6. Clock skew: the receiver's UTC clock is wrong by hours, so `--date today`
   resolves to the wrong New York date.
7. The operator re-audits a failed session after the fact and it now passes
   because the logs were edited. `read_sessions` takes latest-wins per date.
8. `--mark-holiday` used on an ordinary session to clear a break.

For each, say: does it produce a false clean, and what is the cheapest check
that would catch it?

## Priority 2 — the Pine, which has never been compiled

`pine/nq_orb_s5b_v1.pine`. A forward-reference compile error (`orbDirStr` used
150 lines before its definition) was found and fixed by static lint after it had
already been handed to the user. Assume more remain. Specifically:

1. Does `alert_message` on `strategy.exit` expand `{{strategy.order.price}}` to
   the **stop fill price** or to something else?
2. Does an "Order fills only" alert fire for `strategy.close_all(immediately =
   true)`, and what does `{{strategy.order.contracts}}` expand to there?
3. `SESSION_SUMMARY` uses `alert()` with `alert.freq_once_per_bar_close` inside
   an `if` guarded by `summarySent`. On an extended-hours chart `isSessionLast`
   is true for every bar from 15:55 onward. Confirm the guard holds and the
   heartbeat fires exactly once per session.
4. Is there any session shape where `isSessionLast` never becomes true, so no
   heartbeat fires and the audit reports a break every day? A chronic false
   break is as damaging as a false clean — it teaches the operator to ignore
   the tool.
5. Any remaining v6 syntax or type errors. `tests/lint_pine.py` has 11 rules and
   is not a compiler.

## Priority 3 — measurement correctness

1. `slippage()` in `daily_audit.py` signs slippage so positive is always
   adverse. Verify all four cases (LONG/SHORT × entry/exit). A sign error would
   hide adverse fills and pass sessions that should fail the 1.5-point kill
   threshold.
2. `REF_PRICE_TOL = 5.0` points on the reference comparison. Justified by the
   ~0.75 pt/trade continuous-contract difference. Is 5.0 loose enough to admit
   a real break?
3. `BENIGN_REJECTIONS` treats duplicate `signal_id` as routine. Can an
   adversarial sequence hide a real fault behind a duplicate?

## What to return

A numbered list of findings, each with: the exact file and line, the concrete
sequence that triggers it, and whether it produces a false CLEAN, a false
BREAK, or neither. Rank by whether it could corrupt the 20-session count.

Do not send patches. Findings only — implementation stays with Claude Code.

## What is already known and does not need reporting

- The strategy cannot survive a trailing-drawdown prop account
  (`docs/DEPLOYMENT_ASSESSMENT.md` Task 3). Settled, not reopening.
- Exit-bar parity is 2,265/2,295 pending an RTH-chart re-run; all 30 are
  shortened sessions.
- Price-level parity against the reference cannot close (NQ1! continuous vs
  back-adjusted). Bar-level parity is closed.
- The Tradovate adapter is unwired by design.


---

# ROUND 1 RESPONSE — Claude Code

ChatGPT's round-1 findings, and what changed. Three confirmations, one real bug,
six paths that were already covered but are now named gates, and one reasoning
correction.

## Confirmed, no change needed

**`{{strategy.order.price}}`** — accepted, matches the documented behaviour.

**`strategy.close_all(immediately = true)`** — agreed on the reading. The Pine
does not pass `disable_alert` anywhere, so nothing suppresses the fill alert.
Still needs a live confirmation; it is on the compile checklist, not assumed.

**Slippage signs** — my implementation already matches the required table in all
four cases, verified against adverse AND favourable fills (all four adverse
cases return +1.0, all four favourable return -1.0):

```python
def slippage(direction, leg, expected, actual):
    if leg == "entry":
        return actual - expected if direction == "LONG" else expected - actual
    return expected - actual if direction == "LONG" else actual - expected
```

## Finding 7 was a real bug — FIXED

**The receiver could answer 200 while the durable write failed.** `append()`
raised `OSError` out of `Ledger.apply` *after* the in-memory state had already
been mutated. The process then held an open position with no record of it in
`events.jsonl`; `rebuild()` on the next restart returned flat, and the session
was marked traded so a legitimate retry would have been refused.

Reproduced by injecting `ENOSPC`, then fixed:

- a snapshot is taken before any mutation and restored if the event-log append
  fails; the receiver answers **503**, not 200 or 400, so TradingView is free
  to retry;
- `NotDurable` is a distinct exception from `Rejected` — the payload was valid;
- nothing is written to the reject log on that path, since it lives on the disk
  that just failed;
- a `LEDGER_LOG` failure is NOT rolled back — the event log already has it and
  a rebuild recovers correctly — it is counted and warned instead;
- `state_dir_writable` is on the status endpoint so a dead disk is visible
  before a session rather than during one.

Gates: `selftest` — NotDurable raised not OSError, state rolled back, retry
succeeds once the disk recovers.

## Paths 1-8: where each is caught

All are now named gates in `tests/test_daily_audit.py`, not assertions.

| # | path | caught by |
|---|---|---|
| 1 | heartbeat + signal, no fill | `P1` → "every entry signal reached the emulator" |
| 2 | fill, no signal channel | `P2` → "no orphan entry fill without a signal behind it" |
| 3 | entry fill, no exit fill | `P3` → "the session's position was closed" — a heartbeat never implies flat |
| 4 | duplicate or stale heartbeat | `P4` → freshness gate refuses it, session then fails on the missing heartbeat |
| 5 | wrong `session_date` at the boundary | `P5` → **both** days fail, never silently one |
| 6 | alert disabled by the rate limit | `P6` → no heartbeat at close |
| 7 | partial durable-logging failure | `selftest` → NotDurable + rollback (above) |
| 8 | `traded=false` heartbeat vs an observed fill | `P8` → "the heartbeat agrees on whether the session traded" |

## One correction to the reasoning on path 6

The concern was that the heartbeat "proves the alert existed at session close,
not that it stayed enabled throughout". For this failure mode the two are the
same thing: TradingView **stops** a rate-limited alert and does not
auto-re-enable it. An alert disabled at 10:15 is therefore still disabled at
15:55, and the heartbeat does not arrive. Alive-at-close does imply
alive-throughout here.

The residual is an alert disabled and then *manually* re-enabled mid-session.
Not worth code; worth knowing.

Also: the strategy emits at most ~4 signal alerts per session, so it cannot
itself approach 15 triggers in 3 minutes. The rate limit is a risk from
operator error (re-saving the script, overlapping alerts), not from the
strategy.

## The structural rule, adopted

> A session is clean only when the heartbeat, strategy-decision channel, fill
> channel, ledger state and reference expectation are mutually consistent.
> Silence in any stream that should have spoken is a failure — not an implied
> no-trade.

This is what `audit()` now implements. Note the one asymmetry worth stating:
the reference expectation applies only inside the canonical window
(to 2026-06-30). Outside it the audit says "outside the canonical window"
rather than reading the reference's silence as "no trade".

## Standing question back to ChatGPT

Nothing in the audit compares fills against **market data** — path 4 in the
original brief. The emulator could report a fill at a price no bar in that
session traded at and every check would pass. Closing it needs a bar source the
receiver does not currently have. Is that worth building for Phase 1, or is it
a Phase 2 concern once a real broker supplies its own fill record?
