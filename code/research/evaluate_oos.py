"""OOS forward evaluator — implements oos_forward_protocol.md §3 (T3) / §4 / §5.

Runs at evaluate_at = 2026-06-29T00:00:00Z. For every frozen cohort market it:
  1. pulls the realized outcome from Polymarket Gamma (path-style /markets/{id},
     which returns closed/archived markets) → y ∈ {0,1}; still-open → pending,
     excluded. Re-asserts the realized closedTime ≤ evaluate_at (the check that
     freeze_oos.py had to defer).
  2. runs the paired DM Brier test (Opus vs market) — overall and non-sports.
  3. simulates two betting arms on the SAME resolved set, ordered by closedTime:
       baseline   — flat Kelly-capped stake (§4.1)
       bin_aware  — same stake × FROZEN reliability factor[_bin_idx(p_opus)] (§4.2)
  4. evaluates the 5 pre-registered PASS gates (§4.3). ALL must hold.

Outputs:
  - outcomes.jsonl            (y + realized closedTime per resolved market)
  - oos_forward_results.json  (DM tests + both arms + gate verdict)

A PASS does NOT authorize live capital and does NOT itself upload to Pinata —
§6 requires a debate --critique gate first. This script only computes & seals
the verdict; the report-time freeze addendum + git witness + (on PASS) the
debate-gated Pinata upload are separate follow-up steps.

CLI:
  python3 code/research/evaluate_oos.py [--allow-early] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "oos_forward_2026-05-30"
COHORT = DIR / "cohort.jsonl"
BASELINE = DIR / "baseline_prices.jsonl"
PRED = DIR / "predictions.jsonl"
OUTCOMES = DIR / "outcomes.jsonl"
RESULTS = DIR / "oos_forward_results.json"

GAMMA_API = "https://gamma-api.polymarket.com/markets"
UA = "pythia-oos-evaluator/0.1"
SLEEP_SEC = 0.35

EVALUATE_AT = datetime(2026, 6, 29, 0, 0, 0, tzinfo=timezone.utc)

# §4 sim parameters
EDGE_MIN = 0.05
KELLY_FRAC = 0.5
STAKE_CAP = 0.05
FEE = 0.02
SLIP = 0.01
START_NAV = 1000.0

# §4.2 FROZEN reliability factor map — indexed by _bin_idx(p_opus). Locked, not adapted.
FACTOR = {0: 1.0, 1: 1.0, 2: 0.0, 3: 0.0, 4: 1.0,
          5: 1.0, 6: 0.5, 7: 0.0, 8: 0.5, 9: 0.5}

# §4.3 gate thresholds
GATE_DM_P = 0.05
GATE_NONSPORTS_P = 0.10
GATE_NAV_RATIO = 0.95
GATE_DD_RATIO = 1.10
GATE_MIN_RESOLVED = 200


def _http_get(url: str, timeout: float = 30.0, retries: int = 5):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(delay); delay = min(delay * 2, 30.0); continue
            return {"_err": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError):
            time.sleep(delay); delay = min(delay * 2, 30.0)
    return {"_err": "retries_exhausted"}


def _parse_listlike(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _parse_dt(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _bin_idx(p: float) -> int:
    if p >= 1.0:
        return 9
    if p < 0:
        return 0
    return int(p * 10)


def _yes_index(outcomes) -> int | None:
    for i, o in enumerate(outcomes or []):
        if str(o).strip().lower() == "yes":
            return i
    return None


def _fetch_outcome(market_id: str) -> dict:
    """Path-style fetch (returns closed/archived markets). Resolve y if settled."""
    url = f"{GAMMA_API}/{urllib.parse.quote(str(market_id))}"
    m = _http_get(url)
    if isinstance(m, dict) and "_err" in m:
        return {"market_id": market_id, "status": "fetch_error", "detail": m["_err"]}
    if not isinstance(m, dict):
        return {"market_id": market_id, "status": "fetch_error", "detail": "non_dict"}

    closed = bool(m.get("closed"))
    closed_dt = _parse_dt(m.get("closedTime"))
    outcomes = _parse_listlike(m.get("outcomes"))
    prices = _parse_listlike(m.get("outcomePrices"))
    yi = _yes_index(outcomes)

    if not closed or closed_dt is None or yi is None or prices is None or yi >= len(prices):
        return {"market_id": market_id, "status": "pending",
                "closed": closed,
                "closedTime": m.get("closedTime")}

    try:
        yes_price = float(prices[yi])
    except (TypeError, ValueError):
        return {"market_id": market_id, "status": "pending", "closed": closed}

    # Settled binary: outcomePrices are ~[1,0] or [0,1]. Guard ambiguity.
    if yes_price >= 0.99:
        y = 1
    elif yes_price <= 0.01:
        y = 0
    else:
        return {"market_id": market_id, "status": "ambiguous_price",
                "yes_price": yes_price, "closedTime": m.get("closedTime")}

    return {
        "market_id": market_id,
        "status": "resolved",
        "y": y,
        "closedTime": m.get("closedTime"),
        "closed_dt_le_evaluate_at": closed_dt <= EVALUATE_AT,
    }


def _simulate(rows: list[dict], use_factor: bool) -> dict:
    """rows ordered by closedTime asc; each has p_opus, p_market, y, bin_idx."""
    nav = START_NAV
    peak = nav
    max_dd = 0.0
    n_bets = 0
    cost = 1.0 + FEE + SLIP
    for r in rows:
        edge = r["p_opus"] - r["p_market"]
        if abs(edge) < EDGE_MIN:
            continue
        pm = r["p_market"]
        frac = min(KELLY_FRAC * abs(edge) / min(pm, 1.0 - pm), STAKE_CAP)
        if use_factor:
            frac *= FACTOR[r["bin_idx"]]
        if frac <= 0.0:
            continue
        stake = nav * frac
        if edge > 0:  # bet YES
            entry = pm * cost
            profit = stake * (1.0 / entry - 1.0) if r["y"] == 1 else -stake
        else:         # bet NO
            entry = (1.0 - pm) * cost
            profit = stake * (1.0 / entry - 1.0) if r["y"] == 0 else -stake
        nav += profit
        n_bets += 1
        peak = max(peak, nav)
        if peak > 0:
            max_dd = max(max_dd, (peak - nav) / peak)
        if nav <= 0:
            nav = 0.0
            break
    return {
        "final_nav": round(nav, 4),
        "return_pct": round((nav / START_NAV - 1.0) * 100.0, 4),
        "max_drawdown": round(max_dd, 6),
        "n_bets": n_bets,
    }


def _dm_test(rows: list[dict]) -> dict:
    if len(rows) < 2:
        return {"n": len(rows), "verdict": "insufficient"}
    d = [(r["p_opus"] - r["y"]) ** 2 - (r["p_market"] - r["y"]) ** 2 for r in rows]
    mu = statistics.mean(d)
    sd = statistics.stdev(d)
    se = sd / math.sqrt(len(d)) if sd > 0 else None
    t = mu / se if se else None
    p = math.erfc(abs(t) / math.sqrt(2.0)) if t is not None else None
    brier_opus = statistics.mean((r["p_opus"] - r["y"]) ** 2 for r in rows)
    brier_mkt = statistics.mean((r["p_market"] - r["y"]) ** 2 for r in rows)
    return {
        "n": len(rows),
        "brier_opus": round(brier_opus, 6),
        "brier_market": round(brier_mkt, 6),
        "mean_loss_diff": round(mu, 6),
        "se": round(se, 6) if se else None,
        "t_statistic": round(t, 4) if t is not None else None,
        "p_two_sided_normal": round(p, 6) if p is not None else None,
        "opus_better": mu < 0,
    }


def main(allow_early: bool, limit: int | None) -> int:
    now = datetime.now(timezone.utc)
    if now < EVALUATE_AT and not allow_early:
        print(f"🚨 ABORT: evaluate_at={EVALUATE_AT.isoformat()} not reached "
              f"(now {now.isoformat()}). Use --allow-early for a dry run.",
              file=sys.stderr)
        return 1
    for p in (COHORT, BASELINE, PRED):
        if not p.exists():
            print(f"🚨 ABORT: missing {p}", file=sys.stderr)
            return 1

    cohort = {str(r["id"]): r for r in (json.loads(l) for l in COHORT.open() if l.strip())}
    base = {str(r["market_id"]): r for r in (json.loads(l) for l in BASELINE.open() if l.strip())}
    preds = {str(r["market_id"]): r for r in (json.loads(l) for l in PRED.open() if l.strip())}

    ids = list(cohort)
    if limit:
        ids = ids[:limit]

    print(f"pulling outcomes for {len(ids)} markets …")
    outcomes = []
    for i, mid in enumerate(ids):
        outcomes.append(_fetch_outcome(mid))
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(ids)}")
        time.sleep(SLEEP_SEC)

    with OUTCOMES.open("w") as f:
        for o in outcomes:
            f.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n")

    resolved = [o for o in outcomes if o.get("status") == "resolved"]
    late = [o for o in resolved if o.get("closed_dt_le_evaluate_at") is False]
    if late:
        print(f"⚠ {len(late)} markets closed AFTER evaluate_at — excluding", file=sys.stderr)
    resolved = [o for o in resolved if o.get("closed_dt_le_evaluate_at")]

    # Build paired rows (need market_p sealed at register + p_opus).
    rows = []
    for o in resolved:
        mid = o["market_id"]
        b = base.get(mid)
        pr = preds.get(mid)
        if b is None or pr is None or b.get("market_p_at_register") is None:
            continue
        rows.append({
            "market_id": mid,
            "y": o["y"],
            "p_opus": pr["p_yes"],
            "p_market": b["market_p_at_register"],
            "bin_idx": _bin_idx(pr["p_yes"]),
            "category": cohort[mid].get("category", "other"),
            "closed_dt": _parse_dt(o.get("closedTime")) or EVALUATE_AT,
        })
    rows.sort(key=lambda r: r["closed_dt"])
    nonsports = [r for r in rows if r["category"] != "sports"]

    dm_all = _dm_test(rows)
    dm_ns = _dm_test(nonsports)
    sim_base = _simulate(rows, use_factor=False)
    sim_bin = _simulate(rows, use_factor=True)

    n_resolved = len(rows)
    gates = {
        "dm_significant": {
            "metric": "dm_test.p_two_sided_normal",
            "value": dm_all.get("p_two_sided_normal"),
            "threshold": f"< {GATE_DM_P}",
            "pass": dm_all.get("p_two_sided_normal") is not None
                    and dm_all["p_two_sided_normal"] < GATE_DM_P
                    and dm_all.get("opus_better", False),
        },
        "nonsports_significant": {
            "metric": "nonsports.dm_test.p_two_sided_normal",
            "value": dm_ns.get("p_two_sided_normal"),
            "n": dm_ns.get("n"),
            "threshold": f"< {GATE_NONSPORTS_P} (n≥90)",
            "pass": dm_ns.get("p_two_sided_normal") is not None
                    and dm_ns["p_two_sided_normal"] < GATE_NONSPORTS_P
                    and dm_ns.get("opus_better", False)
                    and dm_ns.get("n", 0) >= 90,
        },
        "bin_aware_nav_ok": {
            "metric": "bin_aware.final_nav >= 0.95 * baseline.final_nav",
            "bin_aware_nav": sim_bin["final_nav"],
            "baseline_nav": sim_base["final_nav"],
            "pass": sim_bin["final_nav"] >= GATE_NAV_RATIO * sim_base["final_nav"],
        },
        "bin_aware_dd_ok": {
            "metric": "bin_aware.max_drawdown <= 1.10 * baseline.max_drawdown",
            "bin_aware_dd": sim_bin["max_drawdown"],
            "baseline_dd": sim_base["max_drawdown"],
            "pass": sim_bin["max_drawdown"] <= GATE_DD_RATIO * sim_base["max_drawdown"]
                    if sim_base["max_drawdown"] > 0 else sim_bin["max_drawdown"] == 0,
        },
        "enough_resolved": {
            "metric": "outcomes_resolved_count >= 200",
            "value": n_resolved,
            "pass": n_resolved >= GATE_MIN_RESOLVED,
        },
    }
    n_pass = sum(1 for g in gates.values() if g["pass"])
    all_pass = n_pass == len(gates)

    status_counts: dict[str, int] = {}
    for o in outcomes:
        status_counts[o.get("status", "?")] = status_counts.get(o.get("status", "?"), 0) + 1

    results = {
        "schema": "pythia.oos_forward_results.v1",
        "experiment": "oos_forward_2026-05-30",
        "track": "paper-only",
        "evaluate_at_utc": EVALUATE_AT.isoformat().replace("+00:00", "Z"),
        "evaluated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "early_dry_run": now < EVALUATE_AT,
        "n_cohort": len(cohort),
        "outcome_status_counts": status_counts,
        "n_resolved_paired": n_resolved,
        "n_resolved_nonsports": len(nonsports),
        "dm_test": dm_all,
        "nonsports": {"dm_test": dm_ns},
        "baseline_arm": sim_base,
        "bin_aware_arm": sim_bin,
        "frozen_factor_map": {str(k): v for k, v in FACTOR.items()},
        "gates": gates,
        "verdict": {
            "n_pass": n_pass,
            "n_total": len(gates),
            "all_pass": all_pass,
            "decision": "PASS" if all_pass else "FAIL",
            "deferral_note": (
                "If only `enough_resolved` fails, §4.3 permits ONE 15-day deferral "
                "to 2026-07-14 then accept the resolved set as-is."
            ),
        },
        "post_pass_requirements": [
            "PASS does NOT authorize live capital (§6).",
            "Pinata/IPFS upload requires a debate --critique gate first.",
            "A FAIL is publishable as an honest negative result.",
        ],
    }
    RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")

    print(f"\n=== OOS forward verdict: {results['verdict']['decision']} "
          f"({n_pass}/{len(gates)} gates) ===")
    print(f"resolved paired: {n_resolved} (nonsports {len(nonsports)})  "
          f"status={status_counts}")
    print(f"DM all: p={dm_all.get('p_two_sided_normal')} "
          f"brier_opus={dm_all.get('brier_opus')} brier_mkt={dm_all.get('brier_market')}")
    print(f"DM nonsports: p={dm_ns.get('p_two_sided_normal')} n={dm_ns.get('n')}")
    print(f"baseline NAV={sim_base['final_nav']} dd={sim_base['max_drawdown']}  "
          f"bin_aware NAV={sim_bin['final_nav']} dd={sim_bin['max_drawdown']}")
    for name, g in gates.items():
        print(f"  [{'PASS' if g['pass'] else 'FAIL'}] {name}: {g['metric']}")
    print(f"\n{RESULTS.relative_to(REPO)}")
    if all_pass and now >= EVALUATE_AT:
        print("\n⚠ PASS — next: debate --critique gate, THEN Pinata upload (§6). "
              "Do NOT upload without the debate gate.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-early", action="store_true",
                    help="run a dry-run before evaluate_at (verdict marked early_dry_run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of markets pulled (debug)")
    args = ap.parse_args()
    sys.exit(main(args.allow_early, args.limit))
