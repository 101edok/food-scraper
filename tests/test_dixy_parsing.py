"""Разбор ответов dixy.ru — без сети. Фикстуры урезаны из реальных ответов (recon/dixy.md)."""
from __future__ import annotations

import json
from pathlib import Path

from food_scraper.shops.dixy import parse_card, parse_details, parse_store

FIXTURES = Path(__file__).parent / "fixtures" / "dixy"


def test_parse_card_weighted_price_per_kg_in_kopecks():
    cards = json.loads((FIXTURES / "sample_listing.json").read_text(encoding="utf-8"))[0]["cards"]
    bananas = parse_card(cards[0])
    assert bananas is not None
    assert (bananas.id, bananas.price_minor, bananas.old_price_minor) == ("10E0074360", 12499, None)
    assert (bananas.is_weighted, bananas.unit_price_minor, bananas.unit_label) == (True, 12499, "кг")
    assert (bananas.category_id, bananas.category_name) == ("39", "Фрукты")
    assert bananas.url == "https://dixy.ru/product/banany-spelye-10e0074360/"
    assert bananas.image_urls[0].startswith("https://dixy.ru/upload/iblock/")

    # Скидка: oldPriceSimple отличается от priceSimple
    mandarins = parse_card(cards[1])
    assert (mandarins.price_minor, mandarins.old_price_minor) == (14999, 24999)


def test_parse_card_single_preview_is_object():
    # У товаров с одной картинкой previews — объект, а не список
    card = {"xml_id": "2000205870", "title": "Огурец", "priceSimple": "49.98", "oldPriceSimple": "49.98",
            "amount": 36, "previews": {"src": "/upload/iblock/568/x.webp"}, "url": "/product/ogurets-2000205870/"}
    row = parse_card(card)
    assert (row.price_minor, row.old_price_minor, row.image_urls) == (4998, None, ["https://dixy.ru/upload/iblock/568/x.webp"])


def test_parse_card_without_price_is_skipped():
    assert parse_card({"xml_id": "1", "title": "x", "priceSimple": None}) is None


def test_parse_details_nutrition_brand_weight():
    d = parse_details("2000216292", (FIXTURES / "sample_details.html").read_text(encoding="utf-8"))
    assert (d.kcal, d.protein, d.fat, d.carbs) == (102.0, 6.0, 3.5, 11.7)
    assert d.brand == "Растишка"
    assert d.weight_g == 270
    assert d.composition.startswith("Творожок клубника-банан")
    assert d.description.startswith("Творог Растишка")
    assert d.attributes["Срок хранения"] == "39 дней"


def test_parse_store_coordinates_are_lat_lon_strings():
    features = json.loads((FIXTURES / "sample_stores.json").read_text(encoding="utf-8"))["items"]
    store = parse_store(features[1])
    assert (store.code, store.lat, store.lon) == ("1353", 55.737758, 37.631287)
    assert store.address == "115184, Москва г, Новокузнецкая ул, д.13, 1"
