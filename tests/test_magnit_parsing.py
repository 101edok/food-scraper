"""Разбор ответов magnit.ru/webgate — без сети. Фикстуры урезаны из реальных ответов (recon/magnit.md)."""
from __future__ import annotations

from food_scraper.shops.magnit import parse_details, parse_item

LISTING_ITEM = {
    "id": "8000142739",
    "name": "Коктейль молочный без сахара ореховый раф Neo High Protein 260г",
    "price": 15999,
    "promotion": {"discountPercent": 20, "isPromotion": True, "oldPrice": 19999},
    "quantity": 5,
    "seoCode": "neo_high_protein_kokteyl_mol_orekh_raf_30g_belka_b_sakhar260g",
    "gallery": [
        {"type": "IMAGE", "url": "https://images-foodtech.magnit.ru/a.jpeg@webp"},
        {"type": "VIDEO", "url": "https://images-foodtech.magnit.ru/v.mp4"},
    ],
    "weighted": {"isWeighted": False, "unitPrice": None, "unitLabel": None},
    "isForAdults": False,
}

DETAILS = {
    "id": "8000142739",
    "categories": [115428],
    "gallery": [{"type": "IMAGE", "url": "https://images-foodtech.magnit.ru/a.jpeg@webp"}],
    "details": [
        {
            "name": "Пищевая ценность в 100 граммах",
            "type": "nutritionFactsType",
            "parameters": [
                {"name": "Ккал", "value": "69"},
                {"name": "Белки", "value": "11.6"},
                {"name": "Жиры", "value": "0.2"},
                {"name": "Углеводы", "value": "5.1"},
            ],
        },
        {"name": "Состав", "type": "stringType", "parameters": [], "value": "Молоко обезжиренное"},
        {
            "name": "Характеристики",
            "type": "tableType",
            "parameters": [
                {"name": "Бренд", "value": "Neo"},
                {"name": "Производитель", "value": "Вимм-Билль-Данн"},
                {"name": "Вес, кг", "value": "0.26"},
                {"name": "Артикул", "value": "8000142739"},
            ],
        },
        {
            "name": "Условия хранения",
            "type": "tableType",
            "parameters": [{"name": "Срок годности, дней", "value": "90"}],
        },
    ],
}


def test_parse_item_price_already_in_kopecks():
    row = parse_item(LISTING_ITEM)
    assert row is not None
    assert row.id == "8000142739"
    assert row.price_minor == 15999
    assert row.old_price_minor == 19999
    assert row.in_stock is True
    assert row.url == (
        "https://magnit.ru/product/8000142739-"
        "neo_high_protein_kokteyl_mol_orekh_raf_30g_belka_b_sakhar260g"
    )
    assert row.image_urls == ["https://images-foodtech.magnit.ru/a.jpeg@webp"]


def test_parse_item_without_promo_and_out_of_stock():
    row = parse_item({**LISTING_ITEM, "promotion": {"isPromotion": False, "oldPrice": 0}, "quantity": 0})
    assert row is not None
    assert row.old_price_minor is None
    assert row.in_stock is False


def test_parse_item_skips_items_without_price():
    assert parse_item({**LISTING_ITEM, "price": None}) is None


def test_parse_details_nutrition_brand_weight():
    d = parse_details(DETAILS)
    assert d.category_id is None  # id карточки не из дерева категорий — берём категорию из листинга
    assert (d.kcal, d.protein, d.fat, d.carbs) == (69.0, 11.6, 0.2, 5.1)
    assert d.composition == "Молоко обезжиренное"
    assert d.brand == "Neo"
    assert d.manufacturer == "Вимм-Билль-Данн"
    assert d.weight_g == 260
    assert d.attributes["Срок годности, дней"] == "90"
    assert "Ккал" not in d.attributes
