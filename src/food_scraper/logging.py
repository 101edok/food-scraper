from __future__ import annotations

import logging
import sys

import structlog


class _DropAsyncmyResetOnClose(logging.Filter):
    """Глушит шум 'Exception closing connection … ConnectionResetError' из пула.

    Yandex Cloud Managed MySQL не отвечает на COM_QUIT, asyncmy ловит
    ConnectionResetError при чтении ACK. К работе пайплайна не относится —
    соединение уже закрыто, а exc_info портит вывод.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc = record.exc_info[1]
            if isinstance(exc, ConnectionResetError):
                return False
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    logging.getLogger("sqlalchemy.pool").addFilter(_DropAsyncmyResetOnClose())
    # httpx пишет INFO на каждый запрос — десятки тысяч строк за прогон.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
