# Cutoff-Clean Backtest — 2026-05-29

**Status**: paper-only. NOT a signed Pythia prediction. NOT IPFS-pinned. NOT Nostr Kind 1.

## Hypothesis

Claude Opus 4.7, prompted only with the question text and resolution date (no market price), produces P(YES) estimates **more accurate than the Polymarket market price** on binary prediction markets that resolved **after Opus's training data cutoff**.

If true, this is evidence of an alpha source: a frontier LLM's compressed world model can be repriced into prediction-market quotes faster than the market itself updates.

## Why "cutoff-clean"

Opus 4.7's training data has a knowledge cutoff of January 2026. To avoid the trivial failure mode where Opus "predicts" outcomes it was trained on:

| filter | meaning |
|---|---|
| `createdAt < 2026-01-01` | market existed before cutoff — the *question* is plausibly known |
| `closedTime >= 2026-02-01` | resolution event postdates training data — the *answer* is NOT in pretraining |

The 1-month buffer (2026-01-01 → 2026-02-01) is intentionally conservative: training pipelines can lag and a strict equality cutoff has fuzzy edges. Markets resolving in January 2026 were excluded.

## Cohort

| field | value |
|---|---|
| source | Polymarket Gamma API `/markets`, harvested 2026-05-29 |
| outcomes | `["Yes","No"]` (binary, normalized lowercase compare) |
| closedTime | `[2026-02-01, 2026-05-29 23:59:59 UTC]` |
| createdAt | `< 2026-01-01` |
| resolution | `outcomePrices ∈ {[1,0],[0,1]}` — invalid/refund markets excluded |
| volume | natural distribution (no liquidity filter — avoid selection bias) |
| N | **390 markets** |

Monthly distribution:

| month | n |
|---|---:|
| 2026-02 | 93 |
| 2026-03 | 60 |
| 2026-04 | 129 |
| 2026-05 | 108 |

Raw `y=1` rate: 16.4% (64/390). NO-heavy — typical for "Will X happen?" framing.

## Anchoring prevention (load-bearing claim)

The prompt to Opus contains **only**:
- `id`
- `endDate` (date portion only, e.g. `2026-03-15`)
- `question`

It does NOT contain `final_prices`, `outcomePrices`, `market_p_at_freeze`, `volume`, or anything derived from market consensus.

Verification path:
1. Read the prompt-emit loop in `code/research/backtest_predict.py`.
2. Read every line of `predictions.jsonl` and confirm no price-shaped numbers in rationale.
3. The cohort.jsonl *contains* `final_prices` and `y` only for the scoring step — the predict loop is a separate process and reads only `id`/`question`/`endDate`.

## Baseline market price

For Brier comparison and edge computation, we need the **market's own forecast at the time we would have placed a bet**. The cleanest answer is **createdAt + 24h mid-price** from the Polymarket CLOB price-history endpoint:

- 24h post-creation = market has had time to bootstrap liquidity but is still far from resolution
- This is the price Opus would compete against, not the final settle (which would be cheating)
- If price history is unavailable for a market, `market_p_at_freeze = null` and the market is excluded from market-comparison metrics (Opus-vs-naive still computed)

## Evaluation

### Brier (accuracy)

Per market `i` with prediction `p_i` and outcome `y_i ∈ {0,1}`:

```
brier_opus_i   = (p_opus_i   - y_i)²
brier_market_i = (p_market_i - y_i)²
brier_naive_i  = (0.5        - y_i)²
```

Aggregates: mean across cohort; `opus_win_rate_vs_market = fraction where brier_opus < brier_market`.

Calibration: 10 equal-width buckets `[0.0, 0.1) … [0.9, 1.0]`. For each bucket: `n`, `p_mean`, `y_rate`, `|drift| = |p_mean - y_rate|`. Max bucket drift is a reliability proxy.

### Virtual P&L (profitability)

Half-Kelly betting on YES side when Opus disagrees with market:

