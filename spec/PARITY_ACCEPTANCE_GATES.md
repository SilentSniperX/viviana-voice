# PARITY ACCEPTANCE GATES

## ORB parity comes first
Canonical reference is the verified ORB trade list supplied in research files.

Compare per trade:
- date/session
- direction
- signal bar timestamp
- entry timestamp
- entry price
- ORH
- ORL
- stop
- exit timestamp
- exit price
- exit reason

Target: exact trade-list parity wherever the TradingView data feed matches the FirstRate reference sufficiently.

Every mismatch must be classified:
1. data-feed OHLC difference
2. futures continuous-contract/roll difference
3. timezone/session boundary difference
4. 5m aggregation difference
5. body-filter implementation
6. next-bar execution semantics
7. stop sequencing
8. close timestamp/session close
9. implementation bug
10. unknown

Unknown mismatches are blockers.

## S5b parity
Spot-check at minimum:
- latch timestamp/direction
- pullback activation timestamp
- failure timestamp
- confirmation/reassertion timestamp
- invalidation timestamp
- ORB/S5b alignment

Use supplied state/flag logs where available.

## No-lookahead/repainting
- Completed-bar information only.
- Entry generated only after qualified signal bar is closed.
- S5b confirmation cannot affect an earlier ORB entry.
- Historical and realtime state transitions must be consistent.

## Paper pipeline gate
Before any live-money discussion:
- duplicate webhook test passes
- idempotent signal handling passes
- malformed payload rejected
- stale/out-of-order event handling passes
- paper position cannot exceed one strategy position
- stop event cannot create reverse position
- session-close event flattens paper position
- restart/recovery reconstructs paper state from durable log
- alert -> receiver -> paper ledger reconciliation is auditable
