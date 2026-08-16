---
name: nq-implementation
description: Implement the frozen NQ ORB + S5b TradingView and paper-execution system after parity requirements are understood.
---

Read the project root `CLAUDE.md` and frozen spec first.

Sequence:
1. Pine ORB.
2. ORB parity.
3. S5b classifier.
4. S5b state parity.
5. Alerts.
6. Paper webhook receiver.
7. End-to-end paper reconciliation.
8. Documentation.

Never implement live-money execution by default.
Never add a dead/parked research rule.
