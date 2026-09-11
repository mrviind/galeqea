"""Alembic environment — wired to the app's models and configured DB.

Uses ``settings.database_url`` (so migrations hit the same DB the app does) and
``render_as_batch=True`` so column changes work on SQLite, which can't ALTER a
column in place. ``compare_type`` catches type drift on autogenerate.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models registers every table on Base.metadata.
from galeqea.config import settings
from galeqea.db import Base
from galeqea.models import *  # noqa: F401,F403  (import registers all tables)

config = context.config
# The caller (db.run_migrations / the `galeqea db` CLI) may set the URL on the
# config to target a specific DB; fall back to the app's configured DB otherwise.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.database_url)
_URL = config.get_main_option("sqlalchemy.url")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
