from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import structlog
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .browser_session import BrowserSession
from ...config import Settings, get_settings
from .qrator import QratorWarmer

log = structlog.get_logger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
QRATOR_REFRESH_STATUSES = frozenset({401, 403})


class RetryableHTTPStatus(Exception):
    def __init__(self, response: httpx.Response):
        super().__init__(f"retryable status {response.status_code}")
        self.response = response


class LentaHttpClient:
    """Тонкая обёртка над httpx.AsyncClient.

    Особенности:
      * Общие заголовки + Qrator cookies (warm-up через Playwright headless).
      * Ретраи на 5xx/429 (tenacity).
      * Один автоматический refresh Qrator-сессии при 401/403.
      * Глобальный rate-limit (aiolimiter).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._limiter = AsyncLimiter(max_rate=settings.rps, time_period=1.0)
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            http2=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=self._base_headers(),
            proxy=(settings.proxy_url or None),
            follow_redirects=True,
        )
        self._qrator = QratorWarmer(
            settings.base_url, settings.proxy_url, settings.user_agent
        )
        self._browser_session = BrowserSession.load()
        # Если есть импортированная браузерная сессия — берём её, Playwright не нужен.
        self._qrator_initialized = self._browser_session is not None
        if self._browser_session:
            self._apply_browser_session(self._browser_session)
            log.info(
                "browser_session_loaded",
                cookies=len(self._browser_session.cookies),
                headers=len(self._browser_session.headers),
            )

    # Хедеры из cURL'а навигации (Document) бессмысленно тащить в XHR/JSON-запросы:
    # accept: text/html, sec-fetch-mode: navigate, upgrade-insecure-requests, priority…
    # Берём только то, что должно быть одинаково в любом запросе от того же браузера.
    _BROWSER_HEADERS_WHITELIST = frozenset(
        {
            "user-agent",
            "accept-language",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
        }
    )

    def _apply_browser_session(self, sess: BrowserSession) -> None:
        for name, value in sess.cookies.items():
            self._client.cookies.set(name, value, domain=".lenta.com")
        for name, value in sess.headers.items():
            if name.lower() in self._BROWSER_HEADERS_WHITELIST:
                self._client.headers[name] = value

    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.user_agent or DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru,en;q=0.9",
            "X-Domain": self.settings.x_domain,
            "X-Retail-Brand": "lo",
            "X-Delivery-Mode": self.settings.delivery_mode,
            "X-Device-Web-Platform": "desktop",
            "X-Device-Id": self.settings.device_id,
            "Deviceid": self.settings.device_id,
            "Client": "web",
            "Origin": self.settings.base_url,
            "Referer": f"{self.settings.base_url}/catalog/",
            # XHR fetch metadata (как в Chrome/YaBrowser):
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _apply_qrator(self, *, force_refresh: bool = False) -> None:
        sess = await self._qrator.get_session(force_refresh=force_refresh)
        for name, value in sess.cookies.items():
            self._client.cookies.set(name, value, domain=".lenta.com")
        # User-Agent должен совпадать с тем, под которым решали challenge.
        self._client.headers["User-Agent"] = sess.user_agent
        self._qrator_initialized = True

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not self._qrator_initialized:
            await self._apply_qrator()

        last_resp: httpx.Response | None = None
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(5),
                wait=wait_exponential_jitter(initial=1, max=30),
                retry=retry_if_exception_type(
                    (httpx.TransportError, RetryableHTTPStatus)
                ),
                reraise=True,
            ):
                with attempt:
                    async with self._limiter:
                        resp = await self._client.request(
                            method, url, json=json, params=params, headers=headers
                        )
                    last_resp = resp
                    if resp.status_code in RETRYABLE_STATUSES:
                        log.warning(
                            "retryable_status",
                            status=resp.status_code,
                            url=str(resp.request.url),
                        )
                        raise RetryableHTTPStatus(resp)
        except RetryableHTTPStatus as e:
            last_resp = e.response

        # Qrator выкинул челлендж заново (TTL истёк / переподозрел).
        if last_resp is not None and last_resp.status_code in QRATOR_REFRESH_STATUSES:
            body_head = last_resp.text[:200] if last_resp.content else ""
            if "qrator" in body_head.lower() or "qauth" in body_head.lower():
                if self._browser_session is not None:
                    log.error(
                        "browser_session_expired",
                        status=last_resp.status_code,
                        hint="Открой сайт в браузере, скопируй свежий cURL и перезапусти "
                        "`lenta-scrape import-session`.",
                    )
                else:
                    log.warning(
                        "qrator_challenge_repeat", status=last_resp.status_code
                    )
                    await self._apply_qrator(force_refresh=True)
                    async with self._limiter:
                        last_resp = await self._client.request(
                            method, url, json=json, params=params, headers=headers
                        )

        assert last_resp is not None
        return last_resp

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)


@asynccontextmanager
async def http_client(
    settings: Settings | None = None,
) -> AsyncIterator[LentaHttpClient]:
    s = settings or get_settings()
    client = LentaHttpClient(s)
    try:
        yield client
    finally:
        await client.aclose()
