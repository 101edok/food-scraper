"""init schema: shop_* в общей БД recepter

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _shop() -> sa.Column:
    return sa.Column("shop", sa.String(16), primary_key=True)


def _product_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["shop", "product_id"], ["shop_products.shop", "shop_products.id"], ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "shop_categories",
        _shop(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("parent_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("level", sa.SmallInteger(), nullable=True),
        sa.Column("has_children", sa.Boolean(), nullable=False),
        sa.Column("is_adult", sa.Boolean(), nullable=False),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        **MYSQL,
    )
    op.create_index("ix_shop_categories_parent", "shop_categories", ["shop", "parent_id"])

    op.create_table(
        "shop_products",
        _shop(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("category_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("brand", sa.String(255), nullable=True),
        sa.Column("manufacturer", sa.String(255), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("composition", sa.Text(), nullable=True),
        sa.Column("kcal", sa.Numeric(7, 2), nullable=True),
        sa.Column("protein", sa.Numeric(6, 2), nullable=True),
        sa.Column("fat", sa.Numeric(6, 2), nullable=True),
        sa.Column("carbs", sa.Numeric(6, 2), nullable=True),
        sa.Column("weight_g", sa.Integer(), nullable=True),
        sa.Column("is_weighted", sa.Boolean(), nullable=False),
        sa.Column("is_adult", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("details_at", sa.DateTime(), nullable=True),
        **MYSQL,
    )
    op.create_index("ix_shop_products_category", "shop_products", ["shop", "category_id"])
    op.create_index("ix_shop_products_brand", "shop_products", ["brand"])

    op.create_table(
        "shop_product_images",
        _shop(),
        sa.Column("product_id", sa.String(32), primary_key=True),
        sa.Column("position", sa.SmallInteger(), primary_key=True),
        sa.Column("url", sa.String(1024), nullable=False),
        _product_fk(),
        **MYSQL,
    )

    op.create_table(
        "shop_product_attributes",
        _shop(),
        sa.Column("product_id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("value", sa.String(1024), nullable=False),
        _product_fk(),
        **MYSQL,
    )

    op.create_table(
        "shop_prices",
        _shop(),
        sa.Column("product_id", sa.String(32), primary_key=True),
        sa.Column("city", sa.String(32), primary_key=True),
        sa.Column("store_code", sa.String(32), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("old_price_minor", sa.Integer(), nullable=True),
        sa.Column("unit_price_minor", sa.Integer(), nullable=True),
        sa.Column("unit_label", sa.String(16), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        _product_fk(),
        **MYSQL,
    )
    op.create_index(
        "ix_shop_prices_city_price", "shop_prices", ["shop", "city", "in_stock", "price_minor"]
    )

    op.create_table(
        "shop_price_snapshots",
        _shop(),
        sa.Column("product_id", sa.String(32), primary_key=True),
        sa.Column("city", sa.String(32), primary_key=True),
        sa.Column("captured_at", sa.DateTime(), primary_key=True),
        sa.Column("store_code", sa.String(32), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("old_price_minor", sa.Integer(), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        **MYSQL,
    )

    op.create_table(
        "shop_scrape_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("shop", sa.String(16), nullable=False),
        sa.Column("city", sa.String(32), nullable=False),
        sa.Column("store_code", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("categories_total", sa.Integer(), nullable=False),
        sa.Column("products_total", sa.Integer(), nullable=False),
        sa.Column("details_total", sa.Integer(), nullable=False),
        sa.Column("errors_total", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        **MYSQL,
    )
    op.create_index(
        "ix_shop_scrape_runs_shop_city", "shop_scrape_runs", ["shop", "city", "started_at"]
    )


def downgrade() -> None:
    op.drop_table("shop_scrape_runs")
    op.drop_table("shop_price_snapshots")
    op.drop_table("shop_prices")
    op.drop_table("shop_product_attributes")
    op.drop_table("shop_product_images")
    op.drop_table("shop_products")
    op.drop_table("shop_categories")
