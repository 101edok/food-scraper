"""Количества: фасовка товара из названия («6х45г», «0,5 л», «10шт») и количество ингредиента в рецепте
(«2 столовые ложки», «1/2 чайной ложки», «2-3 зубчика»). Единицы приводим к g / ml / pcs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GRAM, ML, PCS = "g", "ml", "pcs"

_NUM = r"\d+(?:[.,]\d+)?"
# «6х45г», «4 x 100 г», «2*0,5л», «900 мл», «10шт». Число перед «%» — жирность/крепость, не фасовка.
_PACK_RE = re.compile(
    rf"(?<![\d.,])(?:(?P<count>\d+)\s*[xх×*]\s*)?(?P<amount>{_NUM})\s*"
    rf"(?P<unit>кг|гр|г|мл|л|шт)(?![а-яa-z])",
    re.IGNORECASE,
)
_PACK_UNITS = {"кг": (GRAM, 1000), "гр": (GRAM, 1), "г": (GRAM, 1), "л": (ML, 1000), "мл": (ML, 1), "шт": (PCS, 1)}


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: float
    unit: str  # g | ml | pcs


def parse_pack(name: str | None) -> Quantity | None:
    """Фасовка из названия товара. Масса/объём важнее штук: «Яйцо 10шт 550г» → 550 г."""
    if not name:
        return None
    found: list[Quantity] = []
    for m in _PACK_RE.finditer(name):
        unit, factor = _PACK_UNITS[m.group("unit").lower()]
        amount = _num(m.group("amount")) * factor
        if m.group("count"):
            amount *= int(m.group("count"))
        if amount > 0:
            found.append(Quantity(round(amount, 3), unit))
    if not found:
        return None
    # Последняя масса/объём: «25 пакетиков, 37,5 г» → 37,5 г; иначе штуки
    for q in reversed(found):
        if q.unit != PCS:
            return q
    return found[-1]


# ----------------------------------------------------------------------------- рецепты

# Мерки в граммах (для жидкостей — мл; плотность считаем 1, точности хватает для оценки стоимости)
_MEASURES: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"^кг|^килограмм"), GRAM, 1000),
    (re.compile(r"^г(р|рамм)?|^грамм"), GRAM, 1),
    (re.compile(r"^мл|^миллилитр"), ML, 1),
    (re.compile(r"^л(\b|итр)"), ML, 1000),
    (re.compile(r"^стол\w*\s*лож|^ст\.?\s*л"), GRAM, 15),
    (re.compile(r"^чайн\w*\s*лож|^ч\.?\s*л"), GRAM, 5),
    (re.compile(r"^стакан"), ML, 200),
    (re.compile(r"^шт|^штук"), PCS, 1),
    (re.compile(r"^зубч"), PCS, 1),
    (re.compile(r"^долек|^долька|^дольк"), PCS, 1),
    (re.compile(r"^ломтик"), PCS, 1),
    (re.compile(r"^пуч"), PCS, 1),
    (re.compile(r"^банк"), PCS, 1),
    (re.compile(r"^пачк|^упаковк"), PCS, 1),
    (re.compile(r"^щепот"), GRAM, 1),
]
_AMOUNT_RE = re.compile(
    rf"^\s*(?:(?P<frac_a>\d+)\s*/\s*(?P<frac_b>\d+)|(?P<lo>{_NUM})\s*(?:-|–|—)\s*(?P<hi>{_NUM})|(?P<num>{_NUM}))?\s*(?P<rest>.*)$",
)
_WORD_AMOUNTS = {"пол": 0.5, "половина": 0.5, "половинка": 0.5, "четверть": 0.25}


def parse_recipe_quantity(text: str | None) -> Quantity | None:
    """Количество ингредиента. «по вкусу», «для жарки», «немного» — None: в стоимость не входят."""
    if not text:
        return None
    s = text.strip().lower().replace("ё", "е")
    m = _AMOUNT_RE.match(s)
    if not m:
        return None
    rest = m.group("rest").strip()
    if m.group("frac_a"):
        amount = int(m.group("frac_a")) / int(m.group("frac_b"))
    elif m.group("lo"):
        amount = (_num(m.group("lo")) + _num(m.group("hi"))) / 2
    elif m.group("num"):
        amount = _num(m.group("num"))
    else:
        # «пучок», «щепотка», «пол стакана»
        first, _, tail = rest.partition(" ")
        if first in _WORD_AMOUNTS:
            amount, rest = _WORD_AMOUNTS[first], tail
        else:
            amount = 1.0
    for pattern, unit, factor in _MEASURES:
        if pattern.search(rest):
            return Quantity(round(amount * factor, 3), unit)
    # Число без единицы — штуки («2», «2-3»), если это именно число
    if (m.group("num") or m.group("lo") or m.group("frac_a")) and not rest:
        return Quantity(round(amount, 3), PCS)
    return None


def _num(s: str) -> float:
    return float(s.replace(",", "."))
