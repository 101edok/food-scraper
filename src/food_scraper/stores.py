"""Точки сети в городе: обход сеткой и выбор подмножества для ежедневного прогона."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .cities import City

KM_PER_DEG_LAT = 111.0


@dataclass(slots=True)
class StoreRow:
    code: str
    store_type: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None


def grid_points(city: City, step_km: float = 2.0) -> list[tuple[float, float]]:
    """Точки квадратной сетки внутри круга радиуса city.radius_km, от центра наружу.

    Поиск магазина по точке у сетей отдаёт ближайшие точки, так что шага ~2 км хватает,
    чтобы не пропустить магазины между узлами.
    """
    km_per_deg_lon = KM_PER_DEG_LAT * math.cos(math.radians(city.lat))
    n = int(city.radius_km // step_km)
    points = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            dy, dx = i * step_km, j * step_km
            if dx * dx + dy * dy > city.radius_km ** 2:
                continue
            points.append((dx * dx + dy * dy, city.lat + dy / KM_PER_DEG_LAT, city.lon + dx / km_per_deg_lon))
    return [(round(lat, 5), round(lon, 5)) for _, lat, lon in sorted(points)]


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def spread_sample(stores: Sequence[StoreRow], n: int, center: tuple[float, float]) -> list[StoreRow]:
    """n точек, разнесённых по городу: первая — ближайшая к центру, дальше каждый раз самая далёкая
    от уже выбранных (farthest-point sampling). Детерминировано: при том же справочнике —
    те же магазины каждый день, история цен не прыгает между точками.
    """
    located = sorted((s for s in stores if s.lat is not None and s.lon is not None), key=lambda s: s.code)
    if n >= len(located):
        return list(located)
    chosen = [min(located, key=lambda s: (distance_km((s.lat, s.lon), center), s.code))]
    nearest = {s.code: distance_km((s.lat, s.lon), (chosen[0].lat, chosen[0].lon)) for s in located}
    while len(chosen) < n:
        best = max((s for s in located if s.code not in {c.code for c in chosen}),
                   key=lambda s: (nearest[s.code], s.code))
        chosen.append(best)
        for s in located:
            nearest[s.code] = min(nearest[s.code], distance_km((s.lat, s.lon), (best.lat, best.lon)))
    return chosen


def dedupe(rows: Iterable[StoreRow]) -> list[StoreRow]:
    return list({r.code: r for r in rows}.values())
