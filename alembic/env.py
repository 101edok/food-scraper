"""Alembic env: использует **sync**-движок (pymysql).

Async-движок (asyncmy) для одноразовых DDL-команд капризен:
закрытие пула сразу после миграции рвёт TLS-сессию у Yandex Cloud Managed MySQL
и реальная ошибка миграции теряется. Sync-движок проще и надёжнее.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from lenta_scraper.config import get_settings
from lenta_scraper.db import _build_connect_args
from lenta_scraper.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()


def _to_sync_dsn(dsn: str) -> str:
    """mysql+asyncmy://… → mysql+pymysql://…"""
    return dsn.replace("mysql+asyncmy://", "mysql+pymysql://", 1)


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_to_sync_dsn(settings.mysql_dsn),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    sync_dsn = _to_sync_dsn(settings.mysql_dsn)
    url, connect_args = _build_connect_args(sync_dsn)
    # pymysql ожидает ssl-параметры в виде {"ssl": {"ca": ..., "check_hostname": False}},
    # либо принимает SSLContext через "ssl" — обе формы совместимы.
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
