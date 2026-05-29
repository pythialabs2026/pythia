# OOS Forward-Paper Protocol — Amendment v2 (immediate-start register + realized universe)

**Status:** pre-registration amendment, sealed BEFORE freeze and BEFORE any `p_yes` is issued.
**Amends:** `oos_forward_protocol.md` (sha256 `64105914d287d94ee1ced9dfa28655cbdd0ed8b00f103e51bce758cb1c2da384`),
anchored in the cutoff_clean freeze chain (artifact `oos_forward_protocol_v3`, git_witness_v3 commit `4a147c4`).
**Inherits Amendment v1** (sha256 `8cbf4c996ead469dec99359116ca2c3f17d3330cf1254ba0ab47329375297f69`):
predictor model = `claude-opus-4-8`. That swap still stands; this amendment changes nothing about it.
**The sealed protocol doc and Amendment v1 are NOT edited** — editing either would break the chain.
This file is the auditable record of the changes below, satisfying §7 ("no silent re-spec").

---

## 1. The changes

| # | Field | Sealed protocol (v3) | This amendment (v2) |
|---|-------|----------------------|---------------------|
| 1 | `register_at` (T₀) | `2026-05-30T00:00:00Z` (next-day scheduled) | **`2026-05-29T07:00:00Z`** (immediate, current-time start) |
| 2 | `market_p_at_register` band (§2.1) | `[0.02, 0.98]` | **`[0.01, 0.99]`** |
| 3 | Gamma harvest pagination | `PAGE_LIMIT = 500`, `--max-pages 30` | **`PAGE_LIMIT = 100`, `--max-pages 120`** (bugfix) |
| 4 | Cohort selection | implicit "take all qualifying" (assumed ≈ target) | **deterministic round-robin stratified sample, cap = 300** (new, pre-committed) |
| 5 | Admitted market shape | (unspecified beyond §2.1 binary filter) | **Yes/No binary only** (non-Yes/No 2-outcome explicitly deferred — see §3) |

Unchanged and inherited verbatim: `evaluate_at = 2026-06-29T00:00:00Z`, the four T-stage invariants
(§3), Kelly / bin-aware sizing (§4.1–4.2), the five PASS gates (§4.3), storage/cost (§5),
the PASS→debate→Pinata gate (§6), and the soft floors total/non_sports/per-category & bin (§2.2).
`evaluate_at` remains valid: new `CLOSE_MAX = register_at + 30d = 2026-06-28T07:00:00Z` ≤ `evaluate_at`,
so every cohort market is scheduled to resolve before evaluation.

Implemented in `code/research/build_oos_cohort.py` (`REGISTER_AT`, `P_LO/P_HI`, `PAGE_LIMIT`,
`--max-pages` default, and the new `_stratified_sample` round-robin selector) and mirrored in
`code/research/freeze_oos.py` (`REGISTER_AT`, plus this file recorded as a sealed artifact with
`protocol_amendment_v2_sha256`).

## 2. Authority

- User directive 2026-05-29: **"지금 시간 기준으로 진행 바로 forward oos 들어가자"** — start the forward
  OOS *now*, anchored to the current time, rather than waiting for the scheduled next-day fire.
  This motivates change #1 (and retires the one-shot register cron `run_oos_register.sh`).
- User directive 2026-05-29: **"실제로 우리가 학습해서 우위를 얻을 수 있고 실제로 가능성 있는 모든 분야를
  하자"** + AskUserQuestion answer **"멀티옵션 시장도 포함"** — broaden the cohort across *all feasible*
  categories/options where an edge is actually learnable. This motivates changes #3–#5.

## 3. Realized universe, the pagination bugfix, and the Yes/No-only scope decision

**The bug.** The harvester requested `limit=500` per page, but the Gamma `/markets` feed returns
at most ~100 rows per request regardless of `limit`. With `PAGE_LIMIT = 500`, the loop's
`if len(data) < PAGE_LIMIT: break` fired after page 0 (100 < 500), so only ~100 raw markets were
ever seen and only **14** qualified — a degenerate cohort, not the breadth the protocol intended.
Fix: `PAGE_LIMIT = 100` (the true page size) + paginate by `offset` + break only on an empty/short
page; `--max-pages` raised to 120 (offset hard-caps near ~10,100 with HTTP 422, harmless).

**The realized universe (measured 2026-05-29, register-time snapshot).**
- **9,950** unique open markets; outcome-count histogram = `{2: 9950}` — *every* open market is
  2-outcome. There are **no** true >2-outcome single markets on the feed.
- Multi-outcome **events** (e.g. "who wins X?") are represented as a set of **2-outcome Yes/No
  sub-markets**, one per candidate. So multi-outcome events already enter the cohort through their
  Yes/No legs — the user's "멀티옵션 포함" intent is satisfied without a multi-class rebuild.
- Qualifying (close in 24h–30d window, price[yes] ∈ [0.01,0.99]): **1,937 Yes/No** markets, across
  all 7 categories (other / sports / politics / crypto / tech_ai / finance / entertainment). A
  further **~536** non-Yes/No 2-outcome markets (Over/Under, Odd/Even, team-A-vs-team-B,
  spread/handicap) also pass the price/date filters → 2,473 any-2-outcome.

