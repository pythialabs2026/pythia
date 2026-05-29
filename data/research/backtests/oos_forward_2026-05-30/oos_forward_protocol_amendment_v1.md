# OOS Forward-Paper Protocol — Amendment v1 (predictor model)

**Status:** pre-registration amendment, sealed BEFORE freeze (T₂ = 2026-05-30T12:30:00Z).
**Amends:** `oos_forward_protocol.md` (sha256 `64105914d287d94ee1ced9dfa28655cbdd0ed8b00f103e51bce758cb1c2da384`),
which is itself anchored in the cutoff_clean backtest freeze chain
(artifact `oos_forward_protocol_v3`, git_witness_v3 commit `4a147c4`).
**The sealed protocol doc is NOT edited** — doing so would break that anchor. This
amendment is the auditable record of the one change, satisfying §7 ("no silent re-spec").

---

## 1. The change

| Field | Sealed protocol | This amendment |
|-------|-----------------|----------------|
| Predictor model | `claude-opus-4-7` (prose: §Step-10 row, T1 §3, §5) | **`claude-opus-4-8`** |

Implemented as `MODEL_ID = "claude-opus-4-8"` in `code/research/predict_oos.py`.
**Nothing else changes.** Cohort filters (§2.1), soft floors (§2.2), timeline T0–T4
(§3), the four invariants (§3), Kelly/bin-aware sizing (§4.1–4.2), the five PASS
gates (§4.3), storage/cost (§5), and the PASS→debate→Pinata gate (§6) are inherited
verbatim from sha `64105914…`.

## 2. Authority

- User directive 2026-05-29: "4.8로 먼저 바꾸고 진행해" (switch to 4.8 first, then proceed).
- Global policy (CLAUDE.md, 2026-05-29): Opus 4.8 is the mandated default model
  (`max20x-model-policy`). The forward test should validate the model that will
  actually be used in production, not a superseded one.

## 3. Why the swap is leakage-safe (in fact, strengthened)

The sealed protocol's anti-leakage guarantee does **not** depend on any specific
model's cutoff date. Its structural argument is:

> A market whose outcome resolves *after* `register_at` (2026-05-30T00:00:00Z) is,
> by construction, future information with respect to the training cutoff of **any
> model that already exists on that date** — because the resolving event has not
> happened yet and therefore cannot appear in any pre-2026-05-30 training corpus.

Opus 4.8 exists on 2026-05-29 (the day this amendment is written). Every cohort
market closes ≥ `register_at + 24h` (HARD invariant 1, enforced at freeze) and
resolves during 2026-05-31 → 2026-06-29. So for 4.8 — exactly as for 4.7 — the
realized outcomes are strictly post-cutoff. The forward design eliminates
cutoff-leakage **structurally**, independent of which current model issues `p_yes`.
The mention of "January 2026 cutoff" in the sealed doc was 4.7-specific *explanation*;
the binding guarantee is the future-resolution argument above, which holds for 4.8
without modification.

There is no path by which moving 4.7 → 4.8 *introduces* leakage: a newer model has,
if anything, a later cutoff, which only widens the post-cutoff safety margin for a
fixed future resolution window. The swap therefore **strengthens** the guarantee.

## 4. Integrity / chain

- Original protocol doc: **unedited**, sha `64105914…` still verifies; `freeze_oos.py`
  `_check_protocol_sha()` still passes (no chain break).
- This amendment is sealed as its own artifact in the new forward `freeze.json`
  (`oos_forward_2026-05-30/freeze.json`), with its sha256 recorded as
  `protocol_amendment_v1_sha256` and `predictor_model = "claude-opus-4-8"`.
- Any future change to the predictor model requires a new amendment (v2, …) under
  the same rule — never an edit to the sealed protocol or to this v1 file.
