"""Типизированные обёртки над эндпоинтами lenta.com.

Стратегия:
* `categories` — берём из SSR (`<script id="ng-state">`) любой страницы каталога.
  Это надёжнее, чем угадывать API: сервер сам отдаёт полное дерево.
* `listing` (товары категории) — POST к `/api-gateway/v1/catalog/items`. Точное
  имя поля categoryId / лимит / offset берётся из `recon/endpoints.json`,
  созданного командой `lenta-scrape recon`. Если файла нет — используем
  best-guess схему (см. DEFAULT_LIST_BODY).
* `product details` — GET `/api-gateway/v1/catalog/items/{id}`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

import structlog

from .config import RECON_DIR, get_settings
from .http_client import LentaHttpClient

log = structlog.get_logger(__name__)

NG_STATE_RE = re.compile(
    r'<script[^>]*id="ng-state"[^>]*type="application/json"[^>]*>([^<]+)</script>',
    re.IGNORECASE,
)

CATALOG_ROOT_PAGE = "/catalog/molochnye-produkty-syr-yaytsa-3009/"

LIST_PATH = "/api-gateway/v1/catalog/items"
ITEM_PATH = "/api-gateway/v1/catalog/items/{item_id}"

DEFAULT_LIST_BODY: dict[str, Any] = {
    "limit": 24,
    "offset": 0,
    "categoryId": 0,
    "sort": {"type": "popular", "order": "desc"},
    "filters": {"checkbox": [], "multicheckbox": [], "range": []},
}


# ---------------------------------------------------------------------------- #
# DTO
# ---------------------------------------------------------------------------- #
@dataclass(slots=True)
class CategoryRow:
    id: int
    parent_id: int | None
    slug: str | None
    name: str
    level: int | None
    has_children: bool
    is_adult: bool
    image_url: str | None
    url: str | None

    def as_db(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id or None,
            "slug": self.slug,
            "name": self.name,
            "level": self.level,
            "has_children": self.has_children,
            "is_adult": self.is_adult,
            "image_url": self.image_url,
            "url": self.url,
        }


@dataclass(slots=True)
class ProductSummary:
    id: int
    name: str
    url: str
    price_minor: int | None
    old_price_minor: int | None
    in_stock: bool
    image_urls: list[str]


# ---------------------------------------------------------------------------- #
# Recon overrides
# ---------------------------------------------------------------------------- #
def _load_recon_overrides() -> dict[str, Any]:
    p = RECON_DIR / "endpoints.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _list_template() -> tuple[dict[str, Any], dict[str, str]]:
    """Возвращает (body_template, extra_headers) для запроса листинга."""
    overrides = _load_recon_overrides()
    key_candidates = (f"POST {LIST_PATH}", f"POST {LIST_PATH}/")
    for k in key_candidates:
        ep = overrides.get(k)
        if not ep:
            continue
        body: dict[str, Any] = DEFAULT_LIST_BODY.copy()
        if ep.get("request_body"):
            try:
                body = json.loads(ep["request_body"])
            except Exception:
                pass
        # Из реальных headers берём только то, что Qrator/бэкенд могут проверять
        # помимо общих X-*/cookies (которые уже в сессии).
        headers: dict[str, str] = {}
        for name, val in (ep.get("request_headers") or {}).items():
            low = name.lower()
            if low in {"referer", "origin", "x-csrf-token", "x-xsrf-token"}:
                headers[name] = val
        return body, headers
    return DEFAULT_LIST_BODY.copy(), {}


# ---------------------------------------------------------------------------- #
# Categories — извлекаем из SSR HTML
# ---------------------------------------------------------------------------- #
def _parse_ng_state(html: str) -> dict[str, Any] | None:
    m = NG_STATE_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _category_url(slug: str | None, cid: int) -> str | None:
    if not slug:
        return None
    base = get_settings().base_url
    return f"{base}/catalog/{slug}-{cid}/"


async def fetch_categories(client: LentaHttpClient) -> list[CategoryRow]:
    """Скачать страницу категории и разобрать дерево из ng-state."""
    resp = await client.get(CATALOG_ROOT_PAGE)
    resp.raise_for_status()
    state = _parse_ng_state(resp.text)
    if not state:
        raise RuntimeError("ng-state не найден на странице каталога")

    raw = state.get("/catalog/categories", {}).get("categories")
    if not raw:
        raise RuntimeError("/catalog/categories отсутствует в SSR-данных")

    rows: list[CategoryRow] = []
    for c in raw:
        cid = c.get("id")
        if cid is None:
            continue
        rows.append(
            CategoryRow(
                id=int(cid),
                parent_id=int(c["parentId"]) if c.get("parentId") else None,
                slug=c.get("slug"),
                name=c.get("name") or "",
                level=c.get("level"),
                has_children=bool(c.get("hasChildren")),
                is_adult=bool(c.get("isAdult")),
                image_url=c.get("imageUrl") or c.get("imageWebUrl"),
                url=_category_url(c.get("slug"), int(cid)),
            )
        )
    log.info("categories_fetched", count=len(rows))
    return rows


# ---------------------------------------------------------------------------- #
# Listing
# ---------------------------------------------------------------------------- #
def _normalize_listing(payload: dict[str, Any]) -> tuple[int, list[ProductSummary]]:
    total = int(payload.get("total") or 0)
    items_raw = payload.get("items") or []
    out: list[ProductSummary] = []
    for it in items_raw:
        pid = it.get("id") or it.get("itemId") or it.get("sku")
        if pid is None:
            continue
        prices = it.get("prices") if isinstance(it.get("prices"), dict) else None
        # Цены могут быть в `prices.price` (рубли как float) или прямо в корне.
        price = (prices or {}).get("price") if prices else it.get("price")
        old = (prices or {}).get("priceRegular") if prices else it.get("oldPrice")
        out.append(
            ProductSummary(
                id=int(pid),
                name=it.get("title") or it.get("name") or "",
                url=it.get("webUrl") or it.get("url") or "",
                price_minor=_to_minor(price),
                old_price_minor=_to_minor(old),
                in_stock=bool(it.get("inStock", True)),
                image_urls=_collect_images(it),
            )
        )
    return total, out


def _to_minor(v: Any) -> int | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f * 100))


def _collect_images(it: dict[str, Any]) -> list[str]:
    imgs = it.get("images") or []
    if isinstance(imgs, list):
        out: list[str] = []
        for x in imgs:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                u = x.get("url") or x.get("imageUrl") or x.get("fullSize")
                if u:
                    out.append(u)
        return out
    if isinstance(imgs, dict):
        u = imgs.get("url") or imgs.get("imageUrl")
        return [u] if u else []
    return []


async def iter_category_items(
    client: LentaHttpClient, category_id: int, *, page_size: int = 24
) -> AsyncIterator[ProductSummary]:
    """Пагинация по категории: возвращает поток ProductSummary."""
    body_template, extra_headers = _list_template()
    offset = 0
    seen = 0
    while True:
        body = {
            **body_template,
            "categoryId": category_id,
            "offset": offset,
            "limit": page_size,
        }
        resp = await client.post(LIST_PATH, json=body, headers=extra_headers or None)
        if resp.status_code >= 400:
            log.warning(
                "listing_error",
                status=resp.status_code,
                category_id=category_id,
                offset=offset,
                hint="запусти `lenta-scrape recon` чтобы зафиксировать формат запроса",
            )
            return
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            log.warning("listing_bad_json", category_id=category_id)
            return
        total, items = _normalize_listing(payload)
        for p in items:
            yield p
        seen += len(items)
        if not items or seen >= total:
            return
        offset += len(items)


# ---------------------------------------------------------------------------- #
# Product details
# ---------------------------------------------------------------------------- #
async def fetch_product_details(
    client: LentaHttpClient, item_id: int
) -> dict[str, Any] | None:
    resp = await client.get(ITEM_PATH.format(item_id=item_id))
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        log.warning(
            "details_error", status=resp.status_code, item_id=item_id
        )
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


def parse_details(payload: dict[str, Any]) -> dict[str, Any]:
    """Привести ответ карточки к формату, понятному upsert.upsert_product()."""
    pid = payload.get("id") or payload.get("itemId")
    name = payload.get("title") or payload.get("name") or ""
    brand = payload.get("brand") or (payload.get("brandInfo") or {}).get("title")
    description = payload.get("description") or payload.get("annotation")
    composition = payload.get("composition") or payload.get("ingredients")
    prices = payload.get("prices") or {}
    price = prices.get("price") if isinstance(prices, dict) else payload.get("price")
    old = (
        prices.get("priceRegular")
        if isinstance(prices, dict)
        else payload.get("oldPrice")
    )
    cat = payload.get("categoryId") or (payload.get("category") or {}).get("id")
    images = _collect_images(payload)
    attrs_src = payload.get("characteristics") or payload.get("attributes") or []
    attrs: list[dict[str, str]] = []
    for a in attrs_src:
        if not isinstance(a, dict):
            continue
        n = a.get("name") or a.get("title") or a.get("key")
        v = a.get("value") or a.get("text")
        if n and v is not None:
            attrs.append({"name": str(n)[:255], "value": str(v)[:1024]})
    return {
        "product": {
            "id": int(pid) if pid is not None else None,
            "category_id": int(cat) if cat is not None else None,
            "name": name,
            "brand": brand,
            "url": payload.get("webUrl") or payload.get("url") or "",
            "description": description,
            "composition": composition,
            "price_minor": _to_minor(price),
            "old_price_minor": _to_minor(old),
            "currency": "RUB",
            "in_stock": bool(payload.get("inStock", True)),
        },
        "images": images,
        "attributes": attrs,
    }
