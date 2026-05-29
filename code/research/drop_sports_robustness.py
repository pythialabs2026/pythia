"""Step 14 — Drop-1 robustness: exclude sports category, re-run everything.

Step 11 found 82.9% of P&L comes from sports. This script asks: if we
remove sports entirely, is there any alpha left? Or is Opus → market a
sports-only edge dressed up as generalist forecasting?

We re-classify cohort with the same regex as category_breakdown.py,
drop sports markets, then recompute:
  - Brier metrics (opus vs market) on the remaining cohort
  - DM-style paired test on the remaining records
  - Virtual P&L using identical sim parameters (5% cap, 2% fee, 1% slip)
  - Sharpe + Max DD on the resulting NAV curve

Output: data/research/backtests/cutoff_clean_2026-05-29/drop_sports_robustness.json
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path("/home/ubuntu/pythia/code/research")))
from category_breakdown import classify  # reuse classifier

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = DIR / "cohort.jsonl"
PRED = DIR / "predictions.jsonl"
BASE = DIR / "baseline_prices.jsonl"
BRIER = DIR / "brier_scores.jsonl"
OUT = DIR / "drop_sports_robustness.json"

BANKROLL_0 = 1000.0
EDGE_MIN = 0.05
SLIPPAGE = 0.01
FEE = 0.02
STAKE_CAP = 0.05


def _parse_close(ts: str):
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts)


def _brier_block(rows: list[dict]) -> dict:
    n = len(rows)
    paired = [r for r in rows if r["brier_market"] is not None]
    n_p = len(paired)
    if not rows:
        return {"n_total": 0, "n_paired": 0}
    mean_opus = statistics.mean(r["brier_opus"] for r in rows)
    mean_naive = statistics.mean(r["brier_naive"] for r in rows)
    mean_opus_p = statistics.mean(r["brier_opus"] for r in paired) if paired else None
    mean_mkt_p = statistics.mean(r["brier_market"] for r in paired) if paired else None
    opus_wins = sum(1 for r in paired if r["brier_opus"] < r["brier_market"]) if paired else 0

    if n_p >= 2:
        d = [r["brier_opus"] - r["brier_market"] for r in paired]
        mu = statistics.mean(d)
        sd = statistics.stdev(d)
        se = sd / math.sqrt(n_p) if sd > 0 else None
        t = mu / se if se else None
        p_norm = math.erfc(abs(t) / math.sqrt(2.0)) if t is not None else None
        ci_lo = mu - 1.96 * (se or 0); ci_hi = mu + 1.96 * (se or 0)
    else:
        mu = sd = t = p_norm = None; ci_lo = ci_hi = None

    return {
        "n_total":            n,
        "n_paired":           n_p,
        "brier_opus_all":     round(mean_opus, 6),
        "brier_naive_all":    round(mean_naive, 6),
        "brier_opus_paired":  round(mean_opus_p, 6) if mean_opus_p else None,
        "brier_market_paired":round(mean_mkt_p, 6) if mean_mkt_p else None,
        "opus_win_rate":      round(opus_wins / n_p, 4) if n_p else None,
        "mean_loss_diff":     round(mu, 6) if mu is not None else None,
        "stdev_loss_diff":    round(sd, 6) if sd is not None else None,
        "t_statistic":        round(t, 4) if t is not None else None,
        "p_two_sided_normal": round(p_norm, 6) if p_norm is not None else None,
        "ci_95_loss_diff":    [round(ci_lo, 6), round(ci_hi, 6)] if ci_lo is not None else None,
    }


def _simulate(rows: list[dict]) -> dict:
    bankroll = BANKROLL_0
    nav_log = []
    last_day = None; prev_nav = BANKROLL_0
    nbets = 0; nwins = 0; total_stake = 0.0; total_net = 0.0
    for r in rows:
        if r["p_market"] is None:
            continue
        edge = r["p_opus"] - r["p_market"]
        if abs(edge) < EDGE_MIN:
            continue
        side = "YES" if edge > 0 else "NO"
        m = r["p_market"]
        denom = min(m, 1.0 - m)
        if denom <= 1e-9: continue
        kelly = 0.5 * abs(edge) / denom
        stake_frac = min(kelly, STAKE_CAP)
        stake = bankroll * stake_frac
        if stake <= 0: continue
        if side == "YES":
            entry = min(m * (1 + SLIPPAGE), 0.999)
            shares = stake / entry
            payout = shares * r["y"]
        else:
            entry = min((1 - m) * (1 + SLIPPAGE), 0.999)
            shares = stake / entry
            payout = shares * (1 - r["y"])
        fee = stake * FEE
        net = payout - stake - fee
        bankroll += net
        nbets += 1; total_stake += stake; total_net += net
        if net > 0: nwins += 1
        day = r["closed_dt"].date().isoformat()
        if last_day is not None and day != last_day:
            nav_log.append((last_day, prev_nav))
        prev_nav = bankroll
        last_day = day
    if last_day is not None:
        nav_log.append((last_day, prev_nav))

    if nav_log:
        navs = [BANKROLL_0] + [v for _, v in nav_log]
        rets = [navs[i] / navs[i-1] - 1.0 for i in range(1, len(navs)) if navs[i-1] > 0]
        if len(rets) >= 2:
            mu = statistics.mean(rets); sd = statistics.stdev(rets)
            sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else None
        else:
            sharpe = None
        peak = navs[0]; max_dd = 0.0
        for v in navs:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
    else:
        sharpe = None; max_dd = 0.0

    return {
        "n_bets": nbets,
        "n_winners": nwins,
        "win_rate": round(nwins / nbets, 4) if nbets else None,
        "total_stake": round(total_stake, 2),
        "total_net_pnl": round(total_net, 2),
        "final_nav": round(bankroll, 2),
        "roi": round(bankroll / BANKROLL_0 - 1.0, 6),
        "sharpe_daily": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 4),
    }


def main() -> int:
    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open())}
    preds  = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open())}
    base   = {str(r["market_id"]): r for r in (json.loads(l) for l in BASE.open())}
    brier  = [json.loads(l) for l in BRIER.open()]

    # tag brier with category
    cat_map = {mid: classify(c["slug"], c["question"]) for mid, c in cohort.items()}

    full_brier      = brier
    nonsports_brier = [r for r in brier if cat_map[str(r["market_id"])] != "sports"]
    sports_brier    = [r for r in brier if cat_map[str(r["market_id"])] == "sports"]

    # P&L sim rows for full and nonsports
    def _rows(filter_cat: str | None):
        out = []
        for mid, c in cohort.items():
            cat = cat_map[mid]
            if filter_cat == "exclude_sports" and cat == "sports":
                continue
            if filter_cat == "sports_only" and cat != "sports":
                continue
            out.append({
                "y": c["y"],
                "p_opus": preds[mid]["p_yes"],
                "p_market": base[mid]["market_p_at_freeze"],
                "closed_dt": _parse_close(c["closedTime"]),
            })
        out.sort(key=lambda r: r["closed_dt"])
        return out

    sim_full       = _simulate(_rows(None))
    sim_nonsports  = _simulate(_rows("exclude_sports"))
    sim_sportsonly = _simulate(_rows("sports_only"))

    brier_full       = _brier_block(full_brier)
    brier_nonsports  = _brier_block(nonsports_brier)
    brier_sportsonly = _brier_block(sports_brier)

    # alpha decomposition
    full_pnl = sim_full["total_net_pnl"]
    sports_only_pnl = sim_sportsonly["total_net_pnl"]
    nonsports_pnl   = sim_nonsports["total_net_pnl"]
    sports_pnl_share = (sports_only_pnl / full_pnl) if full_pnl else None
    sports_brier_advantage = (
        (brier_full["brier_market_paired"] or 0) - (brier_full["brier_opus_paired"] or 0)
    )
    nonsports_brier_advantage = (
        (brier_nonsports["brier_market_paired"] or 0) - (brier_nonsports["brier_opus_paired"] or 0)
    )

    survives_drop_sports = (
        brier_nonsports.get("p_two_sided_normal") is not None
        and brier_nonsports["p_two_sided_normal"] < 0.05
        and nonsports_brier_advantage > 0
        and nonsports_pnl > 0
    )

    result = {
        "experiment": "cutoff_clean_2026-05-29",
        "analysis": "drop_sports_robustness",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sim_params": {
            "bankroll_0": BANKROLL_0,
            "edge_threshold": EDGE_MIN,
            "stake_cap": STAKE_CAP,
            "fee": FEE,
            "slippage": SLIPPAGE,
        },
        "brier_blocks": {
            "full":       brier_full,
            "nonsports":  brier_nonsports,
            "sports_only": brier_sportsonly,
        },
        "pnl_blocks": {
            "full":       sim_full,
            "nonsports":  sim_nonsports,
            "sports_only": sim_sportsonly,
        },
        "robustness_check": {
            "sports_pnl_share":            round(sports_pnl_share, 4) if sports_pnl_share is not None else None,
            "full_brier_advantage":        round(sports_brier_advantage, 6),
            "nonsports_brier_advantage":   round(nonsports_brier_advantage, 6),
            "edge_survives_dropping_sports": survives_drop_sports,
            "interpretation": (
                "Excluding sports markets: "
                f"n_paired={brier_nonsports['n_paired']}, "
                f"Δ(market-opus) Brier={nonsports_brier_advantage:+.4f}, "
                f"P&L=${nonsports_pnl}, "
                f"t={brier_nonsports.get('t_statistic')}, "
                f"p={brier_nonsports.get('p_two_sided_normal')}. "
                + ("Alpha SURVIVES drop-sports test." if survives_drop_sports
                   else "Alpha does NOT survive — strategy is essentially sports-only.")
            ),
        },
        "caveats": [
            "Drop-1 is the harshest robustness test for category concentration — passing it is strong evidence of generalist edge; failing means the headline numbers come almost entirely from sports.",
            "Even if non-sports survives, sample size shrinks (sports was the largest category) → statistical power drops.",
            "Bet count also drops — non-sports bets may have been fewer per-market than sports.",
            "Sharpe on a tiny NAV curve is noisy; treat sub-cohort Sharpe with caution.",
        ],
    }

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
