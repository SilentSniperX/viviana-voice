# PARITY PROCEDURE

How to run ORB trade-list parity between `pine/nq_orb_s5b_v1.pine` and
`reference/canonical_orb_trades.csv`. Phase 1 only — no alerts, no webhook, no
execution.

## 0. Rebuild and self-check the canonical table

```bash
python3 reference/build_canonical.py        # -> reference/canonical_orb_trades.csv
python3 tests/test_reference_engine.py      # 55 frozen-clause checks across 27 tests
```

Both must pass before any parity run. `build_canonical.py` exits non-zero if any
frozen-spec invariant fails.

## 1. Chart setup (must match exactly)

| setting | value | why |
|---|---|---|
| symbol | `CME_MINI:NQ1!` (or MNQ1!) | the research used continuous adjusted NQ |
| timeframe | **5 minutes** | the reference engine is 5-minute; see `reference/CANONICAL_PROVENANCE.md` §4 |
| session | Regular trading hours | the frozen spec is RTH; ETH bars change the opening range |
| timezone | America/New_York | the script converts internally, but keep the chart in NY so exports are readable |
| bar replay | off | |

Add the script, then **Settings -> Properties**: recalculate on every tick OFF,
on order fills OFF. The script already sets these; do not override them.

## 2. Export the deterministic record

The Pine script publishes its whole state as data-window-only series prefixed
`px_`. These carry the deterministic record, not TradingView's fill engine, and
are the authoritative parity input.

Chart menu -> **Export chart data** -> CSV. The file has one row per bar with
columns `time, px_or_high, px_or_low, px_orb_side, px_entry, px_stop, px_exit,
px_exit_code, px_s5b_state, px_s5b_side, px_ob_high, px_ob_low, px_alignment`.

`px_exit_code`: 0 none, 1 ORB stop, 2 session close.

## 3. Compare

```bash
python3 tests/parity_orb.py \
    --tv-export /path/to/chart_data.csv \
    --canonical reference/canonical_orb_trades.csv \
    --report tests/parity_report_tradingview.md
```

The comparator classifies every mismatch into the ten categories fixed by
`spec/PARITY_ACCEPTANCE_GATES.md` and **exits non-zero if any mismatch is
`unknown`** — unknown mismatches are blockers.

Expected, acceptable classes when comparing against a TradingView feed:
- **1 data-feed OHLC difference** — TradingView's continuous contract is not
  FirstRate's adjusted series.
- **2 continuous-contract/roll difference** — uniform price offsets around roll
  dates.
- **3 timezone/session boundary** — only on shortened sessions.

Not acceptable, must be fixed in the Pine source: **5 body-filter
implementation**, **6 next-bar execution semantics**, **7 stop sequencing**,
**9 implementation bug**, **10 unknown**.

Do **not** widen `--price-tolerance` to make class 1 disappear. If a feed
difference needs quantifying, run once at `0` and once at a stated tolerance and
report both.

## 4. Optional — clean-room rerun on raw data

If the FirstRate archives are on the machine, put them under `data/raw/`
(git-ignored) and unpack them to a 1-minute CSV, then:

```bash
python3 tests/reference_engine.py --minute data/raw/NQ_1m.csv \
        --out /tmp/engine_trades.csv --s5b /tmp/engine_s5b.csv
python3 tests/parity_orb.py --candidate /tmp/engine_trades.csv \
        --report tests/parity_report_python.md
```

This validates the frozen-spec reading itself: `tests/reference_engine.py` is a
clean re-implementation, so agreement with the canonical list is an independent
confirmation of both.

## 5. What "parity achieved" means

`spec/PARITY_ACCEPTANCE_GATES.md`: exact trade-list parity wherever the
TradingView feed matches the FirstRate reference sufficiently, with **every
residual mismatch classified** and **zero unknowns**. Report the class histogram,
not a pass/fail slogan. Phase 2 does not start until that report exists.

## 6. Cross-check (secondary)

The Strategy Tester's *List of Trades* is a useful sanity check but is **not** the
parity input: TradingView's fill model books the session-close exit at the next
session's open, and shows a flat round trip on skipped days. Both seams are
documented in `spec/SPEC_SEAMS.md` (S-9). Compare the tester only for entry
prices and stop fills.
