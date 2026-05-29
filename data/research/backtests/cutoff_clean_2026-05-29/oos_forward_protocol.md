# OOS Forward-Paper Protocol (Step 17)

**Track:** `cutoff_clean_2026-05-29` (paper-only)
**Author:** autonomous /loop run, 2026-05-29
**Status:** design doc — no execution yet
**Constitutional anchor:** G2 Constraint-Fit (no new paid services), paper-only
(no Nostr/IPFS promotion)

---

## 1. Problem statement

The backtest at `cutoff_clean_2026-05-29` (N=390 markets, `closedTime ≥
2026-02-01`) shows Opus's Brier = 0.099 vs market's 0.165 (paired n=299,
DM-style t = -8.96, p < 10⁻⁴), Sharpe 4.44, NAV $1,000 → $27,609. **Honest
caveats from Steps 9-16 force a forward-only follow-up:**

| Step | Concern | Why forward-only fixes it |
|------|---------|---------------------------|
| 10 | Asymmetric Feb→Mar Brier degradation (opus 0.082 → 0.106 vs market 0.114 → 0.197) hints at partial Jan-cutoff leakage. | A market that closes **after** the prediction is necessarily future-information w.r.t. Opus 4.7's January 2026 training cutoff. |
| 11 | 82.9% of P&L from sports — single-category dependency. | Forward sample drawn cross-category at registration time, no post-hoc filter. |
| 14 | Non-sports n_paired=77, p=0.097 fails 5% gate. | Forward sample lets us pre-commit non-sports sub-cohort size before observing outcomes. |
| 15 | Tail bins (n<10) unstable; mid-range bins 2/3/6 miscalibrated. | Forward run validates Step 15's bin-quality classification on never-seen data. |
| 16 | In-sample reliability map → +5.5% NAV vs flat 5% — must verify out-of-sample. | Forward run uses Step 15's bin-aware factors **frozen**; performance proves the rule, not the cohort. |

**This protocol's job: structurally eliminate leakage, lock all decisions
*before* outcomes are observable, and pre-register the success criteria.**

---

## 2. Cohort prescription

### 2.1 Universe

* Source: same Polymarket open-markets feed used by the existing pipeline.
* Filter:
  * `closedTime ≥ register_at + 24h` (anti-staleness)
  * `closedTime ≤ register_at + 30d` (bounded sample-collection horizon)
  * `category ∈ {sports, crypto, politics, finance, entertainment, tech_ai,
    other}` — all 7 buckets, no exclusion.
  * binary YES/NO only (matches v1 cohort schema)
  * market_p_at_register ∈ [0.02, 0.98] (drop degenerate priors that defeat
    Kelly math)
* No look-ahead: the cohort is sealed at `freeze_at` and the file's sha256 is
  written into `freeze.json` BEFORE any prediction is requested from Opus.

### 2.2 Target sizes

| Field | Value | Justification |
|-------|-------|---------------|
| `markets_needed_total` | **300** | Roughly matches v1 paired n=299 → Brier DM-test power preserved. |
| `non_sports_floor` | **90** | n_paired=77 was the failure point in Step 14 (p=0.097). 90 paired non-sports → power to detect Brier Δ≈0.03 at α=0.05. |
| `min_per_category` | **15** | Pre-commit cross-category breadth, avoids accidental sports overweight. |
| `bin_0_floor` | **50** | Bin [0.0,0.1) was 60% of P&L in Step 11/15. Ensure forward sample exercises it. |
| `bin_5_8_floor` | **30** | Mid/high bins (0.5-0.8) were the historic loss sources — need power to invalidate them. |

Soft floors: if the universe doesn't supply them by `freeze_at`, document the
deficit in `cohort_meta.json` rather than padding with synthetic markets.

### 2.3 Sample-size target & power calc

Using Step 9's observed DM-test SE = 0.00738 from n_paired=299 with mean loss
diff μ = -0.066:
* For n_paired = 300, expected SE ≈ 0.0074 → expected t ≈ -8.9 if effect holds
  → trivially significant.
* **Failure detectability:** if the true forward effect is half the historic
  effect (μ_forward = -0.033) and noise stays the same, t ≈ -4.5 → still
  passes the 5% gate but flags effect-size degradation. Anything weaker
  (μ_forward > -0.015) is the no-edge null.
* Non-sports floor n=90 with same SE proportion → SE ≈ 0.0135. To pass at α=0.05
  needs |μ_nonsports| ≥ 0.027.

---

## 3. Timeline

All timestamps UTC.

| Phase | Label | Timestamp | What happens |
|-------|-------|-----------|--------------|
| T₀ | `register_at` | **2026-05-30T00:00:00Z** | Universe snapshot taken. Cohort candidate list sealed. |
| T₁ | `predict_at` | **2026-05-30T00:00:00Z → 2026-05-30T12:00:00Z** | Opus 4.7 issues p_yes for every candidate, NO market_p_at_register revealed to model in prompt. |
| T₂ | `freeze_at` | **2026-05-30T12:30:00Z** | `cohort.jsonl + predictions.jsonl + baseline_prices.jsonl + freeze.json` sealed and git-witnessed. SHA256-locked. |
| T₃ | `evaluate_at` | **2026-06-29T00:00:00Z** | 30 days post-register. Outcomes (`y`) pulled for all markets whose `closedTime ≤ evaluate_at`. Markets still open by then → `pending`, excluded from evaluation cohort but retained for next-window analysis. |
| T₄ | `report_at` | **2026-06-29T12:00:00Z** | `oos_forward_results.json` generated, sha256-anchored as Addendum v4. |

