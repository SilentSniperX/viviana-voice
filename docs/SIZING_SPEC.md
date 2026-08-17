# SIZING SPEC — constant risk, $25,000 account, MNQ

Approved as risk normalisation. Not a strategy modification, not an entry
filter. ORB and S5b signal logic are untouched: same entries, same stops, same
exits, same trades in the same order. Only the contract count varies.

Every number below is measured on the 2,295-trade canonical reference
(2016-01-04 .. 2026-06-30) by replaying the exact rule, integer contracts
included. Reproduce with `research/sizing/sizing_analysis.py`.

---

## READ THIS BEFORE ADOPTING IT

**At $25,000 in today's volatility, this rule is close to inert.**

Median stop distance grew from 18.8 points (2016) to 159.4 points (2026). At
$187.50 of risk and $2 per MNQ point, a stop wider than 93.75 points already
exceeds one unit of risk on a *single* contract. So:

| era | trades where even 1 MNQ over-risks |
|---|---|
| 2016-2020 | 6.7% |
| 2021-2023 | 44.1% |
| **2024-2026** | **60.6%** |

On 60% of modern sessions the rule outputs "1 MNQ" — which is what fixed
sizing would have done anyway. Measured over 2024-2026:

| | total | max DD | return/DD |
|---|---|---|---|
| this rule | +$7,506 | $4,849 | **1.55** |
| fixed 1 MNQ | +$7,463 | $4,834 | **1.54** |

**A $43 difference over two and a half years.** The rule's headline advantage
(return/DD 3.85 vs 3.30 across ten years) is earned almost entirely in
2016-2020, when stops were 18-38 points wide and sizing up was actually
possible.

It is still the correct method — it is right when volatility falls, and it
becomes materially better with more capital (see the table at the end). But
adopt it knowing that at $25,000 today it will mostly tell you "1 MNQ", and
that the reason to add SIZE to the alerts is correctness and future-proofing,
not near-term profit.

---

## 1. What is 1R in dollars?

**1R = $187.50** — 0.75% of a $25,000 account.

Chosen by scanning 0.50% / 0.75% / 1.00% across the full history:

| risk % | 1R | total | max DD | DD % of account | return/DD |
|---|---|---|---|---|---|
| 0.50% | $125.00 | +$16,950 | $4,948 | 19.8% | 3.43 |
| **0.75%** | **$187.50** | **+$18,680** | **$4,849** | **19.4%** | **3.85** |
| 1.00% | $250.00 | +$19,804 | $6,988 | 28.0% | 2.83 |

0.75% is the best return per unit of drawdown and keeps the worst historical
drawdown under 20% of the account. 1.00% earns 6% more and costs 44% more
drawdown.

**Noted and deliberately not acted on:** the 2024-2026 window alone prefers
1.00-1.25% (return/DD 1.94-2.35 vs 1.55, at the same ~19-20% drawdown). That
is a 566-trade window and choosing on it would be fitting the parameter to the
period I am also measuring it in. 0.75% is the full-history answer. Revisit
only with a preregistered test.

## 2. Formula: stop distance → MNQ contracts

```
stop_distance = |ENTRY - STOP|                    (index points)
raw           = 187.50 / (stop_distance * 2.00)   ($2.00 per MNQ point)
SIZE          = clamp(floor(raw), 1, 6)           (integer contracts)
```

`floor`, never round — rounding up over-risks by design. Compute the stop
distance from the alert's own ENTRY and STOP fields, not from the opening range
width; they differ, because entry sits beyond the range.

## 3. Minimum 1 MNQ — what it actually costs

One contract is the floor because it is the smallest tradeable unit. Be clear
about what that means: **it is not a floor on size, it is an override on risk.**
When `floor(raw)` is 0, you take the trade anyway and accept more than 1R.

Measured over the 708 affected trades (30.8% of all trades):

| | |
|---|---|
| median actual risk | $260 = **1.4R** |
| 90th percentile | $421 = 2.2R |
| **maximum** | **$1,542 = 8.2R** — 6.2% of the account on one trade |
| worst realised single-trade loss | −$891 |

The 8.2R case was a 771-point stop. There is no cap on this, because capping it
would mean skipping the trade, and skipping trades is an entry filter — which
this change is explicitly not.

