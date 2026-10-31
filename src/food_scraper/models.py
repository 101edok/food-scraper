"""Схема скрейпера магазинов. БД общая с edalle backend (recepter) — все таблицы с префиксом shop_.

Ключи магазинных сущностей — (shop, id): id внешний, уникален только внутри магазина.
Цена всегда привязана к городу: в разных городах один товар стоит по-разному.
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


class Price(Base):
    """Текущая цена товара в городе — последняя увиденная."""

    __tablename__ = "shop_prices"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    city: Mapped[str] = mapped_column(CITY, primary_key=True)
    store_code: Mapped[str] = mapped_column(String(32))
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
    )


class PriceSnapshot(Base):
    """История: строка пишется только когда цена/наличие в городе изменились."""

    __tablename__ = "shop_price_snapshots"

    shop: Mapped[str] = mapped_column(SHOP, primary_key=True)
    product_id: Mapped[str] = mapped_column(EXT_ID, primary_key=True)
    city: Mapped[str] = mapped_column(CITY, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    store_code: Mapped[str] = mapped_column(String(32))
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
    store_code: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    categories_total: Mapped[int] = mapped_column(Integer, default=0)
    products_total: Mapped[int] = mapped_column(Integer, default=0)
    details_total: Mapped[int] = mapped_column(Integer, default=0)
    errors_total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")

    __table_args__ = (Index("ix_shop_scrape_runs_shop_city", "shop", "city", "started_at"),)


Index("ix_shop_categories_parent", Category.shop, Category.parent_id)
Index("ix_shop_products_category", Product.shop, Product.category_id)
Index("ix_shop_products_brand", Product.brand)
