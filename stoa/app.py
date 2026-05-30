"""
FastAPI backend for 7:3 QQQM:TQQQ portfolio monitoring dashboard.

Endpoints:
    GET /              -> static/index.html
    GET /api/portfolio -> tossctl live holdings + 7:3 aggregate
    GET /api/shield    -> V5_Adv shield (QQQ>SMA200 AND VIX/VIX3M<1)
    GET /api/prices    -> 1y normalized QQQ / TQQQ / 70:30 blend
    GET /api/journal   -> last 20 lines of forecast_journal.md
    GET /api/bot_status-> v5_adv_state.json contents
    GET /healthz       -> liveness

Run:
    python3 app.py        # uvicorn on :8765
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth  # 멀티유저 인증 + 암호화 토스 세션 (같은 디렉토리)

COOKIE = "stoa_session"


def _atomic_write_json(path: pathlib.Path, obj: Any) -> None:
    """임시파일에 쓰고 os.replace로 원자적 교체 — 쓰기 중 충돌해도 깨지지 않음."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE = pathlib.Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"
INDEX_HTML = STATIC_DIR / "index.html"

STRATEGY_DIR = BASE.parent  # .../strategies/tqqq-dca
# STOA_DATA(=Docker 볼륨) 설정 시 런타임 영속 파일을 그쪽에, 없으면 기존 위치(호스트 호환)
DATA = pathlib.Path(os.environ["STOA_DATA"]) if os.environ.get("STOA_DATA") else None
JOURNAL_PATH = (DATA / "forecast_journal.md") if DATA else (STRATEGY_DIR / "committee" / "forecast_journal.md")
V5_STATE_PATH = (DATA / "v5_adv_state.json") if DATA else (STRATEGY_DIR / "bots" / "v5_adv_state.json")

# ⚠️ 레거시 정적 70:30 기준선 — 실제 목표는 동적 글라이드(/api/glide)가 산출.
#   portfolio 응답의 aggregate.drift_*는 이 정적 기준 대비 참고용일 뿐, 리밸 신호는 glide가 권위.
TARGET_QQQM = 0.70
TARGET_TQQQ = 0.30
DRIFT_BAND = 5.0  # percentage-point threshold for "yellow"

# ── 동적 레버리지 글라이드 설정 (최종 고정 전략 2026-05-21) ──
# r = 연 DCA / 현재자산 → 목표 레버리지 L = 1.6 + 0.4·(1-e^(-3r)), 천장 2.0x
#   (0.4 = GLIDE_CEIL - GLIDE_FLOOR = 2.0 - 1.6)
# (3-AI 권고: 실거래 1.6~2.0x 중심, 2.5x는 예외)
GLIDE_FLOOR = 1.6
GLIDE_CEIL = 2.0            # 실거래 천장 (이론 2.5x 대신 2.0x 보수 적용)
GLIDE_K = 3.0

# 공유 설정 파일 (웹앱 입력 ↔ 봇 단일 진실원)
GLIDE_CONFIG = (DATA / "glide_config.json") if DATA else (BASE.parent / "bots" / "glide_config.json")
DCA_DEFAULT_KRW = 100_000


def _read_dca() -> int:
    try:
        import json as _json
        with open(GLIDE_CONFIG, encoding="utf-8") as f:
            return int(_json.load(f).get("dca_monthly_krw", DCA_DEFAULT_KRW))
    except Exception:
        return DCA_DEFAULT_KRW


def _write_dca(krw: int) -> None:
    _atomic_write_json(GLIDE_CONFIG, {
        "dca_monthly_krw": int(krw),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "다음달 투자 예정액. 웹앱 입력칸 또는 직접 수정. 봇·웹앱 공유.",
    })


def _glide_leverage(r: float) -> float:
    import math
    L = GLIDE_FLOOR + (GLIDE_CEIL - GLIDE_FLOOR) * (1 - math.exp(-GLIDE_K * r))
    return max(GLIDE_FLOOR, min(GLIDE_CEIL, L))


def _lev_to_tqqq_weight(L: float) -> float:
    return max(0.0, min(1.0, (L - 1.0) / 2.0))

SHIELD_TTL_SEC = 5 * 60        # 5 minutes
PRICES_TTL_SEC = 60 * 60       # 1 hour
PORTFOLIO_TTL_SEC = 45         # tossctl subprocess 중복 호출 방지 (프론트 60초 폴링 < TTL)
_portfolio_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tqqq-dca-webapp")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="TQQQ-DCA 7:3 Dashboard", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://oracle.tailde4a99.ts.net",
        "http://127.0.0.1:8765",
        "http://localhost:8765",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Static mount (relative path matches CWD-independent layout, but use absolute)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
else:
    log.warning("static directory not found at %s", STATIC_DIR)

auth.init_db()


# ---------------------------------------------------------------------------
# 인증 (멀티유저: 회원가입/로그인/자동로그인 + 사용자별 격리)
# ---------------------------------------------------------------------------
def _uid(request: Request) -> Optional[int]:
    """쿠키 세션 → user_id (없거나 만료면 None). 자동로그인 = 쿠키 지속."""
    return auth.session_user(request.cookies.get(COOKIE))


