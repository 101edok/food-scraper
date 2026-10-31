"""Разбор ответов api.fix-price.com — без сети. Фикстуры урезаны из реальных ответов (recon/fixprice.md)."""
from __future__ import annotations

import json
from pathlib import Path

from food_scraper.shops.fixprice import parse_details, parse_item, parse_store

FIXTURES = Path(__file__).parent / "fixtures" / "fixprice"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_item_rubles_to_kopecks_and_leaf_category():
    item = _load("sample_listing.json")["response"][0]
    row = parse_item(item, "2005")
    assert (row.id, row.price_minor, row.old_price_minor) == ("1011012", 6900, None)
    assert row.category_id == "2005"
    assert row.url == "https://fix-price.com/catalog/" + item["url"]
    assert row.image_urls and row.image_urls[0].startswith("https://img.fix-price.com/")
    # Обход корня («прочее») категорию не ставит — её даст лист
    assert parse_item(item, None).category_id is None


def test_parse_details_nutrition_weight_from_title():
    d = parse_details(_load("sample_card.json")["response"])
    assert (d.kcal, d.protein, d.fat, d.carbs) == (118.0, 2.8, 10.0, 4.3)
    assert d.brand == "Домик в деревне"
    assert d.composition == "сливки нормализованные"
    assert d.weight_g == 200  # «…, 10%, 200 г»
    assert d.attributes["Страна производства"] == "Россия"
    assert d.attributes["Штрихкод"] == "4607025396833"


def test_parse_store_keeps_city_id_for_prices():
    store = parse_store(_load("sample_stores.json")["store_list"]["response"][0])
    assert (store.code, store.store_type) == ("8242", "city:40")
    assert (store.lat, store.lon) == (55.854813, 49.074094)
