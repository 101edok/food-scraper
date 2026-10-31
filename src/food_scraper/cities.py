"""Города, для которых собираем цены. Ключ — slug, он же пишется в shop_prices.city.

Координаты — центр города: по ним магазин подбирает ближайшую точку,
чьи цены считаем ценами города.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class City:
    slug: str
    name: str
    lat: float
    lon: float


CITIES: dict[str, City] = {
    c.slug: c
    for c in [
        City("moscow", "Москва", 55.7558, 37.6173),
        City("spb", "Санкт-Петербург", 59.9343, 30.3351),
        City("novosibirsk", "Новосибирск", 55.0302, 82.9204),
        City("ekaterinburg", "Екатеринбург", 56.8389, 60.6057),
        City("kazan", "Казань", 55.7963, 49.1088),
        City("nizhny-novgorod", "Нижний Новгород", 56.3269, 44.0059),
        City("krasnodar", "Краснодар", 45.0355, 38.9753),
        City("samara", "Самара", 53.1959, 50.1002),
        City("rostov", "Ростов-на-Дону", 47.2357, 39.7015),
        City("chelyabinsk", "Челябинск", 55.1644, 61.4368),
    ]
}


def get_city(slug: str) -> City:
    try:
        return CITIES[slug]
    except KeyError:
        raise ValueError(f"Неизвестный город {slug!r}. Есть: {', '.join(CITIES)}") from None
