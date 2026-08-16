# NQ ORB + S5b — HANDOFF PACKAGE

Prepared by Claude Code. Read this file first, then `STATUS` below tells you
exactly what is proven, what is not, and the one thing that is being asked for.

**Nothing in this package changes a frozen strategy rule.** No constant was
tuned, no indicator added, no dead or parked rule resurrected. The only exits in
the implementation are the ORB hard stop and the RTH close.

---

## STATUS IN ONE TABLE

| leg of the milestone | state | evidence |
|---|---|---|
| PYTHON REFERENCE = PINE LOGIC | **PROVEN** | 664/664 ORB trades and 764/764 S5b sessions, exact, on real 1-minute data |
| PINE LOGIC = TRADINGVIEW OUTPUT | **NOT MEASURED** | no TradingView access in the environment this was built in |

The Pine source has **never been compiled by TradingView**. That is the gap.

---

## WHAT IS BEING ASKED FOR

One artifact, from anyone with a TradingView account:

1. Open a **5-minute, regular-trading-hours** chart of `CME_MINI:NQ1!`.
2. Paste in `pine/nq_orb_s5b_v1.pine` and add it to the chart.
3. Chart menu → **Export chart data** → CSV.
4. Send that CSV back.

The script publishes its entire deterministic state as data-window series named
`px_*` (opening range, direction, entry, stop, exit, exit reason, S5b state,
alignment). The export therefore contains the complete trade and state record,
independent of TradingView's order-fill model.

Then run:

```bash
python3 tests/parity_orb.py --tv-export <that_file>.csv
python3 tests/parity_s5b.py --tv-export <that_file>.csv
```

Both classify every mismatch into the ten categories fixed by
`spec/PARITY_ACCEPTANCE_GATES.md` and exit non-zero on anything unclassified.
Full runbook: `docs/PARITY_PROCEDURE.md`.

A Strategy Tester "List of Trades" export is also useful as a secondary check —
`tests/parity_tv_list_of_trades.py` grades that — but it is **not** the primary
input, because the tester applies its own fill model (`spec/SPEC_SEAMS.md` S-10).

---

## WHAT HAS BEEN PROVEN

### The canonical reference is verified three ways

`reference/canonical_orb_trades.csv` — 4,022 trades, 2008-01-02 to 2026-06-30.

