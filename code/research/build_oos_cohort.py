"""OOS forward cohort builder — implements oos_forward_protocol.md §2 / §5
plus Amendment v2 (sha 33f530a7…): immediate-start register, realized-universe
pagination fix, price band [0.01,0.99], deterministic round-robin stratified
sample (cap 300), Yes/No-only scope.

Runs at register_at = 2026-05-29T07:00:00Z (Amendment v2 §1). Snapshots
Polymarket OPEN markets, applies the pre-registered §2.1 filters, draws a
stratified sample, and seals three artifacts:
  - cohort.jsonl          (NO price — exactly what predict_oos.py will show Opus)
  - baseline_prices.jsonl (market_p_at_register sealed separately, never in prompt)
  - cohort_meta.json      (filter spec + target-floor deficits + sha256)

Filters (oos_forward_protocol.md §2.1, doc sha256 64105914…):
  - scheduled close ≥ register_at + 24h   (anti-staleness; invariant 1)
  - scheduled close ≤ register_at + 30d   (bounded collection horizon)
  - binary YES/NO outcomes only (non-Yes/No 2-outcome deferred — Amendment v2 §3)
  - market_p_at_register ∈ [0.01, 0.99]   (Amendment v2 §1; drop degenerate priors)
  - 7 category buckets, no exclusion
  - capped at 300 via round-robin stratified sample (Amendment v2 §3)

FIELD MAPPING NOTE: open markets do not yet carry `closedTime` (set only on
resolution). The §3 invariant `closedTime ≥ register_at + 24h` is enforced here
against the scheduled `endDate`; the true `closedTime` is re-asserted at
evaluate_at when outcomes are pulled. This is recorded in meta.field_mapping.

Target floors (§2.2, SOFT — document deficit in meta, never pad with synthetics):
  total=300, non_sports=90, min_per_category=15, bin_0=50, bin_5_8=30
The p_opus-indexed bin floors (bin_0, bin_5_8) cannot be measured before
predictions exist; build reports the market_p histogram as a proxy and defers
the p_opus bin-coverage check to freeze_oos.py.

G2: free Gamma feed, no new paid service.

CLI:
  python3 code/research/build_oos_cohort.py [--max-pages N] [--force]
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
OUT_DIR = REPO / "data" / "research" / "backtests" / "oos_forward_2026-05-30"
COHORT = OUT_DIR / "cohort.jsonl"
BASELINE = OUT_DIR / "baseline_prices.jsonl"
META = OUT_DIR / "cohort_meta.json"

GAMMA_API = "https://gamma-api.polymarket.com/markets"
UA = "pythia-oos-cohort-builder/0.1"
SLEEP_SEC = 0.30
PAGE_LIMIT = 100  # Amendment v2: Gamma returns ≤100/req regardless of limit

# Canonical anchors — fixed so the filter is reproducible regardless of the
# actual cron fire time. register_at is the protocol's pre-registered T0.
REGISTER_AT = datetime(2026, 5, 29, 7, 0, 0, tzinfo=timezone.utc)  # Amendment v2 §1
CLOSE_MIN = REGISTER_AT + timedelta(hours=24)
CLOSE_MAX = REGISTER_AT + timedelta(days=30)
P_LO, P_HI = 0.01, 0.99  # Amendment v2 §1

PROTOCOL_SHA256 = "64105914d287d94ee1ced9dfa28655cbdd0ed8b00f103e51bce758cb1c2da384"

TARGETS = {
    "total": 300,
    "non_sports": 90,
    "min_per_category": 15,
    "bin_0": 50,
    "bin_5_8": 30,
}
CATEGORIES = ["sports", "crypto", "politics", "finance",
              "entertainment", "tech_ai", "other"]

# keyword → bucket. First match wins, evaluated in this order.
_CAT_KEYWORDS = [
    ("sports", ["nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
                "baseball", "hockey", "tennis", "golf", "ufc", "boxing", "f1",
                "formula 1", "premier league", "la liga", "champions league",
                "super bowl", "world cup", "olympic", "cricket", "vs.", " vs ",
                "match", "game", "playoff", "wins the", "defeat", "score"]),
    ("crypto", ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
                "token", "coin", "altcoin", "defi", "nft", "stablecoin",
                "binance", "dogecoin", "xrp", "market cap", "halving"]),
    ("politics", ["election", "president", "senate", "congress", "governor",
                  "parliament", "prime minister", "vote", "ballot", "primary",
                  "republican", "democrat", "trump", "biden", "putin", "xi",
                  "impeach", "cabinet", "nominee", "poll", "approval rating"]),
    ("finance", ["fed", "interest rate", "inflation", "cpi", "gdp", "s&p",
                 "nasdaq", "dow", "stock", "ipo", "earnings", "recession",
                 "treasury", "rate cut", "rate hike", "unemployment", "jobs report"]),
    ("entertainment", ["movie", "film", "oscar", "grammy", "box office",
                       "album", "song", "spotify", "netflix", "tv show",
                       "celebrity", "award", "emmy", "billboard", "concert"]),
    ("tech_ai", ["ai", "gpt", "openai", "anthropic", "claude", "gemini",
                 "llm", "model", "chip", "nvidia", "apple", "google", "tesla",
                 "spacex", "launch", "release", "software", "app", "agi"]),
]


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


def _is_binary_yes_no(m: dict) -> bool:
    outs = _parse_listlike(m.get("outcomes")) or []
    if len(outs) != 2:
        return False
    low = sorted(str(o).strip().lower() for o in outs)
    return low == ["no", "yes"]


def _yes_index(m: dict) -> int | None:
    outs = _parse_listlike(m.get("outcomes")) or []
    for i, o in enumerate(outs):
        if str(o).strip().lower() == "yes":
            return i
    return None


def _yes_price(m: dict) -> float | None:
    idx = _yes_index(m)
    prices = _parse_listlike(m.get("outcomePrices")) or []
    if idx is None or idx >= len(prices):
        return None
    try:
        p = float(prices[idx])
    except (TypeError, ValueError):
        return None
    return p if 0.0 <= p <= 1.0 else None


def _yes_token(m: dict) -> str | None:
    idx = _yes_index(m)
    toks = _parse_listlike(m.get("clobTokenIds")) or []
    if idx is None or idx >= len(toks):
        return None
    return str(toks[idx])


def _parse_dt(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _categorize(m: dict) -> str:
    hay = " ".join(str(m.get(k, "")) for k in ("question", "slug", "category")).lower()
    tags = m.get("tags") or []
    if isinstance(tags, list):
        hay += " " + " ".join(str(t.get("label", t) if isinstance(t, dict) else t) for t in tags).lower()
    for bucket, kws in _CAT_KEYWORDS:
        if any(kw in hay for kw in kws):
            return bucket
    return "other"


def _bin_idx(p: float) -> int:
    if p >= 1.0:
        return 9
    if p < 0:
        return 0
    return int(p * 10)


def _harvest(max_pages: int) -> list[dict]:
    """Paginate the open-markets feed, return raw market dicts."""
    out: list[dict] = []
    for page in range(max_pages):
        params = {
            "closed": "false",
            "active": "true",
            "limit": str(PAGE_LIMIT),
            "offset": str(page * PAGE_LIMIT),
            "order": "volume",
            "ascending": "false",
        }
        url = f"{GAMMA_API}?{urllib.parse.urlencode(params)}"
        data = _http_get(url)
        if isinstance(data, dict) and "_err" in data:
            print(f"  page {page}: {data['_err']}, stopping", file=sys.stderr)
            break
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        print(f"  page {page}: +{len(data)} (total {len(out)})")
        if len(data) < PAGE_LIMIT:
            break
        time.sleep(SLEEP_SEC)
    return out


def _stratified_sample(selected, baseline, cap):
    """Amendment v2 §3: deterministic round-robin stratified sample.

    Partition the qualifying pool by category; within each category sort by
    (volume_at_register desc, id asc); draw round-robin across CATEGORIES in
    fixed order one market at a time until `cap` reached or pool exhausted;
    final re-sort by (volume desc, id asc) for deterministic file bytes.
    No outcome-conditioned choice — pre-committed before any p_yes exists.
    """
    def _vol(i):  # Gamma may return volume as str/None — coerce to float
        v = selected[i]["volume_at_register"]
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _key(i):
        return (-_vol(i), str(selected[i]["id"]))

    if len(selected) <= cap:
        order = sorted(range(len(selected)), key=_key)
        return [selected[i] for i in order], [baseline[i] for i in order]

    by_cat: dict[str, list[int]] = {}
    for i, r in enumerate(selected):
        by_cat.setdefault(r["category"], []).append(i)
    for c in by_cat:
        by_cat[c].sort(key=_key)

    cats = [c for c in CATEGORIES if by_cat.get(c)]
    cats += [c for c in by_cat if c not in CATEGORIES]  # safety: unknown cats
    ptr = {c: 0 for c in cats}
    chosen: list[int] = []
    while len(chosen) < cap:
        progressed = False
        for c in cats:
            if ptr[c] < len(by_cat[c]):
                chosen.append(by_cat[c][ptr[c]]); ptr[c] += 1; progressed = True
                if len(chosen) >= cap:
                    break
        if not progressed:
            break

    chosen.sort(key=_key)
    return [selected[i] for i in chosen], [baseline[i] for i in chosen]


def main(max_pages: int, force: bool) -> int:
    if COHORT.exists() and not force:
        print(f"🚨 {COHORT} exists; refusing to overwrite without --force", file=sys.stderr)
        return 1

    print(f"register_at : {REGISTER_AT.isoformat()}")
    print(f"close window: [{CLOSE_MIN.isoformat()}, {CLOSE_MAX.isoformat()}]")
    print("harvesting open markets …")
    raw = _harvest(max_pages)
    print(f"raw open markets: {len(raw)}")

    selected: list[dict] = []
    baseline: list[dict] = []
    reject = {"not_binary": 0, "no_price": 0, "p_out_of_range": 0,
              "no_enddate": 0, "close_too_soon": 0, "close_too_late": 0}
    seen: set[str] = set()
    ts_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for m in raw:
        mid = str(m.get("id", "")).strip()
        if not mid or mid in seen:
            continue
        if not _is_binary_yes_no(m):
            reject["not_binary"] += 1; continue
        close_dt = _parse_dt(m.get("endDate"))
        if close_dt is None:
            reject["no_enddate"] += 1; continue
        if close_dt < CLOSE_MIN:
            reject["close_too_soon"] += 1; continue
        if close_dt > CLOSE_MAX:
            reject["close_too_late"] += 1; continue
        p = _yes_price(m)
        if p is None:
            reject["no_price"] += 1; continue
        if not (P_LO <= p <= P_HI):
            reject["p_out_of_range"] += 1; continue

        seen.add(mid)
        cat = _categorize(m)
        # cohort row — NO price, NO token (nothing that leaks the market's view)
        selected.append({
            "id": mid,
            "slug": m.get("slug", ""),
            "question": m.get("question", ""),
            "endDate": m.get("endDate", ""),
            "scheduled_close": close_dt.isoformat().replace("+00:00", "Z"),
            "category": cat,
            "volume_at_register": m.get("volume"),
        })
        # baseline row — price sealed here, never shown to the predictor
        baseline.append({
            "market_id": mid,
            "slug": m.get("slug", ""),
            "clob_token_yes": _yes_token(m),
            "market_p_at_register": round(p, 6),
            "category": cat,
            "source": "gamma_outcomePrices_yes",
            "polled_at": ts_iso,
        })

    # Amendment v2 §3: cap to TARGETS["total"] via deterministic round-robin
    # stratified sample (also fixes deterministic volume-desc / id-asc order).
    selected, baseline = _stratified_sample(selected, baseline, TARGETS["total"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with COHORT.open("w") as f:
        for r in selected:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    with BASELINE.open("w") as f:
        for r in baseline:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    # ---- target-floor accounting (soft) ----
    cat_counts = {c: 0 for c in CATEGORIES}
    for r in selected:
        cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1
    non_sports = sum(v for c, v in cat_counts.items() if c != "sports")

    # market_p histogram as proxy for the p_opus bin floors (true check deferred
    # to freeze_oos.py once predictions exist).
    mp_hist = {str(i): 0 for i in range(10)}
    for r in baseline:
        mp_hist[str(_bin_idx(r["market_p_at_register"]))] += 1

    deficits = {
        "total": max(0, TARGETS["total"] - len(selected)),
        "non_sports": max(0, TARGETS["non_sports"] - non_sports),
        "categories_below_min": {
            c: TARGETS["min_per_category"] - n
            for c, n in cat_counts.items()
            if n < TARGETS["min_per_category"]
        },
    }

    cohort_sha = hashlib.sha256(COHORT.read_bytes()).hexdigest()
    baseline_sha = hashlib.sha256(BASELINE.read_bytes()).hexdigest()

    meta = {
        "schema": "pythia.oos_forward_cohort.v1",
        "track": "paper-only",
        "experiment": "oos_forward_2026-05-30",
        "pre_registered_protocol_sha256": PROTOCOL_SHA256,
        "register_at_utc": REGISTER_AT.isoformat().replace("+00:00", "Z"),
        "harvested_at_utc": ts_iso,
        "filter": {
            "source": "Polymarket Gamma API /markets?closed=false&active=true",
            "scheduled_close_min_inclusive": CLOSE_MIN.isoformat().replace("+00:00", "Z"),
            "scheduled_close_max_inclusive": CLOSE_MAX.isoformat().replace("+00:00", "Z"),
            "outcomes_required": ["Yes", "No"],
            "market_p_at_register_range": [P_LO, P_HI],
        },
        "field_mapping": {
            "scheduled_close": "endDate",
            "note": ("Open markets carry no closedTime until resolution. The §3 "
                     "invariant closedTime >= register_at + 24h is enforced here "
                     "against endDate (scheduled close); the true closedTime is "
                     "re-asserted at evaluate_at by evaluate_oos.py."),
        },
        "anti_leakage": {
            "cohort_contains_price": False,
            "cohort_contains_token": False,
            "baseline_sealed_separately": True,
        },
        "raw_open_markets_scanned": len(raw),
        "rejections": reject,
        "n_markets": len(selected),
        "category_counts": cat_counts,
        "non_sports_count": non_sports,
        "market_p_histogram": mp_hist,
        "targets": TARGETS,
        "deficits": deficits,
        "bin_floor_note": ("bin_0 / bin_5_8 floors are indexed by p_opus and "
                           "cannot be measured before predictions exist; "
                           "freeze_oos.py performs the p_opus bin-coverage check. "
                           "market_p_histogram above is a build-time proxy only."),
        "cohort_sha256": cohort_sha,
        "cohort_bytes": COHORT.stat().st_size,
        "baseline_sha256": baseline_sha,
        "baseline_bytes": BASELINE.stat().st_size,
        "signing": "NONE",
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"\n✅ cohort sealed: {len(selected)} markets")
    print(f"   non_sports={non_sports} (floor {TARGETS['non_sports']})")
    print(f"   categories: {cat_counts}")
    print(f"   rejections: {reject}")
    if deficits["total"] or deficits["non_sports"] or deficits["categories_below_min"]:
        print(f"   ⚠ deficits (documented, NOT padded): {deficits}")
    print(f"   cohort.jsonl   sha256={cohort_sha[:16]}…")
    print(f"   baseline_prices.jsonl sha256={baseline_sha[:16]}…")
    print(f"   {META.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=120,
                    help="max Gamma pages (×100) to scan")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing cohort.jsonl")
    args = ap.parse_args()
    sys.exit(main(args.max_pages, args.force))
