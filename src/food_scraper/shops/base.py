"""Контракт адаптера магазина. Пайплайн (pipeline/run.py) общий, магазин отвечает только за сеть и парсинг."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from ..stores import StoreRow


@dataclass(slots=True)
class CategoryRow:
    id: str
    parent_id: str | None
    name: str
    slug: str | None = None
    level: int | None = None
    has_children: bool = False
    is_adult: bool = False
    image_url: str | None = None
    url: str | None = None
    # False — служебная «категория» обхода (поисковый запрос ВкусВилла): обходим, но в справочник не пишем
    persist: bool = True


@dataclass(slots=True)
class ItemRow:
    """Товар из листинга: цена уже в нём, она привязана к магазину города."""

    id: str
    name: str
    price_minor: int | None
    old_price_minor: int | None = None
    in_stock: bool = True
    url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    is_weighted: bool = False
    unit_price_minor: int | None = None
    unit_label: str | None = None
    is_adult: bool = False
    # Лист дерева, если магазин отдаёт его прямо в листинге (Дикси); иначе категорию даёт карточка
    category_id: str | None = None
    category_name: str | None = None
    # Раздел над листом, если магазин отдаёт и его (ВкусВилл)
    category_parent_id: str | None = None
    category_parent_name: str | None = None


@dataclass(slots=True)
class DetailsRow:
    """Карточка: то, что не меняется от города к городу."""

    id: str
    category_id: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    description: str | None = None
    composition: str | None = None
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carbs: float | None = None
    weight_g: int | None = None
    image_urls: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)


class Shop(Protocol):
    name: str
    store_code: str | None

    async def __aenter__(self) -> "Shop": ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        """Точки сети рядом с координатой — только форматы, каталог которых умеем читать."""

    # Необязательно: если сеть отдаёт все точки одним запросом (Дикси), пайплайн берёт их отсюда,
    # оставляет попавшие в радиус города и не обходит город сеткой.
    # async def list_stores(self) -> list[StoreRow]: ...

    def use_store(self, store: StoreRow) -> None:
        """Дальнейшие categories/iter_items/details идут в каталог этой точки."""

    async def categories(self) -> list[CategoryRow]: ...

    def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]: ...

    async def details(self, item_id: str) -> DetailsRow | None: ...


def get_shop(name: str) -> Shop:
    from ..config import get_settings

    s = get_settings()
    if name == "magnit":
        from .magnit import Magnit

        return Magnit(s)
    if name == "lenta":
        from .lenta import Lenta

        return Lenta(s)
    if name == "dixy":
        from .dixy import Dixy

        return Dixy(s)
    if name == "fixprice":
        from .fixprice import FixPrice

        return FixPrice(s)
    if name == "vkusvill":
        from .vkusvill import VkusVill

        return VkusVill(s)
    raise ValueError(f"Неизвестный магазин {name!r}. Есть: magnit, dixy, fixprice, vkusvill, lenta")
