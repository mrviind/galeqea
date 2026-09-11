# GaleQEA: single-image deployment.
#
# The UI is built in one stage and served by the API in the final image, so a
# deployment is one container on one port with no reverse proxy to configure.

# --- stage 1: build the web UI ---------------------------------------------
FROM node:22-slim AS web
WORKDIR /build
COPY apps/web/package.json ./
RUN npm install --no-audit --no-fund
COPY apps/web ./
RUN npm run build

# --- stage 2: runtime -------------------------------------------------------
# The Playwright base image already carries the browsers and their system
# libraries; installing those on a plain slim image is where most self-hosted
# test platforms go wrong. The `-noble` tag is Ubuntu 24.04, which ships
# Python 3.12 (the app needs >=3.11) and a pip new enough for
# --break-system-packages; the jammy tag's Python 3.10 cannot run the app.
FROM mcr.microsoft.com/playwright:v1.49.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GALEQEA_HOME=/data \
    GALEQEA_HOST=0.0.0.0 \
    GALEQEA_PORT=8080

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/README.md apps/api/
COPY apps/api/galeqea apps/api/galeqea
RUN python3 -m pip install --break-system-packages -e "./apps/api[postgres,s3,worker]"

COPY apps/runner apps/runner
RUN cd apps/runner && npm install --omit=dev --no-audit --no-fund
# The base image ships browsers for its own Playwright version, but the runner's
# `^1.49.0` resolves to a newer Playwright whose Chromium build differs, so install the
# matching browser so containerised runs can actually launch a browser. OS deps are
# already in the Playwright base image, so no --with-deps is needed.
RUN cd apps/runner && PLAYWRIGHT_BROWSERS_PATH=/ms-playwright npx playwright install chromium

COPY --from=web /build/dist apps/web/dist
COPY examples examples
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /data && useradd -m galeqea && chown -R galeqea /data /app
USER galeqea
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

# The entrypoint is a no-op for the local (SQLite) profile and waits for Postgres
# under the full profile before handing off to the CMD.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "galeqea.main:app", "--host", "0.0.0.0", "--port", "8080"]
