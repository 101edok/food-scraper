"""Схема скрейпера магазинов. БД общая с edalle backend (recepter) — все таблицы с префиксом shop_.

Ключи магазинных сущностей — (shop, id): id внешний, уникален только внутри магазина.
Цена привязана к конкретной точке сети (store_code) и всегда несёт город: внутри
одного города точки продают по разным ценам, «цена города» считается по ним (min/медиана).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SHOP = String(16)
EXT_ID = String(32)
CITY = String(32)


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "shop_categories"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(EXT_ID)
    name: Mapped[str] = mapped_column(String(512))
    slug: Mapped[str | None] = mapped_column(String(255))
    level: Mapped[int | None] = mapped_column(SmallInteger)
    has_children: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adult: Mapped[bool] = mapped_column(Boolean, default=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    url: Mapped[str | None] = mapped_column(String(1024))


class Product(Base):
    __tablename__ = "shop_products"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    category_id: Mapped[str | None] = mapped_column(EXT_ID)
    name: Mapped[str] = mapped_column(String(1024))
    brand: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    composition: Mapped[str | None] = mapped_column(Text)
    # Пищевая ценность на 100 г
    kcal: Mapped[float | None] = mapped_column(Numeric(7, 2))
    protein: Mapped[float | None] = mapped_column(Numeric(6, 2))
    fat: Mapped[float | None] = mapped_column(Numeric(6, 2))
    carbs: Mapped[float | None] = mapped_column(Numeric(6, 2))
    weight_g: Mapped[int | None] = mapped_column(Integer)
    # Фасовка для цены за кг/л/шт: из названия («900 мл», «6х45г») или из API; у весовых — 1000 g (цена за кг)
    net_quantity: Mapped[float | None] = mapped_column(Numeric(10, 3))
    net_unit: Mapped[str | None] = mapped_column(String(4))  # g | ml | pcs
    is_weighted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adult: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    # Когда последний раз забирали карточку целиком (состав, КБЖУ). NULL — ещё не забирали.
    details_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProductImage(Base):
    __tablename__ = "shop_product_images"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024))

    __table_args__ = (
        ForeignKeyConstraint(
            ["shop", "product_id"], ["shop_products.shop", "shop_products.id"],
            ondelete="CASCADE",
        ),
    )


class ProductAttribute(Base):
    __tablename__ = "shop_product_attributes"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024))

    __table_args__ = (
        ForeignKeyConstraint(
            ["shop", "product_id"], ["shop_products.shop", "shop_products.id"],
            ondelete="CASCADE",
        ),
    )


class Store(Base):
    """Точка сети с онлайн-каталогом. Справочник пополняет food-scrape stores (обход города сеткой)."""

    __tablename__ = "shop_stores"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    city: Mapped[str] = mapped_column(CITY)
    # Формат точки в терминах магазина (у Магнита store_type: 1 — «у дома», 10, 16 — другие)
    store_type: Mapped[str] = mapped_column(String(16))
    address: Mapped[str | None] = mapped_column(String(512))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[float | None] = mapped_column(Numeric(9, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (Index("ix_shop_stores_city", "shop", "city", "is_active"),)


class Price(Base):
    """Текущая цена товара в точке сети — последняя увиденная."""

    __tablename__ = "shop_prices"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    city: Mapped[str] = mapped_column(CITY, primary_key=True)
    store_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    price_minor: Mapped[int | None] = mapped_column(Integer)
    old_price_minor: Mapped[int | None] = mapped_column(Integer)
    # Для весовых — цена за unit_label (обычно кг)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer)
    unit_label: Mapped[str | None] = mapped_column(String(16))
    in_stock: Mapped[bool] = mapped_column(Boolean)
    seen_at: Mapped[datetime] = mapped_column(DateTime)
    run_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        ForeignKeyConstraint(
            ["shop", "product_id"], ["shop_products.shop", "shop_products.id"],
            ondelete="CASCADE",
        ),
        Index("ix_shop_prices_city_price", "shop", "city", "in_stock", "price_minor"),
        # Под внешний ключ на shop_products (см. миграцию 0002)
        Index("ix_shop_prices_product", "shop", "product_id"),
    )


class PriceSnapshot(Base):
    """История: строка пишется только когда цена/наличие в точке изменились."""

    __tablename__ = "shop_price_snapshots"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    city: Mapped[str] = mapped_column(CITY, primary_key=True)
    store_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    price_minor: Mapped[int | None] = mapped_column(Integer)
    old_price_minor: Mapped[int | None] = mapped_column(Integer)
    in_stock: Mapped[bool] = mapped_column(Boolean)


class ScrapeRun(Base):
    __tablename__ = "shop_scrape_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    shop: Mapped[str] = mapped_column(SHOP)
    city: Mapped[str] = mapped_column(CITY)
    # Код точки, если прогон был по одной; при обходе нескольких — NULL, см. stores_total
    store_code: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    stores_total: Mapped[int] = mapped_column(Integer, default=0)
    categories_total: Mapped[int] = mapped_column(Integer, default=0)
    products_total: Mapped[int] = mapped_column(Integer, default=0)
    prices_total: Mapped[int] = mapped_column(Integer, default=0)
    details_total: Mapped[int] = mapped_column(Integer, default=0)
    errors_total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")

    __table_args__ = (Index("ix_shop_scrape_runs_shop_city", "shop", "city", "started_at"),)


Index("ix_shop_categories_parent", Category.shop, Category.parent_id)
Index("ix_shop_products_category", Product.shop, Product.category_id)
Index("ix_shop_products_brand", Product.brand)


class IngredientMatch(Base):
    """Ингредиент рецепта edalle (таблица ingredients backend) → товар сети.

    auto — подобран food-scrape match, approved/rejected — решение модератора в админке.
    Отклонённые match больше не предлагает.
    """

    __tablename__ = "shop_ingredient_matches"

    ingredient_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    score: Mapped[float] = mapped_column(Numeric(5, 3))
    status: Mapped[str] = mapped_column(String(16), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_shop_ingredient_matches_product", "shop", "product_id"),
        Index("ix_shop_ingredient_matches_status", "ingredient_id", "status"),
    )


class RecipeCost(Base):
    """Оценка стоимости рецепта (videos backend) в сети и городе — пересчитывает food-scrape recipe-costs."""

    __tablename__ = "shop_recipe_costs"

    video_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    city: Mapped[str] = mapped_column(CITY, primary_key=True)
    cost_minor: Mapped[int] = mapped_column(Integer)
    # Сколько ингредиентов с количеством и сколько из них удалось оценить: покрытие оценки
    ingredients_total: Mapped[int] = mapped_column(Integer)
    ingredients_priced: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[str | None] = mapped_column(Text)  # JSON: ингредиент, количество, товар, цена
    computed_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (Index("ix_shop_recipe_costs_city", "city", "shop", "cost_minor"),)


class QualityIssue(Base):
    """Найденная проверкой качества проблема данных — food-scrape check перезаписывает целиком."""

    __tablename__ = "shop_quality_issues"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    shop: Mapped[str] = mapped_column(SHOP)
    city: Mapped[str | None] = mapped_column(CITY)
    product_id: Mapped[str | None] = mapped_column(EXT_ID)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))  # warning | error
    message: Mapped[str] = mapped_column(String(1024))
    detected_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (Index("ix_shop_quality_issues_shop", "shop", "kind"),)