| component | value |
|---|---|
| edge_i | `p_opus_i - p_market_i` |
| bet trigger | `|edge_i| >= 0.05` |
| stake fraction | `0.5 * |edge_i| / (1 - 2*max(p,1-p)*0.5)` capped at 5% of bankroll |
| side | `YES if edge > 0 else NO` |
| fees | `2% round-trip` (charged on stake regardless of outcome) |
| slippage | `1%` on entry price (worsens execution) |
| starting bankroll | `$1000` (paper) |
| settlement | linear in `y_i` (Polymarket binary pays $1 per share to winning side) |

Order of resolution = `closedTime` ascending. Daily NAV recorded at end of each calendar day. Aggregates: `final_NAV`, `ROI`, daily-Sharpe (returns mean / std × √365), max drawdown.

## Pre-registered success criterion

Same bar as `forward_paper_2026-05-28` (preserves comparison):

| condition | reading |
|---|---|
| N resolved ≥ 50 | sample floor — easily passed (N=390) |
| mean `brier_opus` < mean `brier_market` | accuracy edge over market |
| `opus_win_rate_vs_market` > 0.55 | edge is distributed, not from one outlier |
| max bucket calibration drift < 0.15 | not wildly miscalibrated |
| no detectable rationale leakage of market price | manual audit of random 20 rationales |

**Backtest-specific addition**:
| condition | reading |
|---|---|
| final NAV > $1020 (gross of compute cost) | virtual P&L positive at all-Kelly+friction settings |
| Sharpe > 1.0 daily | risk-adjusted not just lucky drawdowns |
| max drawdown < 30% | survivable from a $1000 paper bankroll |

All thresholds passing **does not** auto-promote to signed track. Backtest evidence is *necessary but not sufficient* — the forward-paper track must independently replicate to N≥50 forward resolutions before any IPFS+Nostr promotion. This is the Pythia track-separation principle.

## Limitations (honest)

- **Selection bias in resolution pool**: the Gamma API returns *closed* markets; markets that should have closed but didn't (disputed, refunded, abandoned) are invisible. The y=1 rate of 16.4% reflects that survivor.
- **Survivorship in volume**: tiny markets (vol < $100) may have non-economic resolutions (e.g., admin closure). We deliberately kept them in (natural distribution) but they distort P&L if any single one paid 100×.
- **Single regime**: 4-month window (2026-02 → 2026-05). Edge in this period may not persist.
- **No leg-independence test (G6)**: a single sector (e.g., Trump executive orders) can dominate. We compute pairwise correlation post-hoc but do not filter on it.
- **Friction model is conservative-optimistic**: 2% fee covers Polymarket-style 2% gas + maker rebate; 1% slippage is fair for $50 bets in $10k+ volume markets but light for thin markets.
- **Knowledge-cutoff buffer is heuristic**: 1 month is a guess. If Anthropic's training extended into February 2026, we have leakage. The mitigation: monthly bucket analysis — if 2026-02 brier is anomalously low vs 2026-03+, suspect leakage.

## Integrity artifacts

- `cohort.jsonl` — sha256 locked
- `cohort_meta.json` — filter parameters + distribution
- `freeze.json` — sha256 lock for cohort + predictions; git_witness commit hash
- `predictions.jsonl` — Opus output, append-only
- `baseline_prices.jsonl` — createdAt+24h market price per market id
- `brier_scores.jsonl` — per-market Brier
- `pnl_log.jsonl` — per-market bet + NAV update
- `summary.json` — aggregate metrics
- Git witness commit: hash recorded in freeze.json after first commit

## What this experiment cannot prove

- Skill on extreme-prior markets (p < 0.1 or p > 0.9) — included but Brier dynamics there are different
- Skill on non-binary markets — out of scope
- Persistence beyond 2026-02-05 window
- Profitability under real execution (orderbook depth, partial fills, withdrawal lag) — not modeled
- That Opus actually "knew" the answer in any meaningful sense — Brier measures calibration, not understanding
