# INDEPENDENT AUDIT — Claude Chat "TradingView v1 package"

> **CORRECTION, after the clean-room run on real data.** Findings P-4, P-5, P-6
> and P-7 below are **WITHDRAWN — they were wrong.** I had taken
> `tmp_s5b_round3c.py` as authoritative for spec section C. Running the frozen
> engine over 764 real sessions against `s5b_day_flags_allmult.csv` shows that
> script is an exploratory variant that does *not* reproduce the reference
> flags, while this package's readings do. On all four of those clauses the
> Claude Chat implementation was right and mine was wrong; my engine and Pine
> have been corrected to match. Evidence and the resolving grid are in
> `spec/SPEC_SEAMS.md` S-1..S-4. P-9 is downgraded to a note for the same
> reason. Everything else in this audit stands, and the ORB findings (P-1, P-2,
> P-3) are unaffected.

Audited as an untrusted third-party contribution, per the instruction not to
assume it is correct because another Claude produced it. The files in this
directory are the **originals, unmodified**, kept for provenance.

Reference used for the audit: `reference/canonical_orb_trades.csv`, built and
cross-verified in Phase 1 (`reference/CROSS_ENGINE_AUDIT.md`), plus the frozen
spec and the two research engines
(`backtest_orb_collective_management.py`, `tmp_s5b_round3c.py`).

---

## VERDICT SUMMARY

| component | verdict |
|---|---|
| `parity_reference_orb_2016_2026.csv` | **TRUSTWORTHY** — matches the verified canonical on every material field |
| `parity_reference_s5b_states_2016_2026.csv` | **TRUSTWORTHY** — 2,705/2,705 sessions match the validated research flags |
| 12 preregistered S5b spot-check dates | **TRUSTWORTHY** — 12/12 internally consistent and consistent with the research flags |
| `parity_check.py` | **NOT FIT FOR USE AS A GATE** — passes a corrupt export, fails a correct one |
| `ORB_S5b_v1.pine` | **NOT READY** — 4 parity-breaking defects (P-1, P-2, P-3, P-8) plus 6 lesser ones. Its S5b clause readings were **right** and mine were wrong; see the correction above |
| `IMPLEMENTATION_AND_PARITY.txt` | mostly accurate; 3 incorrect claims (D-1..D-3) |

---

## 1. REFERENCE DATA — VERIFIED GOOD

### 1.1 ORB reference

2,295 rows, 2016-01-04 to 2026-06-30. Compared field by field against the
canonical list:

- **direction, signal_ts, entry_ts, entry, stop, exit price, exit reason:
  0 differences across all 2,295 trades.**
- `netR` differs on 5 rows by 4-decimal rounding only.
- `exit_ts` is expressed at **1-minute** resolution where the canonical is
  5-minute; 1,693 of those match the independent 1-minute engine's timestamps
  exactly and the rest fall inside the canonical 5-minute exit bar.

Their reference is the same verified engine output. It is safe to use — with the
resolution caveat that breaks their checker (C-1).

### 1.2 S5b state reference

2,705 rows. Against `s5b_day_flags_allmult.csv` (validated in Phase 1 by
reproducing all twelve published profit factors): **latch direction, band,
confirmation, invalidation all match on 2,705/2,705 sessions.** Zero
disagreements.

Their `confirm_ts` equals the standalone log's `entry_ts` on all 308 overlapping
sessions — i.e. they define the confirmation timestamp as the **bar after** the
reassertion. See D-2.

### 1.3 The 12 preregistered spot-check dates

All twelve are consistent with their own reference CSV **and** with the research
flag file: latch times and directions, confirm times, invalidation times, band
status and no-latch status. This list is a usable spot-check.

---

## 2. `parity_check.py` — DISQUALIFYING DEFECTS

Both were demonstrated by running the script.

### C-1 (BLOCKER) A correct implementation fails

`xtime_ok` compares exit times as exact `%H:%M` strings, but **only 234 of the
2,295 reference exit timestamps are 5-minute aligned** — the rest are 1-minute
values such as `15:59`, `11:13`, `10:46`.

Fed a synthetic export that is *byte-for-byte the reference itself* with exits
placed on the containing 5-minute bar — exactly what a perfect Pine on the
mandated 5-minute chart produces — the script reports:

```
EXIT_TIME mismatches: 2061
FULL-MATCH trades: 234/2295  → PARITY INCOMPLETE
```

A gate that fails a perfect implementation cannot be used to accept or reject
one.

### C-2 (BLOCKER) A corrupt implementation passes

`--offset-ok` — the flag the package's own setup notes tell you to use on
TradingView's unadjusted continuous feed — sets `eprice_ok = True`
unconditionally instead of absorbing a constant offset. Exit prices are never
compared at all. Fed an export with **randomised entry prices and every exit
price shifted by +999 points**:

