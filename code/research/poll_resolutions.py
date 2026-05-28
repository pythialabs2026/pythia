"""Forward paper cohort resolution poller.

cohort.jsonl 173개 시장 중 endDate가 지난 것만 Gamma API로 조회 → 결과 박제.

Resolution 판정:
  closed=true + outcomes=["Yes","No"] + outcomePrices=["1","0"]  → y=1 (YES won)
  closed=true + outcomes=["Yes","No"] + outcomePrices=["0","1"]  → y=0 (NO won)
  그 외 (예: ["0","0"] 또는 ["0.5","0.5"]) → status="invalid", y=null

출력:
  data/research/backtests/forward_paper_2026-05-28/resolutions.jsonl
  (append-only — 한 번 박제된 결과는 다시 안 씀)

CLI:
  python3 code/research/poll_resolutions.py [--force-recheck-invalid]
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
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
COHORT_DIR = REPO / "data" / "research" / "backtests" / "forward_paper_2026-05-28"
COHORT = COHORT_DIR / "cohort.jsonl"
RES = COHORT_DIR / "resolutions.jsonl"

GAMMA_API = "https://gamma-api.polymarket.com/markets"
UA = "pythia-resolution-poller/0.1"
SLEEP_SEC = 0.3


def _http_get(params: dict[str, str], timeout: float = 30.0, retries: int = 5) -> list[dict]:
    url = f"{GAMMA_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(delay); delay = min(delay * 2, 30.0); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(delay); delay = min(delay * 2, 30.0)
    raise RuntimeError("gamma fetch retries exhausted")


def _parse_listlike(raw):
    if isinstance(raw, list): return raw
    if isinstance(raw, str) and raw:
        try: return json.loads(raw)
        except json.JSONDecodeError: return None
    return None


def _classify(m: dict) -> tuple[str, int | None, list[float] | None]:
    """returns (status, y, final_prices). status ∈ {"resolved","unresolved","invalid"}."""
    if not m.get("closed"):
        return ("unresolved", None, None)
    outs = _parse_listlike(m.get("outcomes")) or []
    prices = _parse_listlike(m.get("outcomePrices")) or []
    if {o.lower() for o in outs} != {"yes", "no"} or len(prices) != 2:
        return ("invalid", None, None)
    try:
        ps = [float(x) for x in prices]
    except (TypeError, ValueError):
        return ("invalid", None, None)
    yes_idx = next(i for i, o in enumerate(outs) if o.lower() == "yes")
    no_idx = 1 - yes_idx
    py, pn = ps[yes_idx], ps[no_idx]
    if py == 1.0 and pn == 0.0: return ("resolved", 1, [py, pn])
    if py == 0.0 and pn == 1.0: return ("resolved", 0, [py, pn])
    return ("invalid", None, [py, pn])


def _already_resolved() -> set[str]:
    if not RES.exists(): return set()
    ids = set()
    for line in RES.open():
        rec = json.loads(line)
        if rec.get("status") == "resolved":
            ids.add(rec["market_id"])
    return ids


def main(force_recheck_invalid: bool) -> int:
    cohort = [json.loads(l) for l in COHORT.open()]
    done = _already_resolved()
    now = datetime.now(timezone.utc)
    ts_iso = now.isoformat().replace("+00:00", "Z")

    # cohort endDate already passed AND not already resolved
    queue = []
    for r in cohort:
        if r["id"] in done:
            continue
        end_dt = datetime.fromisoformat(r["endDate"].replace("Z", "+00:00"))
        if end_dt > now:
            continue
        queue.append(r)

    print(f"cohort        : {len(cohort)}")
    print(f"already done  : {len(done)}")
    print(f"endDate passed: {len(queue)}  ← polling")

    new_resolved = 0; new_invalid = 0; new_unresolved = 0
    with RES.open("a") as f:
        for r in queue:
            mid = str(r["id"])
            try:
                batch = _http_get({"id": mid})
            except Exception as e:
                print(f"  ⚠ id={mid} fetch failed: {e}", file=sys.stderr)
                continue
            if not batch:
                print(f"  ⚠ id={mid} not found", file=sys.stderr)
                continue
            m = batch[0]
            status, y, final_prices = _classify(m)
            rec = {
                "market_id": mid,
                "slug": r["slug"],
                "endDate": r["endDate"],
                "status": status,
                "y": y,
                "final_outcomePrices": final_prices,
                "closedTime": m.get("closedTime"),
                "polled_at": ts_iso,
            }
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            if status == "resolved":
                new_resolved += 1
                print(f"  ✅ {mid} y={y}  {r['slug'][:60]}")
            elif status == "invalid":
                new_invalid += 1
                print(f"  ⚠ {mid} invalid prices={final_prices}  {r['slug'][:60]}")
            else:
                new_unresolved += 1
            time.sleep(SLEEP_SEC)

    print(f"\nnew resolved : {new_resolved}")
    print(f"new invalid  : {new_invalid}")
    print(f"new still open: {new_unresolved}")
    if RES.exists():
        sha = hashlib.sha256(RES.read_bytes()).hexdigest()
        print(f"resolutions.jsonl sha256 = {sha[:16]}…")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-recheck-invalid", action="store_true",
                    help="re-poll markets previously marked invalid (default: skip)")
    args = ap.parse_args()
    sys.exit(main(args.force_recheck_invalid))
