"""Forward paper / backtest freeze integrity check.

freeze.json에 박힌 sha256 ↔ 현재 파일 sha256 비교.
일치 = ✅ 무결성 보존. 불일치 = 🚨 실험 무효 (artifacts tampered after freeze).

Exit codes:
  0 = all clean
  1 = drift detected (or freeze.json missing)

CLI:
  python3 code/research/verify_freeze.py [--quiet] [--track <name>]

  --track  forward_paper_2026-05-28 (default) | cutoff_clean_2026-05-29 | <any backtest dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
BACKTESTS = REPO / "data" / "research" / "backtests"
DEFAULT_TRACK = "forward_paper_2026-05-28"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(quiet: bool, track: str) -> int:
    freeze_path = BACKTESTS / track / "freeze.json"
    if not freeze_path.exists():
        print(f"🚨 freeze.json missing at {freeze_path}", file=sys.stderr)
        return 1
    freeze = json.loads(freeze_path.read_text())
    # support both legacy (3 fixed keys) and new (artifacts list) schemas
    if "artifacts" in freeze:
        targets = [(a["label"], a) for a in freeze["artifacts"]]
    else:
        targets = []
        for label in ("cohort", "predictions", "cohort_meta"):
            if label in freeze:
                targets.append((label, freeze[label]))
    drift = 0
    for label, spec in targets:
        p = REPO / spec["path"]
        if not p.exists():
            print(f"🚨 {label}: file missing → {p}", file=sys.stderr); drift += 1; continue
        actual_sha = _sha256(p)
        actual_bytes = p.stat().st_size
        ok_sha = actual_sha == spec["sha256"]
        ok_bytes = actual_bytes == spec["bytes"]
        if ok_sha and ok_bytes:
            if not quiet:
                print(f"  ✅ {label:18s} sha256={actual_sha[:16]}…  bytes={actual_bytes}")
        else:
            drift += 1
            print(f"  🚨 {label:18s} DRIFT", file=sys.stderr)
            print(f"     expected sha256={spec['sha256']}  bytes={spec['bytes']}", file=sys.stderr)
            print(f"     actual   sha256={actual_sha}  bytes={actual_bytes}", file=sys.stderr)
    if drift:
        print(f"\n🚨 {drift} drift(s) detected in track={track}. Experiment integrity COMPROMISED.", file=sys.stderr)
        return 1
    if not quiet:
        n = freeze.get("n_markets", "?")
        commit = freeze.get("git_witness", {}).get("commit", "?")[:8]
        print(f"\n✅ track={track} freeze intact. n={n} markets, witness commit {commit}.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--track", default=DEFAULT_TRACK,
                    help="backtest directory under data/research/backtests/")
    args = ap.parse_args()
    sys.exit(main(args.quiet, args.track))
