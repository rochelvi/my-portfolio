# syntax=docker/dockerfile:1
FROM python:3.13-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# состояние (threads.json) лежит в /data — сюда монтируется volume bot-data
RUN adduser -D app && mkdir -p /data && chown app:app /data
ENV DATA_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1
USER app

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD pidof python >/dev/null || exit 1

CMD ["python", "bot.py"]
