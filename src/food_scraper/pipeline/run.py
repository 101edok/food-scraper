"""Один прогон = один магазин (сеть) × один город, по нескольким точкам сети.

1. Справочник точек города (shop_stores). Пустой или --refresh-stores — обойти город сеткой.
2. Выбрать точки: --stores N — N штук, разнесённых по районам (каждый день одни и те же);
   без лимита — все активные.
3. По каждой точке: дерево категорий, листовые категории — товары и цены точки.
4. Дочитать карточки (состав, КБЖУ) только для новых и устаревших товаров — один раз на сеть.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from ..cities import City, get_city
from ..db import session_scope
from ..models import ScrapeRun
from ..shops.base import ItemRow, Shop, get_shop
from ..shops.base import CategoryRow
from ..stores import StoreRow, dedupe, distance_km, grid_points, spread_sample
from ..upsert import (
    ids_needing_details,
    load_stores,
    save_details,
    save_items,
    upsert_categories,
    upsert_stores,
)

log = structlog.get_logger(__name__)


def utcnow() -> datetime:
    # DATETIME в MySQL без зоны — храним UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


async def run(
    shop_name: str,
    city_slug: str,
    *,
    stores: int | None = 12,
    refresh_stores: bool = False,
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
    stats = {"stores": 0, "categories": 0, "products": 0, "prices": 0, "details": 0, "errors": 0}
    status = "ok"
    store_code: str | None = None
    try:
        async with shop:
            registry = await _registry(shop, city, refresh=refresh_stores, workers=workers)
            chosen = registry if stores is None else spread_sample(registry, stores, (city.lat, city.lon))
            if not chosen:
                raise RuntimeError(f"{shop.name}: в {city.name} нет точек с онлайн-каталогом")
            store_code = chosen[0].code if len(chosen) == 1 else None
            log.info("stores_chosen", chosen=len(chosen), registry=len(registry),
                     codes=",".join(s.code for s in chosen))

            # Товар → первая точка, где его видели: через неё дочитываем карточку
            product_store: dict[str, StoreRow] = {}
            for n, store in enumerate(chosen, 1):
                try:
                    await _scrape_store(
                        shop, store, run_id=run_id, city=city.slug, category_id=category_id,
                        limit=limit, workers=workers, stats=stats, product_store=product_store,
                    )
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("store_failed", store_code=store.code, err=str(e))
                stats["stores"] = n
                stats["products"] = len(product_store)
                await _save_progress(run_id, stats, store_code=store_code)
                log.info("store_done", store_code=store.code, progress=f"{n}/{len(chosen)}",
                         products=stats["products"], prices=stats["prices"])

            await _scrape_details(
                shop, product_store, max_age=details_max_age, limit=details_limit,
                workers=workers, stats=stats, run_id=run_id,
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
        await _save_progress(run_id, stats, store_code=store_code, status=status, finished=True)
        log.info("run_done", status=status, **stats)
        structlog.contextvars.unbind_contextvars("run_id", "shop", "city")
    return run_id


async def discover_stores(shop_name: str, city_slug: str, *, workers: int = 4) -> list[StoreRow]:
    """Только обновить справочник точек города, без цен (food-scrape stores)."""
    city = get_city(city_slug)
    shop = get_shop(shop_name)
    async with shop:
        return await _registry(shop, city, refresh=True, workers=workers)


async def _registry(shop: Shop, city: City, *, refresh: bool, workers: int) -> list[StoreRow]:
    if not refresh:
        async with session_scope() as session:
            registry = await load_stores(session, shop=shop.name, city=city.slug)
        if registry:
            return registry

    found: list[StoreRow] = []
    list_stores = getattr(shop, "list_stores", None)
    if list_stores is not None:
        # Вся сеть одним запросом — оставляем точки в радиусе города
        points = []
        found = [
            s for s in await list_stores()
            if s.lat is not None and s.lon is not None
            and distance_km((s.lat, s.lon), (city.lat, city.lon)) <= city.radius_km
        ]
    else:
        points = grid_points(city)
        sem = asyncio.Semaphore(workers)

        async def one(lat: float, lon: float) -> None:
            async with sem:
                try:
                    found.extend(await shop.find_stores(lat, lon))
                except Exception as e:
                    log.warning("stores_point_failed", lat=lat, lon=lon, err=str(e))

        await asyncio.gather(*(one(lat, lon) for lat, lon in points))
    stores = dedupe(found)
    async with session_scope() as session:
        deactivated = await upsert_stores(session, shop=shop.name, city=city.slug, stores=stores, now=utcnow())
        registry = await load_stores(session, shop=shop.name, city=city.slug)
    log.info("stores_discovered", points=len(points), found=len(stores), deactivated=deactivated)
    return registry


_KEEP = object()


async def _save_progress(
    run_id: int, stats: dict[str, int], *, store_code: object = _KEEP,
    status: str | None = None, finished: bool = False,
) -> None:
    """Счётчики пишем по ходу прогона: в админке видно, сколько уже собрано."""
    async with session_scope() as session:
        r = await session.get(ScrapeRun, run_id)
        if r is None:
            return
        if store_code is not _KEEP:
            r.store_code = store_code
        r.stores_total = stats["stores"]
        r.categories_total = stats["categories"]
        r.products_total = stats["products"]
        r.prices_total = stats["prices"]
        r.details_total = stats["details"]
        r.errors_total = stats["errors"]
        if status:
            r.status = status
        if finished:
            r.finished_at = utcnow()


async def _scrape_store(
    shop: Shop,
    store: StoreRow,
    *,
    run_id: int,
    city: str,
    category_id: str | None,
    limit: int | None,
    workers: int,
    stats: dict[str, int],
    product_store: dict[str, StoreRow],
) -> None:
    shop.use_store(store)
    structlog.contextvars.bind_contextvars(store_code=store.code)
    try:
        cats = await shop.categories()
        async with session_scope() as session:
            await upsert_categories(session, shop.name, cats)
        leaves = [category_id] if category_id else [c.id for c in cats if not c.has_children]
        log.info("categories_synced", total=len(cats), leaves=len(leaves))

        # Внутри точки товар лежит в нескольких категориях (плюс промо-подборки) — цену пишем один раз.
        # Проверка и добавление идут без await между ними, так что гонок между задачами нет.
        seen: set[str] = set()
        # Листья, которые магазин отдал в листинге (Дикси): дерево без id, листья узнаём из товаров
        listed_categories: dict[str, CategoryRow] = {}
        sem = asyncio.Semaphore(workers)

        async def one(cid: str) -> None:
            async with sem:
                try:
                    fresh: list[ItemRow] = []
                    n = 0
                    async for item in shop.iter_items(cid):
                        n += 1
                        if item.id not in seen:
                            seen.add(item.id)
                            fresh.append(item)
                        if limit is not None and n >= limit:
                            break
                    async with session_scope() as session:
                        changed = await save_items(
                            session, shop=shop.name, city=city, store_code=store.code,
                            run_id=run_id, items=fresh, now=utcnow(),
                        )
                    for item in fresh:
                        product_store.setdefault(item.id, store)
                        if item.category_id and item.category_id not in listed_categories:
                            listed_categories[item.category_id] = CategoryRow(
                                id=item.category_id, parent_id=cid, name=item.category_name or "", level=1,
                            )
                    stats["categories"] += 1
                    stats["prices"] += len(fresh)
                    log.debug("category_done", category_id=cid, items=n, new=len(fresh), price_changed=changed)
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("category_failed", category_id=cid, err=str(e))

        await asyncio.gather(*(one(c) for c in leaves))
        known = {c.id for c in cats}
        new_categories = [c for c in listed_categories.values() if c.id not in known]
        if new_categories:
            async with session_scope() as session:
                await upsert_categories(session, shop.name, new_categories)
    finally:
        structlog.contextvars.unbind_contextvars("store_code")


async def _scrape_details(
    shop: Shop,
    product_store: dict[str, StoreRow],
    *,
    max_age: timedelta,
    limit: int | None,
    workers: int,
    stats: dict[str, int],
    run_id: int,
) -> None:
    async with session_scope() as session:
        todo = await ids_needing_details(
            session, shop.name, list(product_store), max_age=max_age, now=utcnow()
        )
    if limit is not None:
        todo = todo[:limit]
    log.info("details_todo", count=len(todo))

    # Карточку отдаёт каталог точки — идём точками, в каждой её товары
    by_store: dict[str, list[str]] = {}
    for item_id in todo:
        by_store.setdefault(product_store[item_id].code, []).append(item_id)
    stores = {s.code: s for s in product_store.values()}

    for code, ids in by_store.items():
        shop.use_store(stores[code])
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
                        await _save_progress(run_id, stats)
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("details_failed", item_id=item_id, err=str(e))

        await asyncio.gather(*(one(i) for i in ids))
