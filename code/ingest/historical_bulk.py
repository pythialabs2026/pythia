"""Polymarket 마감 시장 전량 bulk 다운로드.

Gamma API `closed=true` 페이지네이션 (limit=100, offset++). 무인증 public API.
~10,000개 closed market을 JSONL append-only로 저장.

출력:
  data/raw/polymarket/historical/closed_markets.jsonl   ← 1 line / market
  data/raw/polymarket/historical/.bulk_state.json       ← resume state {offset, count, ids}

manifest는 run 종료 시 1회 append.

CLI:
  python code/ingest/historical_bulk.py [--max-pages N] [--resume] [--page-size 100]

⚠ 이 데이터는 **backtest 연구용**. Pythia 서명 예측 track(data/predictions/)과 분리.
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
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.shared.manifest import append as manifest_append

GAMMA_API = "https://gamma-api.polymarket.com/markets"
OUT_DIR = REPO / "data" / "raw" / "polymarket" / "historical"
OUT_JSONL = OUT_DIR / "closed_markets.jsonl"
STATE_PATH = OUT_DIR / ".bulk_state.json"

# 보존 필드: backtest에 충분한 최소 집합. raw는 그대로 저장(나중 분석 위해).
# JSONL 1줄 = market 1개 (Gamma 응답 그대로 + _fetched_at)

UA = "pythia-historical-bulk/0.1 (research; contact: hyungyulove@gmail.com)"


def _http_get(params: dict[str, str], timeout: float = 30.0, retries: int = 5) -> list[dict]:
    url = f"{GAMMA_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 422:  # offset out of range — 종료 신호로 위에서 처리
                raise
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise RuntimeError(f"gamma fetch failed after {retries} retries: {last_err}")


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"offset": 0, "count": 0, "seen_ids": []}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def run(max_pages: int | None, resume: bool, page_size: int, sleep_sec: float) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state() if resume else {"offset": 0, "count": 0, "seen_ids": []}
    seen = set(state["seen_ids"])
    offset = state["offset"]
    new_rows = 0
    pages = 0
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"[{started}] historical_bulk start. resume_offset={offset} resume_count={state['count']} seen={len(seen)}")

    mode = "a" if resume else "w"
    if not resume and OUT_JSONL.exists():
        OUT_JSONL.unlink()

    try:
        with OUT_JSONL.open(mode) as fout:
            while True:
                if max_pages is not None and pages >= max_pages:
                    print(f"  reached --max-pages={max_pages}, stopping")
                    break
                params = {"limit": str(page_size), "closed": "true", "offset": str(offset)}
                try:
                    batch = _http_get(params)
                except urllib.error.HTTPError as e:
                    if e.code == 422:
                        print(f"  offset={offset} → 422 (end of dataset)")
                        break
                    raise
                if not batch:
                    print(f"  offset={offset} → empty batch, done")
                    break
                page_new = 0
                fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                for m in batch:
                    mid = str(m.get("id", ""))
                    if not mid or mid in seen:
                        continue
                    seen.add(mid)
                    m["_fetched_at"] = fetched_at
                    fout.write(json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n")
                    page_new += 1
                new_rows += page_new
                pages += 1
                offset += page_size
                if pages % 10 == 0 or page_new != page_size:
                    print(
                        f"  page {pages:>3}  offset={offset:>6}  batch={len(batch):>3}  new={page_new:>3}  "
                        f"total_new={new_rows:>5}  unique={len(seen):>5}"
                    )
                # persist state every page so abort = safe resume
                state["offset"] = offset
                state["count"] = state.get("count", 0) + page_new
                state["seen_ids"] = sorted(seen)
                _save_state(state)
                time.sleep(sleep_sec)
    except KeyboardInterrupt:
        print("\n  ⚠ interrupted by user. state saved → resume with --resume")
        return 130

    finished = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"[{finished}] done. pages={pages} new_rows={new_rows} total_unique={len(seen)}")

    if OUT_JSONL.exists() and OUT_JSONL.stat().st_size > 0:
        # manifest: 1 run = 1 entry, sha256 of full JSONL after run
        manifest_append(source="polymarket.historical_bulk", path=OUT_JSONL, rows=len(seen))
        print(f"  manifest append: rows={len(seen)} sha256={hashlib.sha256(OUT_JSONL.read_bytes()).hexdigest()[:16]}…")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=None, help="페이지 수 제한 (테스트용)")
    ap.add_argument("--resume", action="store_true", help="이전 .bulk_state.json에서 이어받기")
    ap.add_argument("--page-size", type=int, default=100, help="페이지당 시장 수 (gamma 상한 100)")
    ap.add_argument("--sleep-sec", type=float, default=0.4, help="페이지 간 대기 (rate-limit 예방)")
    args = ap.parse_args()
    sys.exit(run(args.max_pages, args.resume, args.page_size, args.sleep_sec))
