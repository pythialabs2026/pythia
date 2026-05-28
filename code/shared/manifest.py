"""Append-only manifest helper. raw 파일 ingest 시 sha256·rows·bytes 기록."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path("/home/ubuntu/pythia/data/raw/_manifest.jsonl")
INGESTER_VERSION = "0.1.0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def append(source: str, path: Path, rows: int) -> None:
    rel = str(path.relative_to(Path("/home/ubuntu/pythia/data/raw")))
    entry = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "path": rel,
        "sha256": sha256_file(path),
        "rows": rows,
        "bytes": path.stat().st_size,
        "ingester_version": INGESTER_VERSION,
    }
    with MANIFEST_PATH.open("a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
