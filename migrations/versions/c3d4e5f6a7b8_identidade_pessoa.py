"""Rastro de identidade: contexto_origem e identidade_confirmada.

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pessoa", sa.Column("contexto_origem", sa.String(255), nullable=True))
    op.add_column(
        "pessoa",
        sa.Column(
            "identidade_confirmada",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Quem já tem perfil do LinkedIn no banco foi escolhido por um humano
    # (a seleção passou a ser obrigatória) — marca como confirmado.
    op.execute(
        "UPDATE pessoa SET identidade_confirmada = true WHERE linkedin_url IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("pessoa", "identidade_confirmada")
    op.drop_column("pessoa", "contexto_origem")
