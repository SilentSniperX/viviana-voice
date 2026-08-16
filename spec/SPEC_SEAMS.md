# SPEC SEAMS AND SOURCE CONFLICTS — LEDGER

`CLAUDE.md`: *"If any two sources conflict, STOP implementation at that seam and
document the conflict. Do not guess."*

This is that ledger. Every entry states what the frozen spec says, what the
shipped research engines do, what was implemented, and whether the seam blocks
Phase 1. Nothing here changes a frozen rule.

Source-of-truth order used throughout (`CLAUDE.md`):
1. `spec/STRATEGY_SPEC_FROZEN.md`
2. `spec/PARITY_ACCEPTANCE_GATES.md`
3. `research/latest_round4/round4_report.txt`
4. `research/master_handoff/`
5. `research/reference/NQ_Three_Strategy_Vault_Aug2026.pdf`

Status values: **RESOLVED** (a higher-ranked source settles it), **OPEN**
(needs the user), **BLOCKER** (stops Phase 1).

---

## S-1 — S5b classifier time window — RESOLVED EMPIRICALLY (11:30 cap)

Spec C says *"No standalone S5b entries after the 11:30 5m bar"*, which is silent
on whether the classifier itself keeps running. `tmp_s5b_round3c.py::s5b_day`
scans the whole session; the flag file that produced the published results does
not.

**Resolution: the entire classifier — latch included — runs from the 10:00 bar
through the 11:30 bar and stops.** Verified against
`s5b_day_flags_allmult.csv` over 764 real sessions (2023-07-13 .. 2026-06-30):
with the cap, latch direction / band / confirmation / invalidation match
**764/764**; without it, 57 sessions latch after 11:30 that the reference never
latches, and every post-11:30 latch is a mismatch.

## S-2 — is the pullback band sticky? — RESOLVED EMPIRICALLY (live condition)

Spec C.4 defines the valid pullback but does not say whether the band must still
hold on the later failure and reassertion bars.

**Resolution: the band is a LIVE condition on those bars, not a latch.** Making
it sticky adds 48 confirmations across 764 sessions that the reference does not
have.

## S-3 — delay between pullback activation and completion — RESOLVED EMPIRICALLY (none)

`tmp_s5b_round3c.py` gates the failure/reassertion behind
`j - pull_start_j >= 2`. Spec C states no such delay.

**Resolution: no delay.** Only the two-bar no-progress test itself constrains
timing. Imposing the delay costs 46 confirmations across 764 sessions.

## S-4 — "no new extreme" precondition on the pullback — RESOLVED EMPIRICALLY (absent)

`tmp_s5b_round3c.py` requires `not made_new_extreme` when opening the pullback.
Spec C.4 states only the 25-75% band.

**Resolution: there is no such precondition.** Adding it breaks the band flag on
30 of 764 sessions. A bar may both extend the leg and open the pullback.

### How S-1..S-4 were resolved, and what it says about the sources

All sixteen combinations of these four readings (plus the volume-window
question, S-5) were run over the 764 real sessions and scored against the
reference flags. Exactly one combination scores **764/764 on all four state
dimensions**; the runner-up misses 12 confirmations. The winner also reproduces
**695/695 latch timestamps** and **103/103 confirmation directions and
timestamps** against `vendor/claude_chat_v1/parity_reference_s5b_states_2016_2026.csv`.
The resolving grid is `tests/resolve_s5b_clauses.py`.

**`tmp_s5b_round3c.py` is not authoritative for spec section C.** It is an
exploratory variant (note the `tmp_` prefix; its `__main__` sweeps a volume
switch over 2024-2026) and its readings do not reproduce the flag file behind
the published four-state taxonomy. The winning combination is also closer to the
literal text of spec C.4 and C.6 than that script is. An earlier revision of this
project took it as authoritative and was wrong on all four clauses; see the
correction at the top of `vendor/claude_chat_v1/AUDIT.md`.

## S-5 — relative-volume window includes the current bar — RESOLVED EMPIRICALLY

Spec C.7 is silent on whether the current bar is inside the 12-bar mean.
`tmp_s5b_round3c.py` implements both via a `vol_shift` switch and defaults to
including it.

**Resolution: include the current bar.** In the grid above, excluding it costs
12 confirmations across 764 sessions.

## S-6 — the `Failed_Counterattack_*` logs are a DIFFERENT definition — RESOLVED (do not use for S5b parity)

`Failed_Counterattack_Continuation_v1_2008_2026.csv`,
`Failed_Counterattack_1m_execution_2016_2026.csv` and
`Failed_Counterattack_hold_close_variant.csv` were produced by
`backtest_failed_counterattack.py` / `backtest_failed_counterattack_1m.py`, which
build the sequence from the **ORB opening range** using **signed volume** and a
**median-true-range tolerance**.

