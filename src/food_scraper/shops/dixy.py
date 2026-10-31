"""Дикси: PHP-ручки сайта на Битриксе (/ajax/*.php). Разведка — recon/dixy.md.

Магазин хранится в PHP-сессии: use_store запоминает точку, а saveStore уходит перед
первым запросом каталога. Сайт за Qrator: без браузерного UA — 403, у листинга без
параметра collect_stat — тоже 403. Нагрузка — не больше 3 запросов в секунду.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

import httpx
import structlog
from aiolimiter import AsyncLimiter
from selectolax.lexbor import LexborHTMLParser

from ..config import Settings
from ..stores import StoreRow, distance_km
from .base import CategoryRow, DetailsRow, ItemRow

log = structlog.get_logger(__name__)

BASE_URL = "https://dixy.ru"
PAGE_SIZE = 30  # больше 30 — пустой ответ
# Разведка прошла на ~3 rps последовательно, но два параллельных прогона (6 rps и частая смена
# точки) получили бан IP. Держим 2 rps и не запускаем ежедневный и полный прогоны одновременно.
MAX_RPS = 2
RETRY_STATUSES = {429, 500, 502, 503, 504}
# Корни дерева, где товары других корней повторяются, и промо-подборки без своего ассортимента
SKIP_ROOTS = {"Скидки по карте"}
# В HTML вызов многострочный: initSectionBehavior({\n  "sid":839, …
SID_RE = re.compile(r'initSectionBehavior\(\{\s*"sid"\s*:\s*(\d+)')


class Dixy:
    name = "dixy"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store_code: str | None = None
        self._store: StoreRow | None = None
        self._store_saved = False
        self._store_lock = asyncio.Lock()
        self._limiter = AsyncLimiter(min(settings.rps, MAX_RPS), 1)
        self._client: httpx.AsyncClient | None = None
        self._categories: list[CategoryRow] | None = None
        # Карточку отдаёт только HTML по полному slug — берём его из листинга
        self._urls: dict[str, str] = {}

    async def __aenter__(self) -> "Dixy":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            proxy=self.settings.proxy_url or None,
            follow_redirects=True,
            headers={
                "User-Agent": self.settings.user_agent
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
                "Accept-Language": "ru-RU",
                "Referer": f"{BASE_URL}/",
            },
        )
        # PHPSESSID: в нём живёт выбранный магазин
        await self._request("GET", "/catalog/")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        assert self._client is not None, "используй `async with Dixy(...)`"
        delay = 2.0
        for attempt in range(5):
            async with self._limiter:
                try:
                    r = await self._client.request(method, f"{BASE_URL}{path}", **kw)
                except httpx.TransportError as e:
                    if attempt == 4:
                        raise
                    log.warning("dixy_transport_error", path=path, err=str(e), attempt=attempt)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            if r.status_code in RETRY_STATUSES and attempt < 4:
                log.warning("dixy_retry", path=path, status=r.status_code, attempt=attempt)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")

    async def _ajax(self, action: str, **fields: str) -> Any:
        # Ручки ждут multipart/form-data
        files = {"action": (None, action), **{k: (None, v) for k, v in fields.items()}}
        r = await self._request("POST", "/ajax/ajax.php", files=files)
        text = r.text.strip()
        return json.loads(text) if text else None

    # ------------------------------------------------------------------ store
    async def list_stores(self) -> list[StoreRow]:
        data = await self._ajax("getSelfPoints")
        features = data if isinstance(data, list) else (data or {}).get("features") or []
        return [row for row in (parse_store(f) for f in features) if row]

    async def find_stores(self, lat: float, lon: float) -> list[StoreRow]:
        stores = await self.list_stores()
        nearest = min(stores, key=lambda s: distance_km((s.lat, s.lon), (lat, lon)), default=None)
        return [nearest] if nearest else []

    def use_store(self, store: StoreRow) -> None:
        self._store = store
        self.store_code = store.code
        self._store_saved = False

    async def prepare_store(self) -> None:
        await self._ensure_store()

    async def _ensure_store(self) -> None:
        if self._store_saved or self._store is None:
            return
        async with self._store_lock:
            if self._store_saved:
                return
            store = self._store
            await self._ajax(
                "saveStore",
                isDelivery="false",
                store_id=store.code,
                coords=f"{store.lon},{store.lat}",
                address=store.address or "",
            )
            self._store_saved = True

    # ------------------------------------------------------------- categories
    async def categories(self) -> list[CategoryRow]:
        # Дерево одно на всю сеть: id корней есть только в HTML их страниц, ходим за ними один раз
        if self._categories is not None:
            return self._categories
        tree = json.loads((await self._request("GET", "/ajax/json.php", params={"block": "tree"})).text.strip())
        rows = []
        for root in tree.get("tree") or []:
            name, href = root.get("name"), root.get("href")
            if not name or not href or name in SKIP_ROOTS or "retsepty" in href:
                continue
            html = (await self._request("GET", href)).text
            m = SID_RE.search(html)
            if not m:
                log.warning("dixy_no_sid", href=href)
                continue
            # Листинг корня включает все подкатегории — обходим только корни, листья приходят в товарах
            rows.append(CategoryRow(id=m.group(1), parent_id=None, name=name, slug=href.strip("/").split("/")[-1],
                                    level=0, url=f"{BASE_URL}{href}"))
        self._categories = rows
        return rows

    # ---------------------------------------------------------------- listing
    async def iter_items(self, category_id: str) -> AsyncIterator[ItemRow]:
        await self._ensure_store()
        page = 1
        while True:
            r = await self._request(
                "GET",
                # collect_stat без значения обязателен: без него WAF отвечает 403
                f"/ajax/listing-json.php?block=product-list&sid={category_id}"
                f"&perPage={PAGE_SIZE}&page={page}&collect_stat",
            )
            text = r.text.strip()
            if not text:
                return
            data = json.loads(text)
            block = data[0] if isinstance(data, list) and data else {}
            for card in block.get("cards") or []:
                row = parse_card(card)
                if row:
                    if row.url:
                        self._urls[row.id] = row.url
                    yield row
            pagen = block.get("pagenData") or {}
            # page больше pages_count снова отдаёт последнюю страницу — останавливаемся сами
            if pagen.get("isLastPage") or page >= int(pagen.get("pages_count") or 0):
                return
            page += 1

    # ---------------------------------------------------------------- details
    async def details(self, item_id: str) -> DetailsRow | None:
        url = self._urls.get(item_id)
        if not url:
            return None
        await self._ensure_store()
        try:
            r = await self._request("GET", url.removeprefix(BASE_URL))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return parse_details(item_id, r.text)


# ---------------------------------------------------------------------- parsing
def parse_store(feature: dict[str, Any]) -> StoreRow | None:
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    props = feature.get("properties") or {}
    code = feature.get("id") or props.get("id")
    if code is None or len(coords) != 2:
        return None
    try:
        # У Дикси координаты [lat, lon] строками
        lat, lon = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    address = (props.get("balloonContentBody") or "").strip().rstrip(",").strip() or None
    return StoreRow(code=str(code), store_type="self", address=address, lat=lat, lon=lon)


def parse_card(card: dict[str, Any]) -> ItemRow | None:
    item_id = card.get("xml_id")
    price = _minor(card.get("priceSimple"))
    if not item_id or price is None:
        return None
    old = _minor(card.get("oldPriceSimple"))
    weighted = card.get("variant") == "весовой"
    amount = card.get("amount")
    previews = card.get("previews") or []
    if isinstance(previews, dict):  # одна картинка приходит объектом, а не списком
        previews = [previews]
    images = [p.get("src") for p in previews if isinstance(p, dict) and p.get("src")] or [card.get("src")]
    return ItemRow(
        id=str(item_id),
        name=card.get("title") or "",
        price_minor=price,
        old_price_minor=old if old and old != price else None,
        in_stock=bool(card.get("canBuy", True)) and (amount is None or float(amount) > 0),
        url=f"{BASE_URL}{card['url']}" if card.get("url") else None,
        image_urls=[_abs(src) for src in images if src],
        is_weighted=weighted,
        # У весовых priceSimple — цена за кг
        unit_price_minor=price if weighted else None,
        unit_label=card.get("symbol") if weighted else None,
        is_adult=bool(card.get("isAlko")),
        category_id=str(card["section_id"]) if card.get("section_id") else None,
        category_name=card.get("section"),
    )


_NUTRITION = {"Калории": "kcal", "Белки": "protein", "Жиры": "fat", "Углеводы": "carbs"}


def parse_details(item_id: str, html: str) -> DetailsRow:
    tree = LexborHTMLParser(html)
    row = DetailsRow(id=item_id)
    for holder in tree.css("div.detail-data__holder"):
        title = holder.css_first("span.title")
        title = title.text(strip=True) if title else ""
        if title.startswith("Пищевая ценность") and row.kcal is None:
            for wrap in holder.css("div.block__wrap"):
                name, value = wrap.css_first("p.text"), wrap.css_first("span.block__title")
                field = _NUTRITION.get(name.text(strip=True)) if name else None
                if field and value:
                    setattr(row, field, _float(value.text(strip=True)))
        elif title == "Состав":
            text = holder.css_first("p.text")
            row.composition = text.text(strip=True) if text else None
        elif title == "Описание":
            text = holder.css_first("p.text")
            row.description = text.text(strip=True) if text else None
        for line in holder.css("div.list__line"):
            # Первый span — подпись, значение — в следующих (бывает внутри ссылки на фильтр)
            spans = [s.text(strip=True) for s in line.css("span")]
            if len(spans) < 2:
                continue
            name = spans[0]
            value = next((v for v in spans[1:] if v), None)
            if not name or value is None:
                continue
            if name == "Торговая марка":
                row.brand = value
            elif name == "Вес":
                kg = _float(value)
                row.weight_g = round(kg * 1000) if kg is not None else None
            row.attributes[name[:255]] = value[:1024]
    # Фото берём из листинга: в карточке только мелкое превью 54×54
    return row


def _abs(src: str) -> str:
    return src if src.startswith("http") else f"{BASE_URL}{src}"


def _minor(v: Any) -> int | None:
    try:
        return round(float(str(v).replace(",", ".").replace(" ", "")) * 100)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v or ""))
    return float(m.group(0).replace(",", ".")) if m else None
