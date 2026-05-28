"""Forward paper cohort 박제.

forward_candidates.jsonl 중 P(YES) ∈ [0.30, 0.70) 173개를 선별해 cohort.jsonl로 고정.
추가 fields: market_p_yes_at_freeze (anchoring 희석용, 예측 단계에서는 안 보임), freeze_ts.

출력:
  data/research/backtests/forward_paper_2026-05-28/
    cohort.jsonl            ← 173 markets, sha256 freeze
    cohort.meta.json        ← cohort sha256 + n + filter spec

NOT signed. NOT IPFS. 연구용 트랙 (sha256만 의미).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
SRC = REPO / "data" / "raw" / "polymarket" / "forward_candidates.jsonl"
OUT_DIR = REPO / "data" / "research" / "backtests" / "forward_paper_2026-05-28"
COHORT = OUT_DIR / "cohort.jsonl"
META = OUT_DIR / "cohort.meta.json"

P_LO = 0.30
P_HI = 0.70


def main() -> None:
    rows = [json.loads(l) for l in SRC.open()]
    selected = []
    for r in rows:
        p = r["outcomePrices"][0]
        if P_LO <= p < P_HI:
            selected.append({
                "id": r["id"],
                "conditionId": r["conditionId"],
                "slug": r["slug"],
                "question": r["question"],
                "endDate": r["endDate"],
                "volume_at_freeze": r["volume"],
                "market_p_yes_at_freeze": p,
                "_outcomes": r["outcomes"],
            })
    selected.sort(key=lambda r: r["volume_at_freeze"] or 0, reverse=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with COHORT.open("w") as f:
        for r in selected:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    sha = hashlib.sha256(COHORT.read_bytes()).hexdigest()
    meta = {
        "schema": "pythia.forward_paper_cohort.v0",
        "freeze_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "filter": {
            "p_yes_range": [P_LO, P_HI],
            "source": str(SRC.relative_to(REPO)),
        },
        "n": len(selected),
        "cohort_sha256": sha,
        "cohort_bytes": COHORT.stat().st_size,
        "track": "paper-only",
        "signing": "NONE",
        "note": "173 forward markets frozen for fair-test Brier. NOT a Pythia signed prediction track.",
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    print(f"✅ cohort: {len(selected)} markets")
    print(f"   {COHORT.relative_to(REPO)}  sha256={sha[:16]}…")
    print(f"   {META.relative_to(REPO)}")


if __name__ == "__main__":
    main()
