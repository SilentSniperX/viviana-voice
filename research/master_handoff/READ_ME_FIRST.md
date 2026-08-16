# NQ MASTER HANDOFF — AUGUST 16, 2026

## PINNED OBJECTIVE
Extract what 10 successful traders collectively know, then build ONE executable NQ strategy that preserves their ideas.

## WHAT THIS ZIP CONTAINS

### 01_CLAUDE_ROUND3_PART1
Claude's completed Round-3 + Part-1 work, including the classifier controls,
four-state taxonomy, trade/flag CSVs, and findings.

### 02_CHATGPT_DUAL_PROJECT_HANDOFF
The prior two-workstream package:
- Part 1: S5b integrity / day-classifier research
- Part 2: ORB + S5b integration
These are simultaneous, complementary workstreams, not competing strategies.

### 03_ROUND4_NO_LATCH
ChatGPT's next preregistered test:
- timing-only cutoff analysis
- frozen +40-minute NO-LATCH test
- frozen pass/kill gates
No Round-4 P&L was used to choose the cutoff.

### 04_STRATEGY_VAULT
The saved PDF/DOCX vault containing the three major strategy families so they
can be revisited later without reconstructing the project.

### 05_CORRECTED_FCF
Corrected Failed Counterattack Continuation files, including the properly
re-exported 2016–2026 1-minute log.

### 06_REFERENCE_SUMMARIES
Key benchmark, leaderboard, equity, management, and comparison files.

## CURRENT JOINT FINDING

The emerging architecture is:

1. ORB / early direction and location
2. S5b / later day-quality and conviction classifier
3. Hold-to-close as the default once positioned, because every mechanical
   OHLCV early-exit proxy tested so far has underperformed simply holding

Claude's latest work further found that S5b's completed sequence separates
high-quality continuation days across all eras, and the NO-LATCH state may be
the first early-observable candidate worth testing.

## ROUND 4
The next test is intentionally ONE test:
At +40 minutes after ORB entry, if no S5b latch has occurred, exit at that
5-minute open. Otherwise hold the original benchmark.

The +40-minute cutoff was selected from timing distribution only because it is
the earliest tested cutoff where >80% of eventual latches had already occurred
in every era.

No parameter sweep. No rescue variants if it fails.

## RAW NQ DATA
NOT INCLUDED to keep this handoff shareable.

Raw files used throughout the project:
- NQ_1m_by_year_2023-2026(5).zip
- NQ_1m_history_pre2023(1).zip

Those should be supplied separately only if a complete clean-room rerun is needed.