- **0 frozen-spec invariant violations** across all 4,022 trades.
- 2016–2026 reproduces the figure the frozen spec states: **2,295 trades,
  +122.8167R, PF 1.1035**, maxDD −25.65R (spec B says "~2,295 / +122.8R / PF
  ~1.11").
- An independent 1-minute research engine agrees on **every entry-side field
  across all 4,019 shared sessions**, and every 1-minute exit timestamp falls
  inside the corresponding 5-minute exit bar (3,297/3,297). The frozen ORB is
  therefore **timeframe-invariant between 1m and 5m**, which is what makes exact
  parity on a 5-minute chart achievable rather than approximate.

Rebuild and re-verify: `python3 reference/build_canonical.py`.

### Clean-room parity on real data — PASS

The 2023–2026 back-adjusted FirstRate archive, 764 sessions, 2023-07-13 to
2026-06-30, 293,199 RTH minutes, 0 duplicate timestamps:

| gate | result |
|---|---|
| ORB trade list vs canonical | **664/664 exact, 0 mismatches, 0 unknowns** |
| S5b state vs research flags | **764/764 sessions, 0 mismatches** |

Exact on direction, signal bar, entry bar, entry price, stop, exit bar, exit
price and exit reason, at price tolerance **0**. Console output is in
`tests/parity_report_clean_room_console.txt`; the engine's own output is in
`tests/_parity_run/`.

Reproduce it by putting the archives in `data/raw/` and running
`python3 tests/run_full_parity.py`. **The raw market data is not in this zip** —
it is licensed vendor data and was deliberately excluded.

### The S5b reference file was validated before being trusted

`tests/verify_s5b_reference.py` reproduces **all twelve published profit factors**
of the four-state day taxonomy from `round3_and_part1_report.txt`, with matching
session counts (413/269/79). Output: `tests/parity_report_s5b_reference_validation.txt`.

### The implementation is pinned by tests

`python3 tests/test_reference_engine.py` — **58 checks, all passing**, one per
frozen clause. `tests/reference_engine.py` is the Pine's clause-for-clause twin;
both carry the same spec tags on the same branches, so a failure there means the
Pine is wrong too.

---

## TWO THINGS A REVIEWER SHOULD KNOW BEFORE FORMING AN OPINION

### 1. `tmp_s5b_round3c.py` is NOT authoritative for spec section C

Spec section C leaves four details unstated. That script appears to settle them,
but it is an exploratory variant (`tmp_` prefix; its `__main__` sweeps a volume
switch over 2024–2026) and **its readings do not reproduce the reference flag
file** behind the published results.

An earlier revision of this work took it as authoritative and was wrong on all
four clauses. The readings were then resolved empirically:
`tests/resolve_s5b_clauses.py` scores all sixteen combinations against
`s5b_day_flags_allmult.csv` over 764 real sessions. Exactly one scores 764/764 on
latch, band, confirmation and invalidation; it also reproduces 695/695 latch
timestamps and 103/103 confirmation directions and timestamps. The winner:

- the classifier runs 10:00 through the **11:30 bar and stops**, latch included;
- the 25–75% band is a **live condition** on the failure and reassertion bars,
  not a latch;
- there is **no delay** between the pullback opening and the sequence completing;
- there is **no "no new extreme"** precondition on the pullback;
- the rolling 12-bar volume mean **includes** the current bar.

That reading is also closer to the literal text of spec C.4 and C.6 than the
script is. Evidence and counts: `spec/SPEC_SEAMS.md` S-1..S-5.

### 2. The earlier TradingView package was audited, and the audit was itself corrected

`vendor/claude_chat_v1/` holds a previously supplied TradingView v1 package,
unmodified, with `AUDIT.md` alongside it. Summary:

- its **reference CSVs are trustworthy** — they match the verified canonical on
  2,295/2,295 sessions and the research flags on 2,705/2,705, and its 12
  preregistered spot-check dates are 12/12 consistent;
- its **`parity_check.py` cannot be used as a gate** — a perfect 5-minute
  implementation scores 234/2295 "INCOMPLETE" under it, and an export with
  randomised entry prices and every exit price shifted +999 points scores
  "PARITY PASS". Both demonstrated by running it. Replacement:
  `tests/parity_tv_list_of_trades.py`;
- its **Pine has 4 parity-breaking defects** (stop not live on the entry bar;
  shortened sessions never flatten, leaving an unprotected overnight position;
  gap-beyond-stop enters-then-voids instead of skipping; `ta.lowest`/`ta.highest`
  called inside conditional blocks);
- but its **S5b clause readings were right and mine were wrong** — findings P-4
  through P-7 in that audit are formally withdrawn, and one of its mechanics
  (`strategy.close(..., immediately = true)`) was adopted here.

---

## WHAT IS NOT VERIFIED

1. **TradingView, entirely.** The Pine has not been compiled or run there. Static
   checks were done (no `ta.*` in conditional blocks, no `float(na)` casts, no
   line continuations at a four-space multiple, which Pine parses as a new
   block), but static checks are not a compiler.
2. **2008 – mid-2023.** Only the 2023–2026 archive was available, so clean-room
   parity covers 764 of the 4,022 canonical sessions. The pre-2023 archive would
   close the rest; `tools/make_parity_upload.py` shrinks it to a shareable size
   losslessly (RTH-only, ~27% of rows).
3. **Anything downstream of parity.** No webhook receiver, no paper executor, no
   ledger. Deliberately not started: the executor consumes TradingView alerts, so
   building it before the TradingView leg is verified means building on the one
   untested link.

---

## OPEN QUESTIONS FOR A HUMAN

- **`spec/SPEC_SEAMS.md` S-11, entry-alert timing.** The `ORB_*_ENTRY` alert
  fires at the **close** of the entry bar and carries that bar's **open** as the
  entry price, because that is what keeps historical and realtime state
  identical. A live paper executor acting on it fills up to five minutes after
  the canonical price. Phase 2 must choose: accept and measure the slippage, or
  emit the actionable alert on the signal bar as "market on next bar open".
  Both are execution-layer choices; neither changes a strategy rule.
- **`spec/SPEC_SEAMS.md` S-8, three holiday sessions.** 2008-05-26, 2011-05-30
  and 2011-07-04 are US market holidays that the 5-minute research engine traded
  and the 1-minute engine did not. Retained and flagged in the canonical list; a
  TradingView feed will show them as missing trades. Calendar difference, not
  logic. Whether to drop them is the user's call.

---

## PACKAGE MAP

```
HANDOFF_README.md          this file
CLAUDE.md                  standing project rules
START_HERE.md              original engineering handoff

pine/nq_orb_s5b_v1.pine    THE CANDIDATE — needs a TradingView run
spec/                      frozen spec, acceptance gates, state machine,
                           alert schema, and SPEC_SEAMS.md (12 seams, resolved
                           or explicitly open)
reference/                 canonical trade list + provenance + cross-engine audit
tests/                     frozen-spec engine, 58 clause tests, comparators,
                           clean-room runner, S5b clause resolver, reports
tools/                     RTH-only extractor for the raw archives
docs/                      TradingView setup and the parity runbook
vendor/claude_chat_v1/     prior TradingView package, unmodified, + AUDIT.md
research/                  the original research corpus (source of truth)
```

Everything is Python standard library — no pip installs, no pandas.

## REBUILDING THIS PACKAGE

This zip is reproducible from a checkout of the repo
(`SilentSniperX/viviana-voice`, branch `claude/phase-1-audit-trade-parity-lvwv28`):

```bash
python3 tools/make_handoff_zip.py
```

It runs the three self-checks below first and refuses to package if any fails,
then excludes `data/raw/` (licensed market data), `.git/` and caches.

## QUICK VERIFICATION (about 20 seconds, no market data needed)

```bash
python3 reference/build_canonical.py      # rebuilds and audits the canonical list
python3 tests/test_reference_engine.py    # 58 clause checks
python3 tests/verify_s5b_reference.py     # reproduces the 12 published PFs
```

All three must print a pass. With the raw archives in `data/raw/`, add:

```bash
python3 tests/run_full_parity.py          # the clean-room parity run
```
