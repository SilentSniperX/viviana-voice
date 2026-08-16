# STATE MACHINE

## ORB state
PRE_OR -> OR_SET -> WAIT_BREAK -> SIGNAL_LONG/SIGNAL_SHORT -> POSITION_ACTIVE -> STOPPED/CLOSED

Only one SIGNAL/POSITION per session.

## S5b state
WAITING_FOR_LATCH
  -> LATCHED_LONG or LATCHED_SHORT
  -> PULLBACK_ACTIVE
  -> COUNTERATTACK_FAILURE
  -> CONFIRMED
or -> INVALIDATED

S5b is independent of ORB trade entry.

## Combined state
POSITION_ACTIVE + S5b CONFIRMED same side => HIGH_CONVICTION_ALIGNED
POSITION_ACTIVE + S5b CONFIRMED opposite side => DISAGREEMENT
POSITION_ACTIVE + other S5b states => UNRESOLVED

No v1 execution action is triggered by HIGH_CONVICTION except the S5b confirmation alert/state display.
