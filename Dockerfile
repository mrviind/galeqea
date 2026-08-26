# GaleQEA — single-image deployment.
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
# test platforms go wrong.
FROM mcr.microsoft.com/playwright:v1.62.1-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GALEQEA_HOME=/data \
    GALEQEA_HOST=0.0.0.0 \
    GALEQEA_PORT=8080

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/
COPY apps/api/galeqea apps/api/galeqea
RUN python3 -m pip install --break-system-packages -e ./apps/api

COPY apps/runner apps/runner
RUN cd apps/runner && npm install --omit=dev --no-audit --no-fund

COPY --from=web /build/dist apps/web/dist
COPY examples examples

RUN mkdir -p /data && useradd -m galeqea && chown -R galeqea /data /app
USER galeqea
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

CMD ["python3", "-m", "uvicorn", "galeqea.main:app", "--host", "0.0.0.0", "--port", "8080"]
