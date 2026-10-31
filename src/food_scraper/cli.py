from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path  # noqa: F401  (используется в типах команд)
from typing import Optional

import structlog
import typer
from sqlalchemy import desc, select

from .cities import CITIES
from .config import PROJECT_ROOT, get_settings
from .db import session_scope
from .logging import configure_logging
from .models import ScrapeRun

app = typer.Typer(add_completion=False, no_args_is_help=True)
log = structlog.get_logger(__name__)


@app.callback()
def _root(
    log_level: str = typer.Option(None, help="DEBUG/INFO/WARNING/ERROR"),
) -> None:
    s = get_settings()
    configure_logging(log_level or s.log_level)


@app.command(
    "import-session",
    rich_help_panel="Лента",
    help=(
        "Импортировать cURL из DevTools. "
        "Сам определяет тип: навигационный → cookies в browser_session.json, "
        "XHR к /api-gateway/* → шаблон body в endpoints.json. "
        "Можно подать несколько cURL'ов — для покрытия и сессии, и API-вызовов."
    ),
)
def import_session(
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к файлу с cURL-командой. Если не задан — читаем stdin.",
    ),
) -> None:
    if file is not None:
        text = file.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            typer.echo(
                "Передай cURL через pipe или --file. Например:\n"
                "  pbpaste | food-scrape import-session\n"
                "  food-scrape import-session -f request.curl",
                err=True,
            )
            raise typer.Exit(2)
        text = sys.stdin.read()
    _process_curl(text)


def _process_curl(text: str) -> None:
    from .shops.lenta.browser_session import (
        ENDPOINTS_FILE,
        SESSION_FILE,
        is_api_endpoint,
        parse_curl,
        save_api_endpoint,
        save_browser_session,
    )

    try:
        parsed = parse_curl(text)
    except ValueError as e:
        typer.echo(f"FAIL: {e}", err=True)
        raise typer.Exit(1)

    if is_api_endpoint(parsed):
        key, _ = save_api_endpoint(parsed)
        rel = ENDPOINTS_FILE.relative_to(PROJECT_ROOT)
        typer.echo(
            f"→ API-шаблон сохранён: {key}  ({'с body' if parsed.body else 'без body'})  "
            f"в {rel}"
        )
        # На XHR обычно тоже едут cookies — попробуем подхватить и сессию.
        if parsed.cookies:
            from .shops.lenta.browser_session import BrowserSession

            existing = BrowserSession.load() or BrowserSession()
            existing.cookies.update(parsed.cookies)
            existing.save()
            typer.echo(
                f"  +cookies в сессии: {len(parsed.cookies)} → итого {len(existing.cookies)}"
            )
        return

    # Иначе — это навигационный cURL, сохраняем как сессию.
    try:
        sess = save_browser_session(parsed)
    except ValueError as e:
        typer.echo(f"FAIL: {e}", err=True)
        raise typer.Exit(1)
    ua = next(
        (v for k, v in sess.headers.items() if k.lower() == "user-agent"), "(нет)"
    )
    typer.echo(
        f"→ Сессия сохранена в {SESSION_FILE.relative_to(PROJECT_ROOT)}\n"
        f"  cookies: {len(sess.cookies)} (qrator_jsid={'есть' if 'qrator_jsid' in sess.cookies else 'НЕТ'})\n"
        f"  user-agent: {ua[:80]}"
    )


@app.command(help="Лента: однократная разведка эндпоинтов через Playwright.", rich_help_panel="Лента")
def recon(
    city: str = typer.Option(None, help="Поддомен города. По умолчанию — из .env"),
    headless: bool = typer.Option(False, help="Запустить браузер без UI"),
) -> None:
    from .shops.lenta.recon import run_recon

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


@app.command("db-check", help="Проверить подключение к БД (SELECT 1) и список таблиц.")
def db_check() -> None:
    asyncio.run(_db_check_async())


