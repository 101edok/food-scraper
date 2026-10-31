"""Прохождение Qrator JS-challenge через Playwright headless.

Лента сидит за Qrator. На «подозрительных» IP (вне РФ, новые UA, и т. п.)
edge выдаёт 401 + страницу со скриптом `__qrator/qauth.js`, который надо
выполнить в браузере. Решение записывается в cookie `qrator_jsr` (Max-Age=300).

Стратегия: разок прогреться через Playwright → достать cookies → подсунуть
в httpx. Кэшируем на ~4 минуты (запас перед TTL=300с).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

_TTL_SECONDS = 240
_WARMUP_TIMEOUT_MS = 30_000


@dataclass(slots=True)
class QratorSession:
    cookies: dict[str, str]
    user_agent: str
    captured_at: float

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.captured_at) > _TTL_SECONDS


class QratorWarmer:
    """Один экземпляр на скрапер: lazy warm-up + автоматическое обновление."""

    def __init__(
        self,
        base_url: str,
        proxy_url: str | None = None,
        user_agent: str | None = None,
    ):
        self.base_url = base_url
        self.proxy_url = proxy_url
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._session: QratorSession | None = None
        self._lock = asyncio.Lock()

    async def get_session(self, *, force_refresh: bool = False) -> QratorSession:
        async with self._lock:
            if (
                self._session is not None
                and not self._session.expired
                and not force_refresh
            ):
                return self._session
            self._session = await self._warmup()
            return self._session

    async def _warmup(self) -> QratorSession:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError as e:
            raise SystemExit(
                "Для прохождения Qrator-challenge нужен Playwright. "
                "Поставь: pip install -e '.[recon]' && playwright install chromium"
            ) from e

        log.info(
            "qrator_warmup_start", base_url=self.base_url, proxy=self.proxy_url
        )
        async with async_playwright() as p:
            launch_kwargs: dict = {"headless": True}
            if self.proxy_url:
                launch_kwargs["proxy"] = {"server": self.proxy_url}
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(user_agent=self.user_agent)
            page = await context.new_page()
            await page.goto(self.base_url, wait_until="domcontentloaded")
            # qauth.js сам решит challenge и сделает location.reload().
            # Признак прохождения — на странице появляется Angular-приложение
            # (тег с атрибутом ng-version или ssr ng-state).
            await page.wait_for_function(
                """() => document.querySelector('[ng-version]')
                    || document.querySelector('script#ng-state')
                    || (document.title && document.title.toLowerCase().includes('лента'))""",
                timeout=_WARMUP_TIMEOUT_MS,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            ua = await page.evaluate("navigator.userAgent")
            cookies_raw = await context.cookies(self.base_url)
            await context.close()
            await browser.close()

        cookies = {c["name"]: c["value"] for c in cookies_raw}
        log.info(
            "qrator_warmup_done",
            cookies=list(cookies.keys()),
            count=len(cookies),
        )
        return QratorSession(
            cookies=cookies, user_agent=ua, captured_at=time.monotonic()
        )
