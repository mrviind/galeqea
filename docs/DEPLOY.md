# Deploying GaleQEA

GaleQEA ships as a single container and runs two ways from the same
`docker-compose.yml`:

| Profile | Command | Database | Artifacts | External services |
| --- | --- | --- | --- | --- |
| **local** (default) | `docker compose up` | SQLite (named volume) | local disk | none |
| **full** | `docker compose --env-file deploy/compose/full.env up` | Postgres 16 + pgvector | S3 (SeaweedFS) | Postgres, S3, worker, Valkey |

The **local** profile is the zero-config path: no environment variables, no
external services, everything in one container against SQLite. This is what
`make start` and a bare `docker compose up` give you, and it is exactly what the
`zero-config-smoke` CI job boots on every change.

Every bundled backing image is permissively licensed: PostgreSQL
(`pgvector/pgvector:pg16`), **SeaweedFS** (Apache-2.0), and **Valkey** (BSD-3).
GaleQEA never bundles an AGPL/BSL/SSPL component; MinIO, Garage and Redis 8 can
only be *documented, self-hosted integrations you choose*, not defaults.

## Local (default)

```bash
docker compose up            # http://localhost:8080, SQLite, local-disk artifacts
```

A compose deployment is reachable by more than one person, so it defaults to
**multi-user** (`GALEQEA_SINGLE_USER_MODE=false`). On first boot the admin is
made loginable one of two ways:

- set `GALEQEA_ADMIN_EMAIL` **and** `GALEQEA_ADMIN_PASSWORD`, or
- set only the email (or neither) and read the **one-time setup URL** from the
  startup logs. It sets the password once, then stops working.

`make up` on localhost stays single-user (no login) for a desktop install.

## Full (Postgres + S3 + worker + Valkey)

```bash
docker compose --env-file deploy/compose/full.env up
```

`deploy/compose/full.env` sets `COMPOSE_PROFILES=full` (so the extra services
start) and points the app at them. It brings up:

- **postgres**: `pgvector/pgvector:pg16`. Migrations run automatically on boot
  under a Postgres advisory lock, so a fleet (web + worker + a migration job)
  can start together and exactly one applies the schema.
- **seaweedfs**: an S3 gateway on `:8333`; the identity lives in
  `deploy/compose/seaweedfs/s3.json`. Artifacts go here via the `s3` storage
  backend.
- **worker**: a placeholder today; the durable job queue
  (ProcrastinateQueue on Postgres) lands in a later phase. It is present so the
  full topology and its wiring are exercised.
- **valkey**: optional cache/queue backend (BSD-3). The app runs without it.

> `docker compose --profile full up` also starts the five services, but only the
> env file wires the **app** to Postgres and S3, so prefer the `--env-file` command
> above for a working full stack.

Change every secret in `full.env` and `seaweedfs/s3.json` before any real use.

## Using your own managed Postgres

Point GaleQEA at an external database instead of the bundled one: set the URL
and don't start the `postgres` service:

```bash
GALEQEA_DATABASE_URL="postgresql+psycopg://USER:PASSWORD@db.internal:5432/galeqea"
```

- Driver: **`postgresql+psycopg`** (psycopg 3). It ships in the image's
  `postgres` extra; no extra install needed.
- The `pgvector` extension is used for embeddings. Managed Postgres on AWS RDS,
  Cloud SQL, Azure, Neon, Supabase and Crunchy all offer it; enable it once
  (`CREATE EXTENSION IF NOT EXISTS vector;`) or let a superuser role do it.
- Run migrations against it with `galeqea db upgrade` (or let the app do it on
  boot). `galeqea db current` / `galeqea db history` inspect state.

## Using your own S3 / object storage

Any S3-compatible endpoint works: AWS S3, Cloudflare R2, Wasabi, Backblaze B2,
Ceph RGW, or a self-hosted SeaweedFS/MinIO you operate:

```bash
GALEQEA_STORAGE_BACKEND=s3
GALEQEA_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com   # omit for AWS default
GALEQEA_S3_BUCKET=galeqea-artifacts
GALEQEA_S3_REGION=us-east-1
GALEQEA_S3_ACCESS_KEY_ID=...
GALEQEA_S3_SECRET_ACCESS_KEY=...
GALEQEA_S3_FORCE_PATH_STYLE=true    # required by most non-AWS endpoints
```

Leave `GALEQEA_STORAGE_BACKEND` unset (or `local`) to keep artifacts on disk
under `GALEQEA_HOME`. Artifact URLs are always served or signed through the API,
so the bucket never needs to be public.

## Environment reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `GALEQEA_DATABASE_URL` | _(empty → SQLite)_ | `postgresql+psycopg://…` to use Postgres |
| `GALEQEA_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `GALEQEA_S3_ENDPOINT` | _(empty → AWS)_ | S3 endpoint URL |
| `GALEQEA_S3_BUCKET` | _(empty)_ | bucket for artifacts |
| `GALEQEA_S3_REGION` | `us-east-1` | S3 region |
| `GALEQEA_S3_ACCESS_KEY_ID` / `…_SECRET_ACCESS_KEY` | _(empty)_ | S3 credentials |
| `GALEQEA_S3_FORCE_PATH_STYLE` | _(empty)_ | `true` for non-AWS endpoints |
| `GALEQEA_REDIS_URL` | _(empty)_ | optional Valkey/Redis URL |
| `GALEQEA_LOG_FORMAT` | `auto` | `auto` (JSON off a TTY) / `json` / `console` |
| `GALEQEA_AUDIT_SIEM` | `false` | mirror each audit entry as a JSON line on stdout |
| `GALEQEA_OTEL_ENABLED` | `false` | OpenTelemetry tracing (needs `apps/api[otel]`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(unset)_ | OTLP collector, e.g. `http://otel:4317` |
| `GALEQEA_SINGLE_USER_MODE` | `true` (`make up`) / `false` (compose) | skip login for a desktop install |
| `GALEQEA_ADMIN_EMAIL` / `GALEQEA_ADMIN_PASSWORD` | _(empty)_ | first-run admin bootstrap |
