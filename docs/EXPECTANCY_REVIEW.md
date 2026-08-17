# EXPECTANCY REVIEW — the 65-trade TradingView run

Written in response to the handoff concluding *"the strategy appears to have
negative expectancy"* on the strength of a 65-trade TradingView backtest.

**Finding: that conclusion is not supported, and the evidence points the other
way.** Reproduce everything here with `python3 tests/expectancy_check.py`
(needs the raw archive in `data/raw/`).

---

## The one number that settles it

The TradingView run: 65 trades, **−$6,014**, PF 0.57, 35.4% wins.

The verified reference engine — the research output itself, the thing
TradingView is being compared against — over its own most recent 65 trades
(2026-03-18 to 2026-06-30):

| | net | PF | win% | stops |
|---|---|---|---|---|
| TradingView, 65 trades | **−$6,014** | 0.57 | 35.4% | 53.8% |
| Reference, last 65 trades | **−$11,730** | 0.90 | 41.5% | 46.2% |

**The reference lost nearly twice as much over the comparable window.** Whatever
the TradingView run is showing, it is not a failure to reproduce the reference's
recent behaviour — the reference's recent behaviour is *also* a loss.

Killing the strategy on this sample means killing it on a window where the
known-good, decade-validated engine also loses money.

## Why 65 trades cannot answer the question

The edge is PF ~1.10. That is thin by construction — it is a hold-to-close
breakout system whose entire published record is 2,295 trades for +122.8R. A
sample of 65 has no power to resolve it.

Bootstrap, 20,000 independent 65-trade samples drawn from the **2024-2026
reference** (the era that matters, PF 1.12):

| percentile | net $ | PF | win% |
|---|---|---|---|
| 5th | −29,385 | 0.65 | 35.4 |
| median | +8,050 | 1.11 | 44.6 |
| 95th | +47,890 | 1.87 | 55.4 |

- P(net ≤ −$6,014) = **27.3%** — better than one run in four
- P(PF ≤ 0.57) = 2.1%
- P(win% ≤ 35.4) = 4.6%
- P(all three together) = **1.33%**

A 27% event is not evidence of anything. The PF and win-rate figures are on the
unusual side, which is why the implementation question below was still worth
asking — but 1-in-75 is not proof of a broken system, and it is nowhere near the
bar for discarding a 2,295-trade result.

For scale: over the same 2023-07 → 2026-06 window the reference makes
**+$97,205 on 671 trades** and endures a **−$48,340** drawdown along the way.
The current −$11,730 stretch is a quarter of a drawdown this strategy has
already survived inside a profitable run.

## Does the direction rule carry information? (baseline test)

Identical entry bar, identical risk distance, identical hold-to-close — only the
direction varies. 664 trades, 2023-07 to 2026-06:

| variant | net | PF | win% |
|---|---|---|---|
| **FOLLOW ORB (the rule)** | **+$104,865** | **1.16** | 45.2% |
| FADE ORB (inverted) | −$206,265 | 0.75 | 36.3% |
| RANDOM direction ×5 | −$85,825 / −$178,435 / +$88,805 / −$58,590 / −$28,515 | 0.78–1.13 | 36.9–44.6% |

Following beats random on four of five draws and beats fading by $311,130. The
direction rule is doing real work.

*Method note:* a naive baseline that simply inverts the direction while keeping
the original OR stop is **degenerate** — the inverted entry sits on the wrong
side of that stop, so the skip rule deletes almost every trade (n=1 of 664). The
first attempt at this table made exactly that error. Holding the risk *distance*
constant and placing the stop on the correct side is what makes the comparison
mean anything.

## Can S5b be hurting P&L?

No — structurally impossible, not merely unlikely. There are 9 order-placing
lines in the Pine and **0** of them reference any S5b variable
(`s5bSide`, `s5bStateN`, `alignmentStr`, `confirmTs`, `s5bEntryOk`, `pullActive`,
`legHigh`, `legLow`). S5b is display and alerts only. Checked mechanically in
`tests/expectancy_check.py`; the split "ORB only vs S5b only vs combined" has no
meaning here because only one of them ever trades.

## The lifecycle counters corroborate the implementation

| counter | TradingView | reference expectation |
|---|---|---|
| sessions | 91 | inflated by ETH calendar rollovers — see below |
| OR built | 75 | the real session count |
| qualified | 65 | **86.7%** of OR-built sessions |
| submitted | 65 | equals qualified |
| confirmed | 65 | equals submitted |
| skipped | 0 | skips are rare in the reference |

The reference qualification rate is **86.9%** (664 of 764 sessions in the
clean-room run). TradingView produced **86.7%**. That is the ORB qualification
logic behaving correctly.

`sessions 91` vs `OR built 75` is not a fault: on an extended-hours chart the New
York calendar date rolls over during Sunday evening, so the counter was
incrementing on sessions that have no RTH bars. ~16 Sundays over ~4.3 months
accounts for the gap exactly. Fixed in v1.4 — the counter now increments on the
first RTH bar.

## The one implementation risk this did surface

The elevated stop share (53.8% vs the reference's 42–46%) and depressed win rate
are the signature you would expect if **hold-to-close exits were firing late**.

`session.islastbar_regular` was the sole trigger for the close exit, and
TradingView's futures regular session does not necessarily end at 16:00 — 16:15
is common. On such a chart every hold-to-close trade would run 15–20 minutes past
the reference's exit, which turns winners into stop-outs and moves all four
observed statistics in exactly the direction observed.

Fixed in v1.4: the clock is now authoritative
(`nyMinute >= 955` → the 15:55 bar), with `session.islastbar_regular` retained
only to catch shortened sessions, whose final bar comes earlier. The exit no
longer depends on TradingView's session template at all.

This is a hypothesis consistent with the evidence, **not a confirmed diagnosis**.
Confirming it needs the trade list (below).

## What would actually settle the residual

The **List of Trades** export from the Strategy Tester. Then:

```bash
python3 tests/parity_tv_list_of_trades.py <export>.csv --offset-ok
```

That grades every trade against the canonical reference and classifies each
mismatch. If exits are firing late, it will show up immediately as class 8
(session close) on the hold-to-close trades. If they are not, the residual is
sample noise and the matter is closed.

## Recommendation

**Do not kill it on this evidence.** The proposed rule — no strategy gets more
than 30 minutes before proving a statistical edge — is a good rule, and applied
here it says: the edge was already proven on 2,295 trades across three
independent engines, and a 65-trade window in which the reference itself loses
more money is not a test that can overturn it.

The genuine open item is a parity question, not an expectancy question: get the
trade list and close it.
