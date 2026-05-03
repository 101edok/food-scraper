from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(512))
    level: Mapped[int | None] = mapped_column(SmallInteger)
    has_children: Mapped[bool] = mapped_column(Boolean, default=False)
    is_adult: Mapped[bool] = mapped_column(Boolean, default=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    url: Mapped[str | None] = mapped_column(String(1024))


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(1024))
    brand: Mapped[str | None] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    composition: Mapped[str | None] = mapped_column(Text)
    price_minor: Mapped[int | None] = mapped_column(Integer)
    old_price_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(CHAR(3), default="RUB")
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    images: Mapped[list["ProductImage"]] = relationship(
        cascade="all, delete-orphan", lazy="raise"
    )
    attributes: Mapped[list["ProductAttribute"]] = relationship(
        cascade="all, delete-orphan", lazy="raise"
    )


class ProductImage(Base):
    __tablename__ = "product_images"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024))


class ProductAttribute(Base):
    __tablename__ = "product_attributes"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024))


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    price_minor: Mapped[int | None] = mapped_column(Integer)
    old_price_minor: Mapped[int | None] = mapped_column(Integer)
    in_stock: Mapped[bool] = mapped_column(Boolean)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    city: Mapped[str] = mapped_column(String(64))
    delivery_mode: Mapped[str] = mapped_column(String(32))
    products_total: Mapped[int] = mapped_column(Integer, default=0)
    errors_total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="running")


Index("ix_categories_parent_level", Category.parent_id, Category.level)
