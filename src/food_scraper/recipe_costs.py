"""Оценка стоимости рецептов edalle (videos + video_ingredients backend) по сетям и городам.

Цена ингредиента за единицу (копейки за 1 g / ml / pcs): по каждому сопоставленному товару — медиана
по точкам города, затем медиана по товарам. Подтверждённые модератором (approved) товары важнее
подобранных автоматически: если они есть, auto не учитываем. Граммы и миллилитры взаимозаменяемы
(плотность 1) — для оценки «сколько стоит приготовить» точности хватает.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import delete, select, text

from .db import session_scope
from .matching import stems
from .models import IngredientMatch, Price, Product, RecipeCost
from .quantity import GRAM, ML, PCS, parse_recipe_quantity

log = structlog.get_logger(__name__)

# Вес одной штуки, когда в рецепте «2 штуки», а товар продаётся на вес (основа слова → граммы)
PIECE_WEIGHT_G = {stem: grams for word, grams in {
    "яйцо": 55, "желток": 20, "лук": 100, "луковица": 100, "морковь": 100, "картофель": 120,
    "помидор": 120, "томат": 120, "огурец": 100, "лимон": 120, "перец": 150, "яблоко": 150,
    "банан": 120, "кабачок": 300, "баклажан": 250, "авокадо": 200, "апельсин": 200, "чеснок": 40,
}.items() for stem in stems(word)}
CLOVE_G = 5  # зубчик чеснока


@dataclass(slots=True)
class IngredientPrice:
    """Медианная цена ингредиента за единицу по видам фасовки (g/ml сливаются в одну «массу»)."""

    per_mass: float | None = None  # копейки за 1 g (или ml)
    per_piece: float | None = None  # копейки за 1 шт
    product_id: str | None = None  # пример товара — для разбивки в админке
    product_name: str | None = None


@dataclass(slots=True)
class Line:
    ingredient_id: int
    ingredient: str
    quantity: str
    amount: float | None = None
    unit: str | None = None
    product_id: str | None = None
    product_name: str | None = None
    unit_price_minor: float | None = None
    cost_minor: int | None = None
    priced: bool = False
    reason: str | None = None


@dataclass(slots=True)
class Estimate:
    cost_minor: int = 0
    total: int = 0
    priced: int = 0
    lines: list[Line] = field(default_factory=list)


def estimate_recipe(
    ingredients: list[tuple[int, str, str]], prices: dict[int, IngredientPrice]
) -> Estimate:
    """ingredients: (ingredient_id, название, количество из рецепта)."""
    est = Estimate()
    for ingredient_id, name, quantity_text in ingredients:
        line = Line(ingredient_id=ingredient_id, ingredient=name, quantity=quantity_text or "")
        est.lines.append(line)
        q = parse_recipe_quantity(quantity_text)
        if q is None:
            line.reason = "количество не задано («по вкусу», «для жарки»)"
            continue
        est.total += 1
        line.amount, line.unit = q.amount, q.unit
        price = prices.get(ingredient_id)
        if price is None or (price.per_mass is None and price.per_piece is None):
            line.reason = "нет сопоставленных товаров с ценой"
            continue
        line.product_id, line.product_name = price.product_id, price.product_name

        grams = None
        if q.unit in (GRAM, ML):
            grams = q.amount
        elif "зубч" in (quantity_text or "").lower():
            grams = q.amount * CLOVE_G
        if q.unit == PCS and price.per_piece is not None and grams is None:
            line.unit_price_minor, cost = price.per_piece, q.amount * price.per_piece
        elif grams is None:
            # Штуки в рецепте, товар на вес — нужен вес штуки
            weight = next((w for s, w in PIECE_WEIGHT_G.items() if s in stems(name)), None)
            if weight is None or price.per_mass is None:
                line.reason = "в рецепте штуки, товар на вес — вес штуки неизвестен"
                continue
            line.unit_price_minor, cost = price.per_mass, q.amount * weight * price.per_mass
        elif price.per_mass is not None:
            line.unit_price_minor, cost = price.per_mass, grams * price.per_mass
        else:
            # Граммы в рецепте, товар штучный (яйца десятком) — через вес штуки
            weight = next((w for s, w in PIECE_WEIGHT_G.items() if s in stems(name)), None)
            if weight is None:
                line.reason = "в рецепте граммы, товар штучный — вес штуки неизвестен"
                continue
            line.unit_price_minor, cost = price.per_piece, grams / weight * price.per_piece
        line.cost_minor, line.priced = round(cost), True
        est.cost_minor += line.cost_minor
        est.priced += 1
    return est


async def compute_all(now: datetime) -> dict[str, int]:
    """Пересчитать shop_recipe_costs для всех опубликованных рецептов, сетей и городов."""
    async with session_scope() as session:
        recipes: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
        for video_id, ingredient_id, name, quantity in (await session.execute(text(
            "SELECT vi.video_id, vi.ingredient_id, i.name, vi.quantity FROM video_ingredients vi "
            "JOIN ingredients i ON i.id = vi.ingredient_id JOIN videos v ON v.id = vi.video_id "
            "WHERE v.status <> 'deleted' ORDER BY vi.id"
        ))).all():
            recipes[video_id].append((ingredient_id, name, quantity))

        matches = (await session.execute(
            select(IngredientMatch.ingredient_id, IngredientMatch.shop, IngredientMatch.product_id,
                   IngredientMatch.status).where(IngredientMatch.status != "rejected")
        )).all()
        # Только цены сопоставленных товаров: всех цен — сотни тысяч (товары × точки)
        matched = select(IngredientMatch.shop, IngredientMatch.product_id) \
            .where(IngredientMatch.status != "rejected").distinct().subquery()
        rows = (await session.execute(
            select(Price.shop, Price.city, Price.product_id, Price.price_minor,
                   Product.name, Product.net_quantity, Product.net_unit)
            .join(matched, (matched.c.shop == Price.shop) & (matched.c.product_id == Price.product_id))
            .join(Product, (Product.shop == Price.shop) & (Product.id == Price.product_id))
            .where(Price.price_minor.is_not(None), Product.net_quantity.is_not(None))
        )).all()

    # Цена товара за единицу в городе: медиана по точкам
    per_unit: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    product_info: dict[tuple[str, str], tuple[str, str]] = {}
    for shop, city, product_id, price, name, net_quantity, net_unit in rows:
        if net_quantity and float(net_quantity) > 0:
            per_unit[(shop, city, product_id)].append(price / float(net_quantity))
            product_info[(shop, product_id)] = (name, net_unit)

    by_ingredient: dict[tuple[int, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for ingredient_id, shop, product_id, status in matches:
        by_ingredient[(ingredient_id, shop)][status].append(product_id)

    cities_by_shop: dict[str, set[str]] = defaultdict(set)
    for shop, city, _ in per_unit:
        cities_by_shop[shop].add(city)

    stats = {"recipes": len(recipes), "rows": 0}
    async with session_scope() as session:
        for shop, cities in cities_by_shop.items():
            for city in cities:
                prices: dict[int, IngredientPrice] = {}
                for (ingredient_id, match_shop), by_status in by_ingredient.items():
                    if match_shop != shop:
                        continue
                    product_ids = by_status.get("approved") or by_status.get("auto") or []
                    prices[ingredient_id] = ingredient_price(shop, city, product_ids, per_unit, product_info)

                await session.execute(delete(RecipeCost).where(RecipeCost.shop == shop, RecipeCost.city == city))
                for video_id, ingredients in recipes.items():
                    est = estimate_recipe(ingredients, prices)
                    if not est.priced:
                        continue
                    session.add(RecipeCost(
                        video_id=video_id, shop=shop, city=city, cost_minor=est.cost_minor,
                        ingredients_total=est.total, ingredients_priced=est.priced,
                        breakdown=json.dumps([line_dict(l) for l in est.lines], ensure_ascii=False),
                        computed_at=now,
                    ))
                    stats["rows"] += 1
    log.info("recipe_costs_done", **stats)
    return stats


def ingredient_price(shop, city, product_ids, per_unit, product_info) -> IngredientPrice:
    mass, pieces = [], []
    sample: tuple[str, str] | None = None
    for product_id in product_ids:
        values = per_unit.get((shop, city, product_id))
        info = product_info.get((shop, product_id))
        if not values or not info:
            continue
        name, unit = info
        price = statistics.median(values)
        (pieces if unit == PCS else mass).append(price)
        sample = sample or (product_id, name)
    return IngredientPrice(
        per_mass=statistics.median(mass) if mass else None,
        per_piece=statistics.median(pieces) if pieces else None,
        product_id=sample[0] if sample else None,
        product_name=sample[1] if sample else None,
    )


def line_dict(line: Line) -> dict:
    return {
        "ingredient_id": line.ingredient_id, "ingredient": line.ingredient, "quantity": line.quantity,
        "amount": line.amount, "unit": line.unit, "product_id": line.product_id,
        "product_name": line.product_name,
        "unit_price_minor": round(line.unit_price_minor, 4) if line.unit_price_minor is not None else None,
        "cost_minor": line.cost_minor, "priced": line.priced, "reason": line.reason,
    }
