"""Один прогон = один магазин × один город.

1. Выбрать магазин сети, чьи цены считаем ценами города.
2. Синхронизировать дерево категорий.
3. Обойти листовые категории: товары + цены города (дёшево — 50 товаров на запрос).
4. Дочитать карточки (состав, КБЖУ) только для новых и устаревших товаров.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from ..cities import get_city
from ..db import session_scope
from ..models import ScrapeRun
from ..shops.base import ItemRow, Shop, get_shop
from ..upsert import ids_needing_details, save_details, save_items, upsert_categories

log = structlog.get_logger(__name__)


def utcnow() -> datetime:
    # DATETIME в MySQL без зоны — храним UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


async def run(
    shop_name: str,
    city_slug: str,
    *,
    category_id: str | None = None,
    limit: int | None = None,
    workers: int = 4,
    details_max_age: timedelta = timedelta(days=30),
    details_limit: int | None = None,
) -> int:
    city = get_city(city_slug)
    shop = get_shop(shop_name)

    async with session_scope() as session:
        row = ScrapeRun(shop=shop.name, city=city.slug, started_at=utcnow(), status="running")
        session.add(row)
        await session.flush()
        run_id = row.id

    structlog.contextvars.bind_contextvars(run_id=run_id, shop=shop.name, city=city.slug)
    stats = {"categories": 0, "products": 0, "details": 0, "errors": 0}
    status = "ok"
    store_code: str | None = None
    try:
        async with shop:
            store_code = await shop.select_store(city)
            seen = await _scrape_listings(
                shop, run_id=run_id, city=city.slug, store_code=store_code,
                category_id=category_id, limit=limit, workers=workers, stats=stats,
            )
            await _scrape_details(
                shop, seen, max_age=details_max_age, limit=details_limit,
                workers=workers, stats=stats,
            )
    except Exception as e:
        status = "failed"
        log.exception("run_failed", err=str(e))
        raise
    except BaseException:
        # pm2 stop / Ctrl-C (CancelledError, KeyboardInterrupt).
        status = "interrupted"
        log.warning("run_interrupted")
        raise
    finally:
        async with session_scope() as session:
            r = await session.get(ScrapeRun, run_id)
            if r:
                r.store_code = store_code
                r.finished_at = utcnow()
                r.categories_total = stats["categories"]
                r.products_total = stats["products"]
                r.details_total = stats["details"]
                r.errors_total = stats["errors"]
                r.status = status
        log.info("run_done", status=status, **stats)
        structlog.contextvars.unbind_contextvars("run_id", "shop", "city")
    return run_id


async def _scrape_listings(
    shop: Shop,
    *,
    run_id: int,
    city: str,
    store_code: str,
    category_id: str | None,
    limit: int | None,
    workers: int,
    stats: dict[str, int],
) -> list[str]:
    cats = await shop.categories()
    async with session_scope() as session:
        await upsert_categories(session, shop.name, cats)
    leaves = [category_id] if category_id else [c.id for c in cats if not c.has_children]
    log.info("categories_synced", total=len(cats), leaves=len(leaves))

    # Товар лежит в нескольких категориях (плюс промо-подборки) — сохраняем один раз.
    # Проверка и добавление идут без await между ними, так что гонок между задачами нет.
    seen: dict[str, None] = {}
    sem = asyncio.Semaphore(workers)

    async def one(cid: str) -> None:
        async with sem:
            try:
                fresh: list[ItemRow] = []
                n = 0
                async for item in shop.iter_items(cid):
                    n += 1
                    if item.id not in seen:
                        seen[item.id] = None
                        fresh.append(item)
                    if limit is not None and n >= limit:
                        break
                async with session_scope() as session:
                    changed = await save_items(
                        session, shop=shop.name, city=city, store_code=store_code,
                        run_id=run_id, items=fresh, now=utcnow(),
                    )
                stats["categories"] += 1
                stats["products"] += len(fresh)
                log.info(
                    "category_done", category_id=cid, items=n, new=len(fresh),
                    price_changed=changed, progress=f"{stats['categories']}/{len(leaves)}",
                )
            except Exception as e:
                stats["errors"] += 1
                log.warning("category_failed", category_id=cid, err=str(e))

    await asyncio.gather(*(one(c) for c in leaves))
    return list(seen)


async def _scrape_details(
    shop: Shop,
    ids: list[str],
    *,
    max_age: timedelta,
    limit: int | None,
    workers: int,
    stats: dict[str, int],
) -> None:
    async with session_scope() as session:
        todo = await ids_needing_details(session, shop.name, ids, max_age=max_age, now=utcnow())
    if limit is not None:
        todo = todo[:limit]
    log.info("details_todo", count=len(todo))
    sem = asyncio.Semaphore(workers)

    async def one(item_id: str) -> None:
        async with sem:
            try:
                d = await shop.details(item_id)
                if d is None:
                    return
                async with session_scope() as session:
                    await save_details(session, shop=shop.name, details=d, now=utcnow())
                stats["details"] += 1
                if stats["details"] % 500 == 0:
                    log.info("details_progress", done=stats["details"], total=len(todo))
            except Exception as e:
                stats["errors"] += 1
                log.warning("details_failed", item_id=item_id, err=str(e))

    await asyncio.gather(*(one(i) for i in todo))
