"""auth.py — 멀티유저 인증 + 암호화된 토스 세션 저장 (SQLite, stdlib + cryptography).

설계 원칙(공개 노출 대비):
  - 비밀번호: pbkdf2_hmac(sha256, 200k iters) + per-user salt. 평문 저장 X.
  - 세션: 랜덤 토큰(쿠키), 서버측 만료 관리. remember=장기, 아니면 단기.
  - 토스 세션: Fernet 대칭암호로 *암호화 저장*. 마스터키는 파일(0600).
  - 토스는 읽기 전용만 사용 (order/매매 절대 호출 안 함 — app.py에서 강제).
  - 사용자별 완전 격리: settings·push·toss 모두 user_id 스코프.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import pathlib
import secrets
import sqlite3
import time
from typing import Optional

from cryptography.fernet import Fernet

BASE = pathlib.Path(__file__).resolve().parent
# STOA_DATA 설정 시(=Docker 볼륨) 영속 파일을 그쪽에, 없으면 기존 위치(호스트 호환)
_DATA = pathlib.Path(os.environ["STOA_DATA"]) if os.environ.get("STOA_DATA") else BASE
_DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA / "stoa.db"
MASTER_KEY_PATH = _DATA / "master.key"

SESSION_SHORT = 12 * 3600          # 12시간 (remember 미체크)
SESSION_LONG = 60 * 24 * 3600      # 60일 (자동로그인)
PBKDF2_ITERS = 200_000


# ── 마스터키 (토스 세션 암호화) ───────────────────────────
def _fernet() -> Fernet:
    if not MASTER_KEY_PATH.exists():
        key = Fernet.generate_key()
        MASTER_KEY_PATH.write_bytes(key)
        os.chmod(MASTER_KEY_PATH, 0o600)
    return Fernet(MASTER_KEY_PATH.read_bytes())


# ── DB ────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created REAL NOT NULL,
            expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings(
            user_id INTEGER PRIMARY KEY,
            dca_krw INTEGER NOT NULL DEFAULT 100000,
            updated REAL
        );
        CREATE TABLE IF NOT EXISTS toss(
            user_id INTEGER PRIMARY KEY,
            enc_session BLOB NOT NULL,
            updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_subs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            UNIQUE(user_id, endpoint)
        );
        """)


# ── 비밀번호 ───────────────────────────────────────────────
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2${PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, int(iters))
        return secrets.compare_digest(dk, expected)
    except Exception:
        return False


# ── 유저 ──────────────────────────────────────────────────
def create_user(email: str, pw: str) -> dict:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        return {"ok": False, "why": "이메일 형식이 올바르지 않습니다"}
    if len(pw or "") < 8:
        return {"ok": False, "why": "비밀번호는 8자 이상"}
    try:
        with _conn() as c:
            cur = c.execute("INSERT INTO users(email, pw_hash, created) VALUES(?,?,?)",
                            (email, hash_pw(pw), time.time()))
            uid = cur.lastrowid
            c.execute("INSERT INTO settings(user_id, dca_krw, updated) VALUES(?,?,?)",
                      (uid, 100000, time.time()))
        return {"ok": True, "user_id": uid, "email": email}
    except sqlite3.IntegrityError:
        return {"ok": False, "why": "이미 가입된 이메일입니다"}


def verify_user(email: str, pw: str) -> Optional[dict]:
    email = (email or "").strip().lower()
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row and verify_pw(pw, row["pw_hash"]):
        return {"user_id": row["id"], "email": row["email"]}
    return None


def user_email(user_id: int) -> Optional[str]:
    with _conn() as c:
        row = c.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    return row["email"] if row else None


# ── 세션 (쿠키 토큰) ───────────────────────────────────────
def create_session(user_id: int, remember: bool) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    ttl = SESSION_LONG if remember else SESSION_SHORT
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id,created,expires) VALUES(?,?,?,?)",
                  (token, user_id, now, now + ttl))
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))  # 만료 정리
    return token, ttl


def session_user(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    with _conn() as c:
        row = c.execute("SELECT user_id, expires FROM sessions WHERE token=?", (token,)).fetchone()
    if row and row["expires"] > time.time():
        return row["user_id"]
    return None


def destroy_session(token: Optional[str]) -> None:
    if token:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ── 설정 (사용자별 DCA) ────────────────────────────────────
def get_dca(user_id: int) -> int:
    with _conn() as c:
        row = c.execute("SELECT dca_krw FROM settings WHERE user_id=?", (user_id,)).fetchone()
    return int(row["dca_krw"]) if row else 100000


def set_dca(user_id: int, krw: int) -> None:
    with _conn() as c:
        c.execute("INSERT INTO settings(user_id,dca_krw,updated) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET dca_krw=?, updated=?",
                  (user_id, int(krw), time.time(), int(krw), time.time()))


# ── 토스 세션 (암호화 저장) ────────────────────────────────
def set_toss_session(user_id: int, session_json: str) -> dict:
    try:
        json.loads(session_json)   # 유효 JSON 검증
    except Exception:
        return {"ok": False, "why": "세션 JSON이 올바르지 않습니다"}
    enc = _fernet().encrypt(session_json.encode())
    with _conn() as c:
        c.execute("INSERT INTO toss(user_id,enc_session,updated) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET enc_session=?, updated=?",
                  (user_id, enc, time.time(), enc, time.time()))
    return {"ok": True}


def get_toss_session(user_id: int) -> Optional[str]:
    with _conn() as c:
        row = c.execute("SELECT enc_session FROM toss WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    try:
        return _fernet().decrypt(row["enc_session"]).decode()
    except Exception:
        return None


def toss_status(user_id: int) -> dict:
    with _conn() as c:
        row = c.execute("SELECT updated FROM toss WHERE user_id=?", (user_id,)).fetchone()
    return {"registered": bool(row), "updated": row["updated"] if row else None}


def delete_toss(user_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM toss WHERE user_id=?", (user_id,))


# ── 사용자별 푸시 구독 ─────────────────────────────────────
def add_push_sub(user_id: int, endpoint: str, p256dh: str, auth_k: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO push_subs(user_id,endpoint,p256dh,auth) VALUES(?,?,?,?)",
                  (user_id, endpoint, p256dh, auth_k))


def get_push_subs(user_id: int) -> list:
    with _conn() as c:
        rows = c.execute("SELECT endpoint,p256dh,auth FROM push_subs WHERE user_id=?", (user_id,)).fetchall()
    return [{"endpoint": r["endpoint"], "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}} for r in rows]


def del_push_sub(user_id: int, endpoint: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", (user_id, endpoint))


if __name__ == "__main__":
    init_db()
    print("DB 초기화 완료:", DB_PATH)
