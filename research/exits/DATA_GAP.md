# PRE-2023 1-MINUTE DATA — cannot be obtained here

The three-era exit test cannot be run. Recording exactly why, and exactly what
would unblock it.

## What was searched

| source | result |
|---|---|
| `data/raw/` | only `NQ_1m_by_year_20232026.zip` — 2023-2026, adj + unadj |
| both handoff zips (26 MB + 4 MB), fully extracted | no raw bars; every CSV is trade-level |
| all 40+ research CSVs | no MFE, MAE, excursion or target-hit column |
| `S4_Samir_Full_2016_2026.csv` — has a `target` column | **empty on all 2,226 rows** |
| network | data vendors blocked at the proxy (403 on CONNECT); the data is commercial regardless |

`round4_trades.csv` is described in `CANONICAL_PROVENANCE.md` as the output of
an "independent 1-minute engine" covering 2008-2026 — so the bars existed for
whoever produced it. Only the engine's OUTPUT was shipped, not its input.

## Why a reconstruction was rejected

Estimating "did this pre-2023 trade reach +1.5R" from realised outcome and
stop/close status is possible in principle. The conditional probabilities are
reasonably stable in the high-mass buckets, measured across 2024 / 2025 / 2026H1:

| condition | 2024 | 2025 | 2026H1 | spread |
|---|---|---|---|---|
| closed, 0.75 <= R < 1.5 | 48.3% | 46.2% | 46.2% | 2pp |
| closed, 0 <= R < 0.75 | 18.9% | 15.4% | 17.9% | 4pp |
| stopped out | 9.4% | 5.6% | 4.5% | 5pp (a 2x relative range) |
| closed, R < 0 | 12.0% | 7.1% | 18.8% | 12pp |

Stability is not the reason it was rejected. The reason is circularity: any such
model is fitted ENTIRELY on 2024-2026 path behaviour, so applying it to
2016-2020 assumes modern path shape in the era whose difference is the whole
question. It would produce numbers, not evidence.

One asymmetry would have been usable — a model biased toward modern behaviour
that STILL showed the families failing pre-2023 would be meaningful. But a pass
would prove nothing, and a test that can only return one informative answer is
not the test that was asked for.

## What would unblock it

1-minute NQ bars, **2016-01-01 to 2022-12-31**, matching the existing files:

- format: `YYYY-MM-DD HH:MM:SS,open,high,low,close,volume`, no header
- back-adjusted continuous (the `NQ_adj_1m_*` series, not `unadj`)
- **regular trading hours only, 09:30-16:00 New York** — this is the important
  part. The existing files carry the full overnight session, which is why 2024
  alone is 20 MB. RTH-only is ~390 rows per session, roughly 4 MB per year
  uncompressed, so all seven years zip to well under the upload limit in a
  single file.

Nothing else is needed. Entries, stops and exits all come from the canonical
reference; the bars are used only to determine what price did between them.
