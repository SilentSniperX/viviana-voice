# PARITY REPORT — ORB, Phase 1

Status: Phase 1 steps 1.1-1.6 complete plus the step-1.8 reference validation
and tooling. The two runs that need bars (1.7, 1.8) are blocked on inputs.
Regenerate the machine-checked parts with:

```bash
python3 reference/build_canonical.py
python3 tests/test_reference_engine.py
python3 tests/verify_s5b_reference.py
python3 tests/parity_orb.py --candidate reference/canonical_orb_trades.csv \
        --report tests/parity_report_selfcheck.md
```

---

## 1. What has been verified

### 1.1 The canonical table is internally consistent with the frozen spec

**0 violations across 4,022 trades** on every invariant spec section B fixes —
next-bar entry, the 09:45-10:30 qualification window, 5-minute alignment,
one trade per session, stop side and stop fill price, `risk = |entry - stop|`,
the R and dollar identities, and the tick grid. Details in
`reference/CROSS_ENGINE_AUDIT.md`.

### 1.2 Three independent research engines agree

| check | result |
|---|---|
| `Samir_ORB_Benchmark_2016_2026.csv` vs canonical 2016+ | **identical**, 2,295/2,295 sessions, 0 value differences |
| `round4_trades.csv` (1-minute engine) entry-side fields | **0 differences** across 4,019 shared sessions |
| exit-reason agreement, 1m vs 5m, excluding the dead Round-4 rule | **3,297/3,297** |
| 1-minute exit timestamps inside the 5-minute exit bar | **3,297/3,297**, 0 exceptions |

### 1.3 The frozen rules reproduce the published reference numbers

Spec B states "roughly 2,295 trades / +122.8R / PF ~1.11" for 2016-Jun 2026.
Canonical: **2,295 / +122.8167R / PF 1.1035 / maxDD -25.65R / 44.14% wins.**

### 1.4 The frozen ORB is timeframe-invariant between 1m and 5m

This is the finding that makes 5-minute TradingView parity achievable rather
than approximate. Because the 1-minute engine never resolved a stop or a close
outside the 5-minute bar the 5-minute engine used, a correct 5-minute
implementation cannot differ from the canonical list for reasons of bar
granularity. Any granularity mismatch that shows up in a future run is therefore
a real signal, not expected noise.

### 1.5 The implementation is pinned by tests, not by assertion

`tests/reference_engine.py` re-implements spec sections B and C from scratch with
no third-party dependencies; `pine/nq_orb_s5b_v1.pine` carries the same clause
tags on the same branches. `tests/test_reference_engine.py` runs **55 checks across 27 tests and
all pass**, covering: body filter accept/reject, zero-range bars, first-direction
wins, the inclusive 10:30 boundary, the skip rule (entry beyond the stop and
entry exactly at the stop), the missing-entry-bar gap rule, stop-on-the-entry-bar,
stop priority over close, shortened sessions, one-trade-per-day, the accounting
identities, the 1-minute opening-range guard, 5-minute aggregation, and the full
S5b state machine including invalidation, the volume requirement, the short
mirror, the 11:30 entry deadline, and S5b's independence from the ORB trade.

### 1.6 The S5b reference file is validated before being trusted

`tests/verify_s5b_reference.py` joins the canonical ORB list to
`s5b_day_flags_allmult.csv` and recomputes the four-state day taxonomy that
`round3_and_part1_report.txt` published.

| state | 2008-15 | 2016-23 | 2024-26 |
|---|---|---|---|
| COMPLETED | 1.92 (n=413) | 2.54 (n=269) | 2.61 (n=79) |
| LATCH-ONLY | 1.70 (n=141) | 2.76 (n=137) | 1.97 (n=34) |
| NEAR-MISS | 0.70 (n=1047) | 0.90 (n=1167) | 1.04 (n=412) |
| NO LATCH | 0.26 (n=123) | 0.29 (n=156) | 0.22 (n=41) |

**All twelve published profit factors reproduce exactly, and the COMPLETED
session counts match the published 413 / 269 / 79.** The join is 1:1 across all
4,022 canonical sessions.

