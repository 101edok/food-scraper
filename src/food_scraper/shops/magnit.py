"""Магнит: BFF фронта magnit.ru/webgate. Антибота нет, хватает httpx. Разведка — recon/magnit.md."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx
import structlog
from aiolimiter import AsyncLimiter

from ..config import Settings
from ..stores import StoreRow
from .base import CategoryRow, DetailsRow, ItemRow

log = structlog.get_logger(__name__)

BASE_URL = "https://magnit.ru"
API = f"{BASE_URL}/webgate"
PAGE_SIZE = 50
# Точки с доставкой. Каталог умеем читать только у формата «Магнит у дома» (store_type 1):
# его store_type годится и как storeType, и как catalogType — это цены на полке.
# Форматы 10 и 16 тоже express, но с этими параметрами отдают пустой каталог — см. recon/magnit.md.
STORE_DELIVERY_TYPE = "express"
STORE_TYPES = {"1"}
RETRY_STATUSES = {429, 500, 502, 503, 504}


class Magnit:
    name = "magnit"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store_code: str | None = None
        self.store_type: str | None = None
        self._limiter = AsyncLimiter(max(settings.rps, 0.1), 1)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Magnit":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            proxy=self.settings.proxy_url or None,
            headers={
                "User-Agent": self.settings.user_agent
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Device-Platform": "Web",
                "X-New-Magnit": "true",
                "X-Device-ID": self.settings.device_id,
                "X-Client-Name": "magnit",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/catalog/",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        assert self._client is not None, "используй `async with Magnit(...)`"
        delay = 2.0
        for attempt in range(5):
            async with self._limiter:
                try:
                    r = await self._client.request(method, f"{API}{path}", **kw)
                except httpx.TransportError as e:
                    if attempt == 4:
                        raise
                    log.warning("magnit_transport_error", path=path, err=str(e), attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            if r.status_code in RETRY_STATUSES and attempt < 4:
                log.warning("magnit_retry", path=path, status=r.status_code, attempt=attempt)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    @property
    def _catalog_params(self) -> dict[str, str]:
        assert self.store_type is not None, "сначала use_store()"
        return {"storetype": self.store_type, "catalogtype": self.store_type}

    # ------------------------------------------------------------------ store
    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        data = await self._request(
            "GET", "/shops/v1/shop_by_point", params={"x": round(lat, 5), "y": round(lon, 5)}
        )
        rows = []
        for s in (data or {}).get("shops") or []:
            store_type = str(s.get("store_type"))
            if not s.get("active") or s.get("delivery_type") != STORE_DELIVERY_TYPE:
                continue
            if store_type not in STORE_TYPES:
                continue
            coords = s.get("coordinates") or {}
            rows.append(
                StoreRow(
                    code=str(s["xml_id"]),
                    store_type=store_type,
                    address=s.get("formatted_address"),
                    # У Магнита x — широта, y — долгота
                    lat=coords.get("x"),
                    lon=coords.get("y"),
                )
            )
        return rows

    def use_store(self, store: StoreRow) -> None:
        self.store_code = store.code
        self.store_type = store.store_type

    # ------------------------------------------------------------- categories
    async def categories(self) -> list[CategoryRow]:
        data = await self._request(
            "GET", f"/v3/categories/store/{self.store_code}", params=self._catalog_params
        )
        rows: list[CategoryRow] = []

        def walk(node: dict[str, Any], parent: str | None, level: int) -> None:
            children = node.get("children") or []
            code = node.get("code")
            rows.append(
                CategoryRow(
                    id=str(node["id"]),
                    parent_id=parent,
                    name=node.get("name") or "",
                    slug=code,
                    level=level,
                    has_children=bool(children),
                    image_url=node.get("image"),
                    url=f"{BASE_URL}/catalog/{node['id']}-{code}" if code else None,
                )
            )
            for ch in children:
                walk(ch, str(node["id"]), level + 1)

        for root in (data or {}).get("items") or []:
            walk(root, None, 0)
        return rows

    # ---------------------------------------------------------------- listing
    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        offset = 0
        while True:
            data = await self._request(
                "POST",
                "/v2/goods/search",
                json={
                    "catalogType": self.store_type,
                    "storeType": self.store_type,
                    "storeCode": self.store_code,
                    "categories": [int(category_id)],
                    "sort": {"order": "desc", "type": "popularity"},
                    "pagination": {"limit": PAGE_SIZE, "offset": offset},
                },
            )
            if not data:
                return
            for it in data.get("items") or []:
                row = parse_item(it)
                if row:
                    yield row
            page = data.get("pagination") or {}
            if not page.get("hasMore") or page.get("nextOffset") is None:
                return
            offset = int(page["nextOffset"])

    # ---------------------------------------------------------------- details
    async def details(self, item_id: str) -> DetailsRow | None:
        data = await self._request(
            "GET", f"/v2/goods/{item_id}/stores/{self.store_code}", params=self._catalog_params
        )
        return parse_details(data) if data else None


# ---------------------------------------------------------------------- parsing
def parse_item(it: dict[str, Any]) -> ItemRow | None:
    if it.get("id") is None or it.get("price") is None:
        return None
    promo = it.get("promotion") or {}
    weighted = it.get("weighted") or {}
    qty = it.get("quantity")
    seo = it.get("seoCode")
    return ItemRow(
        id=str(it["id"]),
        name=it.get("name") or "",
        price_minor=int(it["price"]),
        old_price_minor=int(promo["oldPrice"]) if promo.get("isPromotion") and promo.get("oldPrice") else None,
        in_stock=(qty is None or qty > 0) and not it.get("isMissing"),
        url=f"{BASE_URL}/product/{it['id']}-{seo}" if seo else None,
        image_urls=[g["url"] for g in it.get("gallery") or [] if g.get("type") == "IMAGE" and g.get("url")],
        is_weighted=bool(weighted.get("isWeighted")),
        unit_price_minor=_int(weighted.get("unitPrice")),
        unit_label=weighted.get("unitLabel"),
        is_adult=bool(it.get("isForAdults")),
    )


_NUTRITION = {"Ккал": "kcal", "Белки": "protein", "Жиры": "fat", "Углеводы": "carbs"}


def parse_details(d: dict[str, Any]) -> DetailsRow:
    row = DetailsRow(id=str(d["id"]))
    cats = d.get("categories") or []
    row.category_id = str(cats[0]) if cats else None
    row.image_urls = [g["url"] for g in d.get("gallery") or [] if g.get("type") == "IMAGE" and g.get("url")]
    for group in d.get("details") or []:
        gname = group.get("name") or ""
        gtype = group.get("type")
        params = group.get("parameters") or []
        if gtype == "nutritionFactsType":
            for p in params:
                field = _NUTRITION.get(p.get("name"))
                if field:
                    setattr(row, field, _float(p.get("value")))
        elif gtype == "stringType":
            value = group.get("value")
            if gname == "Состав":
                row.composition = value
            elif gname == "Описание":
                row.description = value
            elif value:
                row.attributes[gname[:255]] = str(value)[:1024]
        for p in params:
            name, value = p.get("name"), p.get("value")
            if not name or value in (None, "") or gtype == "nutritionFactsType":
                continue
            if name == "Бренд":
                row.brand = value
            elif name == "Производитель":
                row.manufacturer = value
            elif name == "Вес, кг":
                kg = _float(value)
                row.weight_g = round(kg * 1000) if kg is not None else None
            row.attributes[name[:255]] = str(value)[:1024]
    return row


def _float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
