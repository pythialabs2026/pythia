"""Pythia 예측 해소(resolver).

흐름:
1. `data/predictions/*.json` 에서 status=pending & resolve_at <= now 인 항목 수집.
2. 각 Prediction의 market_type 따라 oracle 조회:
   - POLYMARKET: gamma-api에서 closed 여부 확인 → outcomePrices로 outcome 결정.
   - 그 외: skip (or invalid 표기).
3. outcome 확정되면 brier=(prob - y)² 계산, status=RESOLVED, outcome 기록 후 저장.

CLI:
  python code/verify/resolver.py [--dry-run] [--only-id <pred_id>]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.ingest.snapshot_poller import fetch_market
from code.shared.schemas import MarketType, Prediction, PredictionStatus

PREDICTIONS_DIR = REPO / "data" / "predictions"


class ResolverError(RuntimeError):
    pass


def _derive_polymarket_outcome(market: dict) -> str | None:
    """Polymarket gamma response → 'yes'/'no'/'invalid'/None(아직 미해소).

    binary 시장: outcomes=["Yes","No"], outcomePrices=["1","0"] or ["0","1"] when closed.
    """
    if not market.get("closed"):
        return None
    outcomes = market.get("outcomes")
    prices_raw = market.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices_raw, str):
        prices_raw = json.loads(prices_raw)
    if not outcomes or not prices_raw or len(outcomes) != len(prices_raw):
        raise ResolverError(f"unexpected outcomes shape: {outcomes!r} / {prices_raw!r}")
    prices = [float(p) for p in prices_raw]
    # 마감 시장: 두 가격이 정확히 0/1이 아닐 수 있다(부동소수점 노이즈). 합≈1, 한쪽≥0.95면 승자로 판정.
    # 합이 0이면 시장이 정상 해소되지 않은 것(invalid).
    if len(outcomes) == 2 and {o.lower() for o in outcomes} == {"yes", "no"}:
        yi = next(i for i, o in enumerate(outcomes) if o.lower() == "yes")
        ni = 1 - yi
        total = prices[yi] + prices[ni]
        if total < 0.5:
            return "invalid"  # never properly resolved
        if not math.isclose(total, 1.0, abs_tol=0.05):
            raise ResolverError(f"closed but prices don't sum to 1: {prices!r}")
        if prices[yi] >= 0.95:
            return "yes"
        if prices[ni] >= 0.95:
            return "no"
        raise ResolverError(f"closed but prices ambiguous: {prices!r}")
    # multi-outcome — Phase 1 범위 밖, 추후 확장
    return "invalid"


def _load_pending(only_id: str | None = None) -> list[tuple[Path, Prediction]]:
    out: list[tuple[Path, Prediction]] = []
    now = datetime.now(timezone.utc)
    for f in sorted(PREDICTIONS_DIR.glob("p_*.json")):
        try:
            p = Prediction.model_validate_json(f.read_text())
        except Exception as e:
            print(f"  ⚠ skip {f.name}: parse error {type(e).__name__}: {e}")
            continue
        if only_id and p.id != only_id:
            continue
        if p.status != PredictionStatus.PENDING:
            continue
        if p.resolve_at > now and not only_id:
            continue
        out.append((f, p))
    return out


def resolve_one(p: Prediction) -> tuple[str | None, str]:
    """반환: (outcome, message). outcome None=미해소(시장 아직 안 닫힘 등)."""
    if p.market_type == MarketType.POLYMARKET:
        # market_ref는 slug 또는 conditionId 가정. 둘 다 시도.
        market = None
        last_err: Exception | None = None
        for kwargs in [{"slug": p.market_ref}, {"market_id": p.market_ref}]:
            try:
                market = fetch_market(**kwargs)
                break
            except Exception as e:
                last_err = e
        if market is None:
            raise ResolverError(f"polymarket fetch failed for {p.market_ref!r}: {last_err}")
        outcome = _derive_polymarket_outcome(market)
        if outcome is None:
            return None, f"polymarket market not closed yet (slug={market.get('slug')})"
        return outcome, f"polymarket closed → outcome={outcome}"
    # OTHER / KALSHI / DAO_GOVERNANCE: Phase 1 미구현
    return None, f"unsupported market_type={p.market_type.value} (skip)"


def _brier(prob: float, outcome: str) -> float | None:
    if outcome == "yes":
        return (prob - 1.0) ** 2
    if outcome == "no":
        return (prob - 0.0) ** 2
    return None  # invalid


def main(dry_run: bool, only_id: str | None) -> int:
    pending = _load_pending(only_id=only_id)
    if not pending:
        print("(no pending predictions matching criteria)")
        return 0
    print(f"resolving {len(pending)} prediction(s){' [DRY-RUN]' if dry_run else ''}")
    resolved_n = 0
    skipped_n = 0
    for path, p in pending:
        try:
            outcome, msg = resolve_one(p)
        except Exception as e:
            print(f"  ✗ {p.id} — {type(e).__name__}: {e}")
            skipped_n += 1
            continue
        if outcome is None:
            print(f"  · {p.id} — {msg}")
            skipped_n += 1
            continue
        brier = _brier(p.prob, outcome)
        line = f"  ✓ {p.id} — prob={p.prob:.3f} outcome={outcome} brier={brier!r}"
        print(line)
        if dry_run:
            continue
        # mutate + write
        p.outcome = outcome  # type: ignore[assignment]
        p.brier = brier
        p.status = PredictionStatus.RESOLVED if outcome in ("yes", "no") else PredictionStatus.INVALID
        path.write_text(p.model_dump_json(indent=2))
        resolved_n += 1
    print(f"done. resolved={resolved_n} skipped={skipped_n}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="시뮬레이션 (파일 수정 안 함)")
    ap.add_argument("--only-id", help="특정 prediction id만 처리 (resolve_at 무시)")
    args = ap.parse_args()
    sys.exit(main(dry_run=args.dry_run, only_id=args.only_id))
