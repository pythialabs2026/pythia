"""Score the cutoff-clean backtest — Brier 3-way + virtual P&L.

Inputs (all in data/research/backtests/cutoff_clean_2026-05-29/):
  - cohort.jsonl           (id, closedTime, y, final_prices, createdAt, endDate)
  - predictions.jsonl      (market_id, p_yes)
  - baseline_prices.jsonl  (market_id, market_p_at_freeze)

Outputs:
  - brier_scores.jsonl   (per-market Brier scores, all 3 sources)
  - pnl_log.jsonl        (per-bet log: side, stake, slippage, fee, settlement, NAV after)
  - daily_nav.jsonl      (calendar-day NAV samples)

Methodology (locked by methodology.md):
  - Bet trigger: |edge| >= 0.05 where edge = p_opus - p_market
  - Stake frac: 0.5 * |edge| / min(p_market, 1-p_market), capped at 5% of bankroll
  - Side: YES if edge>0 else NO
  - Slippage 1% on entry price (worsens execution)
  - Fees 2% of stake (round-trip)
  - Bankroll $1000 paper. Bets resolved in closedTime order.
  - Markets with market_p_at_freeze=null → excluded from P&L AND from market-Brier.
  - Naive Brier (0.5) and Opus Brier computed for all markets.

CLI:
  python3 code/research/score_backtest.py
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"

COHORT = DIR / "cohort.jsonl"
PRED = DIR / "predictions.jsonl"
BASELINE = DIR / "baseline_prices.jsonl"

OUT_BRIER = DIR / "brier_scores.jsonl"
OUT_PNL = DIR / "pnl_log.jsonl"
OUT_NAV = DIR / "daily_nav.jsonl"

BANKROLL_0 = 1000.0
EDGE_MIN = 0.05
STAKE_CAP = 0.05  # 5% of bankroll
SLIPPAGE = 0.01
FEE = 0.02


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open()]


def _parse_close(ts: str) -> datetime:
    # closedTime in cohort: "2026-02-02 04:03:08+00"
    if ts.endswith("+00"):
        ts = ts.replace("+00", "+00:00")
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts)


def main() -> int:
    cohort = {str(r["id"]): r for r in _load(COHORT)}
    preds = {str(r["market_id"]): r for r in _load(PRED)}
    base = {str(r["market_id"]): r for r in _load(BASELINE)}

    # join
    rows = []
    for mid, c in cohort.items():
        p_opus = preds[mid]["p_yes"]
        p_market = base[mid]["market_p_at_freeze"]
        y = c["y"]
        closed_dt = _parse_close(c["closedTime"])
        rows.append({
            "mid": mid, "slug": c["slug"], "y": y,
            "p_opus": p_opus, "p_market": p_market,
            "closed_dt": closed_dt,
        })
    rows.sort(key=lambda r: r["closed_dt"])

    # === Brier ===
    brier_records = []
    for r in rows:
        y = r["y"]
        b_opus = (r["p_opus"] - y) ** 2
        b_naive = (0.5 - y) ** 2
        b_market = (r["p_market"] - y) ** 2 if r["p_market"] is not None else None
        rec = {
            "market_id": r["mid"], "slug": r["slug"], "y": y,
            "p_opus": r["p_opus"], "p_market": r["p_market"],
            "brier_opus": round(b_opus, 6),
            "brier_market": round(b_market, 6) if b_market is not None else None,
            "brier_naive": round(b_naive, 6),
            "closed_at": r["closed_dt"].isoformat(),
        }
        brier_records.append(rec)
    with OUT_BRIER.open("w") as f:
        for rec in brier_records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    # === Virtual P&L ===
    bankroll = BANKROLL_0
    nav_log = []  # daily NAV samples
    pnl_records = []
    last_day = None
    nbets = 0; nwins = 0
    for r in rows:
        if r["p_market"] is None:
            continue
        edge = r["p_opus"] - r["p_market"]
        if abs(edge) < EDGE_MIN:
            continue
        side = "YES" if edge > 0 else "NO"
        m = r["p_market"]
        denom = min(m, 1.0 - m)
        if denom <= 1e-9:
            continue  # avoid div-by-zero on extreme markets
        stake_frac = min(0.5 * abs(edge) / denom, STAKE_CAP)
        stake = bankroll * stake_frac
        if stake <= 0:
            continue
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
        nbets += 1
        if net > 0: nwins += 1
        pnl_records.append({
            "market_id": r["mid"], "slug": r["slug"],
            "y": r["y"], "p_opus": r["p_opus"], "p_market": m,
            "edge": round(edge, 4), "side": side,
            "stake_frac": round(stake_frac, 5), "stake": round(stake, 4),
            "entry_price": round(entry, 5), "shares": round(shares, 4),
            "payout": round(payout, 4), "fee": round(fee, 4),
            "net_pnl": round(net, 4), "bankroll_after": round(bankroll, 4),
            "closed_at": r["closed_dt"].isoformat(),
        })
        day = r["closed_dt"].date().isoformat()
        if last_day is not None and day != last_day:
            nav_log.append({"date": last_day, "nav": round(prev_nav, 4)})
        prev_nav = bankroll
        last_day = day
    if last_day is not None:
        nav_log.append({"date": last_day, "nav": round(prev_nav, 4)})

    with OUT_PNL.open("w") as f:
        for rec in pnl_records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    with OUT_NAV.open("w") as f:
        for rec in nav_log:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # === Reporting ===
    n = len(rows)
    n_with_market = sum(1 for r in brier_records if r["brier_market"] is not None)
    mean_opus_all = statistics.mean(r["brier_opus"] for r in brier_records)
    mean_naive_all = statistics.mean(r["brier_naive"] for r in brier_records)
    paired = [(r["brier_opus"], r["brier_market"]) for r in brier_records if r["brier_market"] is not None]
    mean_opus_paired = statistics.mean(o for o, _ in paired)
    mean_market_paired = statistics.mean(m for _, m in paired)
    opus_wins = sum(1 for o, m in paired if o < m)
    win_rate = opus_wins / len(paired) if paired else 0.0

    # Calibration: 10 buckets on p_opus
    buckets = [[] for _ in range(10)]
    for r in brier_records:
        idx = min(int(r["p_opus"] * 10), 9)
        buckets[idx].append((r["p_opus"], r["y"]))
    cal = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            cal.append({"bin": f"[{i/10:.1f},{(i+1)/10:.1f})", "n": 0, "p_mean": None, "y_rate": None, "drift": None})
        else:
            p_mean = statistics.mean(p for p, _ in bucket)
            y_rate = statistics.mean(y for _, y in bucket)
            cal.append({"bin": f"[{i/10:.1f},{(i+1)/10:.1f})", "n": len(bucket),
                        "p_mean": round(p_mean, 4), "y_rate": round(y_rate, 4),
                        "drift": round(abs(p_mean - y_rate), 4)})
    max_drift = max((c["drift"] for c in cal if c["drift"] is not None), default=None)

    # NAV series → Sharpe + max DD
    if nav_log:
        navs = [BANKROLL_0] + [d["nav"] for d in nav_log]
        rets = []
        for i in range(1, len(navs)):
            if navs[i-1] > 0:
                rets.append(navs[i] / navs[i-1] - 1.0)
        if len(rets) >= 2:
            mu = statistics.mean(rets)
            sd = statistics.stdev(rets)
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

    print("=" * 60)
    print(f"cutoff_clean_2026-05-29  N={n}  (paired w/ market = {n_with_market})")
    print("=" * 60)
    print(f"\n[Brier — all {n} markets]")
    print(f"  opus  : {mean_opus_all:.4f}")
    print(f"  naive : {mean_naive_all:.4f}")
    print(f"\n[Brier — paired {n_with_market} markets]")
    print(f"  opus  : {mean_opus_paired:.4f}")
    print(f"  market: {mean_market_paired:.4f}")
    print(f"  opus < market on {opus_wins}/{len(paired)} = {win_rate*100:.1f}%")
    print(f"\n[Calibration buckets (max drift = {max_drift})]")
    for c in cal:
        if c["n"] == 0:
            print(f"  {c['bin']}: n=0")
        else:
            print(f"  {c['bin']}: n={c['n']:3d}  p̄={c['p_mean']:.3f}  ȳ={c['y_rate']:.3f}  drift={c['drift']:.3f}")
    print(f"\n[Virtual P&L]")
    print(f"  bets placed   : {nbets}  (winners {nwins} = {nwins/nbets*100 if nbets else 0:.1f}%)")
    print(f"  final NAV     : ${bankroll:,.2f}")
    print(f"  ROI           : {(bankroll/BANKROLL_0 - 1)*100:+.2f}%")
    print(f"  Sharpe (daily): {sharpe:.3f}" if sharpe is not None else "  Sharpe: n/a")
    print(f"  max drawdown  : {max_dd*100:.2f}%")
    print(f"\nartifacts:")
    print(f"  {OUT_BRIER.name}  sha256={_sha16(OUT_BRIER)}…")
    print(f"  {OUT_PNL.name}    sha256={_sha16(OUT_PNL)}…")
    print(f"  {OUT_NAV.name}   sha256={_sha16(OUT_NAV)}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
