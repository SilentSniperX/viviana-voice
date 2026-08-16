# CLAUDE.md — NQ ORB + S5b AUTOMATION PROJECT

## PINNED OBJECTIVE
Extract what 10 successful traders collectively know, then build ONE executable NQ strategy that preserves their ideas.

## YOUR ROLE
Claude Code is the LEAD ENGINEER for implementation and parity.
Research decisions are already frozen unless a file explicitly says a new test is authorized.
Your job is to translate the proven research into deterministic TradingView/Pine + paper-execution infrastructure WITHOUT changing the strategy.

## CURRENT FROZEN ARCHITECTURE
1. ORB = early positioning / direction engine.
2. S5b / Failed Counterattack = later conviction / day-quality classifier.
3. Initial ORB hard stop + hold to RTH close = management.
4. One trade per day.
5. Mechanical early exits are retired. Five separate early-exit attempts failed in the modern regime.
6. Round 4 NO-LATCH +40m exit = KILL. Do not resurrect it.
7. Direction-agreement sizing/add-to-winner is PARKED. Do not implement or test it until separately authorized.

## ENGINEERING PRIORITY
PARITY BEFORE AUTOMATION.
Do not improve P&L, optimize constants, add indicators, or reinterpret source logic.
First reproduce the verified trade/state lists.

## DO NOT CHANGE
- ORB construction.
- ORB body filter.
- Next-bar execution semantics.
- Stop semantics.
- One-trade/day rule.
- S5b constants.
- Relative-volume threshold.
- S5b state definitions.
- Session definitions.
- Hold-to-close behavior.
- No-lookahead constraint.

## LIVE-MONEY RULE
Build PAPER execution first.
Do not enable a live broker adapter or live order submission without explicit user authorization after parity and paper validation.
Never store broker credentials in Pine or TradingView alert payloads.

## SOURCE OF TRUTH ORDER
1. `spec/STRATEGY_SPEC_FROZEN.md`
2. `spec/PARITY_ACCEPTANCE_GATES.md`
3. `research/latest_round4/round4_report.txt`
4. `research/master_handoff/`
5. `research/reference/NQ_Three_Strategy_Vault_Aug2026.pdf`
If any two sources conflict, STOP implementation at that seam and document the conflict. Do not guess.

## REQUIRED DELIVERABLES
- `pine/nq_orb_s5b_v1.pine`
- deterministic state-machine behavior
- machine-readable alert payloads
- parity tooling and reports
- paper webhook receiver
- idempotency / duplicate-signal protection
- paper risk controls
- audit logs
- clear setup docs
- no live-money execution by default

## DEVELOPMENT STYLE
Keep changes small and testable.
Every strategy-rule implementation should be traceable to a named section of the frozen spec.
Every alert must carry a unique deterministic signal ID.
Every execution event must be logged.
No silent fallbacks.
