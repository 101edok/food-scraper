"""Разбор фасовки и количеств — на реальных строках из каталогов и рецептов edalle."""
from __future__ import annotations

import pytest

from food_scraper.quantity import GRAM, ML, PCS, Quantity, parse_pack, parse_recipe_quantity


@pytest.mark.parametrize("name, expected", [
    ("Молоко 2,5% в бутылке, 900 мл", Quantity(900, ML)),
    ("Кефир Экомилк 2,5% 900мл", Quantity(900, ML)),
    ("Джин, Green Baboon, 40%, 0,5 л", Quantity(500, ML)),
    ("Напиток безалкогольный Очаково Cool Cola 1,5л", Quantity(1500, ML)),
    ("Творог Растишка Клубника-банан-пломбир 3,5% 6х45г", Quantity(270, GRAM)),
    ("Молоко сгущенное Рогачевъ с сахаром 8,5% 380г", Quantity(380, GRAM)),
    ("Окунь морской Мореслав, тушка замороженный 800г", Quantity(800, GRAM)),
    ("Шницель из свинины Агроэко 400г", Quantity(400, GRAM)),
    ("Сыр Скит Скаморцца копченый 40%, 150 г БЗМЖ", Quantity(150, GRAM)),
    ("Яйцо куриное Yaratelle С0 10шт", Quantity(10, PCS)),
    ("Чай черный, 25 пакетиков, 37,5 г", Quantity(37.5, GRAM)),
    ("Картофель 2кг", Quantity(2000, GRAM)),
    ("Пиво светлое, 4%, 1,15 л", Quantity(1150, ML)),
])
def test_parse_pack(name, expected):
    assert parse_pack(name) == expected


@pytest.mark.parametrize("name", [
    "Чипсы картофельные с солью",
    "Гольфы женские Esli Allegra 20 черные р.23-26",
    "Мороженое пломбир 12%",
    "",
    None,
])
def test_parse_pack_none(name):
    assert parse_pack(name) is None


@pytest.mark.parametrize("text, expected", [
    ("100 г", Quantity(100, GRAM)),
    ("100 граммов", Quantity(100, GRAM)),
    ("200 грамм", Quantity(200, GRAM)),
    ("1 килограмм", Quantity(1000, GRAM)),
    ("1 кг", Quantity(1000, GRAM)),
    ("100 мл", Quantity(100, ML)),
    ("100 миллилитров", Quantity(100, ML)),
    ("2 столовые ложки", Quantity(30, GRAM)),
    ("1 чайная ложка", Quantity(5, GRAM)),
    ("1/2 чайной ложки", Quantity(2.5, GRAM)),
    ("0,5 чайной ложки", Quantity(2.5, GRAM)),
    ("1 стакан", Quantity(200, ML)),
    ("1 штука", Quantity(1, PCS)),
    ("2-3 штуки", Quantity(2.5, PCS)),
    ("2 зубчика", Quantity(2, PCS)),
    ("1/2 штуки", Quantity(0.5, PCS)),
    ("пучок", Quantity(1, PCS)),
    ("щепотка", Quantity(1, GRAM)),
    ("4 ломтика", Quantity(4, PCS)),
    ("2", Quantity(2, PCS)),
])
def test_parse_recipe_quantity(text, expected):
    assert parse_recipe_quantity(text) == expected


@pytest.mark.parametrize("text", ["по вкусу", "для жарки", "по желанию", "немного", "для украшения", "", None])
def test_parse_recipe_quantity_none(text):
    assert parse_recipe_quantity(text) is None