async def _db_check_async() -> None:
    from sqlalchemy import text

    from .db import get_engine

    engine = get_engine()
    s = get_settings()
    typer.echo(f"DSN: {s.mysql_dsn.split('@', 1)[-1]}")
    try:
        async with engine.connect() as conn:
            v = (await conn.execute(text("SELECT VERSION()"))).scalar()
            typer.echo(f"OK: connected, MySQL {v}")
            tables = (
                await conn.execute(text("SHOW TABLES LIKE 'shop\\_%'"))
            ).scalars().all()
            typer.echo(
                f"Tables ({len(tables)}): {', '.join(tables) if tables else '(пусто — запусти init-db)'}"
            )
    except Exception as e:
        typer.echo(f"FAIL: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1)
    finally:
        await engine.dispose()


def _read_clipboard() -> str | None:
    """Прочитать буфер обмена через pbpaste/wl-paste/xclip — что найдётся."""
    candidates = (
        ["pbpaste"],
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
    )
    for cmd in candidates:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=2, check=False
            )
        except FileNotFoundError:
            continue
        except Exception:
            continue
        if r.returncode == 0 and r.stdout and r.stdout.strip():
            return r.stdout
    return None


def _refresh_browser_session() -> None:
    """Подхватить cURL из буфера или попросить вставить — и сохранить сессию/API."""
    text = _read_clipboard()
    if text and "curl" in text[:50].lower():
        typer.echo("→ Подхватил cURL из буфера обмена.")
    else:
        if not sys.stdin.isatty():
            typer.echo(
                "FAIL: --refresh-session требует tty или непустой буфер обмена с cURL.",
                err=True,
            )
            raise typer.Exit(2)
        typer.echo(
            "Вставь cURL из DevTools (Network → Copy → Copy as cURL) и нажми Ctrl-D:"
        )
        text = sys.stdin.read()
    _process_curl(text)


@app.command(help="Прогон: сеть магазинов × город, по нескольким точкам сети.")
def run(
    shop: str = typer.Option("magnit", "--shop", help="magnit | dixy | fixprice | vkusvill | lenta"),
    city: str = typer.Option("moscow", "--city", help=f"Город: {', '.join(CITIES)}"),
    category_id: Optional[str] = typer.Option(
        None, "--category-id", help="Только эта категория (для smoke-теста)"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Максимум товаров на категорию"
    ),
    stores: int = typer.Option(
        12, "--stores", help="Сколько точек сети обойти: разнесённые по районам, каждый день одни и те же"
    ),
    all_stores: bool = typer.Option(False, "--all-stores", help="Обойти все активные точки города"),
    refresh_stores: bool = typer.Option(
        False, "--refresh-stores", help="Перед прогоном обновить справочник точек (обход города сеткой)"
    ),
    workers: int = typer.Option(4, help="Параллельных запросов к магазину"),
    details_max_age_days: int = typer.Option(
        30, help="Перечитывать карточку (состав, КБЖУ), если ей больше N дней"
    ),
    details_limit: Optional[int] = typer.Option(
        None, help="Максимум карточек за прогон"
    ),
    refresh_session: bool = typer.Option(
        False,
        "--refresh-session",
        "-R",
        help="Лента: перед прогоном подхватить свежий cURL из буфера обмена.",
    ),
) -> None:
    from datetime import timedelta

    from .pipeline.run import run as run_pipeline

    if refresh_session:
        _refresh_browser_session()
    asyncio.run(
        run_pipeline(
            shop,
            city,
            stores=None if all_stores else stores,
            refresh_stores=refresh_stores,
            category_id=category_id,
            limit=limit,
            workers=workers,
            details_max_age=timedelta(days=details_max_age_days),
            details_limit=details_limit,
        )
    )


