"""Polymarket 시장 스냅샷 폴러.

CLI:
  python code/ingest/snapshot_poller.py --slug <slug> [--id <market_id>] [--once]

Gamma API (`https://gamma-api.polymarket.com/markets`)에서 시장 상태(outcomePrices/
volume/liquidity/closed) 가져와 `data/raw/polymarket/snapshots/{slug}/{ts}.json` 저장.
manifest 자동 append. 무인증 public API.

--once: 한 번만 폴링 후 종료. 기본은 sleep 루프 (--interval-sec).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.shared.manifest import append as manifest_append

GAMMA_API = "https://gamma-api.polymarket.com/markets"
SNAPSHOT_ROOT = REPO / "data" / "raw" / "polymarket" / "snapshots"


class PolymarketError(RuntimeError):
    pass


def fetch_market(slug: str | None = None, market_id: str | None = None, timeout: float = 10.0) -> dict:
    """slug 또는 id로 시장 1건 fetch. 활성 우선 → 빈 결과면 closed=true로 재시도."""
    if not slug and not market_id:
        raise PolymarketError("slug or market_id required")
    base: dict[str, str] = {"limit": "1"}
    if slug:
        base["slug"] = slug
    if market_id:
        base["id"] = market_id
    for extra in ({}, {"closed": "true"}):
        params = {**base, **extra}
        url = f"{GAMMA_API}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "pythia-snapshot/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if data:
            return data[0]
    raise PolymarketError(f"market not found: slug={slug} id={market_id}")


def _parse_json_field(raw: str | list, default=None):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
    return default


def snapshot(market: dict) -> dict:
    """Gamma response → 최소 필수 필드만 추린 스냅샷 dict."""
    outcomes = _parse_json_field(market.get("outcomes"), default=[])
    prices = _parse_json_field(market.get("outcomePrices"), default=[])
    clob_tokens = _parse_json_field(market.get("clobTokenIds"), default=[])
    prices_f: list[float] = []
    for p in prices:
        try:
            prices_f.append(float(p))
        except (TypeError, ValueError):
            prices_f.append(float("nan"))
    return {
        "schema": "pythia.polymarket.snapshot.v0",
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "id": str(market.get("id", "")),
        "conditionId": market.get("conditionId"),
        "slug": market.get("slug"),
        "question": market.get("question"),
        "outcomes": outcomes,
        "outcomePrices": prices_f,
        "clobTokenIds": clob_tokens,
        "volume": _safe_float(market.get("volume")),
        "volume24hr": _safe_float(market.get("volume24hr")),
        "liquidity": _safe_float(market.get("liquidity")),
        "active": bool(market.get("active")),
        "closed": bool(market.get("closed")),
        "archived": bool(market.get("archived")),
        "acceptingOrders": bool(market.get("acceptingOrders")),
        "endDate": market.get("endDate"),
        "updatedAt": market.get("updatedAt"),
    }


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_snapshot(snap: dict) -> Path:
    slug = snap.get("slug") or snap.get("id") or "unknown"
    safe_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)[:80]
    ts = snap["ts"].replace(":", "").replace("-", "").replace("Z", "Z")
    out_dir = SNAPSHOT_ROOT / safe_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{ts}.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")))
    manifest_append(source="polymarket.snapshot", path=out, rows=1)
    return out


def poll_once(slug: str | None, market_id: str | None) -> dict:
    market = fetch_market(slug=slug, market_id=market_id)
    snap = snapshot(market)
    path = write_snapshot(snap)
    snap["_path"] = str(path.relative_to(REPO))
    return snap


def poll_loop(slug: str | None, market_id: str | None, interval_sec: int, max_iters: int | None) -> None:
    i = 0
    while True:
        try:
            snap = poll_once(slug, market_id)
            prices = snap["outcomePrices"]
            p_yes = prices[0] if prices else float("nan")
            print(
                f"[{snap['ts']}] {snap['slug']}  P(YES)={p_yes:.4f}  "
                f"vol24h={snap.get('volume24hr')}  closed={snap['closed']}  → {snap['_path']}"
            )
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] poll error: {type(e).__name__}: {e}")
        i += 1
        if max_iters and i >= max_iters:
            break
        time.sleep(interval_sec)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="Polymarket slug (예: new-rhianna-album-before-gta-vi-926)")
    ap.add_argument("--id", dest="market_id", help="Polymarket numeric id")
    ap.add_argument("--once", action="store_true", help="1회 폴링 후 종료")
    ap.add_argument("--interval-sec", type=int, default=300, help="loop interval (default 5min)")
    ap.add_argument("--max-iters", type=int, default=None, help="loop 횟수 제한")
    args = ap.parse_args()
    if not args.slug and not args.market_id:
        ap.error("--slug or --id required")
    try:
        if args.once:
            s = poll_once(args.slug, args.market_id)
            print(json.dumps({
                "ts": s["ts"], "slug": s["slug"], "closed": s["closed"],
                "p_yes": s["outcomePrices"][0] if s["outcomePrices"] else None,
                "path": s["_path"],
            }, indent=2))
        else:
            poll_loop(args.slug, args.market_id, args.interval_sec, args.max_iters)
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        sys.exit(1)
