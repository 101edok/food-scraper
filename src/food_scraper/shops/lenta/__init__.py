"""Лента: обёртка над реверсом lenta.com под общий контракт магазина.

Лента за Qrator и режет IP дата-центров (russia-03, finland-01 — 403),
так что без российского резидентного прокси (PROXY_URL) не работает.
"""
from __future__ import annotations

from typing import AsyncIterator

from ...config import Settings
from ...stores import StoreRow
from ..base import CategoryRow, DetailsRow, ItemRow
from . import api
from .http_client import LentaHttpClient


class Lenta:
    name = "lenta"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store_code: str | None = None
        self._client: LentaHttpClient | None = None

    async def __aenter__(self) -> "Lenta":
        self._client = LentaHttpClient(self.settings)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        # Магазин Ленты задаётся поддоменом и режимом доставки из .env (см. recon) —
        # одна условная точка на город, без координат обхода.
        return [
            StoreRow(
                code=f"{self.settings.city_subdomain}:{self.settings.delivery_mode}",
                store_type=self.settings.delivery_mode,
                lat=lat,
                lon=lon,
            )
        ]

    def use_store(self, store: StoreRow) -> None:
        self.store_code = store.code

    async def categories(self) -> list[CategoryRow]:
        rows = await api.fetch_categories(self._client)
        return [
            CategoryRow(
                id=str(r.id),
                parent_id=str(r.parent_id) if r.parent_id else None,
                name=r.name,
                slug=r.slug,
                level=r.level,
                has_children=r.has_children,
                is_adult=r.is_adult,
                image_url=r.image_url,
                url=r.url,
            )
            for r in rows
        ]

    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        async for p in api.iter_category_items(self._client, int(category_id)):
            yield ItemRow(
                id=str(p.id),
                name=p.name,
                price_minor=p.price_minor,
                old_price_minor=p.old_price_minor,
                in_stock=p.in_stock,
                url=p.url or None,
                image_urls=p.image_urls,
            )

    async def details(self, item_id: str) -> DetailsRow | None:
        payload = await api.fetch_product_details(self._client, int(item_id))
        if not payload:
            return None
        parsed = api.parse_details(payload)
        p = parsed["product"]
        return DetailsRow(
            id=item_id,
            category_id=str(p["category_id"]) if p.get("category_id") else None,
            brand=p.get("brand"),
            description=p.get("description"),
            composition=p.get("composition"),
            image_urls=parsed["images"],
            attributes={a["name"]: a["value"] for a in parsed["attributes"]},
        )
