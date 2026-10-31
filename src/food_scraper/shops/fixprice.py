"""Fix Price: API фронта api.fix-price.com/buyer/v1. Разведка — recon/fixprice.md.

Цена зависит только от города (ценовой зоны), не от магазина — поэтому city_wide_prices:
пайплайн берёт одну точку на город, а справочник хранит все магазины города.
Собираем только раздел «Продукты и напитки»: остальное (химия, дом, игрушки) для рецептов не нужно.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator

import httpx
import structlog
from aiolimiter import AsyncLimiter

from ..config import Settings
from ..stores import StoreRow, distance_km
from .base import CategoryRow, DetailsRow, ItemRow

log = structlog.get_logger(__name__)

API = "https://api.fix-price.com/buyer/v1"
SITE = "https://fix-price.com/catalog"
FOOD_ROOT_ID = 2004  # «Продукты и напитки»
# Около 75 товаров корня не лежат ни в одном листе — добираем их отдельным обходом корня
REST_CATEGORY_SUFFIX = ":rest"
PAGE_SIZE = 99  # при 100 и больше сервер молча отдаёт 28
MAX_RPS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FixPrice:
    name = "fixprice"
    city_wide_prices = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store_code: str | None = None
        self._city_id: int | None = None
        self._limiter = AsyncLimiter(min(settings.rps, MAX_RPS), 1)
        self._client: httpx.AsyncClient | None = None
        self._stores: list[dict[str, Any]] | None = None
        self._category_urls: dict[str, str] = {}

    async def __aenter__(self) -> "FixPrice":
        self._client = httpx.AsyncClient(
            # /store — 3,7 МБ, большие ответы иногда обрываются: таймаут с запасом
            timeout=httpx.Timeout(60.0, connect=10.0),
            proxy=self.settings.proxy_url or None,
            headers={
                "User-Agent": self.settings.user_agent
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                "Accept": "application/json",
                "x-language": "ru",
                "Origin": "https://fix-price.com",
                "Referer": "https://fix-price.com/",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        assert self._client is not None, "используй `async with FixPrice(...)`"
        headers = kw.pop("headers", {})
        if self._city_id is not None:
            headers.setdefault("x-city", str(self._city_id))
        delay = 2.0
        for attempt in range(5):
            async with self._limiter:
                try:
                    r = await self._client.request(method, f"{API}{path}", headers=headers, **kw)
                    if r.headers.get("content-type", "").startswith("application/json"):
                        r.json()  # обрезанный ответ — ретраим, как сетевую ошибку
                except (httpx.TransportError, ValueError) as e:
                    if attempt == 4:
                        raise
                    log.warning("fixprice_transport_error", path=path, err=str(e), attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            if r.status_code in RETRY_STATUSES and attempt < 4:
                log.warning("fixprice_retry", path=path, status=r.status_code, attempt=attempt)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------ store
    async def _all_stores(self) -> list[dict[str, Any]]:
        if self._stores is None:
            self._stores = [s for s in (await self._request("GET", "/store")).json()
                            if s.get("isActive") and not s.get("temporarilyClosed")]
        return self._stores

    async def list_stores(self) -> list[StoreRow]:
        return [row for row in (parse_store(s) for s in await self._all_stores()) if row]

    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        stores = await self.list_stores()
        nearest = min(stores, key=lambda s: distance_km((s.lat, s.lon), (lat, lon)), default=None)
        return [nearest] if nearest else []

    def use_store(self, store: StoreRow) -> None:
        # Цена — по городу точки: в store_type лежит её cityId (см. parse_store)
        self.store_code = store.code
        self._city_id = int(store.store_type.removeprefix("city:"))

    # ------------------------------------------------------------- categories
    async def categories(self) -> list[CategoryRow]:
        menu = (await self._request("GET", "/category/menu")).json()
        root = next((c for c in menu if c.get("id") == FOOD_ROOT_ID), None)
        if root is None:
            raise RuntimeError("Fix Price: в меню нет раздела «Продукты и напитки»")
        rows: list[CategoryRow] = []

        def walk(node: dict[str, Any], parent: str | None, level: int) -> None:
            children = node.get("items") or []
            cid = str(node["id"])
            self._category_urls[cid] = node["url"]
            rows.append(CategoryRow(
                id=cid, parent_id=parent, name=node.get("title") or "", slug=node.get("alias"),
                level=level, has_children=bool(children), is_adult=bool(node.get("adult")),
                url=f"{SITE}/{node['url']}",
            ))
            for child in children:
                walk(child, cid, level + 1)

        walk(root, None, 0)
        # Обход корня целиком — для товаров вне листьев; категорию им не ставим, её даст лист
        rest_id = f"{root['id']}{REST_CATEGORY_SUFFIX}"
        self._category_urls[rest_id] = root["url"]
        rows.append(CategoryRow(id=rest_id, parent_id=str(root["id"]), name="Прочее", level=1,
                                url=f"{SITE}/{root['url']}"))
        return rows

    # ---------------------------------------------------------------- listing
    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        url = self._category_urls[category_id]
        leaf = None if category_id.endswith(REST_CATEGORY_SUFFIX) else category_id
        page = 1
        while True:
            r = await self._request(
                "POST",
                f"/product/in/{url}",
                params={"page": page, "limit": PAGE_SIZE, "sort": "sold"},
                json={"category": url, "brand": [], "badge": [], "price": [],
                      "isDividedPrice": False, "isSpecialPrice": False},
            )
            items = r.json() or []
            for it in items:
                row = parse_item(it, leaf)
                if row:
                    yield row
            total = int(r.headers.get("x-count") or 0)
            if not items or page * PAGE_SIZE >= total:
                return
            page += 1

    # ---------------------------------------------------------------- details
    async def details(self, item_id: str) -> DetailsRow | None:
        try:
            data = (await self._request("GET", f"/product/sku/{item_id}")).json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return parse_details(data) if data else None


# ---------------------------------------------------------------------- parsing
def parse_store(s: dict[str, Any]) -> StoreRow | None:
    if not s.get("pfm") or s.get("latitude") is None or s.get("cityId") is None:
        return None
    # store_type несёт cityId: цена у Fix Price по городу, use_store ставит по нему x-city
    return StoreRow(code=str(s["pfm"]), store_type=f"city:{s['cityId']}", address=s.get("address"),
                    lat=float(s["latitude"]), lon=float(s["longitude"]))


def parse_item(it: dict[str, Any], category_id: str | None) -> ItemRow | None:
    price = _minor(it.get("price"))
    if it.get("id") is None or price is None:
        return None
    images = [img["src"] for img in it.get("images") or [] if img.get("src")]
    return ItemRow(
        id=str(it["id"]),
        name=it.get("title") or "",
        # Обычная цена. Цену по карте Fix Price (specialPrice) не сохраняем: это не скидка от «старой» цены
        price_minor=price,
        in_stock=bool(it.get("inStock")),
        url=f"{SITE}/{it['url']}" if it.get("url") else None,
        image_urls=images,
        is_adult=bool(it.get("adult")),
        category_id=category_id,
    )


_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|г|мл|л)\b", re.IGNORECASE)
_TO_GRAMS = {"кг": 1000, "г": 1, "л": 1000, "мл": 1}


def parse_details(d: dict[str, Any]) -> DetailsRow:
    row = DetailsRow(id=str(d["id"]))
    row.brand = (d.get("brand") or {}).get("title") or None
    row.description = d.get("description") or None
    for prop in d.get("properties") or []:
        if prop.get("alias") == "manufacturer":
            row.manufacturer = prop.get("value")
        if prop.get("title") and prop.get("value") not in (None, ""):
            row.attributes[prop["title"][:255]] = str(prop["value"])[:1024]
    for extra in d.get("extraDescriptions") or []:
        alias, value = extra.get("alias"), extra.get("value")
        if alias == "composition":
            row.composition = value
        elif alias == "nutritionalValueSeparated" and isinstance(value, dict):
            row.kcal = _float(value.get("energyValue"))
            row.protein = _float(value.get("protein"))
            row.fat = _float(value.get("fats"))
            row.carbs = _float(value.get("carbohydrates"))
        elif alias == "manufacturerAddress" and value:
            row.attributes["Адрес производителя"] = str(value)[:1024]
    # Вес нетто отдельным полем не приходит — только в названии («…, 200 г», «973 мл»)
    matches = _WEIGHT_RE.findall(d.get("title") or "")
    if matches:
        value, unit = matches[-1]
        row.weight_g = round(float(value.replace(",", ".")) * _TO_GRAMS[unit.lower()])
    variants = d.get("variants") or []
    barcode = next((v.get("barcode") for v in variants if v.get("barcode") and v.get("barcode") != d.get("sku")), None)
    if barcode:
        row.attributes["Штрихкод"] = barcode
    row.image_urls = [img["src"] for img in d.get("images") or [] if img.get("src")]
    return row


def _minor(v: Any) -> int | None:
    try:
        return round(float(str(v).replace(",", ".")) * 100)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
