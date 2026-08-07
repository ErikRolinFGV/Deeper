"""Disposição salva do grafo por pessoa.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pessoa", sa.Column("grafo_layout", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("pessoa", "grafo_layout")
