# CANONICAL REFERENCE — PROVENANCE

`reference/canonical_orb_trades.csv` is the reference trade table for all ORB
parity work. It is produced by `reference/build_canonical.py`, which selects and
cross-verifies the shipped research logs. **No strategy is re-run and no signal
is recomputed** — the canonical list is the research engines' own output,
normalised and audited.

Regenerate with:

    python3 reference/build_canonical.py

## Why this source

| role | file | rows | verdict |
|---|---|---|---|
| PRIMARY | `research/master_handoff/02_CHATGPT_DUAL_PROJECT_HANDOFF/PART_2_ORB_S5B_INTEGRATION/attachments/Samir_ORB_benchmark_retest_2008_2026.csv` | 4,022 | canonical |
| CONFIRM-A | `research/reference/Samir_ORB_Benchmark_2016_2026.csv` | 2,295 | identical to PRIMARY's 2016+ slice |
| CONFIRM-B | `research/latest_round4/round4_trades.csv` | 4,019 | independent 1-minute engine, agrees |
| LEGACY | `research/master_handoff/06_REFERENCE_SUMMARIES/S4_Samir_Full_2016_2026.csv` | 2,226 | earlier engine, excluded (seam S-6) |
| NOT ORB | `ORB_Collective_Management_2016_2026.csv` | 2,295 | contains DEAD management clauses (spec G) |

PRIMARY is the only shipped log that (a) covers the full 2008-2026 span, (b)
carries every field the acceptance gates ask for, and (c) implements exactly the
frozen rule set — hard stop plus hold-to-RTH-close, with no early exit.

`ORB_Collective_Management_2016_2026.csv` is deliberately excluded: it was
produced by `backtest_orb_collective_management.py`, whose stop-migration and
`counterattack_succeeds` clauses are listed as DEAD in
`spec/STRATEGY_SPEC_FROZEN.md` section G. Its entries match the benchmark; its
exits do not, and must not be used as a parity target.

## What was verified

Run `reference/build_canonical.py` to reproduce all of it; the results land in
`reference/CROSS_ENGINE_AUDIT.md` and `reference/canonical_manifest.json`.

1. **Frozen-spec invariants, 0 violations across 4,022 trades** — entry is
   exactly the signal bar + 5 minutes; every signal bar sits in 09:45-10:30; all
   timestamps are 5-minute aligned; one trade per session; longs sit above their
   stop and shorts below; `risk = |entry - stop|`; stop exits fill exactly at the
   stop; `net_points`, `netR` and `dollars` satisfy the engine identities with
   cost 0.75 points and $20/point; every price is on the 0.25 tick grid.

2. **CONFIRM-A is identical** to PRIMARY's 2016+ slice on all twelve compared
   fields across all 2,295 sessions (547 last-digit float serialisation
   differences, 0 value differences).

3. **CONFIRM-B, an independent 1-minute engine, agrees on every entry-side
   field** — side, entry timestamp, entry price, stop, risk and benchmark R —
   across all 4,019 shared sessions, with zero differences.

4. **The frozen rules are timeframe-invariant between 1m and 5m.** For all 3,297
   sessions where the dead Round-4 rule did not fire, the 1-minute engine
   produced the same exit reason as the 5-minute engine, and every 1-minute exit
   timestamp falls inside the corresponding 5-minute exit bar (1,413 stops,
   1,884 closes, 0 exceptions). This is the finding that makes exact parity on a
   5-minute TradingView chart achievable rather than approximate.

5. **The 2016-2026 aggregate reproduces the number the frozen spec states.**
   Spec B says "roughly 2,295 trades / +122.8R / PF ~1.11". Canonical:
   **2,295 trades / +122.8167R / PF 1.1035 / maxDD -25.65R / 44.14% wins.**

## Column notes

- `or_high` / `or_low` — only the stop side of the opening range exists in the
  shipped logs (`or_known_side` records which). See seam S-8.
- `session_close_bar` / `early_close` — the reference engine exits at the final
  bar of the session, which is 15:55 normally and 11:25 / 12:55 / 13:10 on
  shortened sessions (88 trades).
- `cross_engine` — `VERIFIED_5M_AND_1M` where the independent 1-minute engine
  confirms the entry side, `VERIFIED_5M_ONLY` for the three holiday sessions of
  seam S-7.
