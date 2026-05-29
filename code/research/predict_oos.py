"""OOS forward predictor — implements oos_forward_protocol.md §3 (T1) / §5.

Opus 4.7 issues p_yes for each cohort market. The rendered prompt exposes ONLY
id, scheduled-close date, and question text — NEVER price, volume, or outcome.
This is the anti-leakage invariant (§3 invariant 2): the model must not see the
market's own probability, or the comparison vs market_p stops being independent.

Flow (mirror backtest_predict.py):
  1. `prompt [--batch I] [--size N]`  → prints a no-price prompt block for Opus
  2. (Opus reads it, emits a JSON array of {id, p_yes, rationale})
  3. `record '<json-array>'`           → appends to predictions.jsonl, deduped
  4. `status`                          → coverage vs cohort

Hard guard: `_render_prompt` output is grepped for any stray price-like "0."
substring before printing. If found the script aborts — a leak would void the
whole forward experiment.

CLI:
  python3 code/research/predict_oos.py prompt --batch 0 --size 30
  python3 code/research/predict_oos.py record '[{"id":"...","p_yes":0.4,"rationale":"..."}]'
  python3 code/research/predict_oos.py status
  python3 code/research/predict_oos.py selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/ubuntu/pythia")
DIR = REPO / "data" / "research" / "backtests" / "oos_forward_2026-05-30"
COHORT = DIR / "cohort.jsonl"
PRED = DIR / "predictions.jsonl"

MODEL_ID = "claude-opus-4-7"
EXPERIMENT = "oos_forward_2026-05-30"
BATCH_SIZE_DEFAULT = 30

# Any "0.<digit>" in the rendered prompt would be a price/probability leak.
# Dates are rendered as YYYY-MM-DD (no "0." float), questions are free text;
# a "0." token therefore almost certainly means a stray price slipped in.
_PRICE_LEAK_RE = re.compile(r"\b0\.\d")

PROMPT_HEADER = """You are a calibrated forecaster. For EACH market below output a
probability p_yes ∈ [0.01, 0.99] that the market resolves YES, plus a one-sentence
rationale. You are given ONLY the question and its scheduled close date — no prices,
no market odds, no volume. Do not guess the market's price; estimate the true
probability from your own world model.

Return a JSON array, one object per market:
  [{"id": "<id>", "p_yes": <float>, "rationale": "<one sentence>"}, ...]