def _require(request: Request) -> Optional[int]:
    return _uid(request)


def _set_cookie(resp: Response, token: str, ttl: int) -> None:
    resp.set_cookie(COOKIE, token, max_age=ttl, httponly=True, samesite="lax",
                    secure=True, path="/")


SIGNUP_CODE = os.environ.get("STOA_SIGNUP_CODE", "stoa-family")  # 가족·지인 초대코드


@app.post("/api/auth/signup")
def api_signup(response: Response, body: dict = Body(...)) -> Dict[str, Any]:
    if (body.get("invite") or "").strip() != SIGNUP_CODE:
        return JSONResponse({"ok": False, "why": "초대코드가 올바르지 않습니다"}, status_code=403)
    r = auth.create_user(body.get("email", ""), body.get("password", ""))
    if not r["ok"]:
        return JSONResponse(r, status_code=400)
    # 첫 사용자(소유자)면 서버의 기존 토스 세션을 본인 계정으로 자동 임포트(편의)
    if r["user_id"] == 1:
        try:
            default_sess = pathlib.Path.home() / ".config" / "tossctl" / "session.json"
            if default_sess.is_file():
                auth.set_toss_session(r["user_id"], default_sess.read_text(encoding="utf-8"))
        except Exception:
            pass
    tok, ttl = auth.create_session(r["user_id"], remember=True)
    _set_cookie(response, tok, ttl)
    return {"ok": True, "email": r["email"]}


@app.post("/api/auth/login")
def api_login(response: Response, body: dict = Body(...)) -> Dict[str, Any]:
    u = auth.verify_user(body.get("email", ""), body.get("password", ""))
    if not u:
        return JSONResponse({"ok": False, "why": "이메일 또는 비밀번호가 틀렸습니다"}, status_code=401)
    tok, ttl = auth.create_session(u["user_id"], remember=bool(body.get("remember", True)))
    _set_cookie(response, tok, ttl)
    return {"ok": True, "email": u["email"]}


@app.post("/api/auth/logout")
def api_logout(request: Request, response: Response) -> Dict[str, Any]:
    auth.destroy_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(request: Request) -> Dict[str, Any]:
    uid = _uid(request)
    if not uid:
        return {"ok": False}
    return {"ok": True, "email": auth.user_email(uid), "toss": auth.toss_status(uid)}


# ---------------------------------------------------------------------------
# Web Push (앱이 닫혀 있어도 폰에 알림 — 봇 cron이 신호 발생 시 호출)
# ---------------------------------------------------------------------------
PUSH_KEYS_PATH = (DATA / "push_keys.json") if DATA else (BASE / "push_keys.json")
PUSH_SUBS_PATH = (DATA / "push_subs.json") if DATA else (BASE / "push_subs.json")


