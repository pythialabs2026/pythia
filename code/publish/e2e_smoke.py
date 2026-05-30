"""Tier 0 → Tier 1 publish pipeline 1-shot 스모크 테스트.

흐름:
1. 가짜 Prediction 생성 (resolve_at = 24h 후).
2. JSON payload → Pinata IPFS pin → CID.
3. Nostr Kind 1 노트에 CID 포함 → 릴레이 broadcast → event_id.
4. 결과 Prediction 객체를 data/predictions/<id>.json 으로 저장.

목적: 무료 path 자격증명 (Pinata JWT + Nostr nsec) 실제 작동 검증.
X 유료티어 회피 — broadcast leg는 Nostr (NIP-01 Kind 1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# allow `python e2e_smoke.py` from repo root or anywhere
REPO = Path("/home/ubuntu/pythia")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from code.publish.ipfs_publisher import gateway_url, pin_json
from code.publish.nostr_publisher import publish_nostr
from code.shared.schemas import MarketType, Prediction, PredictionStatus

PREDICTIONS_DIR = REPO / "data" / "predictions"


def _pred_id(market_ref: str, ts: datetime) -> str:
    raw = f"{market_ref}|{ts.isoformat()}".encode()
    return "p_" + hashlib.sha256(raw).hexdigest()[:16]


def _payload(p: Prediction) -> dict:
    """IPFS에 핀할 verifiable 페이로드. rationale 자체는 별도 저장(해시만 박제)."""
    return {
        "schema": "pythia.prediction.v0",
        "id": p.id,
        "ts_created": p.ts_created.isoformat().replace("+00:00", "Z"),
        "market_type": p.market_type.value,
        "market_ref": p.market_ref,
        "prob": p.prob,
        "rationale_hash": p.rationale_hash,
        "resolve_at": p.resolve_at.isoformat().replace("+00:00", "Z"),
    }


def _note_text(p: Prediction, cid: str) -> str:
    """Nostr Kind 1 노트. 16KB까지 가능하지만 가독성 위해 짧게."""
    return (
        f"Pythia 예측 [smoke]\n"
        f"시장: {p.market_type.value} / {p.market_ref}\n"
        f"P(YES)={p.prob:.2f}  해소: {p.resolve_at.strftime('%Y-%m-%d %H:%MZ')}\n"
        f"rationale_sha256: {p.rationale_hash[:16]}…\n"
        f"IPFS: {cid}\n"
        f"gateway: {gateway_url(cid)}"
    )


def run_smoke(dry_nostr: bool = False) -> Prediction:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    resolve_at = now + timedelta(hours=24)
    market_ref = f"smoke-{int(now.timestamp())}"

    p = Prediction(
        id=_pred_id(market_ref, now),
        ts_created=now,
        market_type=MarketType.OTHER,
        market_ref=market_ref,
        prob=0.42,
        rationale_hash=hashlib.sha256("smoke rationale — verifying creds".encode()).hexdigest(),
        resolve_at=resolve_at,
        status=PredictionStatus.PENDING,
    )

    payload = _payload(p)
    print(f"[1/3] pin to Pinata ... id={p.id}")
    pin = pin_json(payload, name=f"pythia-{p.id}")
    p.ipfs_cid = pin["cid"]
    print(f"      ✓ cid={pin['cid']}  size={pin['size']}B")
    print(f"      gateway: {gateway_url(pin['cid'])}")

    text = _note_text(p, pin["cid"])
    print(f"[2/3] publish to Nostr ({len(text)} chars / {len(text.encode())}B) ...")
    if dry_nostr:
        print("      (dry-run: skipped)")
        p.nostr_event_id = "DRY_RUN"
    else:
        res = publish_nostr(text)
        p.nostr_event_id = res["id"]
        p.nostr_pubkey = res["pubkey"]
        p.nostr_relays_accepted = res["accepted"]
        print(f"      ✓ event_id={res['id']}")
        print(f"      pubkey={res['pubkey']}  accepted={res['accepted']} relays")
        for relay, ok, detail in res["relays"]:
            mark = "✓" if ok else "✗"
            print(f"        [{mark}] {relay}" + (f" — {detail}" if not ok else ""))

    out = PREDICTIONS_DIR / f"{p.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(p.model_dump_json(indent=2))
    print(f"[3/3] saved → {out.relative_to(REPO)}")

    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-nostr", action="store_true", help="skip Nostr broadcast (Pinata only)")
    args = ap.parse_args()
    try:
        p = run_smoke(dry_nostr=args.dry_nostr)
        print("\n✅ E2E success.")
        print(json.dumps({
            "id": p.id,
            "cid": p.ipfs_cid,
            "nostr_event_id": p.nostr_event_id,
            "relays_accepted": p.nostr_relays_accepted,
        }, indent=2))
    except Exception as e:
        print(f"\n❌ E2E failed: {type(e).__name__}: {e}")
        sys.exit(1)
