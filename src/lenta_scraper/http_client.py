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

from .config import Settings, get_settings

log = structlog.get_logger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableHTTPStatus(Exception):
    def __init__(self, response: httpx.Response):
        super().__init__(f"retryable status {response.status_code}")
        self.response = response


class LentaHttpClient:
    """Тонкая обёртка над httpx.AsyncClient: общие заголовки, ретраи, rate-limit."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._limiter = AsyncLimiter(max_rate=settings.rps, time_period=1.0)
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            http2=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers=self._base_headers(),
        )

    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_UA,
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
            "Referer": f"{self.settings.base_url}/",
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
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
            return e.response
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
