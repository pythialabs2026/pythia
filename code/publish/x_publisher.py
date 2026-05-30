"""X (Twitter) v2 tweet publisher (OAuth 1.0a User Context).

Free tier: 1,500 POST tweets/월. 4-tuple(Consumer+Access) 사용.
"""
from __future__ import annotations

import json
import os
from typing import Any

from requests_oauthlib import OAuth1Session

from code.shared.secrets_loader import load as load_secrets

X_TWEETS_URL = "https://api.x.com/2/tweets"


class XPublishError(RuntimeError):
    pass


def _session() -> OAuth1Session:
    load_secrets("x_pythia")
    keys = {k: os.environ.get(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")}
    missing = [k for k, v in keys.items() if not v]
    if missing:
        raise XPublishError(f"missing X creds: {missing}")
    return OAuth1Session(
        client_key=keys["X_API_KEY"],
        client_secret=keys["X_API_SECRET"],
        resource_owner_key=keys["X_ACCESS_TOKEN"],
        resource_owner_secret=keys["X_ACCESS_TOKEN_SECRET"],
    )


def post_tweet(text: str, timeout: int = 30) -> dict[str, Any]:
    """tweet 게시. {"id": ..., "text": ...} 반환."""
    if not text or len(text) > 280:
        raise XPublishError(f"invalid tweet length {len(text)}")
    s = _session()
    r = s.post(X_TWEETS_URL, json={"text": text}, timeout=timeout)
    if r.status_code >= 400:
        raise XPublishError(f"x tweet failed: {r.status_code} {r.text[:400]}")
    data = r.json().get("data") or {}
    if "id" not in data:
        raise XPublishError(f"x tweet missing id: {r.text[:300]}")
    return {"id": data["id"], "text": data.get("text", text)}


if __name__ == "__main__":
    import argparse, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=f"Pythia smoke test {int(time.time())}")
    args = ap.parse_args()
    res = post_tweet(args.text)
    print(json.dumps(res, indent=2))
    print(f"https://x.com/PythiaLabsAi/status/{res['id']}")