### Structural anti-leakage invariants (each enforced by code, not vibes)

1. **`closedTime ≥ register_at + 24h`** — the freeze script `assert`s this for every cohort row. Any market whose close-time is within 24h of register is rejected at cohort-build time.
2. **Opus prediction prompt MUST NOT include `market_p_at_register`** — bidding price is sealed in `baseline_prices.jsonl` AFTER prediction, but the prompt template only sees `slug + question + closedTime`. Code path: `code/research/predict_oos.py` (to be written), with a unit test that greps the rendered prompt for any "0." substring that's not the closedTime.
3. **`freeze.json` written BEFORE outcomes exist** — `evaluate_at - register_at = 30d`, so by definition `y` cannot be in the cohort file at freeze. A separate `outcomes.jsonl` is written at `evaluate_at` and sha256-anchored as the v4 addendum.
4. **No model update / re-prompt allowed between T₂ and T₃.** Git history must show zero commits to `predictions.jsonl` after `freeze_at`. CI check: `git log --diff-filter=M predictions.jsonl` must have exactly one entry (the freeze commit).

---

## 4. Sizing policy (locked at T₂)

The forward run **does not re-tune**. Two parallel sims are scored:

### 4.1 Baseline arm — flat 5% Kelly
Identical to v1: `stake_frac = min(0.5*|edge|/min(p_m,1-p_m), 0.05)` with 2%
fee + 1% slip. EDGE_MIN = 0.05.

### 4.2 Treatment arm — bin-aware Kelly (Step 16 rule, frozen)
Reliability map from `calibration_deepdive.json` (sha256
`<filled at T₂>`):
* Bin idx → factor: `{0:1.0, 1:1.0, 2:0.0, 3:0.0, 4:1.0, 5:1.0, 6:0.5, 7:0.0,
  8:0.5, 9:0.5}` (locked at v1 cohort values)
* `stake_frac = min(0.5*|edge|/min(p_m,1-p_m), 0.05) * factor[bin_idx]`
* Same EDGE_MIN, FEE, SLIPPAGE as baseline.

**Locked-not-adapted:** even if forward Bin 5 turns out miscalibrated, we do
NOT update mid-run. This is the OOS test of the v1 reliability map.

### 4.3 Pre-registered success criteria

PASS if **ALL** of:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| `dm_test.p_two_sided_normal` (Opus vs Market on all paired forward markets) | < 0.05 | Standard significance gate. |
| `nonsports.dm_test.p_two_sided_normal` (n≥90 floor) | < 0.10 | Honest acknowledgment Step 14 was at p=0.097; we want the forward to clear at 10% with the larger sample. |
| `bin_aware.final_nav` | ≥ 0.95 × `baseline_flat_5pct.final_nav` | Treatment arm must not collapse OOS. Allow 5% slack — variance reduction is the goal, not NAV maxing. |
| `bin_aware.max_drawdown` | ≤ `baseline_flat_5pct.max_drawdown` × 1.10 | Forward DD may inflate but not blow out. |
| `outcomes_resolved_count` | ≥ 200 | Power floor. If <200 markets close by `evaluate_at`, evaluation deferred to next 15-day window, ONE TIME, then we accept whatever sample exists. |

FAIL ⇒ honest write-up of which thresholds missed and by how much. No
silent re-spec. **Failure is a publishable result** (paper-only) and informs
whether v1 was lucky or real.

---

## 5. Infrastructure

* **Code to write** (Step 17 itself is just this doc; the scripts are Step 19+):
  * `code/research/build_oos_cohort.py` — universe snapshot + filters at T₀
  * `code/research/predict_oos.py` — Opus 4.7 batch predictor (prompt template
    sees no price)
  * `code/research/freeze_oos.py` — assembles cohort+pred+baseline files,
    writes `freeze.json`, requires git push to anchor commit
  * `code/research/evaluate_oos.py` — at T₃ pulls outcomes, runs both sims,
    writes `oos_forward_results.json`
* **Storage:** `data/research/backtests/oos_forward_2026-05-30/` (new dir)
* **Witness:** new freeze.json with `git_witness_oos_forward_v1` block
* **PC routing:** outcomes-fetch + sims run on Oracle (~390 markets, no GPU
  needed). Predictions step uses Anthropic API directly from Oracle (existing
  Opus 4.7 access).
* **Cost:** ~$0.50–$1.50 in Anthropic tokens for 300 predictions. No new paid
  services (G2 satisfied).

---

## 6. What this protocol does NOT solve

* Polymarket itself may have post-cutoff information *built into the price*
  Opus sees only the question text, but the market_p_at_register reflects
  trader information up to T₀. So Opus's edge over market_p_at_register is
  still measured **conditional on Polymarket's information set at T₀**. This
  is fine and intentional — we're measuring Opus-vs-Market, not Opus-vs-truth.
* If Polymarket markets close suspiciously consistently with model expectation
  (manipulation, late-stage info shocks), forward Brier can still mislead.
  Mitigation: future Step 20+ adds OOS-OOS-OOS by holding out a buffer past
  `evaluate_at` for a second cohort.
* Paper-only constraint persists. Even if forward PASSES, this does NOT
  authorize live capital; that decision goes through `debate --critique`
  per global rule.

---

## 7. Pre-registration checksum

When `freeze_at` fires, the sha256 of THIS file is anchored in the new
`freeze.json` under `pre_registered_protocol_sha256`. Any change to success
thresholds after T₂ breaks the chain. The chain is the integrity proof.

This doc's sha256 will be written into the next addendum (Step 18).
