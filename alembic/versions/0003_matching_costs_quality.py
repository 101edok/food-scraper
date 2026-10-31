"""Фасовка товара, сопоставление ингредиентов, стоимость рецептов, проблемы качества

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    with op.batch_alter_table("shop_products") as batch:
        batch.add_column(sa.Column("net_quantity", sa.Numeric(10, 3), nullable=True))
        batch.add_column(sa.Column("net_unit", sa.String(4), nullable=True))

    op.create_table(
        "shop_ingredient_matches",
        sa.Column("ingredient_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("shop", sa.String(16), primary_key=True),
        sa.Column("product_id", sa.String(32), primary_key=True),
        sa.Column("score", sa.Numeric(5, 3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        **MYSQL,
    )
    op.create_index("ix_shop_ingredient_matches_product", "shop_ingredient_matches", ["shop", "product_id"])
    op.create_index("ix_shop_ingredient_matches_status", "shop_ingredient_matches", ["ingredient_id", "status"])

    op.create_table(
        "shop_recipe_costs",
        sa.Column("video_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("shop", sa.String(16), primary_key=True),
        sa.Column("city", sa.String(32), primary_key=True),
        sa.Column("cost_minor", sa.Integer(), nullable=False),
        sa.Column("ingredients_total", sa.Integer(), nullable=False),
        sa.Column("ingredients_priced", sa.Integer(), nullable=False),
        sa.Column("breakdown", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        **MYSQL,
    )
    op.create_index("ix_shop_recipe_costs_city", "shop_recipe_costs", ["city", "shop", "cost_minor"])

    op.create_table(
        "shop_quality_issues",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("shop", sa.String(16), nullable=False),
        sa.Column("city", sa.String(32), nullable=True),
        sa.Column("product_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        **MYSQL,
    )
    op.create_index("ix_shop_quality_issues_shop", "shop_quality_issues", ["shop", "kind"])


def downgrade() -> None:
    op.drop_table("shop_quality_issues")
    op.drop_table("shop_recipe_costs")
    op.drop_table("shop_ingredient_matches")
    with op.batch_alter_table("shop_products") as batch:
        batch.drop_column("net_unit")
        batch.drop_column("net_quantity")
