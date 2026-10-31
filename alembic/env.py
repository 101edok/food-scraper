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

from food_scraper.config import get_settings
from food_scraper.db import _build_connect_args
from food_scraper.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()


def _to_sync_dsn(dsn: str) -> str:
    """mysql+asyncmy://… → mysql+pymysql://…"""
    return dsn.replace("mysql+asyncmy://", "mysql+pymysql://", 1)


target_metadata = Base.metadata
# БД общая с edalle backend (recepter) — держим свою таблицу версий.
VERSION_TABLE = "shop_alembic_version"


def _include_name(name, type_, parent_names) -> bool:
    # Чужие таблицы (users, videos, …) не трогаем, иначе --autogenerate предложит их дропнуть.
    return type_ != "table" or name.startswith("shop_")


def run_migrations_offline() -> None:
    context.configure(
        url=_to_sync_dsn(settings.mysql_dsn),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        include_name=_include_name,
    )
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
