from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
import typer
from sqlalchemy import desc, select

from .config import PROJECT_ROOT, get_settings
from .db import session_scope
from .http_client import http_client
from .logging import configure_logging
from .models import ScrapeRun
from .pipeline.categories import sync_categories
from .pipeline.details import fetch_many
from .pipeline.listings import leaf_categories, stream_categories

app = typer.Typer(add_completion=False, no_args_is_help=True)
log = structlog.get_logger(__name__)


@app.callback()
def _root(
    log_level: str = typer.Option(None, help="DEBUG/INFO/WARNING/ERROR"),
) -> None:
    s = get_settings()
    configure_logging(log_level or s.log_level)


@app.command(help="Однократная разведка эндпоинтов через Playwright.")
def recon(
    city: str = typer.Option(None, help="Поддомен города. По умолчанию — из .env"),
    headless: bool = typer.Option(False, help="Запустить браузер без UI"),
) -> None:
    from .recon import run_recon

    s = get_settings()
    asyncio.run(run_recon(city or s.city_subdomain, headless=headless))


@app.command("init-db", help="alembic upgrade head — создать таблицы.")
def init_db() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    raise typer.Exit(proc.returncode)


@app.command(help="Полный/частичный прогон скрапинга.")
def run(
    category_id: Optional[int] = typer.Option(
        None, "--category-id", help="Только эта категория (для smoke-теста)"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Максимум товаров на категорию"
    ),
    skip_categories_sync: bool = typer.Option(
        False, "--skip-categories-sync", help="Не обновлять справочник категорий"
    ),
    workers: int = typer.Option(8, help="Параллелизм запросов карточек"),
) -> None:
    asyncio.run(
        _run_async(
            category_id=category_id,
            limit=limit,
            skip_categories_sync=skip_categories_sync,
            workers=workers,
        )
    )


async def _run_async(
    *,
    category_id: int | None,
    limit: int | None,
    skip_categories_sync: bool,
    workers: int,
) -> None:
    s = get_settings()

    async with session_scope() as session:
        run_row = ScrapeRun(
            city=s.city_subdomain,
            delivery_mode=s.delivery_mode,
            status="running",
        )
        session.add(run_row)
        await session.flush()
        run_id = run_row.id

    products_total = errors_total = 0
    status = "ok"

    try:
        async with http_client(s) as client:
            if not skip_categories_sync:
                await sync_categories(client)

            cats = await leaf_categories(only_id=category_id)
            if not cats:
                log.warning("no_leaf_categories")
            log.info("targets", categories=len(cats), workers=workers)

            seen_pairs: list[tuple[int, int]] = []
            async for cid, summary in stream_categories(
                client, cats, limit=limit, workers=workers
            ):
                seen_pairs.append((summary.id, cid))
                if len(seen_pairs) >= 200:
                    ok, errs = await fetch_many(client, seen_pairs, workers=workers)
                    products_total += ok
                    errors_total += errs
                    seen_pairs.clear()

            if seen_pairs:
                ok, errs = await fetch_many(client, seen_pairs, workers=workers)
                products_total += ok
                errors_total += errs
    except Exception as e:
        status = "failed"
        log.exception("run_failed", err=str(e))
        raise
    finally:
        async with session_scope() as session:
            row = await session.get(ScrapeRun, run_id)
            if row:
                row.finished_at = datetime.now(timezone.utc).replace(microsecond=0)
                row.products_total = products_total
                row.errors_total = errors_total
                row.status = status

    log.info("done", products=products_total, errors=errors_total)


@app.command(help="Краткий отчёт по последним прогонам.")
def stats(n: int = typer.Option(5, help="Сколько последних прогонов показать")) -> None:
    asyncio.run(_stats_async(n))


async def _stats_async(n: int) -> None:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(n)
            )
        ).scalars().all()
    if not rows:
        typer.echo("Нет прогонов.")
        return
    for r in rows:
        typer.echo(
            f"#{r.id}  {r.started_at}  city={r.city}  {r.status}  "
            f"products={r.products_total}  errors={r.errors_total}"
        )


if __name__ == "__main__":
    app()
