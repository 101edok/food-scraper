"""Постобработка на SQLite: сопоставление ингредиентов, стоимость рецептов, проверка качества, алерты."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text

from food_scraper import alerts, db
from food_scraper.matching import match_all
from food_scraper.models import Base, IngredientMatch, Price, Product, QualityIssue, RecipeCost, ScrapeRun
from food_scraper.quality import check_all
from food_scraper.recipe_costs import compute_all

NOW = datetime(2026, 11, 2, 9, 0)


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSQL_DSN", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_factory", None)
    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Таблицы backend, из которых читает постобработка
        await conn.execute(text("CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name TEXT)"))
        await conn.execute(text("CREATE TABLE videos (id INTEGER PRIMARY KEY, name TEXT, status TEXT)"))
        await conn.execute(text(
            "CREATE TABLE video_ingredients (id INTEGER PRIMARY KEY, video_id INT, ingredient_id INT, quantity TEXT)"))
    yield
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    messages: list[str] = []

    async def fake_send(text_):
        messages.append(text_)

    monkeypatch.setattr(alerts, "send", fake_send)
    return messages


def product(pid, name, qty, unit, category="c1"):
    return Product(shop="magnit", id=pid, name=name, category_id=category, net_quantity=qty, net_unit=unit,
                   is_weighted=False, is_adult=False, first_seen_at=NOW)


def price(pid, value, store="s1"):
    return Price(shop="magnit", product_id=pid, city="moscow", store_code=store, price_minor=value,
                 in_stock=True, seen_at=NOW)


async def seed():
    async with db.session_scope() as s:
        await s.execute(text("INSERT INTO ingredients VALUES (1, 'Мука'), (2, 'Яйцо'), (3, 'Сыр')"))
        await s.execute(text("INSERT INTO videos VALUES (10, 'Блины', 'published'), (11, 'Старое', 'deleted')"))
        await s.execute(text(
            "INSERT INTO video_ingredients (video_id, ingredient_id, quantity) VALUES "
            "(10, 1, '200 г'), (10, 2, '2 штуки'), (10, 3, 'по вкусу'), (11, 1, '1 кг')"))
        s.add_all([
            product("m1", "Мука пшеничная 1кг", 1000, "g"),
            product("m2", "Мука пшеничная высший сорт 2кг", 2000, "g"),
            product("e1", "Яйцо куриное С1 10шт", 10, "pcs", category="eggs"),
            product("x1", "Пластиковое яйцо с сюрпризом", None, None, category="toys"),
            product("s1", "Сыр Российский 200г", 200, "g", category="cheese"),
            product("s2", "Чипсы со вкусом сыра 90г", 90, "g", category="snacks"),
        ] + [product(f"c{i}", f"Крупа {i} 1кг", 1000, "g", category="groats") for i in range(5)]
          + [product("cx", "Крупа ошибка 1кг", 1000, "g", category="groats")])
        s.add_all([price("m1", 6000), price("m2", 12000), price("e1", 11000), price("x1", 9900),
                   price("s1", 20000), price("s2", 9000)]
                  + [price(f"c{i}", 10000) for i in range(5)] + [price("cx", 0)])
        s.add(ScrapeRun(shop="magnit", city="moscow", started_at=NOW - timedelta(days=3),
                        finished_at=NOW - timedelta(days=3), status="ok"))


async def test_match_costs_and_quality(sqlite_db, sent):
    await seed()

    await match_all(NOW)
    async with db.session_scope() as s:
        matches = {(m.ingredient_id, m.product_id): m.status for m in (await s.execute(select(IngredientMatch))).scalars()}
    assert matches == {(1, "m1"): "auto", (1, "m2"): "auto", (2, "e1"): "auto", (3, "s1"): "auto"}

    # Модератор отклонил одну муку — повторный match её не возвращает и не трогает
    async with db.session_scope() as s:
        await s.execute(IngredientMatch.__table__.update()
                        .where(IngredientMatch.product_id == "m2").values(status="rejected"))
    await match_all(NOW)
    async with db.session_scope() as s:
        m2 = await s.get(IngredientMatch, (1, "magnit", "m2"))
    assert m2.status == "rejected"

    await compute_all(NOW)
    async with db.session_scope() as s:
        costs = (await s.execute(select(RecipeCost))).scalars().all()
    assert [(c.video_id, c.cost_minor, c.ingredients_total, c.ingredients_priced) for c in costs] == \
        [(10, 200 * 6 + 2 * 1100, 2, 2)]  # мука 60 ₽/кг × 200 г + 2 яйца по 11 ₽; сыр «по вкусу»; удалённый рецепт пропущен
    lines = {l["ingredient"]: l for l in json.loads(costs[0].breakdown)}
    assert lines["Мука"]["product_id"] == "m1" and not lines["Сыр"]["priced"]

    await check_all(NOW)
    async with db.session_scope() as s:
        issues = {(i.kind, i.product_id) for i in (await s.execute(select(QualityIssue))).scalars()}
    assert ("stale", None) in issues  # успешный прогон 3 дня назад
    assert ("zero_price", "cx") in issues
    assert ("no_pack", None) not in issues  # без фасовки 1 из 13
    assert sent and "Данные старше 48 ч" in sent[0]


async def test_blocked_run_sends_alert(sqlite_db, sent):
    async with db.session_scope() as s:
        s.add(ScrapeRun(id=5, shop="dixy", city="moscow", started_at=NOW, finished_at=NOW, status="blocked"))
    await alerts.on_run_finished(5)
    assert len(sent) == 1 and "Блокировка" in sent[0] and "dixy/moscow" in sent[0]


async def test_products_drop_alert(sqlite_db, sent):
    async with db.session_scope() as s:
        for i, total in enumerate([1000, 1100, 1050, 300], start=1):
            s.add(ScrapeRun(id=i, shop="magnit", city="moscow", started_at=NOW, finished_at=NOW, status="ok",
                            stores_total=12, products_total=total))
    await alerts.on_run_finished(4)
    assert len(sent) == 1 and "меньше обычного" in sent[0]


def test_outlier_message_is_readable():
    from food_scraper.quality import outlier_message
    assert outlier_message(4.75, 66.48, "g") == \
        "Цена за кг в 14 раз ниже, чем обычно в категории: 47,50 ₽/кг против 664,80 ₽/кг. Проверьте фасовку"
    assert "в 3 раза выше" in outlier_message(300, 100, "pcs")
