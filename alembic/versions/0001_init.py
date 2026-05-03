"""init schema

Revision ID: 0001
Revises:
Create Date: 2026-05-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("slug", sa.String(255), nullable=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=True),
        sa.Column("has_children", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_adult", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])
    op.create_index(
        "ix_categories_parent_level", "categories", ["parent_id", "level"]
    )

    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "category_id",
            sa.BigInteger(),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("brand", sa.String(255), nullable=True),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("composition", sa.Text(), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("old_price_minor", sa.Integer(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False, server_default="RUB"),
        sa.Column("in_stock", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "scraped_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_products_category_id", "products", ["category_id"])
    op.create_index("ix_products_brand", "products", ["brand"])

    op.create_table(
        "product_images",
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("position", sa.SmallInteger(), primary_key=True),
        sa.Column("url", sa.String(1024), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "product_attributes",
        sa.Column(
            "product_id",
            sa.BigInteger(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("value", sa.String(1024), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "price_snapshots",
        sa.Column("product_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("captured_at", sa.DateTime(), primary_key=True),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("old_price_minor", sa.Integer(), nullable=True),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("city", sa.String(64), nullable=False),
        sa.Column("delivery_mode", sa.String(32), nullable=False),
        sa.Column("products_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def downgrade() -> None:
    op.drop_table("scrape_runs")
    op.drop_table("price_snapshots")
    op.drop_table("product_attributes")
    op.drop_table("product_images")
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_index("ix_products_category_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_categories_parent_level", table_name="categories")
    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_table("categories")
