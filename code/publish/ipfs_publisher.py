"""Pinata IPFS publisher.

예측 JSON을 IPFS에 핀해서 CID를 돌려준다. JWT 인증.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from code.shared.secrets_loader import load as load_secrets

PINATA_PIN_JSON_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"


class PinataError(RuntimeError):
    pass


def pin_json(payload: dict[str, Any], name: str | None = None, timeout: int = 30) -> dict[str, Any]:
    """payload 를 Pinata 에 pin. {"cid": ..., "size": ..., "ts": ...} 반환."""
    load_secrets("pinata")
    jwt = os.environ.get("PINATA_JWT")
    if not jwt:
        raise PinataError("PINATA_JWT not in env after secrets load")

    body: dict[str, Any] = {"pinataContent": payload}
    if name:
        body["pinataMetadata"] = {"name": name}

    r = requests.post(
        PINATA_PIN_JSON_URL,
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise PinataError(f"pinata pin failed: {r.status_code} {r.text[:300]}")
    data = r.json()
    return {"cid": data["IpfsHash"], "size": data["PinSize"], "ts": data["Timestamp"]}


def gateway_url(cid: str) -> str:
    """Public IPFS gateway URL (검증 보조용)."""
    return f"https://gateway.pinata.cloud/ipfs/{cid}"


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="hello pythia", help="quick test payload")
    args = ap.parse_args()
    res = pin_json({"hello": "pythia", "msg": args.text}, name="pythia-smoke")
    print(json.dumps(res, indent=2))
    print(gateway_url(res["cid"]))
