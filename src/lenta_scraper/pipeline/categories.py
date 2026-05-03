from __future__ import annotations

import structlog

from ..api import fetch_categories
from ..db import session_scope
from ..http_client import LentaHttpClient
from ..upsert import upsert_categories

log = structlog.get_logger(__name__)


async def sync_categories(client: LentaHttpClient) -> int:
    rows = await fetch_categories(client)
    db_rows = [r.as_db() for r in rows]
    async with session_scope() as session:
        # MySQL FK: страхуемся от висячих parent_id
        ids = {r["id"] for r in db_rows}
        for r in db_rows:
            if r["parent_id"] not in ids:
                r["parent_id"] = None
        # Сначала корни, потом потомки — порядком сортировки по level
        db_rows.sort(key=lambda r: (r.get("level") or 0))
        await upsert_categories(session, db_rows)
    log.info("categories_synced", count=len(db_rows))
    return len(db_rows)
