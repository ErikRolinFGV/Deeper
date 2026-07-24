"""Testes de integração da API usando SQLite em memória.

Não exigem Postgres nem Redis: o banco é substituído via dependency override
e o enfileiramento RQ é neutralizado com monkeypatch.
"""

import os
from datetime import date, datetime, timedelta, timezone

# Permite rodar sem .env configurado (CI, sandbox). Valores reais têm precedência.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("CRUNCHBASE_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import busca as busca_api
from app.api.busca import gerar_slug
from app.core.db import Base, get_db
from app.main import app
from app.models.cargo import Cargo
from app.models.empresa import Empresa
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def preparar(monkeypatch):
    """Cria o schema, desliga o RQ e injeta o banco de teste."""
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_api, "enfileirar_coleta", lambda job_id: None)
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    session = TestingSession()
    yield session
    session.close()


# ---------- slug ----------


def test_gerar_slug_normaliza_acentos_e_espacos():
    assert gerar_slug("Eduardo Bartolomeo") == "eduardo-bartolomeo"
    assert gerar_slug("  João  Camargo! ") == "joao-camargo"
    assert gerar_slug("Luiza Trajano (Magalu)") == "luiza-trajano-magalu"


# ---------- /busca ----------


def test_busca_livre_de_pessoa_nova_e_rejeitada(client):
    """Pessoa nova sem linkedin_url confirmada: 422 (seleção obrigatória)."""
    resp = client.post("/busca", json={"nome": "Eduardo Bartolomeo"})
    assert resp.status_code == 422
    assert "sugest" in resp.json()["detail"].lower()


def test_busca_cria_job_com_linkedin_confirmado(client):
    resp = client.post(
        "/busca",
        json={"nome": "Eduardo Bartolomeo",
              "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo"},
    )
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["cache_hit"] is False
    assert corpo["job_id"] is not None

    job = client.get(f"/job/{corpo['job_id']}").json()
    assert job["status"] == "queued"
    assert job["termo_busca"] == "Eduardo Bartolomeo"


def test_busca_retorna_cache_hit_para_perfil_fresco(client, db):
    pessoa = Pessoa(
        slug="eduardo-bartolomeo",
        nome="Eduardo Bartolomeo",
        atualizado_em=datetime.now(timezone.utc),
    )
    db.add(pessoa)
    db.commit()

    resp = client.post("/busca", json={"nome": "Eduardo Bartolomeo"})
    corpo = resp.json()
    assert corpo["cache_hit"] is True
    assert corpo["pessoa_id"] == pessoa.id
    assert corpo["job_id"] is None


def test_busca_recoleta_perfil_velho(client, db):
    pessoa = Pessoa(
        slug="eduardo-bartolomeo",
        nome="Eduardo Bartolomeo",
        atualizado_em=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db.add(pessoa)
    db.commit()

    corpo = client.post("/busca", json={"nome": "Eduardo Bartolomeo"}).json()
    assert corpo["cache_hit"] is False
    assert corpo["job_id"] is not None
    assert corpo["pessoa_id"] == pessoa.id  # job vinculado à pessoa existente


def test_busca_force_refresh_ignora_cache(client, db):
    db.add(
        Pessoa(
            slug="eduardo-bartolomeo",
            nome="Eduardo Bartolomeo",
            atualizado_em=datetime.now(timezone.utc),
        )
    )
    db.commit()

    corpo = client.post(
        "/busca", json={"nome": "Eduardo Bartolomeo", "force_refresh": True}
    ).json()
    assert corpo["cache_hit"] is False
    assert corpo["job_id"] is not None


# ---------- /perfil ----------


def test_perfil_404_para_pessoa_inexistente(client):
    assert client.get("/perfil/999").status_code == 404


def test_perfil_retorna_dossie_completo(client, db):
    vale = Empresa(slug="vale", nome="Vale S.A.", setor="Mineração")
    pessoa = Pessoa(
        slug="eduardo-bartolomeo",
        nome="Eduardo Bartolomeo",
        cargo_atual="CEO da Vale",
        briefing="Briefing executivo de teste.",
    )
    db.add_all([vale, pessoa])
    db.flush()
    db.add_all(
        [
            Cargo(
                pessoa_id=pessoa.id,
                empresa_id=vale.id,
                funcao="CEO",
                inicio=date(2019, 4, 29),
                eh_atual=True,
            ),
            Mencao(
                pessoa_id=pessoa.id,
                fonte="valor",
                url="https://valor.globo.com/exemplo",
                titulo="Vale anuncia investimento",
                data_publicacao=date(2026, 6, 1),
                sentimento=0.4,
                temas="ESG,mineração",
            ),
        ]
    )
    db.commit()

    corpo = client.get(f"/perfil/{pessoa.id}").json()
    assert corpo["pessoa"]["nome"] == "Eduardo Bartolomeo"
    assert corpo["briefing"] == "Briefing executivo de teste."
    assert corpo["cargos"][0]["empresa"] == "Vale S.A."
    assert corpo["cargos"][0]["eh_atual"] is True
    assert corpo["mencoes"][0]["fonte"] == "valor"
    assert corpo["mencoes"][0]["temas"] == ["ESG", "mineração"]


# ---------- /grafo ----------


def test_grafo_404_para_pessoa_inexistente(client):
    assert client.get("/grafo/999").status_code == 404


def test_grafo_expande_rede_e_filtra_por_peso(client, db):
    a = Pessoa(slug="pessoa-a", nome="Pessoa A")
    b = Pessoa(slug="pessoa-b", nome="Pessoa B")
    c = Pessoa(slug="pessoa-c", nome="Pessoa C")
    db.add_all([a, b, c])
    db.flush()
    db.add_all(
        [
            Relacao(pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado", peso=3, evidencias=[]),
            Relacao(pessoa_a_id=b.id, pessoa_b_id=c.id, tipo="co_evento", peso=1, evidencias=[]),
        ]
    )
    db.commit()

    # Profundidade 2, peso >= 1: alcança os três nós.
    corpo = client.get(f"/grafo/{a.id}?profundidade=2&peso_minimo=1").json()
    assert {n["id"] for n in corpo["nodes"]} == {a.id, b.id, c.id}
    assert len(corpo["edges"]) == 2
    raiz = next(n for n in corpo["nodes"] if n["raiz"])
    assert raiz["id"] == a.id
    assert raiz["label"] == "Pessoa A"

    # peso >= 2: aresta fraca some, C fica de fora.
    corpo = client.get(f"/grafo/{a.id}?profundidade=2&peso_minimo=2").json()
    assert {n["id"] for n in corpo["nodes"]} == {a.id, b.id}
    assert len(corpo["edges"]) == 1


def test_grafo_pessoa_isolada_retorna_so_a_raiz(client, db):
    p = Pessoa(slug="isolado", nome="Isolado")
    db.add(p)
    db.commit()

    corpo = client.get(f"/grafo/{p.id}").json()
    assert corpo["nodes"] == [
        {"id": p.id, "label": "Isolado", "cargo_atual": None, "foto_url": None, "raiz": True}
    ]
    assert corpo["edges"] == []
