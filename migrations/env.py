"""Ambiente do Alembic — conectado ao Base e ao DATABASE_URL do projeto.

Para gerar uma nova migration automaticamente (detectando mudanças nos models):
    alembic revision --autogenerate -m "descricao da mudanca"

Para aplicar todas as migrations pendentes:
    alembic upgrade head

Para voltar uma migration:
    alembic downgrade -1
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Importa configurações e modelos do projeto.
# Tem que importar app.models para o autogenerate "ver" todas as tabelas.
from app.core.config import settings
from app.core.db import Base
from app import models  # noqa: F401 — registra os modelos no metadata

# Config do Alembic (lê alembic.ini)
config = context.config

# Injeta a URL do banco vinda do .env via Settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata usado pelo autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Executa migrations em modo offline (gera SQL sem conectar)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa migrations conectado ao banco real."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
