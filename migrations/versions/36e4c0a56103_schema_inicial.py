"""schema inicial

Revision ID: 36e4c0a56103
Revises:
Create Date: 2026-07-07 17:04:48.763726

Cria todas as tabelas do MVP: pessoa, empresa, cargo, relacao, evento,
evento_participante, mencao e job_coleta.

Gerada por autogenerate e validada com `alembic upgrade head`.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "36e4c0a56103"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "empresa",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("setor", sa.String(length=160), nullable=True),
        sa.Column("cnpj", sa.String(length=20), nullable=True),
        sa.Column("ticker_b3", sa.String(length=10), nullable=True),
        sa.Column("site", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_empresa_cnpj"), "empresa", ["cnpj"], unique=False)
    op.create_index(op.f("ix_empresa_nome"), "empresa", ["nome"], unique=False)
    op.create_index(op.f("ix_empresa_slug"), "empresa", ["slug"], unique=True)
    op.create_table(
        "evento",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("tipo", sa.String(length=80), nullable=True),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("local", sa.String(length=255), nullable=True),
        sa.Column("fonte_url", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evento_nome"), "evento", ["nome"], unique=False)
    op.create_table(
        "pessoa",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("nome_completo", sa.String(length=255), nullable=True),
        sa.Column("cargo_atual", sa.String(length=255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.String(length=512), nullable=True),
        sa.Column("foto_url", sa.String(length=512), nullable=True),
        sa.Column("briefing", sa.Text(), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pessoa_nome"), "pessoa", ["nome"], unique=False)
    op.create_index(op.f("ix_pessoa_slug"), "pessoa", ["slug"], unique=True)
    op.create_table(
        "cargo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pessoa_id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("funcao", sa.String(length=255), nullable=False),
        sa.Column("inicio", sa.Date(), nullable=True),
        sa.Column("fim", sa.Date(), nullable=True),
        sa.Column("eh_atual", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresa.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pessoa_id"], ["pessoa.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cargo_empresa_id"), "cargo", ["empresa_id"], unique=False)
    op.create_index(op.f("ix_cargo_pessoa_id"), "cargo", ["pessoa_id"], unique=False)
    op.create_table(
        "evento_participante",
        sa.Column("evento_id", sa.Integer(), nullable=False),
        sa.Column("pessoa_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["evento_id"], ["evento.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pessoa_id"], ["pessoa.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evento_id", "pessoa_id"),
    )
    op.create_table(
        "job_coleta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pessoa_id", sa.Integer(), nullable=True),
        sa.Column("termo_busca", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column(
            "iniciado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pessoa_id"], ["pessoa.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_coleta_termo_busca"), "job_coleta", ["termo_busca"], unique=False
    )
    op.create_table(
        "mencao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pessoa_id", sa.Integer(), nullable=False),
        sa.Column("fonte", sa.String(length=160), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("titulo", sa.String(length=512), nullable=True),
        sa.Column("texto", sa.Text(), nullable=True),
        sa.Column("data_publicacao", sa.Date(), nullable=True),
        sa.Column("sentimento", sa.Float(), nullable=True),
        sa.Column("temas", sa.String(length=512), nullable=True),
        sa.Column(
            "coletado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pessoa_id"], ["pessoa.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mencao_pessoa_id"), "mencao", ["pessoa_id"], unique=False)
    op.create_table(
        "relacao",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pessoa_a_id", sa.Integer(), nullable=False),
        sa.Column("pessoa_b_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.Column("peso", sa.Integer(), nullable=False),
        sa.Column("evidencias", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["pessoa_a_id"], ["pessoa.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pessoa_b_id"], ["pessoa.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pessoa_a_id", "pessoa_b_id", "tipo", name="uq_relacao_tripla"
        ),
    )
    op.create_index(
        op.f("ix_relacao_pessoa_a_id"), "relacao", ["pessoa_a_id"], unique=False
    )
    op.create_index(
        op.f("ix_relacao_pessoa_b_id"), "relacao", ["pessoa_b_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_relacao_pessoa_b_id"), table_name="relacao")
    op.drop_index(op.f("ix_relacao_pessoa_a_id"), table_name="relacao")
    op.drop_table("relacao")
    op.drop_index(op.f("ix_mencao_pessoa_id"), table_name="mencao")
    op.drop_table("mencao")
    op.drop_index(op.f("ix_job_coleta_termo_busca"), table_name="job_coleta")
    op.drop_table("job_coleta")
    op.drop_table("evento_participante")
    op.drop_index(op.f("ix_cargo_pessoa_id"), table_name="cargo")
    op.drop_index(op.f("ix_cargo_empresa_id"), table_name="cargo")
    op.drop_table("cargo")
    op.drop_index(op.f("ix_pessoa_slug"), table_name="pessoa")
    op.drop_index(op.f("ix_pessoa_nome"), table_name="pessoa")
    op.drop_table("pessoa")
    op.drop_index(op.f("ix_evento_nome"), table_name="evento")
    op.drop_table("evento")
    op.drop_index(op.f("ix_empresa_slug"), table_name="empresa")
    op.drop_index(op.f("ix_empresa_nome"), table_name="empresa")
    op.drop_index(op.f("ix_empresa_cnpj"), table_name="empresa")
    op.drop_table("empresa")
