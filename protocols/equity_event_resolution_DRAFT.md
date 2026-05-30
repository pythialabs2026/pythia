# Pythia 주식 이벤트 트랙 — 해소 프로토콜 (DRAFT, 미봉인)

> **상태: DRAFT — 사전등록(pre-registration) 안 됨, 봉인(sealed) 안 됨.**
> 이 문서는 가역적 초안이다. cohort 토론(2026-05-29, C 만장일치) 결정에 따라
> 해소·baseline 룰이 모두 확정될 때까지 **사전등록 보류**. 5/30 Polymarket 코호트와 동시 등록 안 함.
> 편집 자유(봉인 전). 봉인 후에는 amendment로만 변경 가능.

## 출처
3사 교차검증 토론 (`debate --critique`, 2026-05-29):
- `~/runtime/logs/pythia_equity_target_claim.json` — **A 만장일치** (claude c88 / gemini c95 / gpt c70)
- `~/runtime/logs/pythia_equity_target_baseline.json` — ⚠ NO_MAJORITY (실질 2:1, 미확정·보류)
- `~/runtime/logs/pythia_equity_target_cohort.json` — **C 만장일치** (사전등록 보류, c86/95/85)

---

## 1. 해소 가능 이진명제 (CONFIRMED — A 만장일치)

**규칙 A**: `공시 후 첫 정규장 종가 > 공시 직전 정규장 종가 → Yes, 아니면 No`
- horizon **N = 1 거래일**
- 임계 **X = 0%** (단순 방향, 큰 move 필터 없음)
- 채점: Brier score (이진)

### 임계 0을 택한 이유
임의 컷오프(+2% 등)는 자의성·조작 시비를 만들고, 임계 0이 신호대잡음·검증가능성·조작불가성에서 최선. base-rate가 진실에 가깝게 측정됨.

### Claude의 명세 조건 (만장일치 채택의 전제 — 봉인 전 반드시 확정)
1. **종가 소스 고정**: 어느 데이터 소스의 official close를 쓸지 단일 지정 (예: 해당 거래소 공식 정규장 종가; 무료 소스 일관성). pre-market/after-hours 제외.
2. **Corporate-action 조정정책**: 배당락·액면분할·병합 발생 시 adjusted close 사용 여부 명시. (방향 판정이 분할로 뒤집히지 않도록 split-adjusted 기준 권장.)
3. **거래정지/tie 규칙**:
   - 첫 정규장 거래정지(halt)·미개장 시 T0 재정의 규칙 (다음 정규장으로 이월 등).
   - 종가가 직전 종가와 **정확히 동일(=0%)** 일 때의 판정 (No로 처리할지, void로 처리할지 — 사전 명시).
4. **사전봉인(sealed)**: 명제·T0·종가소스·조정정책·tie규칙을 예측 publish **전에** 동결. 이후 amendment로만 변경.

### T0 정의
T0 = 8-K/어닝 공시 **직후 첫 정규 거래 세션**. "직전 종가" = 공시 직전 마지막 정규장 종가.
- 공시 시각 기준: 8-K = SEC EDGAR `file_date`/acceptance datetime(ET). 어닝 = 발표가 장중이면 당일 정규장이 직전, 장마감 후/장전이면 직전 정규장 종가가 기준.
- 장중 공시: 공시 *후* 첫 **완결된** 정규장이 T0+1. (공시 당일 잔여 세션은 T0 아님 — 부분 세션 종가 배제.)

