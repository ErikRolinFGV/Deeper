"""Checagem de ambiente: valida .env, Postgres, Redis e chaves de API.

Rode a partir da raiz do projeto, com o venv ativado:

    python scripts\\checar_ambiente.py

Cada item imprime [OK] ou [FALHOU] com instrução de correção.
"""

import sys
from pathlib import Path

# Permite importar `app` rodando de dentro de scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

OK = "[OK]    "
FALHOU = "[FALHOU]"
resultados: list[bool] = []


def checar(nome: str, fn) -> None:
    try:
        detalhe = fn()
        print(f"{OK} {nome}" + (f" — {detalhe}" if detalhe else ""))
        resultados.append(True)
    except Exception as exc:
        print(f"{FALHOU} {nome} — {exc}")
        resultados.append(False)


# ---------- 1. .env ----------

def checar_env():
    from app.core.config import settings

    if "SUA_SENHA" in settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL ainda tem o placeholder SUA_SENHA. "
            "Edite o .env com a senha real do Postgres."
        )
    return "variáveis carregadas"


# ---------- 2. Postgres ----------

def checar_postgres():
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        versao = conn.execute(text("SELECT version()")).scalar()
    return versao.split(",")[0] if versao else "conectado"


# ---------- 3. Redis ----------

def checar_redis():
    import redis

    from app.core.config import settings

    r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    r.ping()
    return "PONG recebido"


# ---------- 4. Anthropic ----------

def checar_anthropic():
    from app.core.config import settings

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "oi"}],
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.json().get('error', {}).get('message', resp.text[:200])}")
    return "chave válida, crédito ativo"


# ---------- 5. SerpAPI ----------

def checar_serpapi():
    from app.core.config import settings

    resp = httpx.get(
        "https://serpapi.com/account",
        params={"api_key": settings.SERPAPI_KEY},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    d = resp.json()
    return f"plano {d.get('plan_name')}, {d.get('total_searches_left')} buscas restantes no mês"


# ---------- 6. Apify ----------

def checar_apify():
    from app.core.config import settings

    resp = httpx.get(
        "https://api.apify.com/v2/users/me",
        headers={"Authorization": f"Bearer {settings.APIFY_TOKEN}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    d = resp.json()["data"]
    return f"usuário {d.get('username')}"


if __name__ == "__main__":
    print("=== Checagem de ambiente — FSB Executive Intelligence ===\n")
    checar("1. Arquivo .env", checar_env)
    checar("2. Postgres", checar_postgres)
    checar("3. Redis", checar_redis)
    checar("4. Chave Anthropic", checar_anthropic)
    checar("5. Chave SerpAPI", checar_serpapi)
    checar("6. Token Apify", checar_apify)

    print()
    if all(resultados):
        print("Tudo pronto! Próximos comandos:")
        print("  alembic upgrade head       (cria as tabelas)")
        print("  uvicorn app.main:app --reload   (sobe a API)")
    else:
        print(f"{resultados.count(False)} item(ns) pendente(s). Corrija e rode de novo.")
