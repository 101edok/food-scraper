from __future__ import annotations

import asyncio

import structlog

from ..api import fetch_product_details, parse_details
from ..db import session_scope
from ..http_client import LentaHttpClient
from ..upsert import upsert_product

log = structlog.get_logger(__name__)


async def fetch_and_save(
    client: LentaHttpClient, item_id: int, *, fallback_category_id: int | None = None
) -> bool:
    payload = await fetch_product_details(client, item_id)
    if not payload:
        return False
    parsed = parse_details(payload)
    p = parsed["product"]
    if p["id"] is None:
        return False
    if p.get("category_id") is None and fallback_category_id is not None:
        p["category_id"] = fallback_category_id
    if p.get("category_id") is None:
        log.warning("details_no_category", item_id=item_id)
        return False
    async with session_scope() as session:
        await upsert_product(
            session, p, images=parsed["images"], attributes=parsed["attributes"]
        )
    return True


async def fetch_many(
    client: LentaHttpClient,
    pairs: list[tuple[int, int]],
    *,
    workers: int = 8,
) -> tuple[int, int]:
    """pairs = [(item_id, category_id), …]. Возвращает (ok, errors)."""
    sem = asyncio.Semaphore(workers)
    ok = errs = 0

    async def one(item_id: int, cid: int) -> None:
        nonlocal ok, errs
        async with sem:
            try:
                if await fetch_and_save(client, item_id, fallback_category_id=cid):
                    ok += 1
                else:
                    errs += 1
            except Exception as e:
                errs += 1
                log.warning("details_fetch_error", item_id=item_id, err=str(e))

    await asyncio.gather(*(one(i, c) for i, c in pairs))
    return ok, errs
