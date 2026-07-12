"""Campos LinkedIn em pessoa: localizacao, linkedin_dados, linkedin_coletado_em.

Revision ID: 8d4f21c7be02
Revises: 36e4c0a56103
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

revision = "8d4f21c7be02"
down_revision = "36e4c0a56103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pessoa", sa.Column("localizacao", sa.String(255), nullable=True))
    op.add_column("pessoa", sa.Column("linkedin_dados", sa.JSON(), nullable=True))
    op.add_column(
        "pessoa",
        sa.Column("linkedin_coletado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pessoa", "linkedin_coletado_em")
    op.drop_column("pessoa", "linkedin_dados")
    op.drop_column("pessoa", "localizacao")
