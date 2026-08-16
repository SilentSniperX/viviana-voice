# READ ME FIRST — TWO PARALLEL NQ PROJECTS, ONE PROFITABILITY GOAL
Date: 2026-08-16

## PINNED OBJECTIVE
Extract what 10 successful traders collectively know, then build ONE executable NQ strategy that preserves their ideas.

## IMPORTANT: THESE ARE SIMULTANEOUS PROJECTS, NOT COMPETITORS

Do NOT collapse these two workstreams into a winner/loser contest.

They can coexist because they are solving different parts of the same trading problem:

### PART 1 — S5b Integrity + Day-Classifier Project
Purpose:
Validate and understand the ten-trader synthesis itself:
direction → pullback → failed counterattack → renewed pressure/price response.

This project asks:
- Is S5b genuinely identifying exceptional continuation/trend days?
- Does that information survive matched/placebo controls?
- Is the modern edge just a volume-threshold artifact?
- How concentrated is the expectancy?
- Why does the historical trade list differ slightly between ChatGPT and Claude?
- Can the classifier be represented faithfully in TradingView without pretending it is an early entry signal?

This is the "WHAT KIND OF DAY IS THIS BECOMING?" module.

### PART 2 — ORB + S5b Integration Project
Purpose:
Use the independently verified Samir ORB benchmark as an EARLY positioning/direction engine,
and use S5b as a LATER conviction/day-classification engine.

This project asks:
- How should the position behave when S5b later confirms the ORB direction?
- Is there any causal invalidation event worth acting on, or should mechanical early exits be abandoned?
- Can ORB + S5b become one TradingView execution workflow where each component does the job it is actually good at?

This is the "HOW DO WE GET POSITIONED EARLY AND THEN KNOW WHEN TO TRUST THE DAY?" module.

## THE DESIRED END STATE IN TRADINGVIEW

These are partners.

A future TradingView implementation can contain BOTH:

1. ORB / Direction Module
   - Draw the opening range.
   - Detect the qualified Samir-style breakout.
   - Produce the early directional/entry state.

2. S5b / Collective Conviction Module
   - Latch direction.
   - Track the directional leg.
   - Detect a 25–75% pullback.
   - Detect countertrend failure.
   - Require renewed pressure + price response.
   - Mark the completed S5b event as a high-conviction day state.

3. Execution / Risk Layer
   - One trade/day framework.
   - Initial invalidation fixed before entry.
   - No automatic OHLCV "behavior exit" unless it independently earns its place.
   - When S5b confirms the existing ORB direction, that may affect conviction, hold behavior,
     scaling, or future prop/live sizing — but only after causal testing.

The goal is NOT:
"Does ORB beat S5b?" or "Does S5b beat ORB?"

The goal is:
**Can ORB supply location/timing while S5b supplies day-quality information, producing a more profitable and more executable NQ system?**

## RESEARCH DISCIPLINE
- No parameter rescue after a failed preregistered test.
- No lookahead use of S5b as an ORB entry filter; S5b resolves later.
- Separate source-trader ideas from our mechanical OHLCV proxies.
- Mechanical early exits have repeatedly underperformed holding; do not keep inventing them casually.
- Any TradingView version must use only information available at that bar close / next executable price.
- Preserve both projects until one is logically subsumed by a proven integrated implementation.

## RAW DATA
The raw NQ ZIPs are intentionally NOT included in this package to keep it shareable:
- NQ_1m_by_year_2023-2026(5).zip
- NQ_1m_history_pre2023(1).zip

Both ChatGPT and Claude have already worked from the same underlying FirstRate adjusted 1-minute history.
Use the raw ZIPs separately if a complete rerun is required.

## NAVIGATION
Start with:
1. PART_1_S5B_INTEGRITY_CLASSIFIER/README_PART1.md
2. PART_2_ORB_S5B_INTEGRATION/README_PART2.md

The REFERENCE folder contains the strategy vault so none of the core methodology is lost.
