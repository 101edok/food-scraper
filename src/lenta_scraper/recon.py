"""Однократная разведка: открывает сайт через Playwright, выбирает магазин,
переходит в каталог/категорию/карточку и сохраняет:

  recon/har.json         — HAR со всем сетевым трафиком
  recon/endpoints.json   — извлечённые шаблоны запросов:
                           {endpoint: {method, url_path, headers, sample_body, sample_response_keys}}

Запуск: `lenta-scrape recon --city moscow`
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import structlog

from .config import RECON_DIR

log = structlog.get_logger(__name__)

INTEREST_PATHS = (
    "/catalog/items",
    "/catalog/categories",
    "/catalog/items/",
    "/stores",
    "/cities",
    "/api-gateway/v1",
)


def _is_interesting(url: str) -> bool:
    return any(p in url for p in INTEREST_PATHS) and "lenta.com" in url


async def run_recon(city: str = "moscow", headless: bool = False) -> Path:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "playwright не установлен. Поставь: pip install -e '.[recon]' && playwright install chromium"
        ) from e

    RECON_DIR.mkdir(parents=True, exist_ok=True)
    har_path = RECON_DIR / "har.json"
    endpoints_path = RECON_DIR / "endpoints.json"

    captured: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            record_har_path=str(har_path),
            record_har_content="omit",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def on_response(resp):
            try:
                url = resp.url
                if not _is_interesting(url):
                    return
                req = resp.request
                body_text = None
                try:
                    body_text = req.post_data
                except Exception:
                    body_text = None
                payload = None
                ct = (resp.headers.get("content-type") or "").lower()
                if "json" in ct:
                    try:
                        payload = await resp.json()
                    except Exception:
                        payload = None
                captured.append(
                    {
                        "method": req.method,
                        "url": url,
                        "status": resp.status,
                        "request_headers": dict(req.headers),
                        "request_body": body_text,
                        "response_keys": list(payload.keys())
                        if isinstance(payload, dict)
                        else None,
                    }
                )
            except Exception as e:
                log.warning("recon_capture_error", err=str(e))

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        log.info("recon_open", city=city)
        await page.goto(f"https://{city}.lenta.com/", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        log.info("recon_open_catalog")
        await page.goto(
            f"https://{city}.lenta.com/catalog/", wait_until="networkidle"
        )
        await page.wait_for_timeout(2000)

        log.info("recon_open_category")
        # «Молочные продукты, сыр, яйца» — стабильная категория с артикулами
        await page.goto(
            f"https://{city}.lenta.com/catalog/molochnye-produkty-syr-yaytsa-3009/",
            wait_until="networkidle",
        )
        await page.wait_for_timeout(3000)

        # Попробуем кликнуть в первый товар, чтобы поймать details endpoint
        try:
            await page.locator("a[href*='/product/']").first.click(timeout=5000)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
        except Exception:
            log.warning("recon_no_product_card_link")

        await context.close()
        await browser.close()

    by_path: dict[str, dict[str, Any]] = {}
    for c in captured:
        m = re.search(r"https?://[^/]+(/[^?]+)", c["url"])
        path = m.group(1) if m else c["url"]
        key = f"{c['method']} {path}"
        if key in by_path:
            continue
        by_path[key] = c

    endpoints_path.write_text(
        json.dumps(by_path, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(
        "recon_done",
        endpoints=len(by_path),
        har=str(har_path),
        out=str(endpoints_path),
    )
    return endpoints_path


if __name__ == "__main__":
    import sys

    asyncio.run(run_recon(sys.argv[1] if len(sys.argv) > 1 else "moscow"))
