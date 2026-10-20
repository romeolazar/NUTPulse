FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/nutpulse

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 nutpulse \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin nutpulse \
    && mkdir -p /data \
    && chown -R nutpulse:nutpulse /data /opt/nutpulse

COPY --chown=nutpulse:nutpulse app ./app

USER nutpulse
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)); raise SystemExit(0 if d.get('status') in ('ok','degraded') else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
