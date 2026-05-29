"""Baseline market price collector for cutoff-clean backtest.

For each cohort market, fetch the YES-side price at createdAt + 24h
from Polymarket CLOB price-history. This is what Opus would have competed
against — NOT the final outcomePrices (that would be cheating).

Two-stage fetch per market:
  1. Gamma /markets?id=<id> → extract clobTokenIds[YES]
  2. CLOB /prices-history?market=<token>&startTs=...&endTs=...&fidelity=1
     → take closest-in-time sample to createdAt + 24h

If clobTokenIds missing or price history empty around that window,
record market_p_at_freeze=null with reason. Such markets get excluded
from market-comparison metrics but Opus-vs-naive Brier still computed.

Output (append-only, sha256-able):
  data/research/backtests/cutoff_clean_2026-05-29/baseline_prices.jsonl

CLI:
  python3 code/research/baseline_prices.py [--limit N] [--resume]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
COHORT_DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
COHORT = COHORT_DIR / "cohort.jsonl"
BASELINE = COHORT_DIR / "baseline_prices.jsonl"

GAMMA_API = "https://gamma-api.polymarket.com/markets"
CLOB_API = "https://clob.polymarket.com/prices-history"
UA = "pythia-baseline-collector/0.1"
SLEEP_SEC = 0.35
WINDOW_MIN = 60  # ±60 minutes around target


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
    if isinstance(raw, list): return raw
    if isinstance(raw, str) and raw:
        try: return json.loads(raw)
        except json.JSONDecodeError: return None
    return None


def _yes_token_id(m: dict) -> str | None:
    outs = _parse_listlike(m.get("outcomes")) or []
    tokens = _parse_listlike(m.get("clobTokenIds")) or []
    if not outs or not tokens or len(outs) != len(tokens):
        return None
    for o, t in zip(outs, tokens):
        if str(o).strip().lower() == "yes":
            return str(t)
    return None


def _fetch_token(market_id: str) -> tuple[str | None, str]:
    # path-style returns the market record regardless of closed/archived status,
    # while query-style /markets?id= defaults to closed=false (filters out our cohort).
    url = f"{GAMMA_API}/{urllib.parse.quote(str(market_id))}"
    data = _http_get(url)
    if isinstance(data, dict) and "_err" in data:
        return None, f"gamma_err:{data['_err']}"
    if not isinstance(data, dict) or "id" not in data:
        return None, "gamma_not_found"
    tid = _yes_token_id(data)
    if tid is None:
        return None, "yes_token_missing"
    return tid, "ok"


def _fetch_price_around(token_id: str, target_ts: int) -> tuple[float | None, str]:
    start = target_ts - WINDOW_MIN * 60
    end = target_ts + WINDOW_MIN * 60
    params = {
        "market": token_id,
        "startTs": str(start),
        "endTs": str(end),
        "fidelity": "1",
    }
    url = f"{CLOB_API}?{urllib.parse.urlencode(params)}"
    data = _http_get(url)
    if isinstance(data, dict) and "_err" in data:
        return None, f"clob_err:{data['_err']}"
    hist = data.get("history") if isinstance(data, dict) else None
    if not hist:
        # try wider window: ±6h
        params["startTs"] = str(target_ts - 6 * 3600)
        params["endTs"] = str(target_ts + 6 * 3600)
        url = f"{CLOB_API}?{urllib.parse.urlencode(params)}"
        data = _http_get(url)
        hist = data.get("history") if isinstance(data, dict) else None
        if not hist:
            return None, "no_history_in_window"
    # pick closest-in-time sample
    closest = min(hist, key=lambda x: abs(int(x["t"]) - target_ts))
    p = float(closest["p"])
    # bound check
    if not (0.0 <= p <= 1.0):
        return None, f"price_out_of_range:{p}"
    return p, "ok"


def _already_done() -> set[str]:
    if not BASELINE.exists():
        return set()
    return {json.loads(l)["market_id"] for l in BASELINE.open()}


def main(limit: int | None, resume: bool) -> int:
    cohort = [json.loads(l) for l in COHORT.open()]
    done = _already_done() if resume else set()
    if not resume and BASELINE.exists():
        # safety: do not overwrite by mistake
        print(f"🚨 {BASELINE.name} exists and --resume not set; refusing to overwrite", file=sys.stderr)
        return 1
    queue = [r for r in cohort if r["id"] not in done]
    if limit:
        queue = queue[:limit]
    print(f"cohort  : {len(cohort)}")
    print(f"done    : {len(done)}")
    print(f"to fetch: {len(queue)}")
    ts_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    n_ok = 0; n_null = 0
    with BASELINE.open("a") as f:
        for i, r in enumerate(queue, 1):
            mid = str(r["id"])
            created = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            target_ts = int((created + timedelta(hours=24)).timestamp())
            tid, why = _fetch_token(mid)
            if tid is None:
                rec = {"market_id": mid, "slug": r["slug"], "market_p_at_freeze": None,
                       "target_ts": target_ts, "reason": why, "polled_at": ts_iso}
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
                n_null += 1
                if i % 10 == 0 or why != "ok":
                    print(f"  [{i}/{len(queue)}] id={mid} → null ({why})")
                time.sleep(SLEEP_SEC)
                continue
            price, why = _fetch_price_around(tid, target_ts)
            rec = {"market_id": mid, "slug": r["slug"], "clob_token_yes": tid,
                   "market_p_at_freeze": price, "target_ts": target_ts,
                   "reason": why, "polled_at": ts_iso}
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            if price is None:
                n_null += 1
            else:
                n_ok += 1
            if i % 10 == 0:
                print(f"  [{i}/{len(queue)}] id={mid} p={price} ({why})")
            time.sleep(SLEEP_SEC)
    sha = hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    print(f"\n✅ done. ok={n_ok}  null={n_null}")
    print(f"baseline_prices.jsonl sha256 = {sha[:16]}…")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true",
                    help="resume after a partial run (skip already-recorded ids)")
    args = ap.parse_args()
    sys.exit(main(args.limit, args.resume))
