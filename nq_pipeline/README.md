# NQ 1-minute historical dataset pipeline (2016–2020)

Builds the deliverable `NQ_1m_by_year_2016-2020.zip` containing ten CSVs:

```
NQ_adj_1m_2016.csv ... NQ_adj_1m_2020.csv      (continuous, back-adjusted)
NQ_unadj_1m_2016.csv ... NQ_unadj_1m_2020.csv  (continuous, unadjusted)
```

- **Schema (all files):** `timestamp,open,high,low,close,volume`
- **Timestamps:** bar **OPEN** times, `YYYY-MM-DD HH:MM:SS`, **America/New_York**
  wall time (DST-aware conversion from the source timezone)
- **Instrument:** CME E-mini Nasdaq-100 futures (NQ), Globex session bars
- Files are written and zipped programmatically — **Excel is never involved**,
  and the audit explicitly flags any file whose row count equals the Excel
  limit of 1,048,576

## Status: pipeline complete, real data source still required

This repository contains **no market data**. CME NQ 1-minute history is
licensed data and this build environment has no vendor credentials and a
network policy that blocks all market-data hosts (only package registries are
reachable). **No data has been fabricated** — the pipeline was validated
end-to-end using a clearly-labeled synthetic fixture
(`tests/make_fixture.py`), whose output must never be shipped as real data.

To produce the real deliverable, provide ONE of:

1. **Databento** (recommended): run from a machine with network access and a
   `DATABENTO_API_KEY` (dataset `GLBX.MDP3`, schema `ohlcv-1m`):

   ```bash
   pip install pandas numpy databento
   python build_nq_dataset.py --source databento --raw-dir raw/ --out-dir out/
   ```

2. **Any vendor's per-contract 1-minute files** (IQFeed, FirstRate, CQG,
   Norgate, …): place one CSV per quarterly contract in `raw/`
   (`NQH2016.csv` … `NQH2021.csv`, schema `timestamp,open,high,low,close,volume`),
   then:

   ```bash
   python build_nq_dataset.py --source local --raw-dir raw/ \
       --input-tz UTC   # or America/New_York, whatever the vendor uses
   ```

Either way the output directory will contain the 10 CSVs, the ZIP,
`roll_schedule.csv` (exact roll timestamps and measured offsets),
`audit_report.md` and `audit_report.json` (full audit + ZIP SHA-256/size).

## Methodology

### Roll rule (volume-based; identical for adjusted and unadjusted)

The front contract rolls to the next quarterly (H→M→U→Z, expiry 9:30 ET on
the 3rd Friday of the contract month) at the **first 18:00 ET session open
after the first Globex trade date on which the incoming contract's total
session volume exceeds the outgoing front's** (evaluated within 21 days of
expiry). The roll timestamp is therefore known **precisely** — it is that
18:00 ET session-open bar — and both series use the **identical** roll
schedule by construction, so the only difference between them is the price
adjustment.

### Adjustment (difference / back-adjustment, "Panama")

At each roll, the offset is the **median per-minute close spread
(incoming − outgoing)** over the last 120 minutes both contracts traded
before the roll timestamp (`--overlap-minutes`). Offsets are **cumulatively
added to all bars before each roll**; the most recent prices are unchanged.
Rolls therefore create no artificial jumps in the adjusted series, while the
unadjusted series retains the genuine contract-switch gaps.

### What is never done

No interpolation, no forward-filling, no manufactured bars, no smoothing, no
removal of volatile periods (2020/COVID stays fully intact), no session
filtering. Holiday and shortened sessions appear exactly as traded. The only
price modification anywhere is the explicit back-adjustment offset.

## Audit (runs automatically before zipping)

Per file: row count, first/last timestamp, duplicate timestamps, null OHLC
rows, invalid OHLC rows (High ≥ Open/Close/Low, Low ≤ Open/Close/High),
min/max price, volume presence, Saturday bars, bars in the 17:00–17:59 ET
maintenance window, bars outside expected Globex hours (Sun 18:00 → Fri
17:00 ET envelope), the 1,048,576-row Excel-truncation flag, and
mid-session start/end heuristics.

Adjusted vs unadjusted: row counts, timestamps present in only one series,
and detection of every step change in `adjusted_close − unadjusted_close`
(each step must correspond to a roll; timestamps are reported).

ZIP: re-opened and integrity-tested programmatically, all 10 members read
back row-by-row with header verification, then size and SHA-256 reported.

## Pipeline validation (synthetic fixture)

`tests/make_fixture.py` generates fake NQ-shaped per-contract data (random
walks with deliberate inter-contract basis, realistic session envelope,
volume ramps that trigger the crossover rule, stored in UTC to exercise DST
conversion). A full 2016–2020 fixture run produced:

- all 20 rolls detected by the volume rule at 18:00 ET session opens
- exactly 4 adjustment steps per year, each landing on a roll timestamp,
  with the final offset returning to exactly 0.0
- identical timestamp coverage in adjusted vs unadjusted (0 asymmetric)
- unadjusted gap of −488 pts at a roll vs +11 pts (normal drift) adjusted
- 0 duplicate, null, invalid-OHLC, Saturday, maintenance-window, or
  out-of-hours rows; ZIP validated with 10/10 members and SHA-256 reported
