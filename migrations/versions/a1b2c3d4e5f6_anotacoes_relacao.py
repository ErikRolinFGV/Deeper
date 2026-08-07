"""Anotação humana nas relações: rotulo, nota, anotado_em.

Revision ID: a1b2c3d4e5f6
Revises: 8d4f21c7be02
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "8d4f21c7be02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relacao", sa.Column("rotulo", sa.String(80), nullable=True))
    op.add_column("relacao", sa.Column("nota", sa.Text(), nullable=True))
    op.add_column(
        "relacao", sa.Column("anotado_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("relacao", "anotado_em")
    op.drop_column("relacao", "nota")
    op.drop_column("relacao", "rotulo")
