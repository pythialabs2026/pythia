"""Nostr publisher — Kind 1 text-note 게시.

X 유료화 회피용 무료 대체. BIP-340 Schnorr 서명 + secp256k1.
릴레이 다중 broadcast: 최소 1개 OK 받으면 성공.

자격증명: `nostr_credentials.env` 의 NOSTR_NSEC (32-byte hex). 없으면 즉시 RuntimeError —
새 키는 `generate_nostr_key.py` 로 발급 후 BW vault + secrets 양쪽 저장.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any, Iterable

import websockets
from coincurve import PrivateKey, PublicKeyXOnly

from code.shared.secrets_loader import load as load_secrets

DEFAULT_RELAYS = (
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://nostr.band",
    "wss://relay.snort.social",
)


class NostrPublishError(RuntimeError):
    pass


def _privkey() -> PrivateKey:
    load_secrets("nostr")
    nsec_hex = os.environ.get("NOSTR_NSEC")
    if not nsec_hex:
        raise NostrPublishError("NOSTR_NSEC not in env after secrets load")
    return PrivateKey(bytes.fromhex(nsec_hex.strip()))


def _xonly_pub(priv: PrivateKey) -> str:
    """x-only 공개키 (32 bytes hex) — BIP-340 Schnorr 식별자."""
    compressed = priv.public_key.format(compressed=True)
    return compressed[1:].hex()


def _event_id(pub_hex: str, created_at: int, kind: int, tags: list[list[str]], content: str) -> str:
    """NIP-01: id = sha256(JSON([0, pub, ts, kind, tags, content]))."""
    serial = json.dumps(
        [0, pub_hex, created_at, kind, tags, content],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serial.encode()).hexdigest()


def build_event(content: str, kind: int = 1, tags: list[list[str]] | None = None) -> dict[str, Any]:
    """서명된 Nostr event dict."""
    priv = _privkey()
    pub = _xonly_pub(priv)
    ts = int(time.time())
    tags = tags or []
    eid_hex = _event_id(pub, ts, kind, tags, content)
    sig = priv.sign_schnorr(bytes.fromhex(eid_hex)).hex()
    return {
        "id": eid_hex,
        "pubkey": pub,
        "created_at": ts,
        "kind": kind,
        "tags": tags,
        "content": content,
        "sig": sig,
    }


async def _broadcast_one(relay: str, event: dict[str, Any], timeout: float) -> tuple[str, bool, str]:
    msg = json.dumps(["EVENT", event])
    try:
        async with websockets.connect(relay, open_timeout=timeout, close_timeout=2, max_size=2**20) as ws:
            await asyncio.wait_for(ws.send(msg), timeout=timeout)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, list) and len(data) >= 3 and data[0] == "OK" and data[1] == event["id"]:
                    ok = bool(data[2])
                    detail = data[3] if len(data) > 3 else ""
                    return (relay, ok, str(detail))
            return (relay, False, "no OK within timeout")
    except Exception as e:
        return (relay, False, f"{type(e).__name__}: {e}")


async def _broadcast_all(event: dict[str, Any], relays: Iterable[str], timeout: float) -> list[tuple[str, bool, str]]:
    return await asyncio.gather(*[_broadcast_one(r, event, timeout) for r in relays])


def publish_nostr(
    content: str,
    relays: Iterable[str] = DEFAULT_RELAYS,
    timeout: float = 8.0,
    tags: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Kind 1 텍스트 노트 게시. 최소 1릴레이 OK면 성공.

    반환: {"id": event_id, "pubkey": x-only hex, "relays": [(relay, ok, detail), ...], "accepted": int}
    """
    if not content or len(content.encode()) > 16_000:
        raise NostrPublishError(f"content invalid (len bytes={len(content.encode())})")
    event = build_event(content, kind=1, tags=tags or [])
    results = asyncio.run(_broadcast_all(event, list(relays), timeout))
    accepted = sum(1 for _, ok, _ in results if ok)
    if accepted == 0:
        raise NostrPublishError(f"no relay accepted: {results}")
    return {
        "id": event["id"],
        "pubkey": event["pubkey"],
        "relays": results,
        "accepted": accepted,
    }


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=f"Pythia Nostr smoke {int(time.time())}")
    args = ap.parse_args()
    try:
        res = publish_nostr(args.text)
        print(json.dumps({"id": res["id"], "pubkey": res["pubkey"], "accepted": res["accepted"]}, indent=2))
        for relay, ok, detail in res["relays"]:
            print(f"  [{'✓' if ok else '✗'}] {relay} — {detail}")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        sys.exit(1)
