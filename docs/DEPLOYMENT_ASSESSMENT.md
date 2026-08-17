# DEPLOYMENT ASSESSMENT — ORB + S5b v1

Every number here is measured from the 2,295-trade reference (2016-01-04 →
2026-06-30) or the 2,323-trade TradingView export. No estimates, no
extrapolation. Reproduce with `python3 tests/risk_report.py`.

**Headline: the edge is real and the implementation is verified. The strategy is
not deployable on a trailing-drawdown prop account at any size, and the reason is
arithmetic, not opinion.**

---

## TASK 1 — final parity gap

| check | result |
|---|---|
| coverage | 2,295 / 2,295 sessions, 0 missing, 0 extra |
| direction parity | **2,295 / 2,295** |
| entry parity | **2,292 / 2,295** (99.87%) |
| exit-reason parity | 2,292 / 2,295 |
| exit-bar parity | 2,265 / 2,295 |

**All 30 exit-bar mismatches are shortened sessions — confirmed, 30 of 30.** Of
48 shortened sessions in the window, 18 resolved correctly and 30 did not. On an
extended-hours chart an early-close day has no 15:55 bar, so the clock trigger
falls through to the 18:00 Globex reopen.

Those 30 late exits are worth **−139.2 points**; an RTH-only chart should remove
them. The 3 entry differences are marginal qualification calls on a different
price series (class 1).

**Still open, and it is not the 30:** across the 2,265 sessions whose bars match
exactly, the P&L still differs by **+1,698 points**. TradingView's `NQ1!`
continuous is not FirstRate's back-adjusted series, so identical bars carry
slightly different prices — about +0.75 points per trade. Bar-level parity is
closed; *price-level* parity is not, and cannot be, across two different
continuous-contract constructions.

**Consequence for every number below: the reference is used, not the
TradingView export.** The reference is the more conservative of the two
(+8,368 vs +9,927 points).

---

## TASK 2 — risk-survival report

1 NQ contract, $20/point, costs included.

| year | trades | net pts | net $ | PF | win % | max DD pts | max DD $ |
|---|---|---|---|---|---|---|---|
| 2016 | 216 | −134.5 | −2,690 | 0.94 | 39.8 | −510.8 | −10,215 |
| 2017 | 211 | −108.8 | −2,175 | 0.94 | 41.2 | −296.5 | −5,930 |
| 2018 | 212 | +215.2 | +4,305 | 1.04 | 40.6 | −752.5 | −15,050 |
| 2019 | 212 | +98.0 | +1,960 | 1.03 | 42.0 | −388.2 | −7,765 |
| 2020 | 212 | −898.5 | −17,970 | 0.90 | 44.3 | −1,841.2 | −36,825 |
| 2021 | 227 | +1,220.2 | +24,405 | 1.15 | 52.9 | −1,072.8 | −21,455 |
| 2022 | 227 | +2,441.8 | +48,835 | 1.18 | 45.8 | −1,682.0 | −33,640 |
| 2023 | 212 | +1,802.8 | +36,055 | 1.23 | 43.4 | −656.8 | −13,135 |
| 2024 | 233 | +2,188.0 | +43,760 | 1.22 | 43.3 | −1,299.0 | −25,980 |
| 2025 | 219 | +1,802.8 | +36,055 | 1.16 | 46.1 | −2,417.0 | −48,340 |
| 2026¹ | 114 | −259.2 | −5,185 | 0.97 | 46.5 | −2,214.5 | −44,290 |
| **ALL** | **2,295** | **+8,367.8** | **+167,355** | **1.10** | **44.1** | **−2,539.0** | **−50,780** |

¹ part year, to 2026-06-30.

**Four of eleven years are losing years.** The profit is concentrated in
2021-2025; 2016-2020 is net negative.

### Worst-case statistics

| | value |
|---|---|
| max drawdown (closed trades) | **−2,539 pts** = −$50,780 (NQ) / −$5,078 (MNQ), trough 2021-02-24 |
| max drawdown (intraday, TV export) | **−$7,331 on 1 MNQ** |
| worst month | 2020-03, −1,463.2 pts = −$29,265 |
| worst quarter | 2026 Q2, −1,157.8 pts = −$23,155 |
| longest losing streak | **11 trades** |
| longest winning streak | 8 trades |
| largest losing trade | −445.8 pts = −$8,915 |
| largest winning trade | +840.8 pts = +$16,815 |
| average winner | +88.1 pts (+$1,762) |
| average loser | −63.2 pts (−$1,265) |
| expectancy | **+3.65 pts/trade** = +$73 (NQ) / +$7.30 (MNQ) |

### Long vs short, and exit mix

| | n | net pts | PF | win % |
|---|---|---|---|---|
| LONG | 1,194 | +5,354.5 | 1.14 | 49.1 |
| SHORT | 1,101 | +3,013.2 | 1.07 | 38.8 |

| exit | n | share | net pts | avg |
|---|---|---|---|---|
| stop | 989 | 43.1% | −72,899 | −73.7 |
| RTH close | 1,306 | 56.9% | +81,267 | +62.2 |

Both directions are profitable; long is the stronger side. The system loses on
43% of days by design and pays for it on the hold-to-close winners.

---

## TASK 3 — prop-firm survivability

MNQ at $2/point. Intraday adverse excursion taken from the TradingView export —
trailing drawdown is an intraday rule, so closed-trade equity is not sufficient.
Model: the threshold follows the high-water mark including open-trade excursion
and never locks (the harshest common variant).

### Historical path — did it bust?

