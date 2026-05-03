"""Юнит-тесты на чистый парсинг — без сети, без БД."""
from __future__ import annotations

from lenta_scraper.api import _normalize_listing, _to_minor, parse_details


def test_to_minor_handles_floats_and_none():
    assert _to_minor(199.99) == 19999
    assert _to_minor("250") == 25000
    assert _to_minor(None) is None
    assert _to_minor("") is None


def test_normalize_listing_minimal():
    payload = {
        "total": 2,
        "items": [
            {
                "id": 100,
                "title": "Молоко",
                "webUrl": "/product/moloko-100/",
                "prices": {"price": 89.5, "priceRegular": 99},
                "inStock": True,
                "images": ["https://cdn/m1.jpg", {"url": "https://cdn/m2.jpg"}],
            },
            {
                "id": 101,
                "name": "Сыр",
                "url": "/product/syr-101/",
                "price": 350,
                "inStock": False,
            },
        ],
    }
    total, items = _normalize_listing(payload)
    assert total == 2
    assert len(items) == 2
    p1, p2 = items
    assert p1.id == 100 and p1.price_minor == 8950 and p1.old_price_minor == 9900
    assert p1.image_urls == ["https://cdn/m1.jpg", "https://cdn/m2.jpg"]
    assert p2.id == 101 and p2.in_stock is False and p2.price_minor == 35000


def test_parse_details_collects_attrs():
    payload = {
        "id": 555,
        "title": "Кефир",
        "categoryId": 3009,
        "brand": "Простоквашино",
        "description": "Свежий кефир",
        "composition": "Молоко, закваска",
        "prices": {"price": 75, "priceRegular": 80},
        "inStock": True,
        "images": [{"url": "https://cdn/k.jpg"}],
        "characteristics": [
            {"name": "Жирность", "value": "2.5%"},
            {"title": "Объём", "value": "1 л"},
            {"name": "пусто", "value": None},
        ],
        "webUrl": "/product/kefir-555/",
    }
    out = parse_details(payload)
    assert out["product"]["id"] == 555
    assert out["product"]["category_id"] == 3009
    assert out["product"]["brand"] == "Простоквашино"
    assert out["product"]["price_minor"] == 7500
    assert out["images"] == ["https://cdn/k.jpg"]
    names = {a["name"] for a in out["attributes"]}
    assert "Жирность" in names and "Объём" in names and "пусто" not in names
