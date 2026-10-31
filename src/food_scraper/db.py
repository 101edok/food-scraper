from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings, get_settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def _falsy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("0", "false", "no", "off")


def _build_connect_args(dsn: str) -> tuple[URL, dict[str, Any]]:
    """Достаёт SSL-параметры из query-строки DSN и собирает SSLContext.

    Поддерживается:
      ssl_ca=/path/to/root.crt           — CA-сертификат (Yandex Cloud)
      ssl_check_hostname=false           — отключить проверку hostname
                                            (нужно при подключении через CNAME)
      ssl_verify_cert=false              — выключить проверку CA целиком (НЕ для прод)
    """
    url = make_url(dsn)
    query = dict(url.query)
    ssl_ca = query.pop("ssl_ca", None)
    check_hostname = not _falsy(query.pop("ssl_check_hostname", None))
    verify_cert = not _falsy(query.pop("ssl_verify_cert", None))

    connect_args: dict[str, Any] = {}
    if ssl_ca or not check_hostname or not verify_cert:
        ctx = (
            ssl.create_default_context(cafile=ssl_ca)
            if ssl_ca
            else ssl.create_default_context()
        )
        if not check_hostname:
            ctx.check_hostname = False
        if not verify_cert:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx

    return url.set(query=query), connect_args


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine, _factory
    if _engine is None:
        s = settings or get_settings()
        url, connect_args = _build_connect_args(s.mysql_dsn)
        _engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=10,
            connect_args=connect_args,
        )
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _factory is None:
        get_engine()
    assert _factory is not None
    return _factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
