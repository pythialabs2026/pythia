#!/bin/bash
set -e
cron                      # 봇 스케줄 데몬 (/etc/cron.d/stoa)
cd /app/webapp
exec python3 app.py       # uvicorn :8765 (foreground = 컨테이너 메인)
