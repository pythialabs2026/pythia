# 🏛️ 7:3 Portfolio Monitor — 실시간 웹앱

QQQM 70% / TQQQ 30% 잠긴 정책의 실시간 관찰 대시보드.

## 무엇을 보여주나

1. **총 자산 평가액** (₩/$) — tossctl LIVE
2. **드리프트 게이지** — 현재 비율 vs 목표 70:30, ±5%p 트리거 표시
3. **V5_Adv 이중 방패** — QQQ vs SMA200, VIX/VIX3M < 1.0
4. **1년 정규화 곡선** — QQQ (1x) / TQQQ (3x) / 70:30 블렌드 (≈1.6x) 비교
5. **보유 포지션** — 종목/수량/현재가/평가액/일일 손익
6. **예측 저널** — 최근 20행 (forecast_journal.md)
7. **V5_Adv 봇 상태** — 현재 자산/현금 상태 + 지표 스냅샷

## 🧠 3-AI 협업 분담

| AI | 역할 | 산출물 |
|---|---|---|
| **Claude** | 아키텍처·통합·프론트엔드·QA | `static/index.html` + 통합 검증 |
| **GPT-5.5 (Codex)** | 백엔드 FastAPI 구현 | `app.py` (7 endpoints) |
| **Gemini** | UI 디자인 패턴 (Bloomberg/Tufte) | 색상·정보계층 가이드라인 |

## 실행

### 1) 서버 시작 (이미 가동 중이면 skip)

```bash
cd /home/ubuntu/runtime/apps/stock/current/strategies/tqqq-dca/webapp
python3 app.py
# 또는 백그라운드:
nohup python3 app.py > server.log 2>&1 &
```

서버 주소: **http://0.0.0.0:8765**

### 2) 접속

#### a) 로컬 서버에서 직접
```
http://127.0.0.1:8765/
```

#### b) SSH 포트 포워딩 (원격 PC에서 보기)
```bash
# PC 측에서:
ssh -L 8765:127.0.0.1:8765 ubuntu@<runtime_host>
# → 브라우저에서 http://127.0.0.1:8765/
```

#### c) 같은 네트워크 PC/모바일
```
http://<runtime_ip>:8765/
```

### 3) 자동 갱신
- portfolio·shield·journal·bot: **5분마다**
- prices(차트): **1시간마다**
- 키보드 `R`: 즉시 새로고침

## 백엔드 API (FastAPI)

| Endpoint | 설명 | 캐시 |
|---|---|---|
| `GET /` | index.html 서빙 | — |
| `GET /healthz` | 헬스체크 | — |
| `GET /api/portfolio` | tossctl LIVE 보유 + 집계 | 없음 |
| `GET /api/shield` | V5_Adv 방패 (yfinance) | 5분 |
| `GET /api/prices` | 1년 정규화 시계열 | 1시간 |
| `GET /api/journal` | forecast_journal.md 최근 20행 | 없음 |
| `GET /api/bot_status` | v5_adv_state.json | 없음 |
| `GET /docs` | Swagger UI | — |

## 데이터 출처

- **tossctl** — 보유 포지션 (read-only, LIVE)
- **yfinance** — QQQ/^VIX/^VIX3M/TQQQ 일봉
- **로컬 파일** — `forecast_journal.md`, `v5_adv_state.json`

## 키 상태 색상 코드

| 색 | 의미 |
|---|---|
| 🟢 그린 | 정상 (do nothing) |
| 🟡 옐로우 | 주의 (드리프트 3~5%p, 다음 DCA로 조정) |
| 🔴 레드 | 트리거 (드리프트 5%p+ 또는 방패 OFF) |

## 트러블슈팅

```bash
# 서버 로그
tail -50 server.log

# tossctl 직접 테스트
tossctl portfolio positions --output json | head

# yfinance 의존성
python3 -c "import yfinance; print(yfinance.__version__)"

# 포트 충돌 시 (8765 사용 중)
lsof -i :8765
# app.py 마지막 줄 port 변경
```

## 의도적으로 *안* 한 것

- **자동 매매** ❌ — 보고만, 사용자가 토스 앱에서 직접 실행
- **실시간 차트** ❌ — 5분 폴링이면 충분 (HFT 아님)
- **알림 발송** ❌ — 별도 봇(`v5_adv_bot.py`)이 담당
- **계산 권고 변경** ❌ — 잠긴 IPS 그대로 표시만

## 확장 후보 (지금은 *안* 함)

- 분기 리밸런싱 다음 일자 카운트다운
- DCA 입금 시뮬레이터 (₩X 추가 시 비율 변화)
- 모바일 푸시 (PWA 변환)
- Grafana/Prometheus 연동

## 권위 파일

- `app.py` — FastAPI 백엔드 (15.5KB)
- `static/index.html` — 프론트엔드 SPA (17.7KB)
- `server.log` — 실행 로그
- `../ai_committee/ips.yaml` — 잠긴 결정 정의
- `../ai_committee/v5_adv_state.json` — 봇 상태 영구화