| size | intraday peak-to-trough | $2,500 | $3,500 | $5,000 |
|---|---|---|---|---|
| 1 MNQ | −$7,331 | **BUST** 2020-03-18 | **BUST** 2020-03-25 | **BUST** 2026-07-06 |
| 2 MNQ | −$14,662 | **BUST** 2018-12-27 | **BUST** 2020-03-13 | **BUST** 2020-03-18 |
| 5 MNQ | −$36,655 | **BUST** 2016-02-15 | **BUST** 2016-09-06 | **BUST** 2018-12-26 |

**Every combination busts.** The single most generous case — 1 MNQ against a
$5,000 trailing limit — survived ten years and then busted in July 2026.

### Probability of busting — 10,000 bootstrapped sequences

| size | horizon | $2,500 | $3,500 | $5,000 |
|---|---|---|---|---|
| 1 MNQ | 1 year | 60.5% | 29.6% | 8.7% |
| 1 MNQ | 2 years | 87.1% | 57.6% | 23.7% |
| 2 MNQ | 1 year | 98.7% | 89.2% | 59.4% |
| 2 MNQ | 2 years | 100.0% | 99.0% | 86.6% |
| 5 MNQ | 1 year | 100.0% | 100.0% | 99.9% |
| 5 MNQ | 2 years | 100.0% | 100.0% | 100.0% |

### Can this strategy realistically survive prop-firm rules?

**No.** Not at 1, 2 or 5 MNQ against a $2,500-$5,000 trailing drawdown.

The arithmetic that settles it, at the smallest tradeable size:

| | 1 MNQ |
|---|---|
| expectancy | +$7.61 per trade |
| ~219 trades/year | **+$1,667 per year** |
| drawdown room for ~95% two-year survival | **$7,500-10,000** |

The account must carry **4.5 to 6 times its annual expected profit** as
drawdown room. Trailing-drawdown prop accounts are not sold on those terms —
their limits are sized to the profit target, not to the strategy's variance.

Required limits at 1 MNQ: $7,500 → 4.3% two-year bust; $10,000 → 0.4%.

### What the numbers *do* support

Self-funded, no trailing rule, where only real equity matters:

| size | worst peak-to-trough | annual expectancy |
|---|---|---|
| 1 MNQ | −$7,331 | +$1,667 |
| 2 MNQ | −$14,662 | +$3,335 |
| 5 MNQ | −$36,655 | +$8,336 |
| 1 NQ | −$73,310 | +$16,673 |

A self-funded account of roughly **$20,000-25,000 trading 1 MNQ** carries the
historical worst case with margin to spare, at ~7-8% annual return on capital.
That is the honest deployment envelope. Scaling to 1 NQ requires ~$200,000+ on
the same logic.

---

## TASK 5 — go / no-go criteria

### NOT TRADEABLE — kill or halt immediately if any is true

1. Rolling 250-trade net R falls below the **1st percentile** of the reference
   bootstrap (worse than −2,000 points at 1 NQ). This is the objective kill line.
2. Any parity regression: entry or direction parity below 99.5% on a fresh
   export, or any unclassified mismatch.
3. Realised slippage exceeds **1.5 points per side** average over 50 paper
   trades — the edge is 3.65 points per trade and cannot absorb it.
4. A trade executes that the reference engine does not produce, or a reference
   trade is missed, on any live session.
5. Any change to a frozen rule without a preregistered test.

### PAPER TRADE ONLY — minimum evidence to start

1. Exit-bar parity **2,295/2,295** on an RTH chart (Task 1 closed).
2. The paper pipeline passes its own gates: duplicate webhook rejected,
   malformed payload rejected, stale event rejected, restart reconstructs state,
   one position maximum, stop cannot reverse, session close flattens.
3. Daily reconciliation runs unattended for **20 consecutive sessions** with
   zero unexplained differences between the strategy's signals, TradingView's
   own fill reports, and the ledger. Measured by
   `python3 executor/daily_audit.py --date <session>` each day and
   `--streak` to count; sessions the strategy declined count as clean but
   cannot make up more than half the streak.
4. No live broker credentials present anywhere in the repo or alert payloads.
   Phase 1 executes inside TradingView's broker emulator only — the Tradovate
   adapter is built, gated and deliberately unwired.

### LIVE CAPITAL APPROVED — all must hold

1. **60 consecutive paper sessions** with signal-to-fill-to-ledger
   reconciliation clean (`executor/daily_audit.py --streak` reporting
   `live_capital_gate_met: true`).
2. Measured slippage ≤ **0.75 points per side**, the cost already in the
   reference.
2b. **Fill-price plausibility against market data.** Every recorded fill must
   fall inside the traded range of its bar. Deliberately NOT a Phase-1 gate:
   in Phase 1 both signals and fills originate in TradingView, so a
   TradingView-derived bar source would not be independent. Once a real broker
   supplies its own fill record, that record is the independent truth and this
   becomes mandatory.
3. Account is **self-funded or has a non-trailing drawdown ≥ $10,000 per MNQ**.
   A trailing-drawdown prop account is disqualifying on the Task 3 evidence.
4. Position sizing at or below **1 MNQ per $20,000** of risk capital.
5. A written maximum-loss halt: stop trading for the month at −$3,000 per MNQ
   (roughly half the historical worst drawdown) and re-review.
6. The operator has seen an **11-trade losing streak** in paper and did not
   intervene. This is the documented historical worst; anyone who cannot sit
   through it in simulation will not sit through it live.

---

## The one-line answer to the question asked

The edge is real (+8,368 points over 2,295 trades, verified across three
engines and reproduced by TradingView), and it is **too small relative to its own
variance to survive a trailing-drawdown prop account**. It is deployable only on
capital that is allowed to draw down: about $20,000-25,000 per MNQ, for roughly
$1,700 per MNQ per year.
