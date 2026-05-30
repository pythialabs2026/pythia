# Stoa — Docker 단일 서비스

stock 전략 대시보드(webapp + 봇)를 **하나의 컨테이너**로 패키징. (2026-05-22 systemd→Docker 전환)

## 구성
- webapp(FastAPI :8765) + 봇(cron 내장: 평일 06:40 방패 / 월 06:45 리밸) 한 컨테이너
- tossctl 바이너리 이미지 내장 (읽기 전용, order/매매 호출 안 함)
- 영속 데이터: `/home/ubuntu/runtime/state/stock-data` → `/data` (DB·master.key·push_keys·glide_config·state·journal)
- 토스세션 자동임포트: 호스트 `~/.config/tossctl` 읽기전용 마운트(owner uid1 가입 시)
- 공개: tailscale funnel → 127.0.0.1:8765 → 컨테이너

## 운영
```bash
cd strategies/tqqq-dca/deploy
docker compose up -d        # 가동 (restart=unless-stopped → 재부팅 생존)
docker compose down         # 중단
docker compose logs -f      # 로그
docker compose build && docker compose up -d   # 코드 변경 후 재배포
```
- 상태: `docker ps`, `curl localhost:8765/healthz`
- 봇 로그: `/home/ubuntu/runtime/state/stock-data/{v5_adv,glide_rebal}_cron.log`
- 초대코드: compose `STOA_SIGNUP_CODE` (현재 stoa-0816)

## 롤백 (Docker→systemd 복귀)
`docker compose down` 후 `sudo systemctl enable --now stock-web` (구 유닛 보존됨).
