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

## S-1 — S5b `leg_high` initialisation — RESOLVED

Spec C.1/C.2 says `leg_low = session low through direction/latch bar` and
`leg_high = running max after direction latch`, which leaves the *initial* value
of `leg_high` unstated.

`research/.../tmp_s5b_round3c.py` line 42-43 sets **both** extremes from the
session through and including the latch bar (`hist = fd.loc[:ts]`), then runs the
max forward. Implemented that way in `tests/reference_engine.py::s5b_day` and in
`pine/nq_orb_s5b_v1.pine` (section C).

Not blocking: the spec is silent, not contradictory, and the research engine is
the artifact that produced the shipped S5b flag files.

## S-2 — pullback activation also requires "no new extreme" — RESOLVED

Spec C.4 says a valid pullback is a retracement between 0.25 and 0.75. The
reference engine additionally requires that the bar did not itself make a new leg
extreme (`and not made_new_extreme`, tmp_s5b_round3c.py line 54/71).

Implemented per the reference engine. Without it, a bar that both extends the leg
and has a deep low would open a pullback the research flags never recorded.

## S-3 — failure/reassertion cannot be evaluated for two bars after the pullback opens — RESOLVED

`j - pull_start_j < 2: continue` (tmp_s5b_round3c.py line 57/74). Not stated in
spec C.6. Implemented per the reference engine.

## S-4 — relative-volume window includes the current bar — RESOLVED

Spec C.7: *"volume >= 1.2 * rolling 12-bar mean volume, rolling mean can begin
with minimum 6 observations."* Silent on whether the current bar is inside the
window.

`tmp_s5b_round3c.py` implements both (`vol_shift` parameter) and **defaults to
including the current bar** (`fd.volume.rolling(12, min_periods=6).mean()`).
Implemented include-current. `CLAUDE.md` lists the relative-volume threshold as
DO-NOT-CHANGE, so this is fixed, not an input.

## S-5 — the `Failed_Counterattack_*` logs are a DIFFERENT definition — RESOLVED (do not use for S5b parity)

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

## S-6 — `S4_Samir_Full_2016_2026.csv` disagrees with the canonical benchmark — RESOLVED (legacy, not canonical)

2,226 rows vs 2,295 canonical, and per-trade `risk_pts` differs (e.g. 35.0 vs
34.75 on 2016-01-04). It is an earlier engine. Quantified in
`reference/CROSS_ENGINE_AUDIT.md` section 4 and excluded from parity.

## S-7 — reference logs contain trades on three US market holidays — OPEN (non-blocking)

The 5-minute benchmark engine produced trades on `2008-05-26`, `2011-05-30` and
`2011-07-04` (Memorial Day / Independence Day). The independent 1-minute engine
excluded all three. Every other one of the 4,019 shared sessions agrees.

These three rows are retained in the canonical list, flagged `early_close=1`
(they exit on an 11:25 bar), and will show up as `missing_in_candidate` when
parity is run against a TradingView feed that has no RTH session on those dates.
That is a *calendar* difference, not a logic difference. The user may choose to
drop them from the canonical list; nothing in v1 depends on the choice.

## S-8 — only one side of the opening range is recoverable from the shipped logs — OPEN (non-blocking)

`spec/PARITY_ACCEPTANCE_GATES.md` asks parity to compare `ORH` and `ORL` per
trade. The shipped trade logs carry only `stop`, which is ORL for longs and ORH
for shorts. The canonical list therefore fills `or_low` for longs, `or_high` for
shorts, and records which side is known in `or_known_side`.

Full ORH/ORL parity requires either the FirstRate raw archives or a TradingView
chart-data export (which the Pine script emits on every bar). Until then, ORB
parity is checked on the stop side plus the derived breakout constraint.

## S-9 — TradingView fill model cannot reproduce both the entry and the close exit — OPEN (documented, affects the strategy tester only)

The frozen entry semantic is *next 5m bar open*, which requires
`process_orders_on_close = false`. Under that setting a market order placed on
the final RTH bar fills at the **next** bar's open, so the strategy tester books
the session-close exit at the following session's open.

`CLAUDE.md` lists "Next-bar execution semantics" as DO-NOT-CHANGE, so the flag
stays `false` and the session-close fill is a known reporting seam of the
TradingView tester. The **deterministic record** emitted by the script (the
`px_*` parity series and the alert payloads) uses the final bar's close, which is
the canonical value, and that record — not the tester's fill — is what
`tests/parity_orb.py` compares.

Same class of seam: on a skipped day (entry open already beyond the stop, or a
gap where the entry bar belongs) the entry order has already been submitted on
the signal bar, so the tester shows a flat-again round trip that the
deterministic record does not contain.

## S-10 — entry-alert timing vs entry price — OPEN (Phase 2 decision, not Phase 1)

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

## S-11 — latch-rate definitional variance between the two research engines — RESOLVED (informational)

`round4_report.txt` records unlatch-at-40m rates of 20.7 / 20.6 / 20.0% per era
against `Round4_latch_timing_only.csv`'s 18.21 / 18.68 / 18.20%, attributed to
bar-close vs bar-open counting at the cutoff. The +40m NO-LATCH rule is DEAD
(spec G), so nothing in v1 reads either number. Recorded because
`round4_report.txt` explicitly asked for it in the parity ledger.