@app.command("stores", help="Обновить справочник точек сети в городе (обход сеткой) и показать его.")
def stores_cmd(
    shop: str = typer.Option("magnit", "--shop", help="magnit | dixy | fixprice | vkusvill | lenta"),
    city: str = typer.Option("moscow", "--city", help=f"Город: {', '.join(CITIES)}"),
    workers: int = typer.Option(4, help="Параллельных запросов к магазину"),
) -> None:
    from .pipeline.run import discover_stores

    rows = asyncio.run(discover_stores(shop, city, workers=workers))
    for r in sorted(rows, key=lambda r: r.code):
        typer.echo(f"{r.code:>10}  type={r.store_type:<4} {r.address or ''}")
    typer.echo(f"Активных точек: {len(rows)}")


@app.command("match", help="Сопоставить ингредиенты рецептов с товарами сетей (status=auto).")
def match_cmd() -> None:
    from .matching import match_all
    from .pipeline.run import utcnow

    typer.echo(asyncio.run(match_all(utcnow())))


@app.command("recipe-costs", help="Пересчитать стоимость рецептов по сетям и городам.")
def recipe_costs_cmd() -> None:
    from .pipeline.run import utcnow
    from .recipe_costs import compute_all

    typer.echo(asyncio.run(compute_all(utcnow())))


@app.command("check", help="Проверка данных: устаревание, нулевые цены, выбросы, фасовка; ошибки — в Telegram.")
def check_cmd() -> None:
    from .pipeline.run import utcnow
    from .quality import check_all

    typer.echo(asyncio.run(check_all(utcnow())))


@app.command("postprocess", help="После прогонов: match → recipe-costs → check.")
def postprocess_cmd() -> None:
    from .matching import match_all
    from .pipeline.run import utcnow
    from .quality import check_all
    from .recipe_costs import compute_all

    async def go() -> None:
        typer.echo(f"match: {await match_all(utcnow())}")
        typer.echo(f"recipe-costs: {await compute_all(utcnow())}")
        typer.echo(f"check: {await check_all(utcnow())}")

    asyncio.run(go())


@app.command(
    help="Лента: сетевая диагностика — внешний IP и ответ Ленты через PROXY_URL.",
    rich_help_panel="Лента",
)
def probe() -> None:
    asyncio.run(_probe_async())


async def _probe_async() -> None:
    import httpx

    s = get_settings()
    typer.echo(f"PROXY_URL = {s.proxy_url or '(пусто)'}")
    typer.echo(f"USER_AGENT = {s.user_agent or '(default)'}")

    headers = {"User-Agent": s.user_agent or "Mozilla/5.0"}
    targets = [
        ("ipify (видимый IP)", "https://api.ipify.org?format=json"),
        ("lenta.com home", f"https://{s.city_subdomain}.lenta.com/"),
        (
            "lenta catalog page",
            f"https://{s.city_subdomain}.lenta.com/catalog/molochnye-produkty-syr-yaytsa-3009/",
        ),
    ]
    async with httpx.AsyncClient(
        proxy=s.proxy_url,
        timeout=httpx.Timeout(15.0, connect=10.0),
        headers=headers,
        follow_redirects=False,
    ) as c:
        for label, url in targets:
            try:
                r = await c.get(url)
                marker = ""
                if "ipify" in url:
                    marker = f"  body={r.text.strip()[:80]}"
                elif r.status_code in (401, 403):
                    body_low = r.text[:300].lower()
                    if "qrator" in body_low or "qauth" in body_low:
                        marker = "  ← Qrator JS-challenge (норма, решит warm-up)"
                    elif "forbidden" in body_low:
                        marker = "  ← edge-блок (IP/гео/файрвол)"
                typer.echo(f"  {label}: HTTP {r.status_code}{marker}")
            except httpx.HTTPError as e:
                typer.echo(f"  {label}: FAIL {type(e).__name__}: {e}")


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
            f"#{r.id}  {r.started_at}  {r.shop}/{r.city} stores={r.stores_total}  {r.status}  "
            f"products={r.products_total} prices={r.prices_total} "
            f"details={r.details_total} errors={r.errors_total}"
        )


if __name__ == "__main__":
    app()
