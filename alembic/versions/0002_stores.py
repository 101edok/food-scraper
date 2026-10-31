"""Цены по точкам сети: справочник shop_stores, store_code в ключах цен и истории

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def _set_primary_key(table: str, columns: list[str]) -> None:
    if op.get_context().dialect.name == "mysql":
        # Одним ALTER: между DROP и ADD таблица не остаётся без ключа
        op.execute(f"ALTER TABLE {table} DROP PRIMARY KEY, ADD PRIMARY KEY ({', '.join(columns)})")
    else:
        # SQLite (проверка миграций локально): batch пересоздаёт таблицу с новым ключом
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.create_primary_key(f"pk_{table}", columns)


def upgrade() -> None:
    op.create_table(
        "shop_stores",
        sa.Column("shop", sa.String(16), primary_key=True),
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("city", sa.String(32), nullable=False),
        sa.Column("store_type", sa.String(16), nullable=False),
        sa.Column("address", sa.String(512), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        **MYSQL,
    )
    op.create_index("ix_shop_stores_city", "shop_stores", ["shop", "city", "is_active"])

    # Внешний ключ на shop_products опирается на индекс с префиксом (shop, product_id) —
    # даём ему отдельный, чтобы смена первичного ключа его не задела.
    op.create_index("ix_shop_prices_product", "shop_prices", ["shop", "product_id"])
    _set_primary_key("shop_prices", ["shop", "product_id", "city", "store_code"])
    _set_primary_key("shop_price_snapshots", ["shop", "product_id", "city", "store_code", "captured_at"])

    with op.batch_alter_table("shop_scrape_runs") as batch:
        batch.add_column(sa.Column("stores_total", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("prices_total", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    # Несколько цен одного товара в городе (по точкам) в старый ключ «одна на город» без потерь не свернуть
    raise NotImplementedError("0002 → 0001 не поддерживается: удалите лишние цены по точкам вручную")
