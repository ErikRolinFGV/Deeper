"""Testes do inferidor de relações formais (cargos sobrepostos)."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.cargo import Cargo
from app.models.empresa import Empresa
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.graph.inferidor_formal import inferir_relacoes_formais

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def preparar():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    s = TestingSession()
    yield s
    s.close()


def _pessoa(db, slug):
    p = Pessoa(slug=slug, nome=slug.replace("-", " ").title())
    db.add(p)
    db.flush()
    return p


def _empresa(db, slug):
    e = Empresa(slug=slug, nome=slug.title())
    db.add(e)
    db.flush()
    return e


def _cargo(db, pessoa, empresa, funcao, inicio=None, fim=None, atual=False):
    c = Cargo(
        pessoa_id=pessoa.id, empresa_id=empresa.id, funcao=funcao,
        inicio=inicio, fim=fim, eh_atual=atual,
    )
    db.add(c)
    db.flush()
    return c


def test_periodos_sobrepostos_viram_colega_empresa(db):
    vale = _empresa(db, "vale")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, vale, "CEO", date(2019, 1, 1), date(2024, 1, 1))
    _cargo(db, b, vale, "CFO", date(2021, 1, 1), None, atual=True)

    assert inferir_relacoes_formais(db, a) == 1
    rel = db.scalar(select(Relacao))
    assert rel.tipo == "colega_empresa"
    assert {rel.pessoa_a_id, rel.pessoa_b_id} == {a.id, b.id}
    assert rel.evidencias[0]["empresa"] == "Vale"
    assert rel.evidencias[0]["fonte"] == "linkedin_cargos"


def test_periodos_disjuntos_nao_viram_aresta(db):
    vale = _empresa(db, "vale")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, vale, "CEO", date(2004, 1, 1), date(2008, 1, 1))
    _cargo(db, b, vale, "CFO", date(2019, 1, 1), date(2024, 1, 1))

    assert inferir_relacoes_formais(db, a) == 0
    assert db.scalar(select(Relacao)) is None


def test_datas_nulas_contam_como_sobreposicao(db):
    """Sem datas não dá para excluir o vínculo — cria a aresta (MVP)."""
    vale = _empresa(db, "vale")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, vale, "Diretor")
    _cargo(db, b, vale, "Gerente", date(2020, 1, 1), None, atual=True)

    assert inferir_relacoes_formais(db, a) == 1


def test_conselheiros_viram_co_board(db):
    bm = _empresa(db, "boston-metal")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, bm, "Membro do conselho de administração", date(2024, 1, 1), None, atual=True)
    _cargo(db, b, bm, "Board Member", date(2023, 1, 1), None, atual=True)

    assert inferir_relacoes_formais(db, a) == 1
    rel = db.scalar(select(Relacao))
    assert rel.tipo == "co_board"


def test_idempotente_em_force_refresh(db):
    vale = _empresa(db, "vale")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, vale, "CEO", date(2019, 1, 1), None, atual=True)
    _cargo(db, b, vale, "CFO", date(2019, 1, 1), None, atual=True)

    assert inferir_relacoes_formais(db, a) == 1
    assert inferir_relacoes_formais(db, a) == 0  # re-run não duplica
    assert inferir_relacoes_formais(db, b) == 0  # nem pelo outro lado
    rel = db.scalar(select(Relacao))
    assert rel.peso == 1
    assert len(rel.evidencias) == 1


def test_duas_empresas_compartilhadas_somam_peso(db):
    vale = _empresa(db, "vale")
    nts = _empresa(db, "nts")
    a = _pessoa(db, "pessoa-a")
    b = _pessoa(db, "pessoa-b")
    _cargo(db, a, vale, "CEO", date(2019, 1, 1), date(2024, 1, 1))
    _cargo(db, b, vale, "CFO", date(2020, 1, 1), date(2023, 1, 1))
    _cargo(db, a, nts, "CEO", date(2016, 1, 1), date(2018, 1, 1))
    _cargo(db, b, nts, "COO", date(2016, 6, 1), date(2019, 1, 1))

    assert inferir_relacoes_formais(db, a) == 2
    rel = db.scalar(select(Relacao))
    assert rel.peso == 2
    assert {ev["empresa"] for ev in rel.evidencias} == {"Vale", "Nts"}
