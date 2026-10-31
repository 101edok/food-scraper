"""Прогон пайплайна целиком на SQLite с фейковой сетью: цены пишутся по точкам с городом,
история — только при изменении, справочник точек и выбор подмножества."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import AsyncIterator

import pytest
from sqlalchemy import func, select

from food_scraper import db
from food_scraper.models import Base, Price, PriceSnapshot, Product, ScrapeRun, Store
from food_scraper.pipeline import run as run_mod
from food_scraper.shops.base import CategoryRow, DetailsRow, ItemRow
from food_scraper.stores import StoreRow, grid_points, spread_sample
from food_scraper.cities import get_city

# Три точки в Москве: центр, север, юг. Ассортимент и цены у них разные.
STORES = {
    "c": StoreRow(code="c", store_type="1", address="центр", lat=55.75, lon=37.62),
    "n": StoreRow(code="n", store_type="1", address="север", lat=55.88, lon=37.62),
    "s": StoreRow(code="s", store_type="1", address="юг", lat=55.60, lon=37.62),
}


class FakeShop:
    name = "fake"

    def __init__(self):
        self.store_code: str | None = None
        self.visible = set(STORES)  # какие точки «видит» обход сеткой
        # (точка, товар) → цена
        self.prices = {("c", "a"): 10000, ("c", "b"): 5000, ("n", "a"): 10500, ("s", "z"): 700}
        self.details_calls: list[tuple[str, str]] = []
        self.find_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def find_stores(self, lat, lon):
        self.find_calls += 1
        nearest = min(self.visible, key=lambda c: abs(STORES[c].lat - lat))
        return [STORES[nearest]]

    def use_store(self, store):
        self.store_code = store.code

    async def categories(self):
        return [
            CategoryRow(id="1", parent_id=None, name="Молочка", has_children=True),
            CategoryRow(id="10", parent_id="1", name="Молоко"),
            CategoryRow(id="11", parent_id="1", name="Акции"),
        ]

    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        # Товар лежит и в обычной категории, и в промо-подборке
        for (store, item), price in sorted(self.prices.items()):
            if store == self.store_code:
                yield ItemRow(id=item, name=f"Товар {item}", price_minor=price, image_urls=[f"https://img/{item}"])

    async def details(self, item_id: str):
        self.details_calls.append((self.store_code, item_id))
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


@pytest.fixture
def clock(monkeypatch):
    # Каждый вызов utcnow() — на минуту позже: прогоны не слипаются в одну секунду.
    t = [datetime(2026, 10, 31, 12, 0)]

    def tick():
        t[0] += timedelta(minutes=1)
        return t[0]

    monkeypatch.setattr(run_mod, "utcnow", tick)


@pytest.fixture
def shop(monkeypatch):
    s = FakeShop()
    monkeypatch.setattr(run_mod, "get_shop", lambda name: s)
    return s


async def _all(model, *where):
    async with db.session_scope() as s:
        return (await s.execute(select(model).where(*where))).scalars().all()


async def _count(model, *where):
    async with db.session_scope() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


async def test_prices_per_store_with_city(sqlite_db, clock, shop):
    await run_mod.run("fake", "moscow", stores=None)

    prices = sorted((p.store_code, p.product_id, p.city, p.price_minor) for p in await _all(Price))
    assert prices == [
        ("c", "a", "moscow", 10000),
        ("c", "b", "moscow", 5000),
        ("n", "a", "moscow", 10500),
        ("s", "z", "moscow", 700),
    ]
    run_row = (await _all(ScrapeRun))[0]
    assert (run_row.status, run_row.stores_total, run_row.products_total, run_row.prices_total) == ("ok", 3, 3, 4)
    assert run_row.store_code is None  # точек несколько
    assert await _count(Store, Store.is_active.is_(True)) == 3

    # Карточка — один раз на товар, через точку, где он нашёлся первым
    assert sorted(item for _, item in shop.details_calls) == ["a", "b", "z"]
    assert ("s", "z") in shop.details_calls
    product = await _all(Product, Product.id == "a")
    assert (product[0].brand, float(product[0].kcal)) == ("Бренд", 60.0)


async def test_history_only_on_change_and_registry_reused(sqlite_db, clock, shop):
    await run_mod.run("fake", "moscow", stores=None)
    calls_after_discovery = shop.find_calls

    shop.prices[("n", "a")] = 9900
    shop.details_calls.clear()
    await run_mod.run("fake", "moscow", stores=None)

    # Справочник уже есть — сеткой второй раз не ходим, карточки свежие
    assert shop.find_calls == calls_after_discovery
    assert shop.details_calls == []
    assert await _count(PriceSnapshot, PriceSnapshot.store_code == "n", PriceSnapshot.product_id == "a") == 2
    assert await _count(PriceSnapshot, PriceSnapshot.store_code == "c", PriceSnapshot.product_id == "a") == 1


async def test_refresh_deactivates_vanished_stores(sqlite_db, clock, shop):
    await run_mod.run("fake", "moscow", stores=None)
    shop.visible = {"c", "n"}
    await run_mod.run("fake", "moscow", stores=None, refresh_stores=True)

    active = sorted(s.code for s in await _all(Store, Store.is_active.is_(True)))
    assert active == ["c", "n"]
    last = (await _all(ScrapeRun, ScrapeRun.id == 2))[0]
    assert last.stores_total == 2
    # История южной точки остаётся
    assert await _count(Price, Price.store_code == "s") == 1


async def test_store_limit_is_spread_and_stable(sqlite_db, clock, shop):
    await run_mod.run("fake", "moscow", stores=2)
    first = sorted({p.store_code for p in await _all(Price)})
    # Ближайшая к центру + самая далёкая от неё
    assert first == ["c", "n"] or first == ["c", "s"]
    await run_mod.run("fake", "moscow", stores=2)
    assert sorted({p.store_code for p in await _all(Price)}) == first


async def test_run_marks_failed_without_stores(sqlite_db, clock, shop):
    async def none(lat, lon):
        return []

    shop.find_stores = none
    with pytest.raises(RuntimeError):
        await run_mod.run("fake", "moscow")
    r = (await _all(ScrapeRun))[0]
    assert (r.status, r.finished_at is not None) == ("failed", True)


def test_grid_covers_city_from_center():
    city = get_city("moscow")
    points = grid_points(city, step_km=2)
    assert points[0] == (round(city.lat, 5), round(city.lon, 5))
    # Круг радиуса 25 км с шагом 2 км — около π·12.5² ≈ 490 точек
    assert 400 < len(points) < 560


def test_spread_sample_is_deterministic():
    rows = [StoreRow(code=str(i), store_type="1", lat=55.5 + i * 0.01, lon=37.6) for i in range(40)]
    a = spread_sample(rows, 5, (55.7, 37.6))
    b = spread_sample(list(reversed(rows)), 5, (55.7, 37.6))
    assert [s.code for s in a] == [s.code for s in b]
    assert a[0].code == "20"  # ближайшая к центру
    assert {"0", "39"} <= {s.code for s in a}  # края города попали в выборку