```
DIRECTION mismatches: 0   ENTRY_TIME mismatches: 0
ENTRY_PRICE mismatches: 0 EXIT_TIME mismatches: 0
FULL-MATCH trades: 2295/2295  → PARITY PASS
```

### C-3 Advertised buckets that do not exist

`IMPLEMENTATION_AND_PARITY.txt` §5b lists eight buckets including `EXIT_PRICE`
and `STOP`. The script implements neither. `STOP` cannot be implemented from a
List-of-Trades export at all — the stop level is not in it.

### C-4 Lesser issues

- merges on `date` only; two trades on one session silently produce duplicate
  rows rather than a one-trade-per-day violation;
- an open final trade (`NaT` exit) is reported as a generic time mismatch;
- no check that the offset is actually piecewise constant, so the quarterly
  medians it prints are decorative.

**Replacement:** `tests/parity_tv_list_of_trades.py`. On the same three inputs:
correct-5m export **2,295/2,295 PASS**; corrupt export **0/2,295, blocked, 2,295
inconsistent-leg trades reported**; genuine two-era roll offset (+137 then +402
on every leg) **2,295/2,295 PASS with exactly 2 offset levels detected**.

---

## 3. `ORB_S5b_v1.pine` — DEFECTS

Line numbers refer to the original in this directory.

### Parity-breaking

**P-1 (L213-214) The hard stop is not live on the entry bar.**
`strategy.exit` is inside `if strategy.position_size != 0`, so the order is only
submitted at the close of the entry bar and becomes active on the *next* bar.
The reference checks the stop from the entry bar inclusive.
Affected: **2 trades in 2016-2026** (2017-06-01, 2023-02-15), 8 in 2008-2026 —
each becomes a hold instead of a stop, changing direction of P&L on those days.
*Correct approach:* submit the protective stop on the SIGNAL bar, where the stop
price (the opposite OR extreme) is already known.

**P-2 (L34, L215) Shortened sessions never flatten.**
`isEODbar = hm == 1555`. On an early close there is no 15:55 bar, so the
position is carried overnight. The next session's `isNewDay` block sets
`orbStop := na`, after which `strategy.exit(stop = na)` protects nothing.
Affected: **48 sessions in 2016-2026** (36 closing 12:55, 12 closing 13:10).
This is both a parity break and a live risk-control failure — an unprotected
overnight futures position.
*Correct approach:* `session.islastbar_regular`, which is exchange-driven and
resolves shortened sessions.

**P-3 (L203-210) Gap-beyond-stop enters and then voids, instead of skipping.**
Frozen spec B: *"If next-bar entry is already beyond the stop/invalidation, SKIP
the trade."* The script takes the trade and closes it, booking a round trip the
reference does not contain — an EXTRA_DAY plus fabricated P&L on every such
session.

**P-4 — WITHDRAWN. (L149-150, L168-169) Pullback activation and the "no new
extreme" condition.**
*Original finding, now known to be wrong:* `tmp_s5b_round3c.py` requires
`.25 <= retr <= .75 and not pull and not made_new_extreme`. The Pine omits the
last clause, so a wide bar that both extends the leg and prints a deep low opens
a pullback the research never opened.

**P-5 — WITHDRAWN. (L152-158) Failure and reassertion on the pullback bar.**
*Original finding, now known to be wrong:* The reference gates them behind `j - pull_start_j >= 2`. The
Pine can therefore confirm two bars earlier than the research engine.

**P-6 — WITHDRAWN. (L153, L156) `inBand` re-required on the failure and
reassertion bars.** This is CORRECT: the band is a live condition, and treating
it as sticky adds 48 false confirmations across 764 sessions.
*Original finding, now known to be wrong:*
In the reference `pull` is a **sticky flag**: once the band has been touched,
failure and reassertion are evaluated on every later bar regardless of the
current bar's retracement. Because a genuine reassertion bar rallies away from
the pullback low, its retracement is shallow **by construction** — frequently
below 0.25 — so this clause suppresses exactly the confirmations it is meant to
detect. This is the most consequential S5b defect.

**P-7 — WITHDRAWN. (L128) The state machine capped at 11:30.** This is CORRECT.
Without the cap, 57 of 764 sessions latch after 11:30 that the reference never
latches. (The 23 post-11:30 confirmations in their own reference CSV are
sessions that latched before 11:30 and finalise one bar later, which is
consistent with the cap.)
*Original finding, now known to be wrong:*
`s5scan = ... hm >= 1000 and hm <= 1130`. In the reference the 11:30 cap applies
only to standalone *entries*; `s5b_day` classifies across the whole session.
**Their own reference CSV contains 23 confirmations after 11:30** — the Pine
cannot reproduce the file shipped alongside it.