Frozen spec C builds it from the **09:30-09:59 opening balance**, a **25-75%
retracement band**, a **1.0-point** no-progress tolerance and a **1.2x relative
volume** test. These are different constructions with different timestamps.

Consequence: those CSVs are **not** an S5b state-parity reference. The correct
references for spec-C S5b are:
- `research/master_handoff/01_CLAUDE_ROUND3_PART1/s5b_day_flags_allmult.csv`
  (per-session latch side, band reached, reassertion flag at 1.0/1.2/1.4x,
  invalidation) — 4,768 sessions;
- `research/master_handoff/01_CLAUDE_ROUND3_PART1/claude_s5b_hist_2008_2023.csv`
  (763 standalone trades; `entry_ts` is the confirmation bar + 5 minutes, so it
  pins the confirmation timestamp).

## S-7 — `S4_Samir_Full_2016_2026.csv` disagrees with the canonical benchmark — RESOLVED (legacy, not canonical)

2,226 rows vs 2,295 canonical, and per-trade `risk_pts` differs (e.g. 35.0 vs
34.75 on 2016-01-04). It is an earlier engine. Quantified in
`reference/CROSS_ENGINE_AUDIT.md` section 4 and excluded from parity.

## S-8 — reference logs contain trades on three US market holidays — OPEN (non-blocking)

The 5-minute benchmark engine produced trades on `2008-05-26`, `2011-05-30` and
`2011-07-04` (Memorial Day / Independence Day). The independent 1-minute engine
excluded all three. Every other one of the 4,019 shared sessions agrees.

These three rows are retained in the canonical list, flagged `early_close=1`
(they exit on an 11:25 bar), and will show up as `missing_in_candidate` when
parity is run against a TradingView feed that has no RTH session on those dates.
That is a *calendar* difference, not a logic difference. The user may choose to
drop them from the canonical list; nothing in v1 depends on the choice.

## S-9 — only one side of the opening range is recoverable from the shipped logs — OPEN (non-blocking)

`spec/PARITY_ACCEPTANCE_GATES.md` asks parity to compare `ORH` and `ORL` per
trade. The shipped trade logs carry only `stop`, which is ORL for longs and ORH
for shorts. The canonical list therefore fills `or_low` for longs, `or_high` for
shorts, and records which side is known in `or_known_side`.

Full ORH/ORL parity requires either the FirstRate raw archives or a TradingView
chart-data export (which the Pine script emits on every bar). Until then, ORB
parity is checked on the stop side plus the derived breakout constraint.

## S-10 — TradingView fill model on skipped days — OPEN (strategy tester only)

**The session-close half of this seam is now CLOSED.**
`strategy.close_all(..., immediately = true)` executes on the current bar, so
the tester books the session-close exit at the final bar's close — the canonical
value — while `process_orders_on_close` stays `false` for correct next-bar
entries. This mechanic was taken from the Claude Chat package
(`vendor/claude_chat_v1/AUDIT.md`).

What remains: on a **skipped** day (entry open already beyond the stop, or a gap
where the entry bar belongs) the entry order was already submitted on the signal
bar, so the tester shows a flat-again round trip that the deterministic record
does not contain. The flatten is `immediately = true`, so the artifact is
contained within the entry bar. The **deterministic record** emitted by the
script (the `px_*` parity series and the alert payloads) is
the canonical value, and that record — not the tester's fill — is what
`tests/parity_orb.py` compares.

Same class of seam: on a skipped day (entry open already beyond the stop, or a
gap where the entry bar belongs) the entry order has already been submitted on
the signal bar, so the tester shows a flat-again round trip that the
deterministic record does not contain.

## S-11 — entry-alert timing vs entry price — OPEN (Phase 2 decision, not Phase 1)

The `ORB_LONG_ENTRY` / `ORB_SHORT_ENTRY` alerts fire at the **close** of the
entry bar and carry that bar's **open** as the entry price, because
`calc_on_every_tick = false` is what makes historical and realtime state
transitions identical (`spec/PARITY_ACCEPTANCE_GATES.md`, no-lookahead section).

A live paper executor acting on that alert fills up to five minutes after the
canonical entry price. Phase 2 must decide between (a) accepting and measuring
that slippage in the paper ledger, or (b) emitting the actionable alert on the
signal bar with a "market on next bar open" instruction. Both are execution-layer
choices; neither changes a strategy rule. **Do not resolve this by changing the
Pine timing model without the user.**

## S-12 — latch-rate definitional variance between the two research engines — RESOLVED (informational)

`round4_report.txt` records unlatch-at-40m rates of 20.7 / 20.6 / 20.0% per era
against `Round4_latch_timing_only.csv`'s 18.21 / 18.68 / 18.20%, attributed to
bar-close vs bar-open counting at the cutoff. The +40m NO-LATCH rule is DEAD
(spec G), so nothing in v1 reads either number. Recorded because
`round4_report.txt` explicitly asked for it in the parity ledger.
