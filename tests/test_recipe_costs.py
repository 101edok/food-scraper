"""Оценка стоимости рецепта — чистая функция, без БД."""
from __future__ import annotations

from food_scraper.recipe_costs import IngredientPrice, estimate_recipe

# Копейки за единицу: мука 60 ₽/кг, молоко 100 ₽/л, яйца 10 шт за 110 ₽, лук 50 ₽/кг, чеснок 300 ₽/кг
PRICES = {
    1: IngredientPrice(per_mass=6.0, product_id="m", product_name="Мука 1 кг"),
    2: IngredientPrice(per_mass=10.0, product_id="mk", product_name="Молоко 1 л"),
    3: IngredientPrice(per_piece=1100.0, product_id="e", product_name="Яйцо 10 шт"),
    4: IngredientPrice(per_mass=5.0, product_id="l", product_name="Лук 1 кг"),
    5: IngredientPrice(per_mass=30.0, product_id="c", product_name="Чеснок 100 г"),
}


def test_estimate_recipe_units():
    est = estimate_recipe([
        (1, "Мука", "200 г"),            # 200 × 6 = 1200
        (2, "Молоко", "1 стакан"),        # 200 мл × 10 = 2000
        (3, "Яйцо", "2 штуки"),           # 2 × 1100 = 2200
        (4, "Лук", "1 штука"),            # 100 г × 5 = 500
        (5, "Чеснок", "2 зубчика"),       # 10 г × 30 = 300
        (6, "Соль", "по вкусу"),          # не входит
        (7, "Шафран", "1 г"),             # нет товаров
    ], PRICES)
    assert est.cost_minor == 1200 + 2000 + 2200 + 500 + 300
    assert (est.total, est.priced) == (6, 5)
    reasons = {l.ingredient: l.reason for l in est.lines if not l.priced}
    assert "по вкусу" in reasons["Соль"]
    assert reasons["Шафран"] == "нет сопоставленных товаров с ценой"


def test_grams_of_piece_product_use_piece_weight():
    # 110 г яиц при цене 110 ₽ за 10 шт по 55 г: 2 яйца = 22 ₽
    est = estimate_recipe([(3, "Яйцо", "110 г")], PRICES)
    assert est.cost_minor == 2200


def test_price_unit_follows_product_not_recipe():
    # «1 штука» лука, лук продаётся на вес: цена в разбивке — за граммы, а не за штуку
    line = estimate_recipe([(4, "Лук", "1 штука")], PRICES).lines[0]
    assert (line.unit, line.price_unit) == ("pcs", "g")
