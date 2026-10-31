"""Разбор ответов MCP-сервера ВкусВилла — без сети. Фикстуры урезаны из реальных ответов."""
from __future__ import annotations

import json
from pathlib import Path

from food_scraper.shops.vkusvill import _parse_rpc, parse_details, parse_item, parse_store

FIXTURES = Path(__file__).parent / "fixtures" / "vkusvill"


def _data(name):
    d = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return d.get("data") or d


def test_parse_item_price_category_and_parent():
    row = parse_item(_data("search.json")["items"][0])
    assert (row.id, row.price_minor, row.old_price_minor) == ("36296", 10000, None)
    assert row.name == "Молоко 2,5% в бутылке, 900 мл"  # &nbsp; раскрыт
    assert (row.category_id, row.category_name) == ("50390", "Молоко, сливки, сгущёнка")
    assert (row.category_parent_id, row.category_parent_name) == ("50388", "Молочные продукты, яйцо")
    assert row.image_urls[0].startswith("https://img.vkusvill.ru/")


def test_parse_item_discount_and_weighted():
    item = {"id": 1, "name": "Яблоки", "price": {"current": 129.9, "old": 159.9}, "unit": "кг", "category": []}
    row = parse_item(item)
    assert (row.price_minor, row.old_price_minor) == (12990, 15990)
    assert (row.is_weighted, row.unit_price_minor, row.unit_label) == (True, 12990, "кг")


def test_parse_details_nutrition_from_text():
    d = parse_details(_data("details.json"))
    assert (d.kcal, d.protein, d.fat, d.carbs) == (53.3, 3.0, 2.5, 4.7)
    assert (d.brand, d.weight_g, d.manufacturer) == ("ВкусВилл", 900, 'ООО "КОСМОЛ"')
    assert d.composition.startswith('ООО "КОСМОЛ":молоко цельное')
    assert "<br>" not in d.composition


def test_parse_store_and_sse_envelope():
    store = parse_store(_data("shops.json")["items"][0])
    assert (store.code, store.address) == ("2898", "г. Московский, мкр. 3-й, д. 9")
    assert _parse_rpc('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n') == \
        {"jsonrpc": "2.0", "id": 1, "result": {}}
