"""Add an optional Telegram user id on masters."""

import sqlalchemy as sa
from alembic import op

revision = "0002_master_telegram"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("masters", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_masters_telegram_user_id",
        "masters",
        ["telegram_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_masters_telegram_user_id", "masters", type_="unique")
    op.drop_column("masters", "telegram_user_id")
