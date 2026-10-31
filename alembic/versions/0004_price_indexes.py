"""Покрывающие индексы цен под сводки админки: по точке и по товару в городе

Без них «Магазины» и «Товары» каждый раз читают всю shop_prices (~530 тыс. строк) и группируют
через временную таблицу: 1–7 с на страницу. С индексом группировка идёт по индексу без чтения строк.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STORE = ["shop", "store_code", "in_stock", "old_price_minor", "seen_at"]
CITY_PRODUCT = ["shop", "city", "product_id", "price_minor", "old_price_minor", "in_stock", "seen_at", "store_code"]


def upgrade() -> None:
    # InnoDB строит вторичный индекс онлайн: запись цен скрейпером не блокируется
    op.create_index("ix_shop_prices_store", "shop_prices", STORE)
    op.create_index("ix_shop_prices_city_product", "shop_prices", CITY_PRODUCT)


def downgrade() -> None:
    op.drop_index("ix_shop_prices_city_product", table_name="shop_prices")
    op.drop_index("ix_shop_prices_store", table_name="shop_prices")
