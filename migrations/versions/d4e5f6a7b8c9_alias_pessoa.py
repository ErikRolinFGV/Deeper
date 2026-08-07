"""Tabela de apelidos (aliases) para fusão de entidades.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alias_pessoa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column(
            "pessoa_id",
            sa.Integer(),
            sa.ForeignKey("pessoa.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_alias_pessoa_slug", "alias_pessoa", ["slug"], unique=True)
    op.create_index("ix_alias_pessoa_pessoa_id", "alias_pessoa", ["pessoa_id"])


def downgrade() -> None:
    op.drop_index("ix_alias_pessoa_pessoa_id", table_name="alias_pessoa")
    op.drop_index("ix_alias_pessoa_slug", table_name="alias_pessoa")
    op.drop_table("alias_pessoa")
