# Migrations — Alembic

Esta pasta contém o versionamento do schema do banco de dados, gerenciado pelo Alembic.

## Quando você tiver o Postgres rodando e o .env preenchido

**Gerar a primeira migration** (Alembic detecta automaticamente todos os modelos em `app/models/`):

```bash
alembic revision --autogenerate -m "schema inicial"
```

Isso vai criar um arquivo em `migrations/versions/XXXX_schema_inicial.py` com `CREATE TABLE` para cada modelo. Revise o arquivo antes de aplicar.

**Aplicar a migration ao banco:**

```bash
alembic upgrade head
```

**Verificar status:**

```bash
alembic current
alembic history
```

**Reverter uma migration:**

```bash
alembic downgrade -1
```

## Extensão pgvector

O modelo já planeja usar pgvector futuramente. Antes de aplicar migrations que dependam dele, habilite a extensão no Postgres:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Importante

- `env.py` carrega a `DATABASE_URL` automaticamente do `.env` via `app/core/config.py`. Você não precisa duplicar credenciais no `alembic.ini`.
- Os arquivos em `versions/` devem ser versionados no Git.
