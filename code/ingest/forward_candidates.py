"""Polymarket forward 후보 시장 수집 (LLM cutoff 이후 해소될 binary Yes/No).

목적: LLM 학습 cutoff (2026-01) 이후 해소될 시장만 모아서 진짜 fair test 풀 구성.
backtest 아님. **아직 서명 예측 아님** — 후보 리스트일 뿐. 서명은 별도 결정.

필터 (default):
  active=true, closed=false, archived=false
  outcomes == ["Yes","No"]
  endDate ∈ [2026-06-01, 2026-12-31]  (조정 가능)
  volume ≥ 10000 USDC (resolution 신뢰도)
  outcomePrices 양쪽 0.05 ≤ p ≤ 0.95 (extreme 가까운 건 정보값 낮음)

출력:
  data/raw/polymarket/forward_candidates.jsonl

manifest append.

CLI:
  python code/ingest/forward_candidates.py [--end-min YYYY-MM-DD] [--end-max YYYY-MM-DD]
                                           [--min-volume 10000] [--max-pages N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.shared.manifest import append as manifest_append

GAMMA_API = "https://gamma-api.polymarket.com/markets"
OUT_DIR = REPO / "data" / "raw" / "polymarket"
OUT_JSONL = OUT_DIR / "forward_candidates.jsonl"
UA = "pythia-forward-candidates/0.1"


def _http_get(params: dict[str, str], timeout: float = 30.0, retries: int = 5) -> list[dict]:
    url = f"{GAMMA_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 422:
                raise
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


def _safe_float(v):
    if v is None: return None
    try: return float(v)
    except (TypeError, ValueError): return None


def passes_filter(m: dict, min_volume: float, end_min: str, end_max: str,
                  price_lo: float = 0.05, price_hi: float = 0.95) -> bool:
    if m.get("closed") or m.get("archived") or not m.get("active"):
        return False
    end = m.get("endDate") or ""
    if not (end_min <= end <= end_max):
        return False
    outs = _parse_listlike(m.get("outcomes"))
    prices = _parse_listlike(m.get("outcomePrices"))
    if not outs or not prices or len(outs) != 2:
        return False
    if {o.lower() for o in outs} != {"yes", "no"}:
        return False
    try:
        ps = [float(x) for x in prices]
    except (TypeError, ValueError):
        return False
    if not all(price_lo <= p <= price_hi for p in ps):
        return False
    vol = _safe_float(m.get("volume")) or _safe_float(m.get("volumeNum"))
    if vol is None or vol < min_volume:
        return False
    return True


def project(m: dict) -> dict:
    """경량화: 후보 리스트에 필요한 필드만."""
    outs = _parse_listlike(m.get("outcomes")) or []
    prices = _parse_listlike(m.get("outcomePrices")) or []
    return {
        "id": str(m.get("id", "")),
        "conditionId": m.get("conditionId"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "category": m.get("category"),
        "outcomes": outs,
        "outcomePrices": [float(x) for x in prices],
        "endDate": m.get("endDate"),
        "startDate": m.get("startDate"),
        "createdAt": m.get("createdAt"),
        "updatedAt": m.get("updatedAt"),
        "volume": _safe_float(m.get("volume")),
        "volume24hr": _safe_float(m.get("volume24hr")),
        "volume1mo": _safe_float(m.get("volume1mo")),
        "liquidity": _safe_float(m.get("liquidity")),
        "resolutionSource": m.get("resolutionSource"),
        "_harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def run(end_min: str, end_max: str, min_volume: float, max_pages: int | None,
        page_size: int, sleep_sec: float) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"forward harvest: end ∈ [{end_min}, {end_max}], min_vol={min_volume}, price ∈ [0.05, 0.95]")

    offset = 0
    pages = 0
    candidates: list[dict] = []
    scanned = 0
    seen_ids = set()

    while True:
        if max_pages is not None and pages >= max_pages:
            print(f"  reached --max-pages={max_pages}, stopping")
            break
        params = {
            "limit": str(page_size),
            "closed": "false",
            "active": "true",
            "end_date_min": f"{end_min}T00:00:00Z",
            "end_date_max": f"{end_max}T23:59:59Z",
            "offset": str(offset),
        }
        try:
            batch = _http_get(params)
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print(f"  offset={offset} → 422 (end)")
                break
            raise
        if not batch:
            print(f"  offset={offset} → empty, done")
            break
        page_kept = 0
        for m in batch:
            scanned += 1
            mid = str(m.get("id", ""))
            if not mid or mid in seen_ids:
                continue
            if passes_filter(m, min_volume, end_min, end_max):
                seen_ids.add(mid)
                candidates.append(project(m))
                page_kept += 1
        pages += 1
        offset += page_size
        if pages % 5 == 0 or page_kept > 0:
            print(f"  page {pages:>3}  offset={offset:>5}  scanned={scanned:>5}  kept_this_page={page_kept:>3}  total_kept={len(candidates):>4}")
        time.sleep(sleep_sec)

    # write JSONL fresh each run (small file, deterministic snapshot)
    with OUT_JSONL.open("w") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False, separators=(",", ":")) + "\n")

    if candidates:
        manifest_append(source="polymarket.forward_candidates", path=OUT_JSONL, rows=len(candidates))

    print(f"\ndone. scanned={scanned} kept={len(candidates)} → {OUT_JSONL.relative_to(REPO)}")
    # quick category breakdown
    from collections import Counter
    cats = Counter(c.get("category") or "(none)" for c in candidates)
    print("\ntop categories:")
    for cat, n in cats.most_common(10):
        print(f"  {cat:30s} {n}")
    # P(YES) distribution
    if candidates:
        ps = sorted(c["outcomePrices"][0] for c in candidates)
        n = len(ps)
        print(f"\nP(YES) percentiles: p10={ps[n//10]:.3f} p50={ps[n//2]:.3f} p90={ps[9*n//10]:.3f}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-min", default="2026-06-01", help="endDate 최소 (YYYY-MM-DD)")
    ap.add_argument("--end-max", default="2026-12-31", help="endDate 최대 (YYYY-MM-DD)")
    ap.add_argument("--min-volume", type=float, default=10000.0, help="최소 누적 volume (USDC)")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--sleep-sec", type=float, default=0.3)
    args = ap.parse_args()
    sys.exit(run(args.end_min, args.end_max, args.min_volume, args.max_pages,
                  args.page_size, args.sleep_sec))