Two things this pins for step 1.8. First, the flag columns mean what they appear
to mean, so the file is a sound S5b state reference. Second, reproducing the
figures required computing the profit factors on **R, not dollars**, and folding
invalidated sessions into the band / no-band buckets rather than giving them
their own state — both recorded in the script so the reading is not re-derived
by hand later.

### 1.7 The comparators are themselves tested

`tests/parity_orb.py` was run against the canonical list (4,022/4,022 exact) and
against a deliberately perturbed copy carrying one instance of each mismatch
archetype. It classified each into the correct category — roll offset (2), feed
OHLC (1), one-bar-late execution (6), body filter (5), stop sequencing (7),
session close (8), direction bug (9) — and correctly blocked on the injected
missing trade as unknown (10).

`tests/parity_s5b.py` was run against a candidate timeline reconstructed from
the reference flags: **4,768 sessions, 0 mismatches**, including all 763
published confirmation timestamps from `claude_s5b_hist_2008_2023.csv`. Injected
faults (a flipped latch direction and a corrupted confirmation timestamp) were
each detected.

---

## 2. What has NOT been verified

**Pine-vs-canonical trade-list parity has not been run, and no parity claim is
made for it.** Two inputs are missing from this environment and both are stated
in `tests/PHASE1_REPO_AUDIT.md` §C:

1. the raw FirstRate 1-minute archives (excluded from the handoff by design), so
   no bars exist here to drive either engine;
2. any TradingView connection, so `pine/nq_orb_s5b_v1.pine` has not been
   compiled or executed by TradingView — its semantics are pinned by the Python
   twin and the test suite, nothing more.

`docs/PARITY_PROCEDURE.md` is the runbook for closing this the moment either
input is available. Nothing else in Phase 1 is outstanding.

**S5b state parity is likewise pending on the same two inputs.** Its reference
data and its comparator are both ready and validated (§1.6, §1.7); what is
missing is bars to run the classifier on. Coverage limit to expect: the day
flags carry no pullback-activation or failure timestamps, so those two
transitions can only be checked for ordering, not against a published value.
The `Failed_Counterattack_*` logs must **not** be used here — they implement a
different construction (seam S-5).

---

## 3. Mismatch classes to expect when 1.7 runs

Per `spec/PARITY_ACCEPTANCE_GATES.md`. Acceptable against a TradingView feed:
class 1 (feed OHLC), class 2 (continuous-contract roll), class 3 (session
boundary on shortened days). Plus three known non-logic sources already
identified:

- the three holiday sessions of seam S-7 will appear as missing trades;
- the opening range is only half-recoverable from the shipped logs (seam S-8),
  so ORH/ORL comparison starts at 1.7 with the chart export supplying both sides;
- the TradingView strategy tester — not the deterministic export — will disagree
  on session-close fills and on skipped days (seam S-9).

Classes 5, 6, 7, 9 and 10 are implementation faults and must be fixed in the Pine
source, never absorbed by a tolerance.

---

## 4. Gate status

| gate | status |
|---|---|
| canonical reference table exists and is audited | **PASS** |
| cross-engine agreement established | **PASS** |
| frozen-spec implementation exists with clause traceability | **PASS** |
| mismatch taxonomy tooling exists and is tested | **PASS** |
| S5b reference file validated against published results | **PASS** — 12/12 published PFs reproduced |
| S5b state comparator exists and is tested | **PASS** — 4,768 sessions, 0 mismatches on the control |
| no dead or parked rule implemented (spec G) | **PASS** — only exits are the ORB stop and the RTH close |
| no-lookahead / no repainting | **PASS by construction** — completed-bar state only, `calc_on_every_tick=false`, S5b never read by the ORB path; to be re-verified on a live chart at 1.7 |
| Pine-vs-canonical trade-list parity | **NOT RUN** — blocked, see §2 |
| S5b state-timestamp spot check | **NOT RUN** — blocked, see §2 |

Phase 2 (alerts, paper receiver) does not begin until the last two rows are
green.
