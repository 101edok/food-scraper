"""Сопоставление ингредиентов рецептов edalle (таблица ingredients backend) с товарами сетей.

Правило простое и объяснимое: все слова ингредиента (по основам Snowball) есть в названии товара,
а лучше всего — товар с этого слова начинается («Лук репчатый», а не «Чипсы со вкусом лука»).
Результат — строки status=auto в shop_ingredient_matches; решения модератора (approved/rejected)
не трогаем, отклонённое больше не предлагаем.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import snowballstemmer
import structlog
from sqlalchemy import delete, func, select, text

from .db import session_scope
from .models import IngredientMatch, Price, Product

log = structlog.get_logger(__name__)

# Порог требует, чтобы товар начинался со слова ингредиента: «Пластиковое яйцо с сюрпризом» не проходит
MIN_SCORE = 0.8
TOP_PER_SHOP = 15

_stemmer = snowballstemmer.stemmer("russian")
_WORD_RE = re.compile(r"[а-яa-z]+", re.IGNORECASE)

# Как ещё называется ингредиент на полке. Ключ — название ингредиента в нижнем регистре.
ALIASES: dict[str, list[str]] = {
    "курица": ["курица", "куриное филе", "филе куриное", "цыпленок", "окорочка", "бедро куриное", "голень куриная"],
    "яйцо": ["яйцо куриное", "яйца куриные", "яйцо", "яйца"],
    "масло": ["масло сливочное", "масло подсолнечное"],
    "растительное масло": ["масло подсолнечное", "масло растительное", "масло оливковое"],
    "сливочное масло": ["масло сливочное"],
    "перец": ["перец черный молотый", "перец болгарский", "перец сладкий"],
    "чёрный перец": ["перец черный"],
    "лук": ["лук репчатый", "лук"],
    # Не «зелень»: основа «зелен» совпадает с «Зелёный чай», «Зелёный горошек»
    "зелень": ["укроп", "петрушка", "кинза", "базилик"],
    "свежая зелень": ["укроп", "петрушка", "кинза"],
    "петрушка / укроп": ["петрушка", "укроп"],
    "помидоры": ["томаты", "помидоры"],
    "луковица": ["лук репчатый"],
    "желток": ["яйцо куриное", "яйца куриные"],
    "кабачок": ["кабачки", "кабачок"],
    "миндал": ["миндаль"],
    "овсянка": ["хлопья овсяные", "овсяные хлопья"],
    "манная крупа": ["крупа манная", "манка"],
    "лимонный сок": ["лимон", "лимоны"],
    "соль, перец": ["соль"],
    "сахар или мед": ["сахар"],
    "семена кунжута": ["кунжут"],
    "куриные крылья": ["крылья куриные", "крылышки куриные", "крыло куриное"],
    "морепродукты": ["коктейль из морепродуктов", "креветки"],
    "чили перец": ["перец чили"],
    "хлопья перца чили": ["перец чили"],
    "ванильный сахар": ["ванильный сахар", "сахар ванильный", "ванилин"],
    "ваниль": ["ванильный сахар", "ванилин"],
    "экстракт ванили": ["ванильный экстракт", "экстракт ванили"],
    "кукурузный крахмал": ["крахмал кукурузный"],
    "оливковое масло": ["масло оливковое"],
    "болгарский перец": ["перец болгарский", "перец сладкий"],
    "красный болгарский перец": ["перец болгарский красный", "перец сладкий красный", "перец болгарский"],
    "творожный сыр": ["сыр творожный"],
    "специи": ["приправа", "специи"],
    "помидор": ["томаты", "помидоры", "томат"],
    "огурец": ["огурцы", "огурец"],
    "картофель": ["картофель", "картошка"],
    "рыба": ["филе минтая", "филе трески", "лосось", "форель", "горбуша", "скумбрия"],
    "сыр": ["сыр"],
    "фарш": ["фарш"],
}

# Товар с этими словами — не продукт-ингредиент, а вкус/готовое изделие на его основе
PENALTY_STEMS = {_stemmer.stemWord(w) for w in (
    "вкус", "вкусом", "аромат", "чипсы", "сухарики", "снеки", "корм", "игрушка", "батончик",
    "мармелад", "конфеты", "жевательная", "шампунь", "крем", "мыло", "свеча",
)}
# Родовое слово в начале названия: «Сыр Моцарелла», «Крупа манная», «Соус острый» — ингредиент вторым словом
CATEGORY_HEAD_STEMS = {_stemmer.stemWord(w) for w in (
    "сыр", "крупа", "соус", "масло", "мука", "сахар", "уксус", "крахмал", "сок", "перец", "приправа",
    "смесь", "хлопья", "семена", "творог", "сливки", "специи", "паста", "пюре", "филе", "листья",
)}
ALCOHOL_STEMS = {_stemmer.stemWord(w) for w in ("вино", "пиво", "водка", "коньяк", "ром", "ликер", "виски")}


# «Курица с рисом в соусе», «Скумбрия в масле», «Курица по-пекински» — готовое блюдо, а не ингредиент
DISH_PREPOSITIONS = {"с", "со", "в", "во", "по"}


def stems(text_: str | None) -> list[str]:
    words = _WORD_RE.findall((text_ or "").lower().replace("ё", "е"))
    return [_stemmer.stemWord(w) for w in words if len(w) > 1]


def has_dish_preposition(text_: str | None) -> bool:
    return bool(DISH_PREPOSITIONS & set(_WORD_RE.findall((text_ or "").lower())))


@dataclass(slots=True)
class Candidate:
    shop: str
    product_id: str
    score: float
    unit_price: float | None  # копейки за 1 g/ml/pcs — для выбора самых выгодных при равной оценке


def score_product(phrase: list[str], product: list[str], *, is_adult: bool, alcohol_ok: bool,
                  is_dish: bool = False) -> float:
    """0..1. Ноль — не тот товар: не все слова ингредиента в названии, штрафное слово, алкоголь."""
    if not phrase or not product or not set(phrase) <= set(product):
        return 0.0
    if is_adult and not alcohol_ok:
        return 0.0
    if PENALTY_STEMS & set(product) and not PENALTY_STEMS & set(phrase):
        return 0.0
    score = 0.6
    # Товар начинается со слова ингредиента — любого: «Болгарский перец» ↔ «Перец болгарский»
    if product[0] in phrase:
        score += 0.3
    elif product[0] in CATEGORY_HEAD_STEMS and set(phrase) & set(product[1:3]):
        score += 0.3
    elif phrase[0] in product[:3]:
        score += 0.1
    # Короткое название — базовый продукт, а не «Салат из курицы с ананасом и сыром»
    if len(product) <= 5:
        score += 0.05
    if product[:len(phrase)] == phrase:
        score += 0.05
    if is_dish:
        score -= 0.15
    return min(score, 1.0)


def best_candidates(
    ingredient: str, products: list[tuple[str, str, list[str], bool, float | None, bool]]
) -> list[Candidate]:
    """products: (shop, product_id, основы названия, is_adult, цена за единицу, похоже на блюдо)."""
    phrases = [stems(p) for p in ALIASES.get(ingredient.lower(), [ingredient])]
    alcohol_ok = any(set(p) & ALCOHOL_STEMS for p in phrases)
    by_shop: dict[str, list[Candidate]] = {}
    for shop, product_id, name_stems, is_adult, unit_price, is_dish in products:
        score = max((score_product(p, name_stems, is_adult=is_adult, alcohol_ok=alcohol_ok, is_dish=is_dish)
                     for p in phrases), default=0.0)
        if score >= MIN_SCORE:
            by_shop.setdefault(shop, []).append(Candidate(shop, product_id, round(score, 3), unit_price))
    result = []
    for candidates in by_shop.values():
        candidates.sort(key=lambda c: (-c.score, c.unit_price if c.unit_price is not None else float("inf")))
        result.extend(candidates[:TOP_PER_SHOP])
    return result


async def match_all(now: datetime) -> dict[str, int]:
    """Пересчитать auto-сопоставления для всех ингредиентов и сетей."""
    async with session_scope() as session:
        ingredients = (await session.execute(text("SELECT id, name FROM ingredients"))).all()
        # Минимальная цена товара по всем точкам — для выбора выгодных при равной оценке
        min_price = select(Price.shop, Price.product_id, func.min(Price.price_minor).label("price")) \
            .group_by(Price.shop, Price.product_id).subquery()
        rows = (await session.execute(
            select(Product.shop, Product.id, Product.name, Product.is_adult, Product.net_quantity, min_price.c.price)
            .join(min_price, (min_price.c.shop == Product.shop) & (min_price.c.product_id == Product.id))
        )).all()
        existing = {
            (m.ingredient_id, m.shop, m.product_id): m
            for m in (await session.execute(select(IngredientMatch))).scalars()
        }

    products = []
    for shop, product_id, name, is_adult, net_quantity, price in rows:
        unit_price = price / float(net_quantity) if price and net_quantity else None
        products.append((shop, product_id, stems(name), bool(is_adult), unit_price, has_dish_preposition(name)))

    stats = {"ingredients": len(ingredients), "candidates": 0, "inserted": 0, "updated": 0, "removed": 0}
    async with session_scope() as session:
        for ingredient_id, name in ingredients:
            fresh = {(ingredient_id, c.shop, c.product_id): c for c in best_candidates(name, products)}
            stats["candidates"] += len(fresh)
            for key, c in fresh.items():
                old = existing.get(key)
                if old is None:
                    session.add(IngredientMatch(
                        ingredient_id=ingredient_id, shop=c.shop, product_id=c.product_id, score=c.score,
                        status="auto", created_at=now, updated_at=now,
                    ))
                    stats["inserted"] += 1
                elif old.status == "auto":
                    await session.execute(
                        IngredientMatch.__table__.update()
                        .where(IngredientMatch.ingredient_id == ingredient_id,
                               IngredientMatch.shop == c.shop, IngredientMatch.product_id == c.product_id)
                        .values(score=c.score, updated_at=now)
                    )
                    stats["updated"] += 1
            # auto, которые больше не подходят (товар переименовали, правило поменялось) — убираем
            stale = [k for k, m in existing.items() if k[0] == ingredient_id and m.status == "auto" and k not in fresh]
            for _, shop, product_id in stale:
                await session.execute(delete(IngredientMatch).where(
                    IngredientMatch.ingredient_id == ingredient_id,
                    IngredientMatch.shop == shop, IngredientMatch.product_id == product_id,
                ))
            stats["removed"] += len(stale)
    log.info("match_done", **stats)
    return stats
