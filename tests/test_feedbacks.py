"""Testes dos feedbacks de usuário: sugestões, busca com URL e evidências no grafo."""

import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import busca as busca_api
from app.api import sugestoes as sugestoes_api
from app.core.db import Base, get_db
from app.main import app
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_api, "enfileirar_coleta", lambda job_id: None)
    monkeypatch.setattr(sugestoes_api, "sugerir_perfis_linkedin", lambda q, limite=5: [])
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    s = TestingSession()
    yield s
    s.close()


# ---------- /sugestoes ----------


def test_sugestoes_locais_por_nome_e_cargo(client, db):
    db.add_all(
        [
            Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo",
                   cargo_atual="Conselheiro — Boston Metal", briefing="ok"),
            Pessoa(slug="gustavo-pimenta", nome="Gustavo Pimenta", cargo_atual="CEO da Vale"),
            Pessoa(slug="outra-pessoa", nome="Outra Pessoa"),
        ]
    )
    db.commit()

    corpo = client.get("/sugestoes?q=eduardo").json()
    assert [s["nome"] for s in corpo["locais"]] == ["Eduardo Bartolomeo"]
    assert corpo["locais"][0]["tem_briefing"] is True
    assert corpo["linkedin"] == []

    # Busca por cargo/empresa também encontra
    corpo = client.get("/sugestoes?q=vale").json()
    assert [s["nome"] for s in corpo["locais"]] == ["Gustavo Pimenta"]


def test_sugestoes_externas_sob_demanda(client, monkeypatch):
    chamadas = []

    def falso(q, limite=5):
        chamadas.append(q)
        return [{"nome": "Eduardo Bartolomeo", "headline": "Board Member — Boston Metal",
                 "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo"}]

    monkeypatch.setattr(sugestoes_api, "sugerir_perfis_linkedin", falso)

    # Sem externas: SerpAPI não é chamado (economia de cota)
    client.get("/sugestoes?q=eduardo+vale")
    assert chamadas == []

    corpo = client.get("/sugestoes?q=eduardo+vale&externas=true").json()
    assert len(chamadas) == 1
    assert corpo["linkedin"][0]["linkedin_url"].endswith("/in/eduardobartolomeo")


def test_sugestoes_exige_minimo_de_caracteres(client):
    assert client.get("/sugestoes?q=a").status_code == 422


# ---------- /busca com linkedin_url confirmada ----------


def test_busca_com_linkedin_url_cria_pessoa_com_url(client, db):
    corpo = client.post(
        "/busca",
        json={"nome": "Eduardo Bartolomeo",
              "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo"},
    ).json()
    assert corpo["job_id"] is not None

    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.linkedin_url == "https://br.linkedin.com/in/eduardobartolomeo"


def test_busca_trocar_linkedin_url_zera_dossie_do_homonimo(client, db):
    """Trocar o perfil = outra pessoa física: TODO o dossiê antigo é apagado."""
    from app.models.cargo import Cargo
    from app.models.empresa import Empresa
    from app.models.mencao import Mencao

    homonimo = Pessoa(
        slug="renato-costa", nome="Renato Costa",
        linkedin_url="https://www.linkedin.com/in/renato-costa-cio",
        linkedin_dados={"basic_info": {"fullname": "Renato Costa (CIO)"}},
        linkedin_coletado_em=datetime.now(timezone.utc),
        nome_completo="Renato Costa CIO", bio="Bio do CIO errado",
        foto_url="https://x/cio.jpg", cargo_atual="CIO — Odontoprev",
        briefing="Briefing do homônimo errado.",
    )
    outra = Pessoa(slug="outra-pessoa", nome="Outra Pessoa")
    empresa = Empresa(slug="odontoprev", nome="Odontoprev")
    db.add_all([homonimo, outra, empresa])
    db.flush()
    db.add_all(
        [
            Cargo(pessoa_id=homonimo.id, empresa_id=empresa.id, funcao="CIO", eh_atual=True),
            Mencao(pessoa_id=homonimo.id, fonte="valor", url="https://valor.globo.com/cio",
                   titulo="Matéria do CIO", sentimento=0.2),
            Relacao(pessoa_a_id=homonimo.id, pessoa_b_id=outra.id,
                    tipo="co_mencionado", peso=1, evidencias=[]),
        ]
    )
    db.commit()

    client.post(
        "/busca",
        json={"nome": "Renato Costa",
              "linkedin_url": "https://www.linkedin.com/in/renato-costa-friboi"},
    )

    db.expire_all()
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "renato-costa"))
    assert pessoa.linkedin_url == "https://www.linkedin.com/in/renato-costa-friboi"
    # Identidade antiga completamente apagada
    assert pessoa.linkedin_dados is None
    assert pessoa.bio is None
    assert pessoa.foto_url is None
    assert pessoa.cargo_atual is None
    assert pessoa.briefing is None
    assert db.scalar(select(Cargo).where(Cargo.pessoa_id == pessoa.id)) is None
    assert db.scalar(select(Mencao).where(Mencao.pessoa_id == pessoa.id)) is None
    assert db.scalar(select(Relacao)) is None


