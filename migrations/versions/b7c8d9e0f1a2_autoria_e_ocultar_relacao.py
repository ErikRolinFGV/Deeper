"""Papel da pessoa na menção e ocultação de relação incorreta.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mencao", sa.Column("papel", sa.String(20), nullable=True))
    op.add_column(
        "relacao",
        sa.Column("oculta", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("relacao", "oculta")
    op.drop_column("mencao", "papel")
