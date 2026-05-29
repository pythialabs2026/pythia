"""Update freeze.json with the addendum block of post-cohort artifacts.

After Step 4-7 produce predictions / baseline / brier / pnl / nav / summary,
this script appends them to the artifacts list and rewrites freeze.json.

The next manual step is to commit + push, then anchor a witness commit
that records the new HEAD hash (mirroring the existing pattern from
commit 16eeb7c).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29"
FREEZE = DIR / "freeze.json"

NEW_ARTIFACTS = [
    ("predictions",       DIR / "predictions.jsonl"),
    ("baseline_prices",   DIR / "baseline_prices.jsonl"),
    ("brier_scores",      DIR / "brier_scores.jsonl"),
    ("pnl_log",           DIR / "pnl_log.jsonl"),
    ("daily_nav",         DIR / "daily_nav.jsonl"),
    ("summary",           DIR / "summary.json"),
    ("calibration_plot",  DIR / "calibration.txt"),
]


def main() -> int:
    fz = json.loads(FREEZE.read_text())
    existing_paths = {a["path"] for a in fz["artifacts"]}
    added = []
    for label, p in NEW_ARTIFACTS:
        rel = p.relative_to(REPO).as_posix()
        if rel in existing_paths:
            continue
        b = p.read_bytes()
        fz["artifacts"].append({
            "label": label,
            "path": rel,
            "sha256": hashlib.sha256(b).hexdigest(),
            "bytes": len(b),
        })
        added.append(rel)
    fz["addendum_frozen_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fz["notes"].append(
        "Addendum 2026-05-29: predictions/baseline/brier/pnl/nav/summary/calibration sealed."
    )
    FREEZE.write_text(json.dumps(fz, indent=2) + "\n")
    print(f"✅ freeze.json updated. Added artifacts:")
    for r in added:
        print(f"  + {r}")
    print(f"\nTotal artifacts now: {len(fz['artifacts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
