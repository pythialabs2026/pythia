# Forward Paper Brier Experiment — 2026-05-28

**Status**: paper-only. NOT a signed Pythia prediction. NOT IPFS-pinned. NOT Nostr Kind 1.

## Goal

Measure whether Claude Opus 4.7 produces P(YES) estimates **better than the market price** on forward (i.e., not-yet-resolved) binary prediction markets, under conditions that rule out the most common credibility attacks:

1. **No backtesting bias** — every market resolves *after* the prediction is committed and the cohort hash is published.
2. **No anchoring bias** — Opus is shown the question and endDate only; the market's own P(YES) is hidden until evaluation.
3. **No selection bias** — cohort is pre-locked by sha256 and the lock is anchored in a public GitHub commit *before any market resolves*.

## Cohort selection

| filter | value |
|---|---|
| source | Polymarket Gamma API `/markets`, harvested 2026-05-28 |
| `closed` | false |
| `active` | true |
| `outcomes` | `["Yes","No"]` (binary) |
| `endDate` | 2026-06-01 → 2026-12-31 |
| `volume` | ≥ 10 000 USDC |
| `market_p_yes_at_freeze` | ∈ [0.30, 0.70) — high-information-value zone |

This is the **fair-test subset** of 880 harvested forward markets. Extreme markets (P < 0.30 or P ≥ 0.70) are out-of-scope for this experiment — they may be added as a separate cohort later.

Result: **n = 173 markets**.

## Anchoring prevention (the load-bearing claim)

The prompt to Opus contains **only** these fields per market:
- `id`
- `endDate` (date portion only)
- `question`

It does **not** contain `market_p_yes_at_freeze`, `volume`, `outcomePrices`, or any tradable signal.

Implementation: `code/research/forward_predict_helper.py::cmd_prompt()` — the loop that emits the batch deliberately omits price. The cohort file *contains* `market_p_yes_at_freeze` only so the evaluator can use it as a baseline at scoring time.

If you want to verify this claim independently: read the prompt-emit loop directly, then read `predictions.jsonl` and confirm no price leakage into the rationale text.

## Prediction protocol

- Model: `claude-opus-4-7`
- Range: P(YES) ∈ [0.01, 0.99] — extremes (0/1) rejected by validator
- Output schema per market: `{market_id, slug, model, p_yes, rationale, rationale_sha256, predicted_at, track:"paper-only"}`
- Rationale stored in plaintext for audit; SHA-256 of rationale also stored (consistent with Pythia signed-track convention).

## Pre-commit prediction distribution

| bucket | count |
|---|---|
| [0.01, 0.20) | 39 |
| [0.20, 0.35) | 51 |
| [0.35, 0.50) | 42 |
| [0.50, 0.65) | 28 |
| [0.65, 0.80) | 9 |
| [0.80, 0.99] | 4 |

- mean P(YES) **Opus** = 0.341
- mean P(YES) **market** (within the same 173 cohort) = 0.488
- mean |Opus − market| = 0.198
- max divergence = 0.59

Opus shows a strong NO skew relative to market under no-anchoring. This is the dataset under test — it will be either vindicated or invalidated by Brier.

## Evaluation

Daily cron 07:00 KST → `code/research/run_daily_eval.sh`:

1. `verify_freeze.py` — sha256 lock check (abort if drift).
2. `poll_resolutions.py` — Gamma API per cohort market with `endDate ≤ now`. Append-only into `resolutions.jsonl`. Resolution semantics:
   - `closed=true` + `outcomePrices=["1","0"]` → `y=1`
   - `closed=true` + `outcomePrices=["0","1"]` → `y=0`
   - otherwise → `status="invalid"`, excluded from scoring
3. `brier_evaluator.py` — three-way Brier per market and aggregate:
   - `opus`   = `(p_opus − y)²`
   - `market` = `(p_market − y)²`
   - `naive`  = `(0.5 − y)²`
   - Calibration: 10 equal-width buckets, `y_rate` vs `p_mean`.
   - `opus_win_rate_vs_market` = fraction of markets where `brier_opus < brier_market`.

Output: `brier_scores.jsonl` (per market) + `summary.json` (aggregate).

## Pre-registered success criterion (paper → signed promotion)

The Step-3 `debate --critique` should compare against this **predeclared** bar, set before any resolution:

| condition | reading |
|---|---|
| N resolved ≥ 50 | sample-size floor; below this, no decision |
| mean `brier_opus` < mean `brier_market` | edge over market |
| `opus_win_rate_vs_market` > 0.55 | not just one big winner |
| max bucket calibration drift `|p_mean − y_rate|` < 0.15 | not wildly miscalibrated |
| no detectable rationale leakage of market price | manual audit before promotion |

All four numeric thresholds passing **does not** automatically promote — it merely unlocks the debate. The debate decides; promotion is irreversible (Nostr Kind 1, IPFS pin) so the standard for `debate --critique` is a non-negotiable gate.

## What this experiment cannot prove

- Skill on extreme markets (P < 0.30 or P ≥ 0.70) — out of scope.
- Skill on non-binary or scalar markets — not tested.
- Persistence across regimes — single 2026-06→12 window.
- Identifies that Opus *would have profited* — only that it was *more accurate than the market*. Profitability requires bid/ask, slippage, and resolution lag accounting (constitution v3 G4 friction-net), which this experiment intentionally does not model.

## Integrity artifacts

- `freeze.json` — sha256 lock for cohort, predictions, meta; git witness commit hash.
- `verify_freeze.py` — drift check, runs daily before evaluation.
- Git witness commit: `ba09e04c91220fe1ecf08445babe5d528cb4a937` (pushed `git@github.com:pythialabs2026/pythia.git@main`, 2026-05-28 17:19:10 UTC, before earliest resolution 2026-06-01).