If that tail is unacceptable, the honest fix is more capital, not a filter.

## 4. Maximum contract cap

**Cap = 6 MNQ.**

The cap binds when the stop is narrower than 15.6 points. That happened on 110
trades (4.8%), **none of them after 2023**.

Cost of capping, full history:

| cap | total | return/DD |
|---|---|---|
| 4 | +$18,302 | 3.68 |
| **6** | **+$18,680** | **3.85** |
| 10 | +$18,945 | 3.91 |
| uncapped (max 34) | +$19,152 | 3.95 |

Capping at 6 costs **$472 over ten and a half years — 2.5%** — versus
uncapped, and removes a position that would have been 34 MNQ on a $25,000
account (~$1.5M notional, roughly 60x leverage, and probably not marginable).
6 MNQ at NQ 23,000 is already ~$276k notional, about 11x the account.

Cheap insurance against a tail that no longer occurs anyway.

## 5. Historical maximum drawdown, this exact rule

| | |
|---|---|
| closed-trade max drawdown | **$4,849** = **19.4%** of $25,000 |
| estimated intraday max drawdown | **~$7,000** = ~28% of the account |

The intraday figure is an estimate: it scales the closed-trade drawdown by the
1.443x intraday/closed ratio measured directly at fixed 1 MNQ in
`docs/DEPLOYMENT_ASSESSMENT.md`. Bar data for the full history does not exist,
so it cannot be measured exactly. Treat ~28% as the number to plan against.

Worst drawdown per era under this rule: 2016-2020 $4,076 · 2021-2023 $3,364 ·
2024-2026 $4,849.

## 6. Expected annual profit, this exact rule

| basis | total | annual |
|---|---|---|
| full history 2016-2026 (10.49y) | +$18,680 | **+$1,781** |
| 2016-2020 | −$301 | −$60 |
| 2021-2023 | +$11,475 | +$3,825 |
| 2024-2026 | +$7,506 | +$3,002 |

**Plan on +$1,781/year — about 7.1% on $25,000.** The decay analysis found no
evidence the edge is weakening, and the 2021+ subset is the better forward
estimate (~$3,000/year), but the full-history number is the conservative one
and the one the drawdown figures are paired with.

For scale: the expected annual profit is roughly **37% of the expected worst
drawdown**. That ratio is the whole reason this account size is marginal.

## 7. Worked examples

| stop distance | raw = 187.50 / (stop × 2) | floor | **SIZE** | actual risk | in R |
|---|---|---|---|---|---|
| 25 pts | 3.750 | 3 | **3 MNQ** | $150.00 | 0.80R |
| 50 pts | 1.875 | 1 | **1 MNQ** | $100.00 | 0.53R |
| 100 pts | 0.938 | 0 → floor | **1 MNQ** | $200.00 | **1.07R** |
| 200 pts | 0.469 | 0 → floor | **1 MNQ** | $400.00 | **2.13R** |

Two of the four over-risk. That is not an artefact of the examples — it is the
modern distribution. Median 2024-2026 stop is 106 points.

Historical size distribution under this rule, all 2,295 trades:

| size | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| trades | 1,468 | 257 | 184 | 110 | 96 | 180 |

Median 1, mean 1.98.

## 8. The same rule at larger accounts

0.75% risk, cap scaled proportionally:

| account | 1R | total | max DD | DD % | return/DD | over-risked |
|---|---|---|---|---|---|---|
| $25,000 | $187.50 | +$18,680 | $4,849 | 19.4% | 3.85 | 30.8% |
| $50,000 | $375.00 | +$28,816 | $8,672 | 17.3% | 3.32 | 4.7% |
| $100,000 | $750.00 | +$77,890 | $16,387 | 16.4% | 4.75 | 0.2% |
| $250,000 | $1,875.00 | +$210,012 | $40,658 | 16.3% | 5.17 | 0.0% |

The over-risk problem disappears at $50,000 and is gone at $100,000. That is
where constant-risk sizing does what it is supposed to do.

---

## Alert change, when authorised

One line added to the LONG and SHORT alerts only:

```
SIZE: 3 MNQ
```

Nothing else changes. The stop, exit and conviction alerts are untouched.
Not to be implemented until the current phone alert is confirmed working.
