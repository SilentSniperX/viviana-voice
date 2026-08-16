# START HERE

This package is the engineering bridge between completed NQ research and a deterministic TradingView/paper-execution system.

## The strategy in one sentence
Get positioned early when NQ proves direction through the verified ORB entry; then use the ten-trader Failed Counterattack/S5b sequence as a later high-conviction state; keep the original hard stop and otherwise leave the winner alone to the RTH close.

## What has been proven enough to implement
- The Samir-style 15-minute ORB benchmark reproduced independently.
- The S5b completed sequence contains strong era-stable day-quality information.
- Mechanical OHLCV early exits repeatedly underperformed holding in the modern regime.
- Round 4 NO-LATCH +40m exit failed the frozen modern-era gate and is dead.

## What is NOT being implemented yet
- add-to-winner at S5b confirmation
- dynamic position sizing
- disagreement exits
- NO-LATCH exits
- behavior/trailing exits
- prop-firm sizing logic
- live broker orders

## Package map
- `CLAUDE.md`: persistent project rules for Claude Code.
- `PROMPT_PASTE_INTO_CLAUDE_CODE.txt`: initial instruction.
- `spec/`: frozen implementation and acceptance rules.
- `research/latest_round4/`: latest Claude results.
- `research/master_handoff/`: accumulated research evidence/scripts.
- `research/reference/`: strategy vault.
- `pine/`: destination for Pine v6 implementation.
- `executor/`: paper webhook/execution service scaffolding.
- `tests/`: parity and event tests.
- `docs/`: TradingView and deployment notes.

## Raw NQ history
The two large raw FirstRate ZIPs are intentionally NOT embedded because an earlier 125MB handoff was difficult to share:
- `NQ_1m_by_year_2023-2026(5).zip`
- `NQ_1m_history_pre2023(1).zip`

They are only necessary for a complete clean-room Python rerun. Pine development and parity can begin from the supplied reference trade logs. If Claude Code is running on the machine that already has those ZIPs, place them under `data/raw/` without committing them.
