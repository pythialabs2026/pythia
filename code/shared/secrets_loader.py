"""Load Pythia 자격증명 env 파일을 os.environ 에 머지."""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

SECRETS_DIR = Path("/home/ubuntu/.claude/shared/secrets")


def load(*names: str) -> None:
    """이름 목록(`pinata`, `x_pythia` 등)에 해당하는 `<name>_credentials.env` 를 로드.

    파일이 없으면 FileNotFoundError. 호출자가 즉시 실패하도록 함.
    """
    for name in names:
        path = SECRETS_DIR / f"{name}_credentials.env"
        if not path.exists():
            raise FileNotFoundError(f"missing secrets file: {path}")
        load_dotenv(path, override=False)