### §1 구체값 (2026-05-29 확정)
1. **종가 소스 (단일 정본)**: **Stooq** 일봉 정규장 종가 (`https://stooq.com/q/d/l/?s=<ticker>.us&i=d`, 무료·무인증·CSV·재현가능). 교차검증: yfinance 일봉 종가. 두 소스 차가 0.5% 초과면 resolution 보류 후 수동 amendment. 정규장만 — pre/post-market 배제.
2. **스냅샷 타이밍**: T0+1 거래일 **장마감 +3시간**(미 동부 19:00 ET) 1차 기록 → **T+2 재확인**(소스 정정 반영). 재확인값이 정본. 두 값·정정 여부 resolution record에 보존.
3. **Corporate-action 조정 (구간 한정·재현가능)**: 기본은 raw 정규장 종가 양 끝점 비교. 단 prior-close ~ T0+1-close **구간 내** 코퍼릿액션 발생 시만 조정:
   - 액면분할/병합: 분할비율로 T0+1 종가 환산.
   - 배당락(ex-div가 구간 내): 주당 배당금을 T0+1 종가에 **가산**(total-return 기준 — 기계적 ex-div 하락이 거짓 "하락" 안 만들게).
   - ⚠ Yahoo의 소급 adjusted-close는 **사용 안 함**(미래 배당으로 재조정돼 봉인 예측 재현성 깨짐). 조정계수는 resolution 시점에 스냅샷해 record에 박제.
4. **tie / 거래정지**:
   - **tie**(T0+1 종가 = 직전 종가, 센트 정밀도): 명제가 strict "higher than"이므로 → **No**. void 아님(재현·결정론).
   - **halt/미거래**: T0+1에 정규장 종가가 안 찍히면 다음 정규장으로 **이월**. 최대 **5거래일**까지 롤; 그래도 종가 없으면 → **void**(채점 제외, 공개 disclosure).
   - **half-day(조기마감)**: 정규장 종가 존재 → 유효.
5. **사전봉인 절차**: publish 전 record 동결 → 필드 {claim text, ticker, event_id(8-K accession 또는 earnings_date), T0 정의, 종가소스=Stooq, 조정정책, tie규칙, predicted_prob, ts}. sha256 → `ledger.jsonl` append + IPFS pin + Nostr Kind 1 publish([[pythia-nostr-pivot-2026-05-28]]). CID/event-id가 불변 타임스탬프 증명. 이후 append-only, amendment는 링크된 신규 record로만.

---

## 2. 이길 baseline (CONFIRMED — 계층형, 사용자 결정 2026-05-29)

토론은 NO_MAJORITY(claude·gemini=B / gpt=C)였으나, 세 입장은 충돌이 아니라 층위로 통합.
사용자 선택: **계층형(layered)**.

- **A (floor / 바닥선)**: 50:50 무정보 = Brier 0.25. 이걸 못 이기면 무의미. 자동 최소 합격선.
- **B (1차 의무 기준선)**: 과거 동종 이벤트 base-rate (예: 어닝후 상승확률 historical drift).
  → **합격 판정선.** B를 통계적으로 유의하게 이기면 "개미보다 낫다" 입증.
- **C (도전 기준 / 보조)**: 시장 컨센서스·옵션 내재 방향. 무료 근사 가능한 대형주 한정 보조 채점.
  → C까지 이기면 진짜 엣지(보너스). **못 이겨도 실패 아님.**

### §2 구체값 (2026-05-29 확정)

**B — base-rate 산출 (1차 합격선)**
- **이벤트 버킷**: 8-K item 코드별 분리(2.02 실적 / 1.01 계약 / 2.01 인수 / 5.02 임원) + 어닝(2.02와 별도 취급). 버킷마다 고유 base-rate.
- **룩백**: T0 직전까지의 데이터만(look-ahead 금지). trailing 동종 이벤트 **최근 60건** 또는 **252거래일** 중 표본 더 많은 쪽.
- **산출식**: 과거 동종 이벤트에 §1 규칙(익일 종가 > 직전)을 그대로 적용한 **상승 빈도** = base-rate. "매번 역사적 상승률을 그대로 예측"하는 naive 기준선.
- **폴백 계층**(표본 부족 시): ticker-specific → (sector + item) 풀링 → (전체 + item) 풀링. 최소 표본 미달이면 상위 풀로.
- **산출은 PC 위임**(과거 8-K×가격 결합 = 네트워크+dataframe, >5s): EDGAR full-text(2001+)로 과거 동종 이벤트 일자 + Stooq/yfinance 과거 종가 → `mcp__pc__pc_python`. 결과 parquet → Oracle pull.

