"""Алерты в Telegram: прогон закончился блокировкой/ошибкой, товаров резко меньше обычного.

Бот и чат — ALERT_TELEGRAM_TOKEN / ALERT_TELEGRAM_CHAT_ID в .env; без них алерт только пишется в лог.
"""
from __future__ import annotations

import statistics

import httpx
import structlog
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import ScrapeRun

log = structlog.get_logger(__name__)

STATUS_TEXT = {"blocked": "🚫 Блокировка (магазин ответил 401/403)", "failed": "❌ Ошибка"}
# Сколько товаров «обычно»: медиана последних успешных прогонов с тем же числом точек
DROP_RATIO = 0.5
DROP_HISTORY = 5


async def send(text: str) -> None:
    s = get_settings()
    if not s.alert_telegram_token or not s.alert_telegram_chat_id:
        log.warning("alert_not_configured", text=text)
        return
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.telegram.org/bot{s.alert_telegram_token}/sendMessage",
            json={"chat_id": s.alert_telegram_chat_id, "text": text, "disable_web_page_preview": True},
        )
        r.raise_for_status()
    log.info("alert_sent", text=text)


def run_title(run: ScrapeRun) -> str:
    return f"food-scraper #{run.id} {run.shop}/{run.city}"


async def on_run_finished(run_id: int) -> None:
    async with session_scope() as session:
        run = await session.get(ScrapeRun, run_id)
        if run is None:
            return
        if run.status in STATUS_TEXT:
            await send(f"{STATUS_TEXT[run.status]}\n{run_title(run)}: точек {run.stores_total}, "
                       f"товаров {run.products_total}, ошибок {run.errors_total}. Логи — admin.edalle.ru/shop/runs")
            return
        if run.status != "ok":
            return
        previous = (await session.execute(
            select(ScrapeRun.products_total).where(
                ScrapeRun.shop == run.shop, ScrapeRun.city == run.city, ScrapeRun.status == "ok",
                ScrapeRun.stores_total == run.stores_total, ScrapeRun.id < run.id,
            ).order_by(ScrapeRun.id.desc()).limit(DROP_HISTORY)
        )).scalars().all()
    if len(previous) >= 2:
        usual = statistics.median(previous)
        if usual and run.products_total < usual * DROP_RATIO:
            await send(f"📉 Товаров меньше обычного\n{run_title(run)}: {run.products_total} "
                       f"при обычных ~{round(usual)}. Возможно, сменилась вёрстка или API.")