**P-8 (L159, L178) `ta.lowest` / `ta.highest` are called inside conditional
blocks.** These functions must execute on every bar to maintain their internal
history; called conditionally they return unreliable values. This corrupts
`s5pStop`, hence the risk floor, hence whether CONFIRMED fires at all. Pine
flags this as a warning rather than an error, so it will not stop compilation.

**P-9 — DOWNGRADED to a note. (L119-126, L25) CONFIRMED gated on a ≥5-point
risk floor.** The floor is empirically inert: their confirmation flags match
`re12` on all 2,705 sessions, so it never suppressed a confirmation in the
reference window. It remains an extra condition the frozen spec does not state,
and could bite on future data.
*Original finding:*
The floor and the `ta.lowest(low,7) - 2` stop come from `standalone()` — the
S5b *trade* function — not from the classifier. Frozen spec C.8 says the state
is CONFIRMED when pullback + failure + reassertion complete, with no risk
condition. As written, a completed ten-trader sequence can fail to register as
CONFIRMED because a derived stop happened to sit under 5 points away.

### Lesser

- **P-10 (L90)** No opening-range completeness guard; the reference requires ≥10
  one-minute bars in the OR window.
- **P-11** No entry-bar gap guard; the reference skips the trade when the bar
  exactly five minutes after the signal is missing.
- **P-12 (L264-266)** The entry alert payload carries the **signal bar's close**
  as `entry`, while the fill is the next bar's open. A paper executor consuming
  this books a price that never traded.
- **P-13 (L103)** `[float(na), n]` — a `float(na)` cast expression; verify it
  compiles in v6, the idiomatic form is a declared `float x = na`.
- **P-14 (L94-97)** `obCnt < 6` counts the first six RTH bars regardless of
  clock time, so a gapped feed builds the opening balance from the wrong window.
- **P-15 (L205-210)** `orbDir` is not cleared after a VOID, so Module C can
  report an alignment against a position that does not exist.

### What the script gets right

Worth stating, because it is most of the file: the NY-clock gating, the
15-minute OR from the three 5m bars, the 09:45-10:30 inclusive window, the
≥50% body filter with the zero-range guard, the next-bar entry via an order
placed on the signal bar, one-trade-per-day, the leg-extreme tracking at latch
(session extremes, correctly), the >100% invalidation, the two-bar 1-point
failure test, the within-day rolling volume array (correctly session-scoped,
min 6 observations, current bar included — better than an `ta.sma` would be),
and the structural guarantee that Module A never reads a Module B variable.

**One idea from this file was adopted into the project implementation:**
`strategy.close(..., immediately = true)` for the session-close exit. It books
the exit at the final bar's *close* while leaving `process_orders_on_close`
false for correct next-bar entries — which resolves seam S-9, previously an
accepted reporting artifact in `pine/nq_orb_s5b_v1.pine`.

---

## 4. DOCUMENTATION ERRORS

**D-1** §1: *"Stop … submitted as the ONLY intrabar order. Because it is the only
intrabar order, TradingView's intrabar path assumptions cannot differ from the
research engine's 1m touch logic."* The reasoning is sound but the premise is
not met on the entry bar, where no stop order exists at all (P-1).

**D-2** §1: *"the CONFIRMED timestamp in Pine equals the research engine's
confirmation timestamp (which is also next-bar)."* The research engine's
`s5b_day` returns `event_ts = ts`, **the reassertion bar**. Next-bar is where
`standalone()` *enters*. Their reference CSV consistently uses the next-bar
convention, so the package is internally consistent — but it does not match the
frozen spec's C.8 wording, and a reader comparing against `s5b_day` output will
see a one-bar offset.

**D-3** §7 item 5: *"the research excluded sessions with <200 1m bars."* The
shipped ORB engine excludes sessions with fewer than **10** one-minute bars in
the opening range; there is no 200-bar rule in any supplied engine. The
canonical list contains 88 shortened sessions that were *not* excluded.

**D-4** §5c: *"2,295 trades / 216-227 per full year"* — correct; the canonical
list agrees.

---

## 5. DISPOSITION

- Reference CSVs: **accepted** as an additional cross-check source.
- Spot-check list: **accepted**, used as the S5b validation set.
- `parity_check.py`: **superseded** by `tests/parity_tv_list_of_trades.py`.
  The original is retained here unmodified as the audited artifact.
- `ORB_S5b_v1.pine`: **not adopted as the implementation**, because P-1, P-2,
  P-3 and P-8 are real and P-2 is a live risk-control failure. But its S5b
  reading was more faithful than mine, and `pine/nq_orb_s5b_v1.pine` has been
  corrected to match it on all four clauses. Two mechanics were ported across:
  `strategy.close(..., immediately = true)` for the session-close exit, and the
  S5b clause readings themselves. Every ORB and S5b behaviour is pinned by a
  named test in `tests/test_reference_engine.py` (58 checks), and both engines
  now reproduce the references exactly on 764 sessions of real data.
