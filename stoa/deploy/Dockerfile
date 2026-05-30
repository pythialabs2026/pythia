FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends cron tzdata curl \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Seoul STOA_DATA=/data PYTHONUNBUFFERED=1
WORKDIR /app

COPY deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# tossctl (ARM64 static) — 읽기 전용 보유조회용. order/매매 호출 안 함.
COPY deploy/tossctl /usr/local/bin/tossctl
RUN chmod +x /usr/local/bin/tossctl

COPY webapp/ /app/webapp/
COPY bots/   /app/bots/
COPY deploy/entrypoint.sh /app/entrypoint.sh
COPY deploy/crontab /etc/cron.d/stoa
RUN chmod +x /app/entrypoint.sh && chmod 0644 /etc/cron.d/stoa

EXPOSE 8765
VOLUME /data
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD curl -sf http://127.0.0.1:8765/healthz || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
