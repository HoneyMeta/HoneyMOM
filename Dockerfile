# syntax=docker/dockerfile:1
# HoneyMOM image: the web UI is built with Node, then served by the Python application.
# The same image also runs the data replayer (python -m replayer), see docker-compose.yml.

# ---- 1. web UI ------------------------------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. runtime -----------------------------------------------------------------------------------------------
FROM python:3.12-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOM_DATA=/data

# postgresql-client: pg_dump / pg_restore for backups of a PostgreSQL database (same major version as the
# postgres:17 service in docker-compose.yml); tzdata: TZ support; tini: signal handling as PID 1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client tzdata tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
COPY replayer/requirements.txt /tmp/requirements-replayer.txt
RUN pip install -r /tmp/requirements.txt -r /tmp/requirements-replayer.txt && rm /tmp/requirements*.txt

COPY honeymom/ ./honeymom/
COPY replayer/ ./replayer/
COPY config/llm.example.json ./config/llm.example.json
COPY --from=web /src/web/dist ./web/dist

RUN useradd --system --uid 10001 --gid 0 --home-dir /app --shell /usr/sbin/nologin honeymom \
 && mkdir -p /data \
 && chown -R 10001:0 /data \
 && chmod -R g=u /data

USER 10001
VOLUME ["/data"]
EXPOSE 8900

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8900/api/setup/admin', timeout=4); sys.exit(0)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "honeymom", "serve", "--http", "8900"]
