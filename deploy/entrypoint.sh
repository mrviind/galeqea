#!/bin/sh
# Container entrypoint. For the local (SQLite) profile this is a straight exec,
# no wait, no external services. When GALEQEA_DATABASE_URL points at Postgres
# (the full profile), wait for it to accept connections before starting, so a
# `docker compose up` that boots the app and Postgres together doesn't race.
set -e

case "${GALEQEA_DATABASE_URL:-}" in
  postgres*|postgresql*)
    echo "[entrypoint] Postgres configured, waiting for it to accept connections..."
    python3 - <<'PY'
import os, sys, time
from sqlalchemy import create_engine, text
url = os.environ["GALEQEA_DATABASE_URL"]
for attempt in range(60):
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[entrypoint] Postgres is ready.")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - any connect error means "not yet"
        if attempt == 0:
            print(f"[entrypoint] waiting for Postgres: {exc.__class__.__name__}")
        time.sleep(1)
print("[entrypoint] Postgres did not become ready within 60s", file=sys.stderr)
sys.exit(1)
PY
    ;;
esac

exec "$@"
