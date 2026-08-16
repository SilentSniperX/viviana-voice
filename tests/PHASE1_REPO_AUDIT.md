# PHASE 1 — REPO AUDIT, PLAN, AND DEPENDENCIES

Answers the three items `PROMPT_PASTE_INTO_CLAUDE_CODE.txt` asks for before
Phase 1 work: (A) repo audit, (B) implementation plan, (C) anything genuinely
blocking.

---

## A. REPO AUDIT

### A.1 Starting state

The git repository `SilentSniperX/viviana-voice` contained a single empty file
(`viviana-voice.html`, 0 bytes) and no history relevant to this project. The
handoff package was imported wholesale onto branch
`claude/phase-1-audit-trade-parity-lvwv28`; the empty placeholder was removed.
All 84 files listed in `MANIFEST.json` are present and their sha256 values match
the manifest.

### A.2 What the research artifacts actually contain

| area | artifact | status |
|---|---|---|
| ORB reference trades | `Samir_ORB_benchmark_retest_2008_2026.csv` (4,022) | **canonical**, fully audited |
| ORB reference trades | `Samir_ORB_Benchmark_2016_2026.csv` (2,295) | identical to the 2016+ slice |
| ORB, independent engine | `round4_trades.csv` (4,019, 1-minute) | confirms every entry-side field |
| ORB, legacy | `S4_Samir_Full_2016_2026.csv` (2,226) | earlier engine, excluded (seam S-6) |
| ORB, dead management | `ORB_Collective_Management_2016_2026.csv` | contains spec-G DEAD clauses, excluded |
| S5b engine source | `tmp_s5b_round3c.py` | **authoritative** for spec section C |
| S5b state reference | `s5b_day_flags_allmult.csv` (4,768 sessions) | day-level flags, no timestamps |
| S5b timestamps | `claude_s5b_hist_2008_2023.csv` (763 trades) | `entry_ts` pins confirmation + 5m |
| "Failed Counterattack" logs | `Failed_Counterattack_*.csv` | **different definition**, not S5b parity (seam S-5) |
| raw 1-minute NQ history | not shipped | see C.1 |

### A.3 Audit findings on the canonical ORB list

Reproduce with `python3 reference/build_canonical.py`.

1. **Zero frozen-spec violations across 4,022 trades.** Entry is exactly signal +
   5 minutes; every signal bar is inside 09:45-10:30; all timestamps are
   5-minute aligned; one trade per session; entry always on the correct side of
   its stop; `risk = |entry-stop|`; stop exits fill exactly at the stop; the
   R and dollar identities hold at 0.75 points cost and $20/point; every price
   is on the 0.25 tick grid.

2. **The 2016-2026 aggregate reproduces the number the frozen spec states.**
   Spec B: "roughly 2,295 trades / +122.8R / PF ~1.11".
   Canonical: **2,295 trades / +122.8167R / PF 1.1035 / maxDD -25.65R**.

3. **The frozen ORB rules are timeframe-invariant between 1 and 5 minutes.**
   Across the 3,297 sessions where the dead Round-4 rule did not fire, the
   independent 1-minute engine returned the same exit reason as the 5-minute
   engine on every one, and every 1-minute exit timestamp falls inside the
   corresponding 5-minute bar (1,413 stops, 1,884 closes, 0 exceptions). This is
   what makes exact 5-minute TradingView parity attainable rather than
   approximate, and it is the single most useful audit result for Phase 1.

4. **Signal-bar distribution** is concentrated early — 09:45: 1,067, 09:50: 768,
   09:55: 479, 10:00: 492, then decaying to 118 at the 10:30 bar. Nothing sits
   outside the window.

5. **88 trades exit on a shortened session** (11:25 x28, 12:55 x40, 13:10 x20);
   2,142 exit on the normal 15:55 bar; 1,792 stop out. Eight stops resolve on the
   entry bar itself — the Pine stop must therefore be live on the entry bar, and
   it is.

6. **Three sessions are US market holidays** (2008-05-26, 2011-05-30,
   2011-07-04) present in the 5-minute engine and absent from the 1-minute
   engine. Flagged, retained, documented as seam S-7.