def _push_keys() -> dict:
    try:
        with open(PUSH_KEYS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_subs() -> list:
    try:
        with open(PUSH_SUBS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_subs(subs: list) -> None:
    _atomic_write_json(PUSH_SUBS_PATH, subs)


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


@app.get("/api/push/key")
def api_push_key() -> Dict[str, Any]:
    pk = _push_keys().get("public_key")
    return {"ok": bool(pk), "public_key": pk}


@app.post("/api/push/subscribe")
def api_push_subscribe(request: Request, sub: dict = Body(...)) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    ep = sub.get("endpoint")
    keys = sub.get("keys") or {}
    if not isinstance(ep, str) or not ep.startswith("https://"):
        return {"ok": False, "why": "invalid endpoint"}
    if not (keys.get("p256dh") and keys.get("auth")):
        return {"ok": False, "why": "missing keys"}
    if len(json.dumps(sub)) > 4000:
        return {"ok": False, "why": "subscription too large"}
    auth.add_push_sub(uid, ep, keys["p256dh"], keys["auth"])
    return {"ok": True}


def _send_push_subs(subs: list, title: str, body: str, tag: str, url: str) -> dict:
    keys = _push_keys()
    if not keys.get("private_key") or not subs:
        return {"ok": False, "sent": 0, "why": "no keys or no subscribers"}
    from pywebpush import webpush, WebPushException
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
    sent = 0
    for s in subs:
        try:
            webpush(subscription_info=s, data=payload,
                    vapid_private_key=keys["private_key"],
                    vapid_claims={"sub": keys.get("claim_email", "mailto:admin@example.com")})
            sent += 1
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "sent": sent}


@app.post("/api/push/send")
def api_push_send(request: Request, msg: dict = Body(...)) -> Any:
    """봇이 호출 — localhost 전용. user_id 주면 그 사용자에게만, 없으면 전체."""
    if not _is_local(request):
        return JSONResponse({"ok": False, "why": "localhost only"}, status_code=403)
    title = str(msg.get("title", "Stoa"))[:120]; body = str(msg.get("body", ""))[:400]
    tag = str(msg.get("tag", "stoa"))[:40]; url = str(msg.get("url", "/"))[:200]
    uid = msg.get("user_id")
    if uid:
        subs = auth.get_push_subs(int(uid))
    else:   # 전체 사용자 (방패 등 시장공통 신호)
        subs = []
        with auth._conn() as c:
            for r in c.execute("SELECT DISTINCT user_id FROM push_subs").fetchall():
                subs += auth.get_push_subs(r["user_id"])
    return _send_push_subs(subs, title, body, tag, url)


# ---------------------------------------------------------------------------
# In-memory caches
# ---------------------------------------------------------------------------
_cache: Dict[str, Dict[str, Any]] = {
    "shield": {"ts": 0.0, "data": None},
    "crisis": {"ts": 0.0, "data": None},
    "prices": {"ts": 0.0, "data": None},
    "portfolio": {"ts": 0.0, "data": None},
}


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    entry = _cache.get(key)
    if not entry or entry["data"] is None:
        return None
    if (time.time() - entry["ts"]) > ttl:
        return None
    return entry["data"]


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = {"ts": time.time(), "data": data}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    # 서버가 UTC 타임존에서 가동되므로 *타임존 명시*된 ISO 반환
    # → 클라이언트(KST 등)에서 toLocaleString()이 올바른 로컬 시각으로 환산
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
LOGIN_HTML = STATIC_DIR / "login.html"


@app.get("/", include_in_schema=False)
def root(request: Request) -> Any:
    # 로그인 안 됐으면 로그인 페이지, 됐으면 대시보드
    if not _uid(request) and LOGIN_HTML.is_file():
        return FileResponse(str(LOGIN_HTML))
    if INDEX_HTML.is_file():
        return FileResponse(str(INDEX_HTML))
    return JSONResponse({"ok": False, "why": "index.html not found"}, status_code=200)


@app.get("/login", include_in_schema=False)
def login_page() -> Any:
    if LOGIN_HTML.is_file():
        return FileResponse(str(LOGIN_HTML))
    return JSONResponse({"ok": False}, status_code=404)


# --------------------------- /api/toss (사용자별 토스 세션 등록) ---------------
@app.get("/api/toss/status")
def api_toss_status(request: Request) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    return {"ok": True, **auth.toss_status(uid)}


@app.post("/api/toss/register")
def api_toss_register(request: Request, body: dict = Body(...)) -> Any:
    """본인 토스 세션 JSON 등록 (암호화 저장). tossctl auth login으로 얻은 session.json."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    r = auth.set_toss_session(uid, body.get("session", ""))
    if not r["ok"]:
        return JSONResponse(r, status_code=400)
    _pf_cache.pop(uid, None)   # 캐시 무효화 → 다음 조회 시 새 세션 사용
    return {"ok": True}


@app.delete("/api/toss")
def api_toss_delete(request: Request) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    auth.delete_toss(uid)
    _pf_cache.pop(uid, None)
    return {"ok": True}


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> Any:
    # 서비스워커는 루트 스코프 필요 → /static이 아닌 / 에서 서빙
    sw = BASE / "static" / "sw.js"
    if sw.is_file():
        return FileResponse(str(sw), media_type="application/javascript")
    return JSONResponse({"ok": False}, status_code=404)


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True, "ts": _now_iso()}


# ----------------------------- /api/portfolio ------------------------------
_pf_cache: Dict[int, Dict[str, Any]] = {}   # user_id → {"ts","data"}


@app.get("/api/portfolio")
def api_portfolio(request: Request) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    return _portfolio_for(uid)


def _portfolio_for(uid: int) -> Dict[str, Any]:
    """사용자별 캐시(45s)+락+stale. 각자 등록한 토스 세션으로 조회(읽기 전용)."""
    ent = _pf_cache.get(uid)
    if ent and (time.time() - ent["ts"]) <= PORTFOLIO_TTL_SEC:
        return ent["data"]
    with _portfolio_lock:
        ent = _pf_cache.get(uid)
        if ent and (time.time() - ent["ts"]) <= PORTFOLIO_TTL_SEC:
            return ent["data"]
        sess = auth.get_toss_session(uid)
        if not sess:
            return {"ok": False, "why": "toss_not_registered"}
        # 복호화한 세션을 임시파일에 써서 tossctl --session-file로 실행 후 즉시 삭제
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f".sess_{uid}_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(sess)
            os.chmod(tmp, 0o600)
            data = _compute_portfolio(session_file=tmp)
        finally:
            if tmp:
                try: os.unlink(tmp)
                except OSError: pass
        if data.get("ok"):
            _pf_cache[uid] = {"ts": time.time(), "data": data}
            return data
        if ent and ent["data"].get("ok"):   # stale-on-error
            stale = dict(ent["data"]); stale["stale"] = True
            stale["stale_reason"] = data.get("why")
            return stale
        return data


def _compute_portfolio(session_file: Optional[str] = None) -> Dict[str, Any]:
    """Live holdings via tossctl (읽기 전용 positions). order/매매 호출 절대 안 함."""
    cmd = ["tossctl"]
    if session_file:
        cmd += ["--session-file", session_file]
    cmd += ["portfolio", "positions", "--output", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        log.warning("tossctl binary not found")
        return {"ok": False, "why": "tossctl binary not found on PATH"}
    except subprocess.TimeoutExpired:
        log.warning("tossctl timed out")
        return {"ok": False, "why": "tossctl timed out after 20s"}
    except Exception as exc:  # noqa: BLE001
        log.warning("tossctl invocation failed: %s", exc)
        return {"ok": False, "why": f"tossctl invocation failed: {exc}"}

    if proc.returncode != 0:
        log.warning("tossctl rc=%s stderr=%s", proc.returncode, proc.stderr.strip())
        return {
            "ok": False,
            "why": f"tossctl rc={proc.returncode}: {proc.stderr.strip()[:300]}",
        }

    raw = proc.stdout.strip()
    if not raw:
        return {"ok": False, "why": "tossctl returned empty output"}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("tossctl JSON decode failed: %s", exc)
        return {"ok": False, "why": f"tossctl JSON decode failed: {exc}"}

    # The CLI is expected to emit a list of positions; tolerate {"positions": [...]}.
    if isinstance(parsed, dict) and "positions" in parsed:
        positions_raw = parsed["positions"]
    elif isinstance(parsed, list):
        positions_raw = parsed
    else:
        return {"ok": False, "why": "unexpected tossctl JSON shape"}

    positions: List[Dict[str, Any]] = []
    total_krw = 0.0
    total_usd = 0.0
    bucket: Dict[str, float] = {"QQQM": 0.0, "TQQQ": 0.0, "OTHER": 0.0}

    for p in positions_raw:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol", "")).upper()
        qty = _safe_float(p.get("quantity"))
        px_usd = _safe_float(p.get("current_price_usd"))
        mv_krw = _safe_float(p.get("market_value"))
        mv_usd = _safe_float(p.get("market_value_usd"))
        upnl = _safe_float(p.get("unrealized_pnl"))
        dpr = _safe_float(p.get("daily_profit_rate"))

        positions.append(
            {
                "symbol": sym,
                "quantity": qty,
                "current_price_usd": px_usd,
                "market_value": mv_krw,
                "market_value_usd": mv_usd,
                "unrealized_pnl": upnl,
                "daily_profit_rate": dpr,
            }
        )

        total_krw += mv_krw
        total_usd += mv_usd
        if sym == "QQQM":
            bucket["QQQM"] += mv_usd
        elif sym == "TQQQ":
            bucket["TQQQ"] += mv_usd
        else:
            bucket["OTHER"] += mv_usd

    if total_usd > 0:
        qqqm_ratio = bucket["QQQM"] / total_usd
        tqqq_ratio = bucket["TQQQ"] / total_usd
    else:
        qqqm_ratio = 0.0
        tqqq_ratio = 0.0

    drift_pp = (tqqq_ratio - TARGET_TQQQ) * 100.0

    if abs(drift_pp) >= DRIFT_BAND:
        drift_status = "red"
    elif abs(drift_pp) >= DRIFT_BAND * 0.5:
        drift_status = "yellow"
    else:
        drift_status = "green"

    aggregate = {
        "total_krw": total_krw,
        "total_usd": total_usd,
        "qqqm_value_usd": bucket["QQQM"],
        "tqqq_value_usd": bucket["TQQQ"],
        "other_value_usd": bucket["OTHER"],
        "qqqm_ratio": qqqm_ratio,
        "tqqq_ratio": tqqq_ratio,
        "target_qqqm": TARGET_QQQM,
        "target_tqqq": TARGET_TQQQ,
        "drift_pp": drift_pp,
        "drift_band": DRIFT_BAND,
        "drift_status": drift_status,
    }

    return {
        "ok": True,
        "positions": positions,
        "aggregate": aggregate,
        "last_updated": _now_iso(),
    }


# ------------------------------- /api/glide --------------------------------
@app.post("/api/glide_config")
def api_glide_config_set(request: Request, dca: int = Query(..., ge=0)) -> Any:
    """다음달 DCA 예정액 저장 (사용자별)."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    auth.set_dca(uid, dca)
    if uid == 1:          # 소유자: 봇이 읽는 glide_config.json도 동기화
        try: _write_dca(dca)
        except Exception: pass
    return {"ok": True, "dca_monthly_krw": int(dca)}


@app.get("/api/glide")
def api_glide(request: Request, dca: Optional[int] = Query(None, ge=0)) -> Any:
    """동적 레버리지 글라이드 (사용자별 자산 + DCA → 목표 비중 + 리밸 신호)."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    return _glide_for(uid, dca)


def _glide_for(uid: int, dca: Optional[int] = None) -> Dict[str, Any]:
    dca_krw = dca if (dca and dca > 0) else auth.get_dca(uid)
    pf = _portfolio_for(uid)
    if not pf.get("ok"):
        return {"ok": False, "why": pf.get("why", "portfolio 실패")}
    agg = pf["aggregate"]
    total_krw = agg["total_krw"]
    total_usd = agg.get("total_usd", 0)
    cur_tqqq = agg["tqqq_ratio"]
    # ⚠️ QQQM(1x)+TQQQ(3x)만 가정. OTHER 보유 시 cur_tqqq가 전체대비라 레버리지 과소평가됨.
    other_usd = agg.get("other_value_usd", 0) or 0
    cur_lev = 1.0 + 2.0 * cur_tqqq
    # r = 연 DCA / 현재자산
    r = (12 * dca_krw) / total_krw if total_krw > 0 else 0.0
    target_lev = _glide_leverage(r)
    target_tqqq = _lev_to_tqqq_weight(target_lev)
    target_qqqm = 1.0 - target_tqqq
    drift = (cur_tqqq - target_tqqq) * 100.0
    # 리밸 신호 (±5%p 밴드)
    if abs(drift) >= 5.0:
        rebal = "red"
    elif abs(drift) >= 3.0:
        rebal = "yellow"
    else:
        rebal = "green"
    # 리밸 금액
    tqqq_target_krw = total_krw * target_tqqq
    tqqq_cur_krw = total_krw * cur_tqqq
    move_krw = tqqq_target_krw - tqqq_cur_krw  # +면 TQQQ 매수

    # ── 구체적 지정가 주문 산출 ──
    # 현재가: USD(있음) + KRW(market_value/quantity로 유도)
    px = {"QQQM": {}, "TQQQ": {}}
    for p in pf.get("positions", []):
        sym = p.get("symbol")
        if sym in px:
            qty = _safe_float(p.get("quantity"))
            mv_krw = _safe_float(p.get("market_value"))
            px[sym]["usd"] = _safe_float(p.get("current_price_usd"))
            px[sym]["krw"] = (mv_krw / qty) if qty > 0 else 0.0
    # 환율 추정
    fx = 0.0
    if px["TQQQ"].get("usd") and px["TQQQ"].get("krw"):
        fx = px["TQQQ"]["krw"] / px["TQQQ"]["usd"]
    elif total_usd > 0:
        fx = total_krw / total_usd
    orders = []
    if abs(move_krw) > 1000 and px["TQQQ"].get("usd") and px["QQQM"].get("usd"):
        amt_krw = abs(move_krw)
        if move_krw > 0:   # TQQQ 매수 / QQQM 매도
            buy_sym, sell_sym = "TQQQ", "QQQM"
        else:
            buy_sym, sell_sym = "QQQM", "TQQQ"
        for sym, side in [(buy_sym, "buy"), (sell_sym, "sell")]:
            upx = px[sym]["usd"]
            kpx = px[sym].get("krw") or (upx * fx)
            shares = amt_krw / kpx if kpx > 0 else 0
            limit_usd = upx * (1.001 if side == "buy" else 0.999)
            orders.append({
                "ticker": sym, "side": side,
                "side_kr": "매수" if side == "buy" else "매도",
                "shares": round(shares, 2),
                "limit_usd": round(limit_usd, 2),
                "limit_krw": round(kpx * (1.001 if side == "buy" else 0.999)),
                "amount_krw": round(amt_krw),
                "current_usd": round(upx, 2),
            })

    return {
        "ok": True,
        "dca_monthly_krw": dca_krw,
        "r_pct": r * 100.0,
        "current_leverage": cur_lev,
        "current_tqqq_pct": cur_tqqq * 100.0,
        "target_leverage": target_lev,
        "target_tqqq_pct": target_tqqq * 100.0,
        "target_qqqm_pct": target_qqqm * 100.0,
        "drift_pp": drift,
        "rebal_status": rebal,
        "rebal_move_krw": move_krw,   # +TQQQ 매수 / -TQQQ 매도
        "orders": orders,             # 구체적 지정가 주문 (매수+매도)
        "glide_floor": GLIDE_FLOOR,
        "glide_ceil": GLIDE_CEIL,
        "last_updated": _now_iso(),
    }


# ------------------------------- /api/shield -------------------------------
def _compute_shield() -> Dict[str, Any]:
    try:
        import yfinance as yf  # local import so import errors are graceful
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"yfinance import failed: {exc}"}

    try:
        tickers = yf.download(
            tickers=["QQQ", "^VIX", "^VIX3M"],
            period="250d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"yfinance download failed: {exc}"}

    try:
        # yf.download with multiple tickers returns a column-multiindex.
        def close_series(sym: str):
            if (sym, "Close") in tickers.columns:
                return tickers[(sym, "Close")].dropna()
            if sym in tickers.columns:
                return tickers[sym]["Close"].dropna()
            return None

        qqq = close_series("QQQ")
        vix = close_series("^VIX")
        vix3m = close_series("^VIX3M")

        if qqq is None or qqq.empty:
            return {"ok": False, "why": "QQQ series empty"}
        if vix is None or vix.empty:
            return {"ok": False, "why": "VIX series empty"}
        if vix3m is None or vix3m.empty:
            return {"ok": False, "why": "VIX3M series empty"}

        qqq_close = float(qqq.iloc[-1])
        qqq_sma200 = float(qqq.tail(200).mean()) if len(qqq) >= 200 else float(qqq.mean())
        vix_last = float(vix.iloc[-1])
        vix3m_last = float(vix3m.iloc[-1])
        vix_ratio = vix_last / vix3m_last if vix3m_last else float("nan")

        qqq_above_pct = (qqq_close / qqq_sma200 - 1.0) * 100.0 if qqq_sma200 else 0.0

        shield_ok = bool(qqq_close > qqq_sma200 and vix_ratio < 1.0)

        return {
            "ok": True,
            "qqq_close": qqq_close,
            "qqq_sma200": qqq_sma200,
            "qqq_above_pct": qqq_above_pct,
            "vix": vix_last,
            "vix3m": vix3m_last,
            "vix_ratio": vix_ratio,
            "shield_ok": shield_ok,
            "last_updated": _now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"shield compute failed: {exc}"}


@app.get("/api/shield")
def api_shield() -> Dict[str, Any]:
    cached = _cache_get("shield", SHIELD_TTL_SEC)
    if cached is not None:
        return cached
    data = _compute_shield()
    if data.get("ok"):
        _cache_set("shield", data)
    return data



# ------------------------------- /api/prices -------------------------------
def _glide_path(qqq_norm, tqqq_norm, dates, uid: int) -> list:
    """과거 1년 동적 글라이드 경로(정규화 100 기준).

    월초마다 목표 비중 재설정. 목표 비중은 r=연DCA/자산에 따라 변동 —
    자산이 적던 연초엔 레버리지↑, 자산이 커진 최근엔 1.6x로 글라이드.
    자산 규모는 시뮬 지수를 사용자의 실제 현재 자산에 맞춰 스케일링(2-pass).
    """
    n = len(qqq_norm)
    if n < 2:
        return list(qqq_norm)

    def _rets(s):
        return [0.0] + [(s[i] / s[i - 1] - 1.0) if s[i - 1] else 0.0 for i in range(1, len(s))]

    rq, rt = _rets(qqq_norm), _rets(tqqq_norm)

    # 월초(리밸런싱 시점) 인덱스
    rebal = set()
    prev_m = None
    for i, d in enumerate(dates):
        m = d[:7]
        if m != prev_m:
            rebal.add(i)
            prev_m = m

    def simulate(weight_at):
        w0 = weight_at(100.0)
        q, t = 100.0 * (1.0 - w0), 100.0 * w0
        out = [100.0]
        for i in range(1, n):
            q *= (1.0 + rq[i]); t *= (1.0 + rt[i])
            P = q + t
            if i in rebal:
                w = weight_at(P)
                q, t = P * (1.0 - w), P * w
            out.append(round(P, 4))
        return out

    # 실제 현재 자산·DCA 기준 (없으면 기본값으로 폴백)
    dca = auth.get_dca(uid)
    try:
        agg = _portfolio_for(uid).get("aggregate", {})
        total_krw = float(agg.get("total_krw") or 0.0)
    except Exception:
        total_krw = 0.0
    if total_krw <= 0:
        # 자산 미상 → 현재 목표 비중 고정으로 폴백
        r0 = 0.18
        w0 = _lev_to_tqqq_weight(_glide_leverage(r0))
        return simulate(lambda P: w0)

    # pass 1: 현재 목표 비중 고정으로 종가 지수 → 스케일 산출
    r0 = (12 * dca) / total_krw
    w_const = _lev_to_tqqq_weight(_glide_leverage(r0))
    end_idx = simulate(lambda P: w_const)[-1] or 100.0
    scale = total_krw / end_idx  # 지수 1점 = ₩scale

    # pass 2: 자산(=지수×스케일)으로 매월 목표 비중 동적 산출
    def w_dyn(P):
        assets = P * scale
        r = (12 * dca) / assets if assets > 0 else 0.0
        return _lev_to_tqqq_weight(_glide_leverage(r))

    return simulate(w_dyn)


def _compute_prices(uid: int) -> Dict[str, Any]:
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"yfinance import failed: {exc}"}

    try:
        df = yf.download(
            tickers=["QQQ", "TQQQ"],
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"yfinance download failed: {exc}"}

    try:
        def close_series(sym: str):
            if (sym, "Close") in df.columns:
                return df[(sym, "Close")].dropna()
            if sym in df.columns:
                return df[sym]["Close"].dropna()
            return None

        qqq = close_series("QQQ")
        tqqq = close_series("TQQQ")
        if qqq is None or tqqq is None or qqq.empty or tqqq.empty:
            return {"ok": False, "why": "QQQ/TQQQ series empty"}

        # Align on common index
        joined = qqq.to_frame("QQQ").join(tqqq.to_frame("TQQQ"), how="inner").dropna()
        if joined.empty:
            return {"ok": False, "why": "no overlapping dates"}

        joined = joined.tail(252)

        qqq_first = float(joined["QQQ"].iloc[0])
        tqqq_first = float(joined["TQQQ"].iloc[0])

        qqq_norm = (joined["QQQ"] / qqq_first * 100.0).round(4).tolist()
        tqqq_norm = (joined["TQQQ"] / tqqq_first * 100.0).round(4).tolist()
        dates = [d.strftime("%Y-%m-%d") for d in joined.index]

        # 동적 글라이드 경로: 월별 리밸런싱 + 자산 성장에 따라 목표 비중 변동
        glide_path = _glide_path(qqq_norm, tqqq_norm, dates, uid)

        return {
            "ok": True,
            "dates": dates,
            "qqq": qqq_norm,
            "tqqq": tqqq_norm,
            "glide_path": glide_path,
            "last_updated": _now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"prices compute failed: {exc}"}


_prices_cache: Dict[int, Dict[str, Any]] = {}


@app.get("/api/prices")
def api_prices(request: Request) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    ent = _prices_cache.get(uid)
    if ent and (time.time() - ent["ts"]) <= PRICES_TTL_SEC:
        return ent["data"]
    data = _compute_prices(uid)
    if data.get("ok"):
        _prices_cache[uid] = {"ts": time.time(), "data": data}
    return data


# ------------------------------- /api/stress -------------------------------
STRESS_TTL_SEC = 6 * 60 * 60   # 6시간 (과거 데이터, 자주 안 변함)
_cache["stress"] = {"ts": 0.0, "data": None}

# 과거 폭락 구간 (패닉셀 사전 대비용)
_CRASH_WINDOWS = [
    ("닷컴버블", "2000-03-01", "2002-10-31"),
    ("금융위기", "2007-10-01", "2009-03-31"),
    ("코로나", "2020-02-15", "2020-04-30"),
    ("2022 긴축", "2021-11-01", "2022-12-31"),
]


def _compute_stress(uid: int) -> Dict[str, Any]:
    """현재 글라이드 목표 레버리지로 과거 폭락장 MDD (방패 ON/OFF)."""
    try:
        import yfinance as yf
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"import failed: {exc}"}

    # 현재 목표 레버리지·TQQQ 비중 (사용자별)
    try:
        g = _glide_for(uid)
        L = g.get("target_leverage", 1.77) if g.get("ok") else 1.77
        w = g.get("target_tqqq_pct", 38.0) / 100.0 if g.get("ok") else 0.38
    except Exception:
        L, w = 1.77, 0.38

    try:
        df = yf.download(["QQQ", "^VIX", "^VIX3M"], start="1999-03-10",
                         interval="1d", progress=False, auto_adjust=True, group_by="ticker", threads=True)
        def col(sym):
            if (sym, "Close") in df.columns: return df[(sym, "Close")].dropna()
            if sym in df.columns: return df[sym]["Close"].dropna()
            return None
        qqq = col("QQQ")
        if qqq is None or qqq.empty:
            return {"ok": False, "why": "QQQ empty"}
        vix = col("^VIX"); vix3m = col("^VIX3M")
        qret = qqq.pct_change().fillna(0)
        tret = (3 * qret - 0.00015).clip(lower=-0.99)        # 합성 TQQQ (3x daily)
        sma200 = qqq.rolling(200).mean()
        if vix is not None and vix3m is not None:
            vr = (vix / vix3m).reindex(qqq.index).ffill()
        else:
            vr = qqq * 0 + 0.9
        shield = ((qqq > sma200) & (vr < 1.0)).reindex(qqq.index).fillna(True)

        rows = []
        for name, s, e in _CRASH_WINDOWS:
            mask = (qqq.index >= s) & (qqq.index <= e)
            if mask.sum() < 20:
                continue
            # naked & shield MDD (구간 내)
            def win_mdd(use_shield, _mask=mask):
                eq = [1.0]
                idxs = np.where(_mask)[0]
                for k in range(1, len(idxs)):
                    i = idxs[k]
                    bret = (1 - w) * qret.iloc[i] + w * tret.iloc[i]
                    if use_shield and not bool(shield.iloc[i - 1]):
                        bret = (1 - w) * qret.iloc[i]
                    eq.append(eq[-1] * (1 + bret))
                eqa = np.array(eq)
                return float(((eqa / np.maximum.accumulate(eqa)) - 1).min() * 100)
            qqq_mdd = float(((qqq[mask] / qqq[mask].cummax()) - 1).min() * 100)
            rows.append({"name": name, "period": f"{s[:7]}~{e[:7]}",
                         "qqq_mdd": round(qqq_mdd, 1),
                         "naked_mdd": round(win_mdd(False), 1),
                         "shield_mdd": round(win_mdd(True), 1)})
        return {"ok": True, "target_leverage": round(L, 2), "tqqq_pct": round(w * 100),
                "windows": rows, "last_updated": _now_iso()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"stress compute failed: {exc}"}


_stress_cache: Dict[int, Dict[str, Any]] = {}


@app.get("/api/stress")
def api_stress(request: Request) -> Any:
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "why": "auth_required"}, status_code=401)
    ent = _stress_cache.get(uid)
    if ent and (time.time() - ent["ts"]) <= STRESS_TTL_SEC:
        return ent["data"]
    data = _compute_stress(uid)
    if data.get("ok"):
        _stress_cache[uid] = {"ts": time.time(), "data": data}
    return data


# ------------------------------- /api/journal ------------------------------
@app.get("/api/journal")
def api_journal() -> Dict[str, Any]:
    try:
        if not JOURNAL_PATH.is_file():
            return {"ok": False, "why": f"journal not found: {JOURNAL_PATH}"}
        mtime = JOURNAL_PATH.stat().st_mtime
        with JOURNAL_PATH.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = [ln.rstrip("\n") for ln in lines[-20:]]
        return {
            "ok": True,
            "lines": tail,
            "file_mtime": mtime,
            "file_mtime_iso": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "path": str(JOURNAL_PATH),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("journal read failed: %s", exc)
        return {"ok": False, "why": f"journal read failed: {exc}"}


# ------------------------------- /api/bot_status ---------------------------
@app.get("/api/bot_status")
def api_bot_status() -> Dict[str, Any]:
    try:
        if not V5_STATE_PATH.is_file():
            return {"ok": False, "why": f"state file not found: {V5_STATE_PATH}"}
        mtime = V5_STATE_PATH.stat().st_mtime
        with V5_STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
        out: Dict[str, Any] = {
            "ok": True,
            "file_mtime": mtime,
            "file_mtime_iso": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "path": str(V5_STATE_PATH),
        }
        if isinstance(state, dict):
            # Merge state at top level while preserving meta keys above
            for k, v in state.items():
                if k not in out:
                    out[k] = v
            out["state"] = state
        else:
            out["state"] = state
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("bot_status read failed: %s", exc)
        return {"ok": False, "why": f"bot_status read failed: {exc}"}


# ---------------------------------------------------------------------------
# Pythia Quant API
# ---------------------------------------------------------------------------
@app.get("/api/quant/base_rate")
def api_quant_base_rate(
    ticker: str = Query(..., description="Stock ticker (e.g. AAPL)"),
    event: str = Query(..., description="Event type (2.02, 1.01, 2.01, 5.02, earnings)"),
    cutoff: Optional[str] = Query(None, description="Cutoff datetime YYYY-MM-DD")
) -> Dict[str, Any]:
    try:
        import sys
        sys.modules.pop('code', None)
        if str(STRATEGY_DIR) not in sys.path:
            sys.path.insert(0, str(STRATEGY_DIR))
        
        from code.research.equity_base_rate import calculate_base_rate
        
        cutoff_str = cutoff or datetime.now(timezone.utc).isoformat()
        res = calculate_base_rate(ticker, event, cutoff_str)
        return res
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/quant/status")
def api_quant_status() -> Dict[str, Any]:
    try:
        freeze_path = STRATEGY_DIR / "data" / "research" / "backtests" / "oos_forward_2026-05-30" / "freeze.json"
        results_path = STRATEGY_DIR / "data" / "research" / "backtests" / "oos_forward_2026-05-30" / "oos_forward_results.json"
        
        freeze_data = {}
        if freeze_path.is_file():
            with freeze_path.open("r", encoding="utf-8") as f:
                freeze_data = json.load(f)
                
        results_data = {}
        if results_path.is_file():
            with results_path.open("r", encoding="utf-8") as f:
                results_data = json.load(f)
                
        return {
            "status": "success",
            "has_freeze": freeze_path.is_file(),
            "has_results": results_path.is_file(),
            "freeze": freeze_data,
            "results": results_data,
        }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


_eval_thread_running = False
_eval_last_log = ""

def _run_eval_bg():
    global _eval_thread_running, _eval_last_log
    _eval_thread_running = True
    try:
        script = STRATEGY_DIR / "code" / "research" / "evaluate_oos.py"
        import sys
        proc = subprocess.run(
            [sys.executable, str(script), "--allow-early"],
            capture_output=True, text=True, cwd=str(STRATEGY_DIR),
            timeout=180
        )
        _eval_last_log = proc.stdout + "\n" + proc.stderr
    except Exception as exc:
        _eval_last_log = f"Execution failed: {exc}"
    finally:
        _eval_thread_running = False


@app.post("/api/quant/run_eval")
def api_quant_run_eval() -> Dict[str, Any]:
    global _eval_thread_running, _eval_last_log
    if _eval_thread_running:
        return {"status": "running", "msg": "Evaluation is already running in background"}
    
    threading.Thread(target=_run_eval_bg, daemon=True).start()
    return {"status": "started", "msg": "Evaluation started in background"}


@app.get("/api/quant/eval_status")
def api_quant_eval_status() -> Dict[str, Any]:
    global _eval_thread_running, _eval_last_log
    return {
        "running": _eval_thread_running,
        "log": _eval_last_log
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    log.info("starting TQQQ-DCA dashboard on :8765 (base=%s)", BASE)
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
    )
