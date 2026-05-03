from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Category,
    PriceSnapshot,
    Product,
    ProductAttribute,
    ProductImage,
)


async def upsert_categories(
    session: AsyncSession, rows: Sequence[dict]
) -> None:
    if not rows:
        return
    stmt = mysql_insert(Category).values(list(rows))
    upd = {
        c.name: stmt.inserted[c.name]
        for c in Category.__table__.columns
        if c.name != "id"
    }
    await session.execute(stmt.on_duplicate_key_update(**upd))


async def upsert_product(
    session: AsyncSession,
    product: dict,
    images: Iterable[dict] = (),
    attributes: Iterable[dict] = (),
) -> None:
    """Идемпотентная запись товара с историей цен."""
    pid = product["id"]
    new_price = product.get("price_minor")
    new_old_price = product.get("old_price_minor")
    new_in_stock = bool(product.get("in_stock", True))

    existing = await session.scalar(
        select(Product).where(Product.id == pid)
    )

    p_stmt = mysql_insert(Product).values(**product)
    p_upd = {
        c.name: p_stmt.inserted[c.name]
        for c in Product.__table__.columns
        if c.name != "id"
    }
    await session.execute(p_stmt.on_duplicate_key_update(**p_upd))

    price_changed = (
        existing is None
        or existing.price_minor != new_price
        or existing.old_price_minor != new_old_price
        or existing.in_stock != new_in_stock
    )
    if price_changed:
        await session.execute(
            mysql_insert(PriceSnapshot)
            .values(
                product_id=pid,
                captured_at=datetime.now(timezone.utc).replace(microsecond=0),
                price_minor=new_price,
                old_price_minor=new_old_price,
                in_stock=new_in_stock,
            )
            .prefix_with("IGNORE")
        )

    img_rows = [
        {"product_id": pid, "position": i, "url": u}
        for i, u in enumerate(images)
        if u
    ]
    if img_rows:
        i_stmt = mysql_insert(ProductImage).values(img_rows)
        await session.execute(
            i_stmt.on_duplicate_key_update(url=i_stmt.inserted.url)
        )

    attr_rows = [
        {"product_id": pid, "name": a["name"], "value": a["value"]}
        for a in attributes
        if a.get("name") and a.get("value") is not None
    ]
    if attr_rows:
        a_stmt = mysql_insert(ProductAttribute).values(attr_rows)
        await session.execute(
            a_stmt.on_duplicate_key_update(value=a_stmt.inserted.value)
        )