### A.4 Conflicts found and where they are recorded

Eleven seams are catalogued in **`spec/SPEC_SEAMS.md`** with status and
resolution. The four that mattered for implementation:

- **S-5** the `Failed_Counterattack_*` logs implement an older, opening-range
  and signed-volume construction, **not** frozen spec C. They cannot be used as
  S5b state parity references. Use `s5b_day_flags_allmult.csv` and
  `claude_s5b_hist_2008_2023.csv`.
- **S-1..S-4** spec section C is silent on four details that the Round-3c engine
  fixes (leg_high initialisation, the "no new extreme" condition on pullback
  activation, the two-bar delay before failure evaluation, and whether the
  rolling volume window includes the current bar). Implemented per the reference
  engine, each tagged in the code.
- **S-9** TradingView's fill model cannot honour both next-bar-open entries and
  a session-close exit at the close. The frozen entry semantic wins; the
  deterministic record, not the strategy tester, is the parity input.
- **S-6** the legacy S4 file disagrees on risk and R; excluded.

None of these are blockers. No unresolved contradiction between two
higher-ranked sources was found.

---

## B. IMPLEMENTATION PLAN

Phase 1 is split so that each step is independently testable, per `CLAUDE.md`
("keep changes small and testable").

| step | deliverable | state |
|---|---|---|
| 1.1 | repo import + full research audit | **done** |
| 1.2 | canonical reference trade table + cross-engine audit | **done** — `reference/` |
| 1.3 | frozen-spec engine in dependency-free Python (ORB + S5b), the testable twin of the Pine | **done** — `tests/reference_engine.py` |
| 1.4 | clause-by-clause test suite | **done** — 55 checks across 27 tests pass |
| 1.5 | Pine v6 ORB + S5b classifier + state machine + alert payloads | **done** — `pine/nq_orb_s5b_v1.pine` |
| 1.6 | parity comparator with the ten-class taxonomy | **done** — `tests/parity_orb.py` |
| 1.7 | run TradingView parity and classify every mismatch | **blocked on C.1/C.2** |
| 1.8 | S5b state spot-check against `s5b_day_flags_allmult.csv` | **blocked on C.1** |

Phases 2-4 (alerts, paper webhook receiver, idempotency, reconciliation) do not
start until 1.7 and 1.8 produce a report with zero unknown mismatches.

---

## C. DEPENDENCIES THAT ACTUALLY BLOCK PHASE 1 COMPLETION

### C.1 The raw FirstRate archives are not in this environment — blocks 1.7/1.8 offline

`NQ_1m_by_year_2023-2026(5).zip` and `NQ_1m_history_pre2023(1).zip` were
deliberately excluded from the handoff (`research/RAW_DATA_NOTE.md`). Without
them no bar data exists here, so the Pine implementation cannot be executed
against the same bars the canonical list came from *inside this session*.

Everything that does not need bars has been completed: the canonical table, the
cross-engine verification, the frozen-spec engine, the test suite, the Pine
source and the comparator. What remains is running them.

Unblock by either:
- placing the two archives under `data/raw/` (git-ignored) and running the
  clean-room path in `docs/PARITY_PROCEDURE.md` §4; or
- exporting chart data from TradingView (§2), which needs no local raw data.

### C.2 No TradingView connection in this session — blocks 1.7

There is no TradingView MCP connector or browser session available here, so the
Pine source cannot be compiled, loaded onto a chart, or exported. The Pine is
committed and its semantics are pinned by the Python twin and the test suite, but
**it has not been run by the TradingView compiler**, and that is stated plainly
rather than implied.

Unblock by pasting `pine/nq_orb_s5b_v1.pine` into the Pine editor on a 5-minute
RTH NQ chart and following `docs/PARITY_PROCEDURE.md` §1-§3, then handing back
the exported CSV.

### C.3 Not blocking

- ORH/ORL are only half-recoverable from the shipped logs (seam S-8) — the
  TradingView export supplies both sides, so this resolves itself at 1.7.
- The entry-alert timing question (seam S-10) is a Phase 2 execution decision and
  is deliberately left for the user.
