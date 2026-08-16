TRADINGVIEW IMPLEMENTATION TARGET

This handoff is intentionally structured around two modules that can live in ONE Pine Script workflow.

MODULE A — ORB EARLY DIRECTION / ENTRY
- Opening range box
- Qualified breakout state
- One-trade/day state
- Initial risk/invalidation

MODULE B — S5b COLLECTIVE CLASSIFIER
- Direction latch
- Directional leg tracking
- 25–75% pullback state
- Full-retrace invalidation state
- Two-bar countertrend failure
- Relative-volume pressure state
- Reassertion state
- Same-direction / disagreement status relative to ORB

DISPLAY / ALERTS
- ORB qualified
- S5b pullback active
- S5b failure detected
- S5b reassertion confirmed
- ORB + S5b aligned = high-conviction day
- S5b invalidation

Do not encode hindsight classification as an earlier entry.
Every state must be calculated only from completed bars available at that time.

The eventual profitability decision can combine:
early ORB location + later S5b conviction.