**유의성 판정 ("이겼다" 선언 조건)**
- 코호트 누적 **resolved ≥ 30건** 전엔 어떤 "baseline 이김" 주장도 금지.
- 메트릭: **Brier Skill Score** BSS = 1 − Brier_pred / Brier_base.
- 판정: 예측-기준선 per-prediction Brier 차에 **부트스트랩(resample) 95% CI**. CI 하한 > 0 일 때만 "B를 유의하게 이김" 선언. (보조로 paired Wilcoxon one-sided.)
- A(floor) 동시 보고: Brier_pred < 0.25 못 넘으면 무정보 — 자동 실격.

**C — 도전·보조 채점 (못 이겨도 실패 아님)**
- **대상 종목 집합**: 옵션 유동성 충분한 대형주 = `equity_events.py`의 `DEFAULT_WATCHLIST` 20종(AAPL·MSFT·NVDA·GOOGL·AMZN·META·TSLA·AVGO·AMD·NFLX·PLTR·MU·TSM·ASML·ARM·SMCI·COIN·MSTR·INTC·QCOM). 이 집합에서만 C 채점.
- **무료 내재방향 근사**: T0(이벤트 직전) yfinance `option_chain` 스냅샷 → put-call parity로 implied forward 도출. implied forward > spot → 시장 내재 P(up) > 0.5. ATM 스트래들·델타로 risk-neutral P(up) 근사.
  - ⚠ 어닝 직전 옵션체인은 노이즈 큼 → C는 **best-effort 근사**, 비구속 보너스 채점이라 근사 오차 허용. 근사 불가(체인 미수신) 종목·이벤트는 C 스킵(disclosure).

---

## 3. 코호트 출범 (CONFIRMED — C 만장일치)

- 주식 트랙을 5/30 Polymarket 코호트와 **동시 사전등록하지 않는다.**
- 해소규칙(§1) + baseline(§2)이 모두 확정·봉인될 때까지 **후보 수집만 지속.**
- 룰 확정 후 **순차 추가** (별도 OOS 코호트로 나중에 사전등록).
- 후보 수집 소스: SEC EDGAR 8-K (2.02/1.01/2.01/5.02) + yfinance 어닝 일정 — `code/ingest/equity_events.py`.

### §3 봉인·출범 트리거 (2026-05-29 확정)
- **봉인 조건(전부 충족 시)**: ① §1·§2 구체값 코드 구현·검증 완료(base-rate 산출기 PC에서 1회 실행해 버킷별 base-rate 산출 확인) ② 후보 풀 충분(현 386 8-K + 8 earnings → 향후 어닝 ≥ 30건 forward 확보) ③ resolution·scoring 파이프라인 e2e_smoke 통과.
- **출범**: 위 충족 후 봉인 → **별도(2nd) OOS 코호트**로 사전등록. **날짜는 5/30 아님** — 충족 시점 이후 별도 지정.
- 5/30: Polymarket 코호트만 사전등록(주식 트랙 무관).

---

## 다음 단계 (봉인 전 체크리스트)
- [x] §1 명세 4항목 구체값 확정 (Stooq 종가·구간한정 조정·tie=No·5일 롤·sha256+Nostr 봉인)
- [x] §2 baseline 확정 (계층형: A floor / B=base-rate 1차선 / C=옵션내재 보조)
- [x] §3 봉인·출범 트리거 정의
- [ ] **구현**: base-rate 산출기(PC) + resolution/scoring 파이프라인 + e2e_smoke
- [ ] 후보 풀 축적 (forward 어닝 ≥ 30건)
- [ ] 봉인 → 별도 OOS 코호트로 순차 사전등록 (5/30 아님)

> 설계(§1–§3) 완료. 남은 건 **코드 구현 + 데이터 축적**이며 설계 선택지는 없음.
