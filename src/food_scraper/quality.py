"""Ежедневная проверка данных (food-scrape check): устаревание, нулевые цены, выбросы цены за единицу,
товары без фасовки. Результат перезаписывает shop_quality_issues; сводка с ошибками — в Telegram.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import structlog
from sqlalchemy import case, delete, func, select

from . import alerts
from .db import session_scope
from .models import Category, Price, Product, QualityIssue, ScrapeRun

log = structlog.get_logger(__name__)

STALE_AFTER = timedelta(hours=48)
OUTLIER_FACTOR = 10  # цена за кг/л в 10 раз выше или ниже медианы категории
OUTLIER_MIN_CATEGORY = 5
NO_PACK_SHARE = 0.3  # доля товаров без фасовки, после которой цену за кг не посчитать для заметной части
RECENT_RUNS = timedelta(days=14)


# Разделы не с едой: цена за кг у кассет для бритья и ополаскивателей не говорит о качестве данных.
# Проверяем название категории и всех её родителей (основы слов, без регистра).
NON_FOOD_WORDS = (
    "гигиен", "косметик", "химия", "хозтовар", "для дома", "для животных", "для кошек", "для собак",
    "алкогол", "товары для детей", "канцтовар", "цветы", "игры", "игруш", "уход за", "маникюр",
    "стирк", "чистящ", "мелочей", "подарк", "для лица", "одежд",
)
# Разделы со смешанным ассортиментом — только по точному названию: «Для детей» у Магнита, а не «Каши для детей»
NON_FOOD_NAMES = {"для детей", "здоровье", "полка находок", "что ещё интересного", "для дома и не только"}


def non_food_categories(rows: list[tuple[str, str, str | None, str]]) -> set[tuple[str, str]]:
    """rows: (shop, id, parent_id, name) → (shop, id) категорий, у которых сама она или родитель — не еда."""
    nodes = {(shop, cid): (parent, (name or "").lower()) for shop, cid, parent, name in rows}

    def is_non_food(key: tuple[str, str]) -> bool:
        for _ in range(10):  # глубина дерева небольшая; защита от циклов
            node = nodes.get(key)
            if node is None:
                return False
            parent, name = node
            # «Детское питание» — еда, а «Товары для детей» и «Детское развитие, творчество и игры» — нет
            if (name.strip() in NON_FOOD_NAMES or any(w in name for w in NON_FOOD_WORDS)) and "питани" not in name:
                return True
            if not parent:
                return False
            key = (key[0], parent)
        return False

    return {key for key in nodes if is_non_food(key)}


def outliers(rows: list[tuple[str, str | None, str, float, str]]) -> list[tuple[str, str, float, float]]:
    """rows: (shop, category_id, product_id, цена за единицу, единица). → (shop, product_id, цена, медиана)."""
    groups: dict[tuple[str, str, str], list[tuple[str, float]]] = defaultdict(list)
    for shop, category_id, product_id, unit_price, unit in rows:
        if category_id and unit_price > 0:
            groups[(shop, category_id, unit)].append((product_id, unit_price))
    found = []
    for (shop, _, _), items in groups.items():
        if len(items) < OUTLIER_MIN_CATEGORY:
            continue
        median = statistics.median(p for _, p in items)
        for product_id, price in items:
            if price > median * OUTLIER_FACTOR or price < median / OUTLIER_FACTOR:
                found.append((shop, product_id, price, median))
    return found


UNIT_LABELS = {"g": (1000, "кг"), "ml": (1000, "л"), "pcs": (1, "шт")}


def rub(minor: float) -> str:
    return f"{minor / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def outlier_message(price: float, median: float, unit: str) -> str:
    """«Цена за кг в 14 раз ниже, чем обычно в категории: 47,50 ₽/кг против 664,80 ₽/кг»."""
    per, label = UNIT_LABELS.get(unit, (1, unit))
    ratio = price / median
    times = round(ratio if ratio >= 1 else 1 / ratio)
    word = "раза" if times % 10 in (2, 3, 4) and times % 100 not in (12, 13, 14) else "раз"
    direction = f"в {times} {word} {'выше' if ratio >= 1 else 'ниже'}"
    return (f"Цена за {label} {direction}, чем обычно в категории: {rub(price * per)}/{label} "
            f"против {rub(median * per)}/{label}. Проверьте фасовку")


async def check_all(now: datetime) -> dict[str, int]:
    issues: list[QualityIssue] = []

    def issue(shop, kind, severity, message, city=None, product_id=None):
        issues.append(QualityIssue(shop=shop, city=city, product_id=product_id, kind=kind, severity=severity,
                                   message=message[:1024], detected_at=now))

    async with session_scope() as session:
        # Устаревание: сеть × город, у которых были прогоны за 2 недели, но успешного — давно нет
        recent = (await session.execute(
            select(ScrapeRun.shop, ScrapeRun.city).where(ScrapeRun.started_at > now - RECENT_RUNS).distinct()
        )).all()
        for shop, city in recent:
            last_ok = await session.scalar(select(func.max(ScrapeRun.finished_at)).where(
                ScrapeRun.shop == shop, ScrapeRun.city == city, ScrapeRun.status == "ok"))
            if last_ok is None or last_ok < now - STALE_AFTER:
                when = f"последний успешный прогон {last_ok:%Y-%m-%d %H:%M} UTC" if last_ok else "успешных прогонов нет"
                issue(shop, "stale", "error", f"Данные старше 48 ч: {when}", city=city)

        for shop, city, product_id, price in (await session.execute(
            select(Price.shop, Price.city, Price.product_id, Price.price_minor)
            .where(Price.price_minor <= 0).distinct()
        )).all():
            issue(shop, "zero_price", "error", f"Цена {price} коп.", city=city, product_id=product_id)

        # Выбросы цены за единицу внутри категории (минимальная цена товара по точкам)
        min_price = select(Price.shop, Price.product_id, func.min(Price.price_minor).label("price")) \
            .where(Price.price_minor > 0).group_by(Price.shop, Price.product_id).subquery()
        rows = (await session.execute(
            select(Product.shop, Product.category_id, Product.id, min_price.c.price,
                   Product.net_quantity, Product.net_unit)
            .join(min_price, (min_price.c.shop == Product.shop) & (min_price.c.product_id == Product.id))
            .where(Product.net_quantity > 0)
        )).all()
        non_food = non_food_categories((await session.execute(
            select(Category.shop, Category.id, Category.parent_id, Category.name))).all())
        unit_rows = [(shop, cat, pid, price / float(qty), unit) for shop, cat, pid, price, qty, unit in rows
                     if (shop, cat) not in non_food]
        units = {(shop, pid): unit for shop, _, pid, _, unit in unit_rows}
        for shop, product_id, price, median in outliers(unit_rows):
            issue(shop, "price_outlier", "warning", outlier_message(price, median, units[(shop, product_id)]),
                  product_id=product_id)

        # Доля товаров без фасовки по сети
        for shop, total, without in (await session.execute(
            select(Product.shop, func.count(), func.sum(case((Product.net_quantity.is_(None), 1), else_=0)))
            .group_by(Product.shop)
        )).all():
            without = int(without or 0)
            if total and without / total > NO_PACK_SHARE:
                issue(shop, "no_pack", "warning",
                      f"Без фасовки {without} из {total} товаров ({without / total:.0%}): "
                      f"для них нет цены за кг/л и они не попадают в стоимость рецептов")

        await session.execute(delete(QualityIssue))
        session.add_all(issues)

    counts = defaultdict(int)
    for i in issues:
        counts[f"{i.severity}:{i.kind}"] += 1
    log.info("quality_done", **counts)

    errors = [i for i in issues if i.severity == "error"]
    if errors:
        lines = [f"• {i.shop}/{i.city or '—'}: {i.message}" for i in errors if i.kind == "stale"]
        other = len(errors) - len(lines)
        text = "⚠️ Проверка данных food-scraper\n" + "\n".join(lines[:10])
        if other:
            text += f"\nЕщё ошибок: {other} (нулевые цены и др.)"
        text += "\nПодробнее — admin.edalle.ru/shop/quality"
        try:
            await alerts.send(text)
        except Exception as e:
            log.warning("alert_failed", err=str(e))
    return dict(counts)
