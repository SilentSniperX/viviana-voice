---
name: nq-parity
description: Audit NQ Pine/TradingView outputs against the frozen Python/research reference without changing strategy rules.
---

Read `CLAUDE.md`, `spec/STRATEGY_SPEC_FROZEN.md`, and `spec/PARITY_ACCEPTANCE_GATES.md`.

Perform parity only. Do not optimize.

1. Build/refresh canonical reference table from supplied research logs.
2. Compare ORB events trade-by-trade.
3. Compare S5b state timestamps where reference states exist.
4. Classify mismatches.
5. Fix implementation bugs only.
6. Do not change a frozen rule to improve parity unless the source spec itself was implemented incorrectly.
7. Write `tests/parity_report.md`.
