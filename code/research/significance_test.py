"""Step 9 — Paired Brier significance test (Diebold-Mariano-style).

For paired records (brier_opus, brier_market), compute the loss differential
  d_i = brier_opus_i - brier_market_i
Under H0: E[d]=0 (equal accuracy). H1: E[d]≠0.

DM statistic for one-step horizon with no autocorrelation assumption:
  t = mean(d) / (s_d / sqrt(n))
where s_d = sample stdev. For large n, t ~ N(0,1); we report both the
two-sided normal p-value and the conservative t(n-1) p-value.

We also report the 95% CI on mean(d), and a paired sign test (binomial)
as a non-parametric robustness check.

Output: data/research/backtests/cutoff_clean_2026-05-29/significance_test.json
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
BRIER = DIR / "brier_scores.jsonl"
OUT = DIR / "significance_test.json"


def _norm_sf(z: float) -> float:
    """Two-sided survival, P(|Z| > |z|) for Z ~ N(0,1)."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def _t_sf_approx(t: float, df: int) -> float:
    """Two-sided p-value approx for Student's t via incomplete beta.

    Uses regularized incomplete beta: P(|T|>|t|) = I_x(df/2, 1/2)
    with x = df / (df + t^2). For large df, this collapses to normal.
    Implementation via Lentz continued fraction for I_x(a,b).
    """
    a = df / 2.0
    b = 0.5
    x = df / (df + t * t)
    # Symmetric: use the standard relation.
    # I_x(a,b) = x^a (1-x)^b / (a B(a,b)) * cf
    # Use log B(a,b) = lgamma(a)+lgamma(b)-lgamma(a+b)
    log_bt = (a * math.log(x) + b * math.log(1 - x)
              - math.log(a) - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)))
    bt = math.exp(log_bt)
    # Lentz CF for the regularized incomplete beta tail
    # Use symmetry if x > (a+1)/(a+b+2)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-12) -> float:
    qab = a + b
    qap = a + 1
    qam = a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30: d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30: c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30: d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30: c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            return h
    return h


def _binom_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial test p-value (sum of probs ≤ that of k)."""
    # log-binom pmf
    def logpmf(x):
        return (math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1)
                + x * math.log(p) + (n - x) * math.log(1 - p))
    target = logpmf(k)
    total = 0.0
    for x in range(0, n + 1):
        lp = logpmf(x)
        if lp <= target + 1e-12:
            total += math.exp(lp)
    return min(total, 1.0)


def main() -> int:
    rows = [json.loads(l) for l in BRIER.open()]
    paired = [r for r in rows if r["brier_market"] is not None]
    n = len(paired)
    d = [r["brier_opus"] - r["brier_market"] for r in paired]

    mean_d = statistics.mean(d)
    sd_d = statistics.stdev(d)
    se_d = sd_d / math.sqrt(n)

    t_stat = mean_d / se_d
    p_norm = _norm_sf(t_stat)
    p_t = _t_sf_approx(t_stat, n - 1)

    # 95% CI (normal approximation, large n)
    ci_z = 1.96
    ci_lo = mean_d - ci_z * se_d
    ci_hi = mean_d + ci_z * se_d

    # Paired sign test: count of opus-better records
    opus_better = sum(1 for x in d if x < 0)
    sign_p = _binom_two_sided(opus_better, n)

    # Interpretation
    direction = "opus_better" if mean_d < 0 else "market_better"
    sig_05 = p_norm < 0.05
    sig_01 = p_norm < 0.01

    result = {
        "experiment": "cutoff_clean_2026-05-29",
        "test": "diebold_mariano_paired_brier",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_paired": n,
        "loss_differential": {
            "definition": "d_i = brier_opus_i - brier_market_i  (negative ⇒ opus better)",
            "mean_d":   round(mean_d, 6),
            "stdev_d":  round(sd_d, 6),
            "se_d":     round(se_d, 6),
            "ci_95_normal": [round(ci_lo, 6), round(ci_hi, 6)],
        },
        "dm_test": {
            "t_statistic": round(t_stat, 4),
            "df_for_t":    n - 1,
            "p_two_sided_normal":   round(p_norm, 6),
            "p_two_sided_t_approx": round(p_t, 6),
            "significant_at_0_05": sig_05,
            "significant_at_0_01": sig_01,
            "direction": direction,
        },
        "sign_test": {
            "opus_better_count": opus_better,
            "market_better_count": n - opus_better,
            "p_two_sided_exact_binomial": round(sign_p, 6),
        },
        "interpretation": (
            f"Mean(d)={mean_d:.4f} with SE={se_d:.4f}, t={t_stat:.2f} on df={n-1}. "
            f"Two-sided normal p={p_norm:.2e}. "
            f"{'Reject' if sig_05 else 'Fail to reject'} H0 at α=0.05; "
            f"direction = {direction}. "
            f"Sign test: opus better on {opus_better}/{n}, p={sign_p:.2e}."
        ),
        "caveats": [
            "Brier loss differentials are NOT i.i.d. across markets — heterogeneity is large.",
            "DM was designed for forecast horizons; we apply it cross-sectionally. Treat p-value as a guide, not gospel.",
            "Sign test is robust to outliers but ignores magnitudes.",
            "Survivorship: only resolved markets included.",
        ],
    }

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
