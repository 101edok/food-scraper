"""Идемпотентная запись: INSERT … ON DUPLICATE KEY UPDATE (MySQL) / ON CONFLICT (SQLite в тестах)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Category,
    Store,
    Price,
    PriceSnapshot,
    Product,
    ProductAttribute,
    ProductImage,
)
from .shops.base import CategoryRow, DetailsRow, ItemRow
from .stores import StoreRow

BATCH = 500


async def _upsert(
    session: AsyncSession,
    model: type,
    rows: Sequence[dict[str, Any]],
    update: Iterable[str],
) -> None:
    update = list(update)
    dialect = session.bind.dialect.name
    for i in range(0, len(rows), BATCH):
        chunk = list(rows[i : i + BATCH])
        if dialect == "mysql":
            stmt = mysql_insert(model).values(chunk)
            stmt = (
                stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in update})
                if update
                else stmt.prefix_with("IGNORE")
            )
        else:
            stmt = sqlite_insert(model).values(chunk)
            pk = [c.name for c in model.__table__.primary_key.columns]
            stmt = (
                stmt.on_conflict_do_update(
                    index_elements=pk, set_={c: stmt.excluded[c] for c in update}
                )
                if update
                else stmt.on_conflict_do_nothing(index_elements=pk)
            )
        await session.execute(stmt)


async def upsert_categories(
    session: AsyncSession, shop: str, rows: Sequence[CategoryRow]
) -> None:
    cols = ["parent_id", "name", "slug", "level", "has_children", "is_adult", "image_url", "url"]
    await _upsert(
        session,
        Category,
        [{"shop": shop, "id": r.id, **{c: getattr(r, c) for c in cols}} for r in rows],
        cols,
    )


async def save_items(
    session: AsyncSession,
    *,
    shop: str,
    city: str,
    store_code: str,
    run_id: int | None,
    items: Sequence[ItemRow],
    now: datetime,
) -> int:
    """Товары из листинга + цены точки store_code в городе. Возвращает, у скольких цена изменилась."""
    if not items:
        return 0
    # Один товар может встретиться в нескольких категориях одного батча.
    items = list({it.id: it for it in items}.values())
    ids = [it.id for it in items]

    # first_seen_at не перетираем: в update его нет.
    await _upsert(
        session,
        Product,
        [
            {
                "shop": shop,
                "id": it.id,
                "name": it.name[:1024],
                "url": it.url,
                "is_weighted": it.is_weighted,
                "is_adult": it.is_adult,
                "first_seen_at": now,
            }
            for it in items
        ],
        ["name", "url", "is_weighted", "is_adult"],
    )

    images = [
        {"shop": shop, "product_id": it.id, "position": pos, "url": url[:1024]}
        for it in items
        for pos, url in enumerate(it.image_urls)
    ]
    if images:
        await _upsert(session, ProductImage, images, ["url"])

    prev = {
        p.product_id: p
        for p in (
            await session.execute(
                select(Price).where(
                    Price.shop == shop,
                    Price.city == city,
                    Price.store_code == store_code,
                    Price.product_id.in_(ids),
                )
            )
        ).scalars()
    }

    snapshots = []
    for it in items:
        old = prev.get(it.id)
        if (
            old is None
            or old.price_minor != it.price_minor
            or old.old_price_minor != it.old_price_minor
            or old.in_stock != it.in_stock
        ):
            snapshots.append(
                {
                    "shop": shop,
                    "product_id": it.id,
                    "city": city,
                    "captured_at": now,
                    "store_code": store_code,
                    "price_minor": it.price_minor,
                    "old_price_minor": it.old_price_minor,
                    "in_stock": it.in_stock,
                }
            )

    price_cols = [
        "price_minor", "old_price_minor", "unit_price_minor",
        "unit_label", "in_stock", "seen_at", "run_id",
    ]
    await _upsert(
        session,
        Price,
        [
            {
                "shop": shop,
                "product_id": it.id,
                "city": city,
                "store_code": store_code,
                "price_minor": it.price_minor,
                "old_price_minor": it.old_price_minor,
                "unit_price_minor": it.unit_price_minor,
                "unit_label": it.unit_label,
                "in_stock": it.in_stock,
                "seen_at": now,
                "run_id": run_id,
            }
            for it in items
        ],
        price_cols,
    )
    if snapshots:
        # Два прогона в одну секунду дают одинаковый ключ — оставляем последнюю цену.
        await _upsert(
            session, PriceSnapshot, snapshots, ["price_minor", "old_price_minor", "in_stock"],
        )
    return len(snapshots)


async def ids_needing_details(
    session: AsyncSession, shop: str, ids: Sequence[str], *, max_age: timedelta, now: datetime
) -> list[str]:
    """Карточку (состав, КБЖУ) перечитываем редко: только новые и устаревшие."""
    if not ids:
        return []
    rows = await session.execute(
        select(Product.id).where(
            Product.shop == shop,
            Product.id.in_(list(ids)),
            or_(Product.details_at.is_(None), Product.details_at < now - max_age),
        )
    )
    return [r[0] for r in rows]


async def save_details(
    session: AsyncSession, *, shop: str, details: DetailsRow, now: datetime
) -> None:
    fields = [
        "category_id", "brand", "manufacturer", "description", "composition",
        "kcal", "protein", "fat", "carbs", "weight_g",
    ]
    product = await session.get(Product, (shop, details.id))
    if product is None:
        return
    for f in fields:
        value = getattr(details, f)
        if value is not None:
            if f in ("brand", "manufacturer") and isinstance(value, str):
                value = value[:255]
            setattr(product, f, value)
    product.details_at = now

    if details.image_urls:
        await _upsert(
            session,
            ProductImage,
            [
                {"shop": shop, "product_id": details.id, "position": i, "url": u[:1024]}
                for i, u in enumerate(details.image_urls)
            ],
            ["url"],
        )
    if details.attributes:
        await _upsert(
            session,
            ProductAttribute,
            [
                {"shop": shop, "product_id": details.id, "name": k[:255], "value": v[:1024]}
                for k, v in details.attributes.items()
            ],
            ["value"],
        )



async def upsert_stores(
    session: AsyncSession, *, shop: str, city: str, stores: Sequence[StoreRow], now: datetime
) -> int:
    """Справочник точек города. Те, что обход больше не нашёл, помечаем неактивными, но не удаляем:
    на них ссылается история цен. Возвращает число деактивированных."""
    if stores:
        await _upsert(
            session,
            Store,
            [
                {
                    "shop": shop, "code": s.code, "city": city, "store_type": s.store_type,
                    "address": (s.address or "")[:512] or None, "lat": s.lat, "lon": s.lon,
                    "is_active": True, "first_seen_at": now, "last_seen_at": now,
                }
                for s in stores
            ],
            ["city", "store_type", "address", "lat", "lon", "is_active", "last_seen_at"],
        )
    result = await session.execute(
        update(Store)
        .where(Store.shop == shop, Store.city == city, Store.is_active.is_(True), Store.last_seen_at < now)
        .values(is_active=False)
    )
    return result.rowcount or 0


async def load_stores(session: AsyncSession, *, shop: str, city: str) -> list[StoreRow]:
    rows = await session.execute(
        select(Store).where(Store.shop == shop, Store.city == city, Store.is_active.is_(True))
    )
    return [
        StoreRow(
            code=s.code, store_type=s.store_type, address=s.address,
            lat=float(s.lat) if s.lat is not None else None,
            lon=float(s.lon) if s.lon is not None else None,
        )
        for s in rows.scalars()
    ]
