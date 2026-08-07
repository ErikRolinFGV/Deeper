"""Fusão de entidades: o analista aponta que dois registros são a mesma pessoa.

Caso real: a imprensa cita "Dani Braun", o LinkedIn diz "Daniela Braun".
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import busca as busca_api
from app.core.db import Base, get_db
from app.main import app
from app.models.alias import AliasPessoa
from app.models.cargo import Cargo
from app.models.empresa import Empresa
from app.models.evento import Evento, evento_participante
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.manutencao import localizar_por_slug

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


@pytest.fixture()
def cenario(db):
    """Daniela (dossiê completo) + Dani (nó solto no grafo de um terceiro)."""
    daniela = Pessoa(slug="daniela-braun", nome="Daniela Braun",
                     linkedin_url="https://l/in/danibraun", identidade_confirmada=True,
                     briefing="Dossiê da Daniela.", cargo_atual="Jornalista")
    dani = Pessoa(slug="dani-braun", nome="Dani Braun",
                  contexto_origem="repórter de tecnologia")
    terceiro = Pessoa(slug="terceiro", nome="Terceiro Executivo", briefing="x")
    empresa = Empresa(slug="valor", nome="Valor Econômico")
    db.add_all([daniela, dani, terceiro, empresa])
    db.flush()
    db.add_all([
        # a rede do nó solto
        Relacao(pessoa_a_id=dani.id, pessoa_b_id=terceiro.id, tipo="co_mencionado",
                peso=2, evidencias=[{"titulo": "M1"}, {"titulo": "M2"}]),
        Mencao(pessoa_id=dani.id, fonte="valor", url="https://v/dani", titulo="Da Dani"),
        Cargo(pessoa_id=dani.id, empresa_id=empresa.id, funcao="Repórter",
              inicio=date(2020, 1, 1)),
    ])
    db.commit()
    return {"daniela": daniela.id, "dani": dani.id, "terceiro": terceiro.id}


def test_fusao_une_as_redes(client, db, cenario):
    corpo = client.post(
        f"/perfil/{cenario['daniela']}/fundir",
        json={"duplicada_id": cenario["dani"]},
    ).json()
    assert corpo["fundido"] is True
    assert corpo["absorvida"] == "Dani Braun"
    assert corpo["relacoes"] == 1 and corpo["mencoes"] == 1 and corpo["cargos"] == 1

    db.expire_all()
    assert db.get(Pessoa, cenario["dani"]) is None          # registro some
    daniela = db.get(Pessoa, cenario["daniela"])
    assert daniela.briefing == "Dossiê da Daniela."          # dossiê preservado

    # a conexão que era do nó agora é da Daniela
    rel = db.scalar(select(Relacao))
    assert {rel.pessoa_a_id, rel.pessoa_b_id} == {cenario["daniela"], cenario["terceiro"]}
    assert rel.peso == 2
    # menções e cargos migraram
    assert db.scalar(select(Mencao)).pessoa_id == cenario["daniela"]
    assert db.scalar(select(Cargo)).pessoa_id == cenario["daniela"]


def test_fusao_registra_apelido_e_futuras_coletas_reconhecem(client, db, cenario):
    client.post(f"/perfil/{cenario['daniela']}/fundir",
                json={"duplicada_id": cenario["dani"]})

    db.expire_all()
    alias = db.scalar(select(AliasPessoa).where(AliasPessoa.slug == "dani-braun"))
    assert alias is not None and alias.pessoa_id == cenario["daniela"]

    # o coletor, ao ver "Dani Braun" numa matéria, cai na Daniela
    achada = localizar_por_slug(db, "dani-braun")
    assert achada.id == cenario["daniela"]


def test_busca_por_apelido_abre_o_dossie_certo(client, db, cenario):
    client.post(f"/perfil/{cenario['daniela']}/fundir",
                json={"duplicada_id": cenario["dani"]})

    # buscar pelo nome antigo não cria registro novo nem exige LinkedIn
    corpo = client.post("/busca", json={"nome": "Dani Braun"}).json()
    assert corpo["pessoa_id"] == cenario["daniela"]


def test_fusao_soma_arestas_repetidas(client, db):
    """Se os dois já se ligavam à mesma pessoa, os pesos somam."""
    a = Pessoa(slug="a", nome="A", briefing="x")
    b = Pessoa(slug="b", nome="B")
    c = Pessoa(slug="c", nome="C")
    db.add_all([a, b, c])
    db.flush()
    db.add_all([
        Relacao(pessoa_a_id=min(a.id, c.id), pessoa_b_id=max(a.id, c.id),
                tipo="co_mencionado", peso=3, evidencias=[{"t": 1}]),
        Relacao(pessoa_a_id=min(b.id, c.id), pessoa_b_id=max(b.id, c.id),
                tipo="co_mencionado", peso=2, evidencias=[{"t": 2}]),
    ])
    db.commit()
    ids = {"a": a.id, "b": b.id, "c": c.id}

    client.post(f"/perfil/{ids['a']}/fundir", json={"duplicada_id": ids["b"]})

    db.expire_all()
    rels = db.scalars(select(Relacao)).all()
    assert len(rels) == 1
    assert rels[0].peso == 5
    assert len(rels[0].evidencias) == 2


def test_fusao_descarta_auto_relacao(client, db):
    """Se os dois registros estavam ligados entre si, a aresta não sobrevive."""
    a = Pessoa(slug="a", nome="A", briefing="x")
    b = Pessoa(slug="b", nome="B")
    db.add_all([a, b])
    db.flush()
    db.add(Relacao(pessoa_a_id=min(a.id, b.id), pessoa_b_id=max(a.id, b.id),
                   tipo="co_mencionado", peso=4, evidencias=[]))
    db.commit()
    ids = {"a": a.id, "b": b.id}

    client.post(f"/perfil/{ids['a']}/fundir", json={"duplicada_id": ids["b"]})

    db.expire_all()
    assert db.scalars(select(Relacao)).all() == []


def test_fusao_preenche_campos_vazios_da_principal(client, db):
    principal = Pessoa(slug="p", nome="Principal", briefing="tem briefing")
    dup = Pessoa(slug="d", nome="Dup", foto_url="https://x/f.jpg",
                 cargo_atual="CEO", linkedin_url="https://l/in/d",
                 identidade_confirmada=True)
    db.add_all([principal, dup])
    db.commit()
    ids = {"p": principal.id, "d": dup.id}

    client.post(f"/perfil/{ids['p']}/fundir", json={"duplicada_id": ids["d"]})

    db.expire_all()
    p = db.get(Pessoa, ids["p"])
    assert p.foto_url == "https://x/f.jpg"       # buraco preenchido
    assert p.cargo_atual == "CEO"
    assert p.briefing == "tem briefing"           # o que já existia mandou
    assert p.identidade_confirmada is True


def test_fusao_nao_duplica_evento_compartilhado(client, db):
    a = Pessoa(slug="a", nome="A", briefing="x")
    b = Pessoa(slug="b", nome="B")
    ev = Evento(nome="Fórum")
    db.add_all([a, b, ev])
    db.flush()
    db.execute(evento_participante.insert().values(evento_id=ev.id, pessoa_id=a.id))
    db.execute(evento_participante.insert().values(evento_id=ev.id, pessoa_id=b.id))
    db.commit()
    ids = {"a": a.id, "b": b.id, "ev": ev.id}

    client.post(f"/perfil/{ids['a']}/fundir", json={"duplicada_id": ids["b"]})

    db.expire_all()
    linhas = db.execute(select(evento_participante)).all()
    assert len(linhas) == 1
    assert linhas[0].pessoa_id == ids["a"]


def test_fusao_com_ela_mesma_e_rejeitada(client, db):
    p = Pessoa(slug="p", nome="P")
    db.add(p)
    db.commit()
    assert client.post(f"/perfil/{p.id}/fundir",
                       json={"duplicada_id": p.id}).status_code == 400


# ---------- conexão manual (conhecimento da casa) ----------


def _pesquisada(slug, nome, **kw):
    return Pessoa(slug=slug, nome=nome, briefing="dossiê", identidade_confirmada=True, **kw)


def test_conexao_manual_cria_aresta_marcada(client, db):
    a = _pesquisada("a", "Executivo A")
    b = _pesquisada("b", "Executivo B")
    db.add_all([a, b])
    db.commit()
    ids = {"a": a.id, "b": b.id}

    corpo = client.post("/grafo/relacao", json={
        "pessoa_a_id": ids["a"], "pessoa_b_id": ids["b"],
        "rotulo": "sócios", "nota": "fundaram a gestora juntos em 2015",
    }).json()
    assert corpo["tipo"] == "manual"
    assert corpo["rotulo"] == "sócios"

    grafo = client.get(f"/grafo/{ids['a']}?peso_minimo=1").json()
    aresta = grafo["edges"][0]
    assert aresta["tipo"] == "manual"
    assert aresta["rotulo"] == "sócios"
    ev = aresta["evidencias"][0]
    assert ev["fonte"] == "analista"
    assert ev["justificativa"] == "fundaram a gestora juntos em 2015"


def test_conexao_manual_exige_justificativa(client, db):
    a = _pesquisada("a", "A")
    b = _pesquisada("b", "B")
    db.add_all([a, b])
    db.commit()

    resp = client.post("/grafo/relacao", json={
        "pessoa_a_id": a.id, "pessoa_b_id": b.id, "rotulo": "sócios", "nota": "",
    })
    assert resp.status_code == 422  # sem o porquê, não registra


def test_conexao_manual_so_entre_pessoas_pesquisadas(client, db):
    a = _pesquisada("a", "A")
    fantasma = Pessoa(slug="fantasma", nome="Só Citado")  # veio de co-menção
    db.add_all([a, fantasma])
    db.commit()

    resp = client.post("/grafo/relacao", json={
        "pessoa_a_id": a.id, "pessoa_b_id": fantasma.id,
        "rotulo": "amigos", "nota": "sei de fonte própria",
    })
    assert resp.status_code == 400
    assert "pesquisadas" in resp.json()["detail"]


def test_conexao_manual_repetida_acumula_evidencia(client, db):
    a = _pesquisada("a", "A")
    b = _pesquisada("b", "B")
    db.add_all([a, b])
    db.commit()
    ids = {"a": a.id, "b": b.id}
    corpo_base = {"pessoa_a_id": ids["a"], "pessoa_b_id": ids["b"], "rotulo": "sócios"}

    client.post("/grafo/relacao", json={**corpo_base, "nota": "primeira razão"})
    corpo = client.post("/grafo/relacao", json={**corpo_base, "nota": "segunda razão"}).json()

    assert corpo["peso"] == 2
    db.expire_all()
    rel = db.scalar(select(Relacao).where(Relacao.tipo == "manual"))
    assert len(rel.evidencias) == 2
    assert rel.nota == "segunda razão"


def test_conexao_manual_nao_duplica_invertendo_a_ordem(client, db):
    a = _pesquisada("a", "A")
    b = _pesquisada("b", "B")
    db.add_all([a, b])
    db.commit()
    ids = {"a": a.id, "b": b.id}

    client.post("/grafo/relacao", json={
        "pessoa_a_id": ids["a"], "pessoa_b_id": ids["b"], "rotulo": "aliados", "nota": "razão"})
    client.post("/grafo/relacao", json={
        "pessoa_a_id": ids["b"], "pessoa_b_id": ids["a"], "rotulo": "aliados", "nota": "razão"})

    db.expire_all()
    assert len(db.scalars(select(Relacao).where(Relacao.tipo == "manual")).all()) == 1


def test_conexao_manual_com_ela_mesma_e_rejeitada(client, db):
    a = _pesquisada("a", "A")
    db.add(a)
    db.commit()
    resp = client.post("/grafo/relacao", json={
        "pessoa_a_id": a.id, "pessoa_b_id": a.id, "rotulo": "aliados", "nota": "razão"})
    assert resp.status_code == 400


def test_conexao_manual_sobrevive_a_fusao(client, db):
    """Conhecimento da casa não pode se perder quando dois registros se unem."""
    a = _pesquisada("a", "A")
    b = _pesquisada("b", "B")
    dup = Pessoa(slug="dup", nome="Dup", briefing="x", identidade_confirmada=True)
    db.add_all([a, b, dup])
    db.commit()
    ids = {"a": a.id, "b": b.id, "dup": dup.id}

    client.post("/grafo/relacao", json={
        "pessoa_a_id": ids["dup"], "pessoa_b_id": ids["b"],
        "rotulo": "sócios", "nota": "conhecimento interno"})
    client.post(f"/perfil/{ids['a']}/fundir", json={"duplicada_id": ids["dup"]})

    db.expire_all()
    rel = db.scalar(select(Relacao).where(Relacao.tipo == "manual"))
    assert rel is not None
    assert {rel.pessoa_a_id, rel.pessoa_b_id} == {ids["a"], ids["b"]}
    assert rel.rotulo == "sócios"


def test_fusao_pessoa_inexistente_404(client, db):
    p = Pessoa(slug="p", nome="P")
    db.add(p)
    db.commit()
    assert client.post(f"/perfil/{p.id}/fundir",
                       json={"duplicada_id": 9999}).status_code == 404