def test_busca_sem_linkedin_url_so_para_pessoa_existente(client, db):
    """Pessoa já no banco pode ser re-buscada sem URL; nova é rejeitada."""
    resp = client.post("/busca", json={"nome": "Fulano Qualquer"})
    assert resp.status_code == 422  # pessoa nova sem seleção: bloqueada

    db.add(Pessoa(slug="fulano-qualquer", nome="Fulano Qualquer"))
    db.commit()
    corpo = client.post(
        "/busca", json={"nome": "Fulano Qualquer", "force_refresh": True}
    ).json()
    assert corpo["job_id"] is not None  # existente: re-coleta liberada


# ---------- /acervo ----------


def test_acervo_lista_pessoas_com_flag_de_briefing(client, db):
    db.add_all(
        [
            Pessoa(slug="com-dossie", nome="Com Dossiê", cargo_atual="CEO",
                   briefing="Briefing pronto."),
            Pessoa(slug="sem-dossie", nome="Sem Dossiê"),
        ]
    )
    db.commit()

    corpo = client.get("/acervo").json()
    assert corpo["total"] == 2
    por_nome = {p["nome"]: p for p in corpo["pessoas"]}
    assert por_nome["Com Dossiê"]["tem_briefing"] is True
    assert por_nome["Sem Dossiê"]["tem_briefing"] is False
    assert por_nome["Com Dossiê"]["cargo_atual"] == "CEO"


def test_acervo_vazio(client):
    corpo = client.get("/acervo").json()
    assert corpo == {"total": 0, "pessoas": []}


# ---------- /grafo com evidências ----------


def test_grafo_expoe_evidencias_com_contexto(client, db):
    a = Pessoa(slug="a", nome="Pessoa A")
    b = Pessoa(slug="b", nome="Pessoa B")
    db.add_all([a, b])
    db.flush()
    db.add(
        Relacao(
            pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado", peso=2,
            evidencias=[
                {"mencao_url": "https://valor.globo.com/x", "titulo": "Matéria real",
                 "contexto": "direta"},
                {"mencao_url": "https://exame.com/50-mais-ricos",
                 "titulo": "50 mais ricos do Brasil", "contexto": "lista"},
            ],
        )
    )
    db.commit()

    corpo = client.get(f"/grafo/{a.id}?profundidade=1&peso_minimo=1").json()
    assert len(corpo["edges"]) == 1
    evs = corpo["edges"][0]["evidencias"]
    assert len(evs) == 2
    assert evs[0]["contexto"] == "direta"
    assert evs[1]["contexto"] == "lista"
    assert evs[1]["titulo"] == "50 mais ricos do Brasil"


def test_grafo_limita_evidencias_por_aresta(client, db):
    from app.services.graph.queries import MAX_EVIDENCIAS

    a = Pessoa(slug="a", nome="Pessoa A")
    b = Pessoa(slug="b", nome="Pessoa B")
    db.add_all([a, b])
    db.flush()
    db.add(
        Relacao(
            pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado", peso=20,
            evidencias=[{"mencao_url": f"https://x.com/{i}", "titulo": f"M{i}"} for i in range(20)],
        )
    )
    db.commit()

    corpo = client.get(f"/grafo/{a.id}?profundidade=1&peso_minimo=1").json()
    evs = corpo["edges"][0]["evidencias"]
    assert len(evs) == MAX_EVIDENCIAS
    assert evs[-1]["titulo"] == "M19"  # mantém as mais recentes
