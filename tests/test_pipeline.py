"""Прогон пайплайна целиком на SQLite с фейковым магазином: цены пишутся с городом, история — только при изменении."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import AsyncIterator

import pytest
from sqlalchemy import func, select

from food_scraper import db
from food_scraper.cities import City
from food_scraper.models import Base, Price, PriceSnapshot, Product, ScrapeRun
from food_scraper.pipeline import run as run_mod
from food_scraper.shops.base import CategoryRow, DetailsRow, ItemRow


class FakeShop:
    name = "fake"

    def __init__(self, prices: dict[str, int]):
        self.prices = prices
        self.store_code: str | None = None
        self.details_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def select_store(self, city: City) -> str:
        self.store_code = f"store-{city.slug}"
        return self.store_code

    async def categories(self):
        return [
            CategoryRow(id="1", parent_id=None, name="Молочка", has_children=True),
            CategoryRow(id="10", parent_id="1", name="Молоко"),
            CategoryRow(id="11", parent_id="1", name="Акции"),
        ]

    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        # Товар "a" лежит и в обычной категории, и в промо-подборке.
        ids = {"10": ["a", "b"], "11": ["a"]}[category_id]
        for i in ids:
            yield ItemRow(id=i, name=f"Товар {i}", price_minor=self.prices[i], image_urls=[f"https://img/{i}"])

    async def details(self, item_id: str):
        self.details_calls.append(item_id)
        return DetailsRow(id=item_id, category_id="10", brand="Бренд", kcal=60.0)


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSQL_DSN", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_factory", None)
    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


async def _count(model, *where):
    async with db.session_scope() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def clock(monkeypatch):
    # Каждый вызов utcnow() — на минуту позже: прогоны не слипаются в одну секунду.
    t = [datetime(2026, 10, 31, 12, 0)]

    def tick():
        t[0] += timedelta(minutes=1)
        return t[0]

    monkeypatch.setattr(run_mod, "utcnow", tick)


async def test_run_writes_city_prices_and_history(sqlite_db, clock, monkeypatch):
    shop = FakeShop({"a": 10000, "b": 5000})
    monkeypatch.setattr(run_mod, "get_shop", lambda name: shop)

    await run_mod.run("fake", "moscow")

    async with db.session_scope() as s:
        prices = (await s.execute(select(Price).order_by(Price.product_id))).scalars().all()
        run_row = (await s.execute(select(ScrapeRun))).scalar_one()
        product = await s.get(Product, ("fake", "a"))
    assert [(p.product_id, p.city, p.store_code, p.price_minor) for p in prices] == [
        ("a", "moscow", "store-moscow", 10000),
        ("b", "moscow", "store-moscow", 5000),
    ]
    assert (run_row.status, run_row.products_total, run_row.details_total) == ("ok", 2, 2)
    assert (product.brand, float(product.kcal), product.category_id) == ("Бренд", 60.0, "10")
    assert sorted(shop.details_calls) == ["a", "b"]
    assert await _count(PriceSnapshot) == 2

    # Второй прогон: цена "a" изменилась, "b" — нет; карточки свежие и не перечитываются.
    shop.prices["a"] = 9000
    shop.details_calls.clear()
    await run_mod.run("fake", "moscow")

    assert await _count(PriceSnapshot, PriceSnapshot.product_id == "a") == 2
    assert await _count(PriceSnapshot, PriceSnapshot.product_id == "b") == 1
    assert shop.details_calls == []

    # Другой город — отдельные цены того же товара.
    await run_mod.run("fake", "spb")
    assert await _count(Price, Price.product_id == "a") == 2
    assert await _count(PriceSnapshot, PriceSnapshot.city == "spb") == 2


async def test_run_marks_failed_on_error(sqlite_db, monkeypatch):
    shop = FakeShop({"a": 1, "b": 1})

    async def boom(city):
        raise RuntimeError("магазин недоступен")

    shop.select_store = boom
    monkeypatch.setattr(run_mod, "get_shop", lambda name: shop)
    with pytest.raises(RuntimeError):
        await run_mod.run("fake", "moscow")
    async with db.session_scope() as s:
        r = (await s.execute(select(ScrapeRun))).scalar_one()
    assert (r.status, r.finished_at is not None) == ("failed", True)
