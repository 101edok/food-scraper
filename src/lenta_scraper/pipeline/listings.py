from __future__ import annotations

import asyncio
from typing import AsyncIterator

import structlog
from sqlalchemy import select

from ..api import ProductSummary, iter_category_items
from ..db import session_scope
from ..http_client import LentaHttpClient
from ..models import Category

log = structlog.get_logger(__name__)


async def leaf_categories(*, only_id: int | None = None) -> list[int]:
    async with session_scope() as session:
        if only_id is not None:
            return [only_id]
        result = await session.execute(
            select(Category.id).where(Category.has_children.is_(False))
        )
        return [row[0] for row in result.all()]


async def stream_category(
    client: LentaHttpClient, category_id: int, *, limit: int | None = None
) -> AsyncIterator[ProductSummary]:
    n = 0
    async for item in iter_category_items(client, category_id):
        yield item
        n += 1
        if limit is not None and n >= limit:
            return


async def stream_categories(
    client: LentaHttpClient,
    category_ids: list[int],
    *,
    limit: int | None = None,
    workers: int = 4,
) -> AsyncIterator[tuple[int, ProductSummary]]:
    """Параллельный обход списка категорий, общий лимит на категорию."""
    sem = asyncio.Semaphore(workers)
    queue: asyncio.Queue[tuple[int, ProductSummary] | None] = asyncio.Queue(
        maxsize=workers * 16
    )

    async def worker(cid: int) -> None:
        async with sem:
            try:
                async for p in stream_category(client, cid, limit=limit):
                    await queue.put((cid, p))
            except Exception as e:
                log.warning("category_stream_error", cid=cid, err=str(e))

    tasks = [asyncio.create_task(worker(c)) for c in category_ids]
    finisher = asyncio.create_task(_finish(tasks, queue))
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
    await finisher


async def _finish(tasks: list[asyncio.Task], q: asyncio.Queue) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)
    await q.put(None)
