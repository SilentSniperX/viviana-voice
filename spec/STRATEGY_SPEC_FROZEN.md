# FROZEN STRATEGY SPEC — NQ ORB + S5b v1

## A. Market / session
- Target: NQ/MNQ.
- Timezone: America/New_York.
- TradingView execution chart: 5-minute.
- RTH context.

## B. ORB — EARLY POSITIONING ENGINE
Opening Range:
- 09:30:00 through 09:44:59 ET.
- ORH = high of that window.
- ORL = low of that window.

Qualification window:
- first qualified completed 5m bar from 09:45 through 10:30 ET inclusive.

Qualified LONG breakout:
- close > ORH
- candle body / candle full range >= 0.50

Qualified SHORT breakout:
- close < ORL
- candle body / candle full range >= 0.50

Rules:
- First qualified direction wins.
- Entry = next 5m bar open.
- LONG stop = ORL.
- SHORT stop = ORH.
- If next-bar entry is already beyond the stop/invalidation, SKIP the trade.
- One trade per day.
- Stop has priority if touched.
- Otherwise exit at RTH close.
- No other v1 exit.

Verified reference:
2016–Jun 2026 roughly 2,295 trades / +122.8R / PF ~1.11 in the research engines.

## C. S5b — FAILED COUNTERATTACK / CONVICTION CLASSIFIER
S5b opening balance:
- first six 5m bars, 09:30–09:59 ET.
- OBH / OBL.

Direction latch:
- Starting with completed 10:00 5m bar, first close beyond OBH/OBL latches direction.
- First direction latches.

LONG mechanics:
1. leg_low = session low through direction/latch bar.
2. leg_high = running max after direction latch.
3. Pullback retracement = (leg_high - bar.low) / (leg_high - leg_low).
4. Valid pullback when retracement is between 0.25 and 0.75.
5. Retracement > 1.00 invalidates S5b state only; it does NOT create a v1 trade exit.
6. Failure: two consecutive bars with no meaningful new downside pullback progress:
   low[i] >= low[i-1] - 1.0 point
   AND low[i-1] >= low[i-2] - 1.0 point.
7. Reassertion/pressure bar:
   - close > prior bar high
   - close > open
   - volume >= 1.2 * rolling 12-bar mean volume
   - rolling mean can begin with minimum 6 observations.
8. When pullback + failure + reassertion complete, S5b LONG = CONFIRMED.

SHORT = exact mirror:
- leg_high / running leg_low
- retracement upward 25–75%
- two bars with no meaningful new upside progress
- close below prior bar low
- close < open
- same relative-volume condition.

No standalone S5b entries after the 11:30 5m bar.

## D. RELATIONSHIP STATE
Track separately:
ORB direction = LONG / SHORT / NONE.
S5b direction = LONG / SHORT / NONE.
S5b state:
0 WAITING_FOR_LATCH
1 LATCHED
2 PULLBACK_ACTIVE
3 COUNTERATTACK_FAILURE
4 CONFIRMED
X INVALIDATED

Relationship:
- ALIGNED = S5b CONFIRMED in current ORB direction.
- DISAGREEMENT = S5b CONFIRMED opposite current ORB direction.
- UNRESOLVED otherwise.

S5b NEVER retroactively filters the ORB entry.

## E. VISUALS
Display only useful state:
- ORH / ORL
- optional S5b OBH / OBL
- ORB entry marker
- original hard stop
- current ORB direction
- S5b state
- relationship state
Prominent labels:
- HIGH CONVICTION LONG
- HIGH CONVICTION SHORT

## F. ALERT EVENTS
- ORB_LONG_ENTRY
- ORB_SHORT_ENTRY
- S5B_LONG_CONFIRMED
- S5B_SHORT_CONFIRMED
- ORB_STOP
- SESSION_CLOSE_EXIT

## G. DEAD / PARKED RULES
DEAD:
- structure-flip mechanical exit
- FCF dynamic exit
- counterattack-succeeds exit
- full-retrace early exit
- +40m NO-LATCH exit

PARKED:
- add-to-winner / sizing when ORB and S5b align
- direction-agreement sizing
These are NOT v1.
