"""주식 이벤트-드리븐 후보 수집 (어닝 일정 + SEC 8-K 공시 이벤트).

목적: "실시간 어닝콜 / 누가 어디 투자 / 임원 변동" 같은 기업 이벤트를 모아
      이벤트→주가 방향 예측의 *후보 풀*을 구성한다.
backtest 아님. **아직 서명 예측 아님** — 후보 리스트일 뿐. 서명·점수화 규칙은 별도 결정.

무료/무인증 소스만 사용 (CLAUDE.md 데이터 정책: 유료 API 금지):
  1) SEC EDGAR full-text search (efts.sec.gov) — User-Agent만, 10 req/s 제한. 무인증.
     8-K item 코드로 이벤트 종류 분류:
       2.02 = 실적 발표 (Results of Operations)
       1.01 = 중요 계약 체결 (Material Definitive Agreement)
       2.01 = 인수·매각 완료 (Completion of Acquisition/Disposition)
       5.02 = 임원·이사 변동 (Departure/Election of Officers)
       1.03 = 파산, 2.06 = 손상차손, 8.01 = 기타
     ⚠ 8-K는 사건 *발생 후* 공시 (look-ahead 아님). 가치는 빠른 해석-엣지.
  2) yfinance — 예정 어닝 *날짜* (forward-looking). 예측 사전등록의 기준 시점.
     ⚠ 네트워크 루프. 큰 watchlist는 PC(mcp_pc.pc_python)로 위임 권장.

출력:
  data/raw/equity_events/edgar_8k.jsonl
  data/raw/equity_events/earnings_calendar.jsonl
manifest append.

CLI:
  python code/ingest/equity_events.py [--days-back 7] [--items 2.02,1.01,2.01,5.02]
                                      [--skip-earnings] [--watchlist AAPL,NVDA,...]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.shared.manifest import append as manifest_append

EFTS_API = "https://efts.sec.gov/LATEST/search-index"
OUT_DIR = REPO / "data" / "raw" / "equity_events"
OUT_8K = OUT_DIR / "edgar_8k.jsonl"
OUT_EARN = OUT_DIR / "earnings_calendar.jsonl"
# SEC fair-access policy: User-Agent must identify requester
UA = "pythia-research hyungyulove@gmail.com"

# 8-K item codes → 사람이 읽는 이벤트 라벨
ITEM_LABELS = {
    "1.01": "material_agreement",
    "1.03": "bankruptcy",
    "2.01": "acquisition_completed",
    "2.02": "earnings_release",
    "2.06": "material_impairment",
    "5.02": "officer_change",
    "7.01": "reg_fd_disclosure",
    "8.01": "other_event",
}
# 기본: 시장 영향 큰 이벤트 항목
DEFAULT_ITEMS = ["2.02", "1.01", "2.01", "5.02"]

# 기본 watchlist: 대형·고유동성 + 한국 투자자 관심 (어닝 leg용)
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "PLTR", "MU", "TSM", "ASML", "ARM", "SMCI",
    "COIN", "MSTR", "INTC", "QCOM",
]


def _http_get_json(url: str, timeout: float = 30.0, retries: int = 5) -> dict:
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
    raise RuntimeError("efts fetch retries exhausted")


def _parse_display(names: list[str]) -> tuple[str | None, str | None, str | None]:
    """'NVIDIA CORP  (NVDA)  (CIK 0001045810)' → (name, ticker, cik)."""
    if not names:
        return None, None, None
    raw = names[0]
    ticker, cik = None, None
    import re
    tm = re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})\)", raw)
    if tm:
        ticker = tm.group(1)
    cm = re.search(r"CIK\s+(\d+)", raw)
    if cm:
        cik = cm.group(1)
    name = raw.split("(")[0].strip()
    return name, ticker, cik


def harvest_8k(days_back: int, items: list[str], max_pages: int, sleep_sec: float) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days_back)).isoformat()
    end = today.isoformat()
    item_set = set(items)
    print(f"EDGAR 8-K: {start}..{end}, items={sorted(item_set)}")

    out: list[dict] = []
    seen = set()
    page_from = 0
    PAGE = 100
    pages = 0
    while pages < max_pages:
        params = {
            "forms": "8-K",
            "startdt": start,
            "enddt": end,
            "from": str(page_from),
        }
        d = _http_get_json(f"{EFTS_API}?{urllib.parse.urlencode(params)}")
        hits = d.get("hits", {}).get("hits", [])
        if not hits:
            break
        for h in hits:
            s = h.get("_source", {})
            filing_items = s.get("items") or []
            matched = [it for it in filing_items if it in item_set]
            if not matched:
                continue
            adsh = s.get("adsh")
            key = (adsh, tuple(sorted(matched)))
            if not adsh or key in seen:
                continue
            seen.add(key)
            name, ticker, cik = _parse_display(s.get("display_names") or [])
            out.append({
                "source": "edgar_8k",
                "accession": adsh,
                "company": name,
                "ticker": ticker,
                "cik": cik,
                "file_date": s.get("file_date"),
                "items": matched,
                "item_labels": [ITEM_LABELS.get(it, it) for it in matched],
                "form": s.get("form"),
                "_harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
        pages += 1
        page_from += PAGE
        # efts caps pagination ~ from<=1000; stop early if exhausted
        total = d.get("hits", {}).get("total", {}).get("value", 0)
        if page_from >= min(total, 1000):
            break
        time.sleep(sleep_sec)
    print(f"  8-K matched={len(out)} (pages={pages})")
    return out


def harvest_earnings(watchlist: list[str], horizon_days: int) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance 없음 — earnings leg skip (PC로 위임 권장)")
        return []
    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=horizon_days)
    print(f"earnings: {len(watchlist)} tickers, 향후 {horizon_days}일 ({today}..{horizon})")
    out: list[dict] = []
    for tk in watchlist:
        try:
            cal = yf.Ticker(tk).calendar
        except Exception as e:
            print(f"  {tk}: ERR {type(e).__name__}")
            continue
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        if not ed:
            continue
        dates = ed if isinstance(ed, list) else [ed]
        for d0 in dates:
            try:
                ds = d0.isoformat() if hasattr(d0, "isoformat") else str(d0)
                dd = datetime.fromisoformat(ds[:10]).date()
            except Exception:
                continue
            if today <= dd <= horizon:
                out.append({
                    "source": "yfinance_earnings",
                    "ticker": tk,
                    "earnings_date": dd.isoformat(),
                    "estimate_window": [x.isoformat() if hasattr(x, "isoformat") else str(x) for x in dates],
                    "_harvested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
                break
    print(f"  earnings within horizon={len(out)}")
    return out


def _write(path: Path, rows: list[dict], source_tag: str) -> None:
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    if rows:
        manifest_append(source=source_tag, path=path, rows=len(rows))


def run(days_back: int, items: list[str], skip_earnings: bool, watchlist: list[str],
        horizon_days: int, max_pages: int, sleep_sec: float) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows_8k = harvest_8k(days_back, items, max_pages, sleep_sec)
    _write(OUT_8K, rows_8k, "equity.edgar_8k")

    rows_earn = [] if skip_earnings else harvest_earnings(watchlist, horizon_days)
    if rows_earn:
        _write(OUT_EARN, rows_earn, "equity.earnings_calendar")

    # breakdown
    from collections import Counter
    print(f"\ndone. 8-K={len(rows_8k)} → {OUT_8K.relative_to(REPO)}")
    if rows_8k:
        c = Counter(lbl for r in rows_8k for lbl in r["item_labels"])
        print("  8-K event types:")
        for lbl, n in c.most_common():
            print(f"    {lbl:24s} {n}")
    if rows_earn:
        print(f"earnings={len(rows_earn)} → {OUT_EARN.relative_to(REPO)}")
        for r in sorted(rows_earn, key=lambda x: x["earnings_date"]):
            print(f"    {r['earnings_date']}  {r['ticker']}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=7, help="8-K 조회 과거 일수")
    ap.add_argument("--items", default=",".join(DEFAULT_ITEMS), help="8-K item 코드 (콤마)")
    ap.add_argument("--skip-earnings", action="store_true", help="yfinance 어닝 leg 건너뛰기")
    ap.add_argument("--watchlist", default=",".join(DEFAULT_WATCHLIST), help="어닝 watchlist (콤마)")
    ap.add_argument("--horizon-days", type=int, default=45, help="어닝 향후 조회 일수")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--sleep-sec", type=float, default=0.2)
    args = ap.parse_args()
    items = [x.strip() for x in args.items.split(",") if x.strip()]
    watch = [x.strip().upper() for x in args.watchlist.split(",") if x.strip()]
    sys.exit(run(args.days_back, items, args.skip_earnings, watch,
                  args.horizon_days, args.max_pages, args.sleep_sec))