**Decision: admit Yes/No binary markets only this round; defer the 536 non-Yes/No.** Rationale,
recorded for audit:
1. **Intent already honored.** Multi-outcome events enter via their Yes/No sub-markets; the
   1,937-market pool spans all 7 categories. The pagination fix (14 → 1,937) *is* the "모든 분야"
   breadth win the user asked for.
2. **The 536 add little domain breadth.** They are overwhelmingly sports/esports
   (Over/Under, team-vs-team, spread). Sports is precisely the category whose over-dependence we are
   stress-testing (Drop-1, step 14); padding the cohort with more sports duplicates that signal
   rather than diversifying it.
3. **Settlement integrity (decisive).** `evaluate_oos.py` resolves outcomes *strictly* by the
   literal "yes" label (`_yes_index` → `_fetch_outcome`); a non-Yes/No market yields `yi is None`
   → status "pending" → silently excluded *at evaluation*. Admitting them properly would require
   generalizing the trust-critical settlement surface **and** rendering decimal outcome labels
   ("Over 0.5") into the predict prompt, which collides with the price-leak guard
   (`_PRICE_LEAK_RE = \b0\.\d`). Doing that under the leakage-clean 24h start window is a shortcut on
   an irreversible surface — refused on principle (신뢰-결정적; 우회 금지).
4. **Feasibility qualifier.** The user's own "실제로 가능성 있는 (feasible)" wording favors tractable
   breadth over breadth that endangers the evaluator. Non-Yes/No admission is deferred to a future
   round behind a proper generalization of the settlement + label-rendering logic.

**Change #4 — why a sampler is now needed.** Pre-fix, "take all qualifying" yielded 14, so no
selection rule was needed and none was specified. Post-fix it yields 1,937 — far above the §2.2
design target (total ≈ 300). The freeze HARD coverage invariant requires *every* cohort market be
predicted exactly once by the brain (Opus 4.8); 1,937 interactive predictions is infeasible within
the register+24h freeze deadline. The protocol's evident design intent is a ~300-market cohort
(its soft floors). We therefore draw a **deterministic, pre-committed** sample of 300:

> **`_stratified_sample` (round-robin):** partition the qualifying pool by category; within each
> category sort by `(volume_at_register desc, id asc)`; then draw round-robin across categories (in
> a fixed category order) one market at a time until 300 are selected or the pool is exhausted.

This maximizes cross-category diversity (every category gets equal turns → small categories are
fully included, sports cannot dominate), guarantees the §2.2 per-category and non_sports floors by
construction (with 300 slots / 7 categories ≈ 42 each, non_sports ≈ 257 ≫ 90), and is fully
deterministic.

## 4. Why every change is leakage-safe

The sealed protocol's anti-leakage guarantee is **structural**, not parameter-dependent:

> A market that resolves *after* `register_at` is, by construction, future information w.r.t. the
> training cutoff of any model that exists at `register_at` — the resolving event has not happened
> and cannot appear in any prior training corpus.

- **#1 register_at → earlier (2026-05-29T07:00:00Z).** Moving T₀ *earlier* only tightens the test:
  predictions are still issued strictly *after* the price snapshot and *before* each market's close
  (HARD invariant 1: `scheduled_close ≥ register_at + 24h`, enforced per-row at freeze). A later
  resolution relative to an earlier-or-equal model cutoff *widens* the post-cutoff margin. No leak.
- **#2 price band widening.** Admits a few more near-degenerate-priced markets; affects which
  markets qualify, not whether outcomes are known. Selection precedes resolution. No leak.
- **#3 pagination fix.** Pure data-completeness bugfix; it changes how many of the *already-open*
  markets we see, nothing about timing. No leak.
- **#4 stratified sampler.** Selection rule fixed and pre-committed in this amendment *before* any
  outcome is observable; deterministic (no outcome-conditioned choice, no p-hacking). Markets are
  all open and resolve in the future. No leak.
- **#5 Yes/No-only.** A *restriction* of the admitted set; restricting cannot introduce
  future-information.

All five changes are pre-freeze and individually reversible until the sha-anchored freeze; the only
irreversibility is the freeze itself, which is the intended pre-registration act.

## 5. Directory label note

The artifact directory is named `oos_forward_2026-05-30/` (the *original* scheduled register date).
With change #1, the actual `register_at` is `2026-05-29T07:00:00Z`. The directory name is retained
as a stable path label to avoid churn across already-written tooling (`freeze_oos.py`,
`evaluate_oos.py`, this amendment chain); **the authoritative `register_at` is the value in this
amendment and in `freeze.json`/`cohort_meta.json`, not the directory name.**

## 6. Integrity / chain

- Sealed protocol doc: **unedited**, sha `64105914…` still verifies.
- Amendment v1: **unedited**, sha `8cbf4c99…` still verifies; predictor stays `claude-opus-4-8`.
- This amendment is sealed as its own artifact in `oos_forward_2026-05-30/freeze.json`, with its
  sha256 recorded as `protocol_amendment_v2_sha256`. `freeze_oos.py._check_protocol_sha()` verifies
  all three (protocol, v1, v2); any drift aborts the seal (exit 3).
- Any future change (admitting non-Yes/No markets, altering the sampler, moving dates) requires a
  new amendment (v3, …) under the same rule — never an edit to the sealed protocol or to v1/v2.
