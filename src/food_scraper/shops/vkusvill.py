"""ВкусВилл: официальный MCP-сервер mcp.vkusvill.ru/mcp (JSON-RPC поверх HTTP). Разведка — recon/vkusvill.md.

Сервер сделан ВкусВиллом для ИИ-агентов, без авторизации отдаёт поиск, карточки и магазины.
Это поиск, а не выгрузка каталога: без текста запроса листинг не работает, на страницу — 10 товаров.
Поэтому «категории» обхода — поисковые запросы: ингредиенты рецептов edalle (таблица ingredients
в общей БД) плюс базовые продукты. Без OAuth цена одна на всех и наличия нет — city_wide_prices.
Темп — 1 запрос в секунду: канал официальный, но рассчитан на агентов, а не на выгрузки.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any, AsyncIterator

import httpx
import structlog
from aiolimiter import AsyncLimiter
from sqlalchemy import text

from ..config import Settings
from ..stores import StoreRow, distance_km
from .base import CategoryRow, DetailsRow, ItemRow

log = structlog.get_logger(__name__)

MCP_URL = "https://mcp.vkusvill.ru/mcp"
PAGE_SIZE = 10  # максимум сервера
PAGES_PER_QUERY = 3  # по популярности: первые 30 товаров запроса — то, что реально покупают
MAX_RPS = 1
QUERY_PREFIX = "q:"
# Москва и Московская обл (фильтр region у vkusvill_shops); точки в радиусе города отберёт пайплайн
STORE_REGIONS = [3872]
# Базовые продукты в дополнение к ингредиентам рецептов
STAPLES = [
    "молоко", "кефир", "сметана", "творог", "йогурт", "сливки", "сыр", "масло сливочное", "яйца",
    "хлеб", "батон", "лаваш", "мука", "сахар", "соль", "рис", "гречка", "овсяные хлопья", "макароны",
    "масло подсолнечное", "оливковое масло", "курица", "куриное филе", "фарш", "говядина", "свинина",
    "индейка", "рыба", "лосось", "креветки", "колбаса", "сосиски", "ветчина", "картофель", "морковь",
    "лук", "чеснок", "капуста", "помидоры", "огурцы", "перец", "кабачки", "баклажаны", "зелень",
    "яблоки", "бананы", "лимоны", "апельсины", "грибы", "фасоль", "горох", "кукуруза", "томатная паста",
    "майонез", "кетчуп", "горчица", "соевый соус", "уксус", "мёд", "чай", "кофе", "какао", "шоколад",
    "печенье", "орехи", "сухофрукты", "специи", "разрыхлитель", "дрожжи", "крахмал", "желатин",
]


class VkusVill:
    name = "vkusvill"
    city_wide_prices = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store_code: str | None = None
        self._limiter = AsyncLimiter(min(settings.rps, MAX_RPS), 1)
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._rpc_id = 0

    async def __aenter__(self) -> "VkusVill":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            proxy=self.settings.proxy_url or None,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "edalle-food-scraper/1.0 (+https://edalle.ru)",
            },
        )
        init = await self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "edalle-food-scraper", "version": "1.0"},
        })
        log.info("vkusvill_mcp_ready", server=(init or {}).get("serverInfo"))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        assert self._client is not None, "используй `async with VkusVill(...)`"
        self._rpc_id += 1
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        delay = 2.0
        for attempt in range(5):
            async with self._limiter:
                try:
                    r = await self._client.post(MCP_URL, json=payload, headers=headers)
                except httpx.TransportError as e:
                    if attempt == 4:
                        raise
                    log.warning("vkusvill_transport_error", method=method, err=str(e), attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                log.warning("vkusvill_retry", method=method, status=r.status_code, attempt=attempt)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            self._session_id = r.headers.get("mcp-session-id") or self._session_id
            message = _parse_rpc(r.text)
            if "error" in message:
                raise RuntimeError(f"MCP {method}: {message['error']}")
            return message.get("result")
        raise RuntimeError("unreachable")

    async def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        content = result.get("content") or []
        body = json.loads(content[0]["text"]) if content and content[0].get("text") else {}
        if not body.get("ok"):
            error = body.get("error") or {}
            if error.get("http_status") == 404:
                return None
            raise RuntimeError(f"{name}: {error.get('message') or body}")
        return body.get("data")

    # ------------------------------------------------------------------ store
    async def list_stores(self) -> list[StoreRow]:
        rows: list[StoreRow] = []
        for region in STORE_REGIONS:
            page = 1
            while True:
                data = await self._tool("vkusvill_shops", {"page": page, "id_region_filter": region}) or {}
                rows.extend(r for r in (parse_store(s) for s in data.get("items") or []) if r)
                if not (data.get("meta") or {}).get("has_more"):
                    break
                page += 1
        return rows

    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        stores = await self.list_stores()
        nearest = min(stores, key=lambda s: distance_km((s.lat, s.lon), (lat, lon)), default=None)
        return [nearest] if nearest else []

    def use_store(self, store: StoreRow) -> None:
        # Без OAuth выдача не зависит от точки: точка — только для справочника и store_code цены
        self.store_code = store.code

    # ------------------------------------------------------------- categories
    async def categories(self) -> list[CategoryRow]:
        terms = await _ingredient_terms()
        seen: set[str] = set()
        rows = []
        for term in terms + STAPLES:
            key = term.strip().lower()
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            rows.append(CategoryRow(id=f"{QUERY_PREFIX}{key}", parent_id=None, name=key, persist=False))
        log.info("vkusvill_queries", ingredients=len(terms), total=len(rows))
        return rows

    # ---------------------------------------------------------------- listing
    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        query = category_id.removeprefix(QUERY_PREFIX)
        for page in range(1, PAGES_PER_QUERY + 1):
            data = await self._tool("vkusvill_products_search", {
                "q": query, "page": page, "limit": PAGE_SIZE, "sort": "popularity", "vvonly": 0,
                "mode": "custom", "fields": ["id", "name", "price", "unit", "weight", "url", "images", "category"],
            }) or {}
            for it in data.get("items") or []:
                row = parse_item(it)
                if row:
                    yield row
            if not (data.get("meta") or {}).get("has_more"):
                return

    # ---------------------------------------------------------------- details
    async def details(self, item_id: str) -> DetailsRow | None:
        data = await self._tool("vkusvill_product_details", {"id": int(item_id)})
        return parse_details(data) if data else None


async def _ingredient_terms() -> list[str]:
    """Ингредиенты рецептов edalle — из таблицы backend в той же БД; нет таблицы (тесты) — пусто."""
    from ..db import session_scope

    try:
        async with session_scope() as session:
            rows = await session.execute(text("SELECT name FROM ingredients ORDER BY name"))
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        log.warning("vkusvill_no_ingredients", err=str(e))
        return []


# ---------------------------------------------------------------------- parsing
def _parse_rpc(body: str) -> dict[str, Any]:
    # Ответ — JSON или SSE (data: {...})
    m = re.search(r"^data: (\{.*\})\s*$", body, re.M)
    return json.loads(m.group(1) if m else body)


def parse_store(s: dict[str, Any]) -> StoreRow | None:
    if s.get("id") is None or s.get("lat") is None or s.get("lon") is None:
        return None
    city = (s.get("city") or {}).get("name")
    address = ", ".join(x for x in (city, s.get("address")) if x) or None
    return StoreRow(code=str(s["id"]), store_type="shop", address=address,
                    lat=float(s["lat"]), lon=float(s["lon"]))


def parse_item(it: dict[str, Any]) -> ItemRow | None:
    price = it.get("price") or {}
    current = _minor(price.get("current"))
    if it.get("id") is None or current is None:
        return None
    old = _minor(price.get("old"))
    categories = it.get("category") or []
    leaf = categories[0] if categories else {}
    parent = categories[1] if len(categories) > 1 else {}
    unit = (it.get("unit") or "").strip()
    images = [img.get("large") or img.get("medium") for img in it.get("images") or [] if isinstance(img, dict)]
    return ItemRow(
        id=str(it["id"]),
        name=_clean(it.get("name")),
        price_minor=current,
        old_price_minor=old if old and old != current else None,
        # Наличие без OAuth не отдаётся — считаем, что товар продаётся, раз он в выдаче
        in_stock=True,
        url=it.get("url"),
        image_urls=[u for u in images if u],
        is_weighted=unit == "кг",
        unit_price_minor=current if unit == "кг" else None,
        unit_label="кг" if unit == "кг" else None,
        category_id=str(leaf["id"]) if leaf.get("id") else None,
        category_name=leaf.get("name"),
        category_parent_id=str(parent["id"]) if parent.get("id") else None,
        category_parent_name=parent.get("name"),
    )


_NUTRITION = re.compile(
    r"белки\s*([\d.,]+).*?жиры\s*([\d.,]+).*?углеводы\s*([\d.,]+).*?([\d.,]+)\s*ккал", re.I | re.S,
)


def parse_details(d: dict[str, Any]) -> DetailsRow:
    row = DetailsRow(id=str(d["id"]))
    row.brand = d.get("brand") or None
    row.description = _clean(d.get("description")) or None
    weight = d.get("weight") or {}
    if weight.get("value") and weight.get("unit") in ("кг", "л"):
        row.weight_g = round(float(weight["value"]) * 1000)
    images = [img.get("large") or img.get("medium") for img in d.get("images") or [] if isinstance(img, dict)]
    row.image_urls = [u for u in images if u]
    categories = d.get("category") or []
    if categories and categories[0].get("id"):
        row.category_id = str(categories[0]["id"])
    for prop in d.get("properties") or []:
        name, value = prop.get("name") or "", _clean(prop.get("value"))
        if not name or not value:
            continue
        if name == "Состав":
            row.composition = value
        elif name.startswith("Пищевая и энергетическая ценность"):
            # У товара бывает несколько поставщиков с одинаковым КБЖУ — берём первое вхождение
            m = _NUTRITION.search(value)
            if m:
                row.protein, row.fat, row.carbs, row.kcal = (_float(x) for x in m.groups())
        elif name == "Изготовитель":
            # «ООО "КОСМОЛ":Россия, 156026, …» — до двоеточия, первый из поставщиков
            row.manufacturer = value.split(":", 1)[0].strip()[:255] or None
        row.attributes[name[:255]] = value[:1024]
    return row


def _clean(v: Any) -> str:
    if v is None:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", str(v))
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ").strip()


def _minor(v: Any) -> int | None:
    try:
        return round(float(v) * 100) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