Markets:"""


def _load_cohort() -> list[dict]:
    if not COHORT.exists():
        print(f"🚨 cohort not found: {COHORT}", file=sys.stderr)
        sys.exit(1)
    return [json.loads(l) for l in COHORT.open() if l.strip()]


def _predicted_ids() -> set[str]:
    if not PRED.exists():
        return set()
    return {str(json.loads(l)["market_id"]) for l in PRED.open() if l.strip()}


def _render_rows(rows: list[dict]) -> str:
    """Just the per-market lines — the dynamic, leak-prone part of the prompt.

    The trusted PROMPT_HEADER legitimately contains the range "[0.01, 0.99]";
    the leak guard must run on THIS (market data) only, never the header.
    """
    lines = []
    for r in rows:
        # scheduled-close DATE only (no time) — and crucially NO price field.
        end = str(r.get("endDate", ""))[:10]
        lines.append(f"  - id={r['id']}  close={end}  Q: {r['question']}")
    return "\n".join(lines)


def _render_prompt(rows: list[dict]) -> str:
    return PROMPT_HEADER + "\n" + _render_rows(rows)


def _assert_no_leak(rows_text: str) -> None:
    """Run ONLY on the rendered market rows (see _render_rows)."""
    hit = _PRICE_LEAK_RE.search(rows_text)
    if hit:
        print(f"🚨 ABORT: market rows contain a price-like token {hit.group()!r} — "
              f"anti-leakage invariant (§3.2) violated.", file=sys.stderr)
        sys.exit(2)


def cmd_prompt(batch_idx: int, size: int) -> int:
    rows = _load_cohort()
    done = _predicted_ids()
    pending = [r for r in rows if str(r["id"]) not in done]
    start = batch_idx * size
    chunk = pending[start:start + size]
    if not chunk:
        print(f"(no pending markets in batch {batch_idx}; "
              f"{len(pending)} pending total)")
        return 0
    _assert_no_leak(_render_rows(chunk))
    print(_render_prompt(chunk))
    print(f"\n# batch {batch_idx}: {len(chunk)} markets "
          f"({len(pending)} pending / {len(rows)} total)", file=sys.stderr)
    return 0


def cmd_record(batch_json: str) -> int:
    try:
        items = json.loads(batch_json)
    except json.JSONDecodeError as e:
        print(f"🚨 invalid JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(items, list):
        print("🚨 expected a JSON array", file=sys.stderr)
        return 1

    cohort = {str(r["id"]): r for r in _load_cohort()}
    done = _predicted_ids()
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    written = 0
    with PRED.open("a") as f:
        for it in items:
            mid = str(it["id"])
            if mid in done:
                continue
            if mid not in cohort:
                print(f"  skip {mid}: not in cohort", file=sys.stderr)
                continue
            p = float(it["p_yes"])
            assert 0.01 <= p <= 0.99, f"p_yes out of range for {mid}: {p}"
            rationale = str(it.get("rationale", ""))
            rec = {
                "market_id": mid,
                "slug": cohort[mid].get("slug", ""),
                "model": MODEL_ID,
                "p_yes": round(p, 6),
                "rationale": rationale,
                "rationale_sha256": hashlib.sha256(rationale.encode()).hexdigest(),
                "predicted_at": ts,
                "track": "paper-only",
                "experiment": EXPERIMENT,
            }
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            done.add(mid)
            written += 1
    print(f"✅ recorded {written} predictions ({len(done)} total)")
    return 0


def cmd_status() -> int:
    rows = _load_cohort()
    done = _predicted_ids()
    n = len(rows)
    d = len([r for r in rows if str(r["id"]) in done])
    print(f"cohort={n}  predicted={d}  pending={n - d}")
    if d:
        n_batches = (n - d + BATCH_SIZE_DEFAULT - 1) // BATCH_SIZE_DEFAULT
        print(f"remaining batches @ size {BATCH_SIZE_DEFAULT}: {n_batches}")
    return 0


def cmd_selftest() -> int:
    """Verify the leak guard fires on a price and passes on clean market rows."""
    clean_rows = _render_rows([
        {"id": "1", "endDate": "2026-06-15T00:00:00Z", "question": "Will X happen?"},
    ])
    _assert_no_leak(clean_rows)  # must NOT exit
    # Full prompt (header + rows) must still render without the header's
    # legitimate "0.01/0.99" range tripping the guard — header is NOT scanned.
    leaky = clean_rows + "\n  market_p=0.43"
    if _PRICE_LEAK_RE.search(leaky) is None:
        print("🚨 selftest FAIL: guard did not catch injected price", file=sys.stderr)
        return 1
    print("✅ selftest pass: clean market rows clear (header range ignored), "
          "injected price is caught")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_prompt = sub.add_parser("prompt")
    p_prompt.add_argument("--batch", type=int, default=0)
    p_prompt.add_argument("--size", type=int, default=BATCH_SIZE_DEFAULT)
    p_record = sub.add_parser("record")
    p_record.add_argument("json")
    sub.add_parser("status")
    sub.add_parser("selftest")
    args = ap.parse_args()

    if args.cmd == "prompt":
        sys.exit(cmd_prompt(args.batch, args.size))
    if args.cmd == "record":
        sys.exit(cmd_record(args.json))
    if args.cmd == "status":
        sys.exit(cmd_status())
    if args.cmd == "selftest":
        sys.exit(cmd_selftest())
