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
| **the host machine** | **not reachable — see below** |

`round4_trades.csv` is described in `CANONICAL_PROVENANCE.md` as the output of
an "independent 1-minute engine" covering 2008-2026 — so the bars existed for
whoever produced it. Only the engine's OUTPUT was shipped, not its input.

## The host machine is not reachable from here

Asked to search the user's local drives for a ~100 MB pre-2023 file. It is not
reachable, and this is architectural, not a permission that can be granted.
This session runs in an isolated cloud VM; the repository was cloned into it
over the network at container start. The user's disk was never attached.

Evidence, gathered directly:

| question | answer |
|---|---|
| working directory | `/home/user/viviana-voice` |
| kernel | `Linux vm 6.18.5-fc-v20 ... (builder@sandboxing)` — a Firecracker microVM |
| container | `container_01JFB8CVqsqepx4YhDaJXT9Q--claude_code_remote--87ee8f` |
| every mounted filesystem | `/` (`/dev/vda`, ext4), `/opt/rclone`, `/opt/claude-code`, `/opt/env-runner`, `/mnt/skills/public`, `/mnt/skills/examples` — all virtio block devices supplied by the VM image |
| block devices (`lsblk`) | `zram0`, `vda`-`vdf`. No Windows volume, no 9p/virtiofs/cifs/nfs share, no removable media |
| `/mnt/c` | `ls: cannot access '/mnt/c': No such file or directory` |
| `/mnt/d`, `/mnt/e`, `/Volumes`, `/host` | same — do not exist |
| `/mnt` actual contents | `attach/` (empty), `skills/`, `user-data/working/` (empty) |
| `/media` | empty |
| WSL interop marker `/proc/sys/fs/binfmt_misc/WSLInterop` | absent — this is not WSL, so there is no Windows filesystem to bridge to |

There is no sandbox *denial* to quote. `/mnt/c` does not return `EACCES`
("Permission denied"), it returns `ENOENT` ("No such file or directory") — the
path is not blocked, it was never created, because no host disk is attached.
Nothing on the user's machine can be reached by any command available here.

A filesystem-wide search was run anyway, across every mounted device, excluding
only `/proc`, `/sys`, `/dev`, `/run`:

- filenames matching `*NQ*`, `*nasdaq*`, `*firstrate*`, `*1min*`, `*_1m*`,
  `*1m_*`, `*ohlcv*`, `*bars*` with extension
  `.csv .zip .7z .txt .parquet .gz .tar .feather .h5`
- **and, separately, every file of any name over 20 MB** — this would have
  caught a ~100 MB data file whatever it was called

Every hit was an already-known artefact: the four `NQ_adj_1m_20{23,24,25,26}.csv`
files, the handoff zips, trade-level research CSVs, and toolchain binaries
(`rustc`, `llvm`, `chromium`, `node`). The only files >20 MB outside
`/usr`, `/opt` and `/root/.cache` are the two handoff uploads themselves.

Every file ever uploaded to this session was also enumerated and opened. All
seven are accounted for; the largest, `CLAUDE_CODE_FIX_ZERO_TRADES_BIG_HANDOFF.zip`
(26.8 MB, 240 entries, 38.8 MB uncompressed), contains **no bar data at all** —
its 29 entries naming 2016-2022 are all trade-level summaries, the largest being
`Samir_ORB_Benchmark_2016_2026.csv` at 0.32 MB.

**Conclusion: the file exists on the user's computer and cannot be read from
here. It has to be uploaded. There is no alternative path.**

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

## What the six families CAN be answered on today

Path data exists from **2023-07-13** onward: 664 of 2,295 trades, 28.9%.
Complete calendar years: **2024 and 2025 only.**

| era requested | coverage | trades |
|---|---|---|
| 2016-2020 | **none** | 0 of 1,063 |
| 2021-2023 | **14.7%** — 2023-07-13 onward only | 98 of 666 |
| 2024-2026 | complete | 566 of 566 |
| ALL | **28.9%** | 664 of 2,295 |

Answers to A-E, stated at the confidence the data actually supports:

- **A. Does 1.5R beat baseline pre-2023?** — **UNANSWERABLE.** Zero pre-2023
  path data other than 98 trades from 2023H2, where B2 (+9.7R) *underperforms*
  baseline (+10.9R).
- **B. Does half-at-1R + BE beat baseline pre-2023?** — **UNANSWERABLE.** On the
  same 98 trades D is the *worst* of the six (+3.0R vs +10.9R baseline).
- **C. Does either survive all three eras?** — **UNANSWERABLE, and the one
  fragment we have is a warning.** Both B2 and D beat baseline over 2024-2026
  (+60.5R and +59.6R vs +43.9R) and both lose to it over 2023H2. Two eras
  disagree, and the earlier one is the one that disagrees. That is the exact
  signature the three-era test was designed to detect.
- **D. Best durable improvement?** — **NOT ESTABLISHED.** D has the best
  2024-2026 PF (1.27) and by far the lowest drawdown (7.1R vs 16.5R, a 57%
  reduction), and that drawdown advantage is mechanically robust — it comes
  from banking half the position, not from a fitted level. But "durable"
  requires the eras it has not been tested on.
- **E. If none survive, keep baseline.** — **Baseline stays**, by default and
  not by evidence. No exit change is authorised on 28.9% coverage where the
  earliest slice contradicts the latest.
