"""Парсинг ng-state категории: проверяем на реальном фрагменте HTML."""
from __future__ import annotations

import json

from lenta_scraper.api import _parse_ng_state, _category_url

SAMPLE_HTML = (
    '<html><head>'
    '<script id="ng-state" type="application/json">'
    + json.dumps(
        {
            "/catalog/categories": {
                "categories": [
                    {
                        "id": 3009,
                        "parentId": 0,
                        "level": 1,
                        "slug": "molochnye-produkty",
                        "name": "Молочные продукты",
                        "hasChildren": True,
                        "isAdult": False,
                        "imageUrl": "https://cdn/img.jpg",
                    },
                    {
                        "id": 30091,
                        "parentId": 3009,
                        "level": 2,
                        "slug": "moloko",
                        "name": "Молоко",
                        "hasChildren": False,
                        "isAdult": False,
                        "imageUrl": None,
                    },
                ]
            }
        }
    )
    + "</script></head><body></body></html>"
)


def test_parse_ng_state_returns_dict():
    state = _parse_ng_state(SAMPLE_HTML)
    assert state is not None
    assert "/catalog/categories" in state
    cats = state["/catalog/categories"]["categories"]
    assert cats[0]["id"] == 3009


def test_category_url_uses_slug_and_id(monkeypatch):
    # _category_url зависит от Settings (base_url), убедимся, что формат стабилен
    url = _category_url("moloko", 30091)
    assert url is not None
    assert url.endswith("/catalog/moloko-30091/")
