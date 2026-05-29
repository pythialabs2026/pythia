"""OOS forward freeze — implements oos_forward_protocol.md §3 (T2) / §5.

Seals the cohort + predictions + baseline into freeze.json (schema
pythia.backtest_freeze.v1) BEFORE any outcome is known. Enforces the §3
invariants that are checkable at freeze time:

  HARD (abort on failure — these protect the experiment's integrity):
    1. every cohort row: scheduled_close (endDate) ≥ register_at + 24h
    2. anti-leakage: cohort.jsonl carries no price field
    3. coverage: every cohort market predicted exactly once (no gap, no dup)

  SOFT (record deficit in freeze.json, NEVER pad):
    - p_opus bin coverage: bin_0 ≥ 50, bins 5-8 aggregate ≥ 30 (§2.2)
    - total / non_sports / per-category floors (already in cohort_meta.json)

freeze.json embeds pre_registered_protocol_sha256 = 64105914… so the chain
links back to the sealed protocol doc. The realized closedTime assertion is
NOT done here (markets still open) — it runs at evaluate_at in evaluate_oos.py.

Two-step git witness (mirrors finalize_addendum_v*.py):
  1. `seal`    → write freeze.json (no git_witness yet); print files to commit
  2. (session commits the artifacts + freeze.json, pushes, captures hash+ts)
  3. `witness --commit <hash> --pushed-at <iso>`  → add git_witness_oos_forward_v1

CLI:
  python3 code/research/freeze_oos.py seal
  python3 code/research/freeze_oos.py witness --commit <sha> --pushed-at <iso8601>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "oos_forward_2026-05-30"
COHORT = DIR / "cohort.jsonl"
BASELINE = DIR / "baseline_prices.jsonl"
META = DIR / "cohort_meta.json"
PRED = DIR / "predictions.jsonl"
PROTOCOL = REPO / "data" / "research" / "backtests" / "cutoff_clean_2026-05-29" / "oos_forward_protocol.md"
AMENDMENT = DIR / "oos_forward_protocol_amendment_v1.md"
FREEZE = DIR / "freeze.json"

REGISTER_AT = datetime(2026, 5, 30, 0, 0, 0, tzinfo=timezone.utc)
CLOSE_MIN = REGISTER_AT + timedelta(hours=24)
PROTOCOL_SHA256 = "64105914d287d94ee1ced9dfa28655cbdd0ed8b00f103e51bce758cb1c2da384"
# Amendment v1 swaps the predictor 4.7 → 4.8 without editing the sealed protocol
# doc. The amendment is itself sha-anchored so it cannot drift either.
AMENDMENT_SHA256 = "8cbf4c996ead469dec99359116ca2c3f17d3330cf1254ba0ab47329375297f69"
PREDICTOR_MODEL = "claude-opus-4-8"

BIN_0_FLOOR = 50
BIN_5_8_FLOOR = 30


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _parse_dt(ts: str):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _bin_idx(p: float) -> int:
    if p >= 1.0:
        return 9
    if p < 0:
        return 0
    return int(p * 10)


def _load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open() if l.strip()]


def _check_protocol_sha() -> None:
    for label, path, want in (
        ("protocol doc", PROTOCOL, PROTOCOL_SHA256),
        ("amendment v1", AMENDMENT, AMENDMENT_SHA256),
    ):
        actual = _sha(path)
        if actual != want:
            print(f"🚨 ABORT: {label} sha256 mismatch.\n"
                  f"   expected {want}\n   actual   {actual}\n"
                  f"   The pre-registered protocol has changed — chain broken.",
                  file=sys.stderr)
            sys.exit(3)


def cmd_seal() -> int:
    for p in (COHORT, BASELINE, META, PRED, PROTOCOL, AMENDMENT):
        if not p.exists():
            print(f"🚨 ABORT: missing required input {p}", file=sys.stderr)
            return 1

    _check_protocol_sha()
    cohort = _load_jsonl(COHORT)
    preds = _load_jsonl(PRED)

    # HARD 1 — scheduled close ≥ register_at + 24h, per row
    bad_close = []
    for r in cohort:
        sc = r.get("scheduled_close") or r.get("endDate")
        if sc is None or _parse_dt(sc) < CLOSE_MIN:
            bad_close.append(r.get("id"))
    if bad_close:
        print(f"🚨 ABORT (invariant 1): {len(bad_close)} rows close before "
              f"register_at+24h: {bad_close[:10]}", file=sys.stderr)
        return 1

    # HARD 2 — anti-leakage: no price field in cohort rows
    leak_fields = {"price", "outcomePrices", "market_p", "market_p_at_register",
                   "clob_token_yes", "clobTokenIds"}
    leaked = [r["id"] for r in cohort if leak_fields & set(r.keys())]
    if leaked:
        print(f"🚨 ABORT (invariant 2): cohort rows carry price/token fields: "
              f"{leaked[:10]}", file=sys.stderr)
        return 1

    # HARD 3 — coverage: every cohort market predicted exactly once
    cohort_ids = [str(r["id"]) for r in cohort]
    pred_ids = [str(r["market_id"]) for r in preds]
    cohort_set, pred_set = set(cohort_ids), set(pred_ids)
    missing = cohort_set - pred_set
    extra = pred_set - cohort_set
    dups = len(pred_ids) - len(pred_set)
    if missing or extra or dups:
        print(f"🚨 ABORT (invariant 3): coverage broken — "
              f"missing={len(missing)} extra={len(extra)} dups={dups}",
              file=sys.stderr)
        if missing:
            print(f"   missing (sample): {list(missing)[:10]}", file=sys.stderr)
        return 1

    # SOFT — p_opus bin coverage
    pmap = {str(r["market_id"]): r["p_yes"] for r in preds}
    bin_counts = {i: 0 for i in range(10)}
    for mid in cohort_ids:
        bin_counts[_bin_idx(pmap[mid])] += 1
    bin_0 = bin_counts[0]
    bin_5_8 = sum(bin_counts[i] for i in (5, 6, 7, 8))
    soft_deficits = {
        "bin_0": max(0, BIN_0_FLOOR - bin_0),
        "bin_5_8": max(0, BIN_5_8_FLOOR - bin_5_8),
    }

    artifacts = [
        ("cohort", COHORT),
        ("cohort_meta", META),
        ("baseline_prices", BASELINE),
        ("predictions", PRED),
        ("oos_forward_protocol", PROTOCOL),
        ("oos_forward_protocol_amendment_v1", AMENDMENT),
    ]
    art_block = []
    for label, p in artifacts:
        b = p.read_bytes()
        art_block.append({
            "label": label,
            "path": p.relative_to(REPO).as_posix(),
            "sha256": hashlib.sha256(b).hexdigest(),
            "bytes": len(b),
        })

    fz = {
        "schema": "pythia.backtest_freeze.v1",
        "track": "oos_forward_2026-05-30",
        "experiment_kind": "oos_forward_paper_only",
        "pre_registered_protocol_sha256": PROTOCOL_SHA256,
        "protocol_amendment_v1_sha256": AMENDMENT_SHA256,
        "predictor_model": PREDICTOR_MODEL,
        "register_at_utc": REGISTER_AT.isoformat().replace("+00:00", "Z"),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_markets": len(cohort),
        "n_predictions": len(preds),
        "invariants_checked_at_freeze": {
            "scheduled_close_ge_register_plus_24h": True,
            "cohort_has_no_price_field": True,
            "coverage_exactly_once": True,
            "realized_closedTime_check": "DEFERRED to evaluate_at (markets still open)",
        },
        "p_opus_bin_counts": {str(k): v for k, v in bin_counts.items()},
        "soft_floor_status": {
            "bin_0": {"count": bin_0, "floor": BIN_0_FLOOR, "deficit": soft_deficits["bin_0"]},
            "bin_5_8": {"count": bin_5_8, "floor": BIN_5_8_FLOOR, "deficit": soft_deficits["bin_5_8"]},
            "note": "Soft floors — deficits recorded, cohort NOT padded (§2.2).",
        },
        "artifacts": art_block,
        "git_witness_oos_forward_v1": None,
        "notes": [
            "OOS forward-paper experiment. Outcomes UNKNOWN at freeze — "
            "predictions.jsonl sealed before any y is observable.",
            f"Predictor model = {PREDICTOR_MODEL} (amendment v1, sha "
            f"{AMENDMENT_SHA256[:16]}…). Sealed protocol doc names 4.7 but is "
            "unedited; the swap is recorded in the amendment, leakage-safe "
            "because future-resolving markets are post-cutoff for any current model.",
            "Anti-leakage: cohort.jsonl exposes only id/close-date/question; "
            "price sealed separately in baseline_prices.jsonl.",
            "closedTime invariant enforced at freeze against scheduled endDate; "
            "realized closedTime re-asserted at evaluate_at (2026-06-29).",
            "Track is paper-only. Pinata/IPFS upload is GATED on a PASS verdict at "
            "evaluate_at AND a debate --critique gate (§6) — NOT done at freeze.",
            "NO re-prompt permitted T2→T3: predictions.jsonl must show exactly one "
            "modifying commit in git history (§3 invariant 4).",
        ],
    }
    FREEZE.write_text(json.dumps(fz, indent=2, ensure_ascii=False) + "\n")

    print(f"✅ freeze.json sealed: {len(cohort)} markets, {len(preds)} predictions")
    print(f"   p_opus bins: {bin_counts}")
    print(f"   bin_0={bin_0} (floor {BIN_0_FLOOR}, deficit {soft_deficits['bin_0']})")
    print(f"   bin_5_8={bin_5_8} (floor {BIN_5_8_FLOOR}, deficit {soft_deficits['bin_5_8']})")
    print(f"   protocol sha256 verified: {PROTOCOL_SHA256[:16]}…")
    print("\n# Files to `git add` (by name only — never -A):")
    for _, p in artifacts:
        print(f"  {p.relative_to(REPO)}")
    print(f"  {FREEZE.relative_to(REPO)}")
    print("\n# Then: commit + push, capture <sha> & <pushed_at>, and run:")
    print("#   python3 code/research/freeze_oos.py witness --commit <sha> --pushed-at <iso>")
    return 0


def cmd_witness(commit: str, pushed_at: str) -> int:
    if not FREEZE.exists():
        print(f"🚨 ABORT: {FREEZE} not sealed yet (run `seal` first)", file=sys.stderr)
        return 1
    fz = json.loads(FREEZE.read_text())
    fz["git_witness_oos_forward_v1"] = {
        "commit": commit,
        "branch": "main",
        "remote": "git@github.com:pythialabs2026/pythia.git",
        "pushed_at_utc": pushed_at,
        "anchors": "cohort + cohort_meta + baseline_prices + predictions + oos_forward_protocol + amendment_v1",
    }
    fz.setdefault("notes", []).append(
        f"git_witness_oos_forward_v1 anchored at commit {commit} ({pushed_at})."
    )
    FREEZE.write_text(json.dumps(fz, indent=2, ensure_ascii=False) + "\n")
    print(f"✅ git_witness_oos_forward_v1 anchored: {commit}")
    print("# Now make the witness commit (freeze.json change) + push.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seal")
    p_w = sub.add_parser("witness")
    p_w.add_argument("--commit", required=True)
    p_w.add_argument("--pushed-at", required=True)
    args = ap.parse_args()

    if args.cmd == "seal":
        sys.exit(cmd_seal())
    if args.cmd == "witness":
        sys.exit(cmd_witness(args.commit, args.pushed_at))
