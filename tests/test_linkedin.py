"""Testes do coletor LinkedIn/Apify: normalização, descoberta e integração.

Nenhum teste toca API externa — SerpAPI e Apify são mockados.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.cargo import Cargo
from app.models.empresa import Empresa
from app.models.job import JobColeta, StatusJob
from app.models.pessoa import Pessoa
from app.services.collectors import apify_linkedin
from app.services.collectors.apify_linkedin import (
    _parse_data_li,
    descobrir_linkedin_url,
    normalizar_perfil,
)
from app.workers import busca_worker

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Payload no formato do actor apimaestro/linkedin-profile-detail
PERFIL_BRUTO = {
    "basic_info": {
        "fullname": "Eduardo de Salles Bartolomeo",
        "headline": "CEO at Vale",
        "about": "Executivo com 30 anos de experiência em mineração e logística.",
        "profile_picture_url": "https://media.licdn.com/foto.jpg",
        "location": {"country": "Brazil", "city": "Rio de Janeiro", "full": "Rio de Janeiro, Brazil"},
        "current_company": "Vale",
        "follower_count": 120000,
        "connection_count": 500,
    },
    "experience": [
        {
            "title": "Chief Executive Officer",
            "company": "Vale",
            "location": "Rio de Janeiro",
            "start_date": {"year": 2019, "month": 4},
            "end_date": "Present",
            "is_current": True,
            "description": "Lidera a maior mineradora das Américas.",
        },
        {
            "title": "Diretor Executivo de Metais Básicos",
            "company": "Vale",
            "start_date": {"year": 2018},
            "end_date": {"year": 2019, "month": 4},
            "is_current": False,
        },
        {
            "title": "COO",
            "company": "Louis Dreyfus",
            "start_date": "Jan 2015",
            "end_date": "Dec 2017",
        },
    ],
    "education": [
        {
            "school": "COPPE/UFRJ",
            "degree": "MBA",
            "field_of_study": "Gestão Empresarial",
            "start_date": {"year": 1998},
            "end_date": {"year": 2000},
        }
    ],
    "certifications": [{"name": "Board Member Certification"}],
}


# ---------- normalização ----------


def test_normalizar_perfil_completo():
    perfil = normalizar_perfil(PERFIL_BRUTO)

    assert perfil["nome_completo"] == "Eduardo de Salles Bartolomeo"
    assert perfil["headline"] == "CEO at Vale"
    assert perfil["localizacao"] == "Rio de Janeiro, Brazil"
    assert perfil["empresa_atual"] == "Vale"
    assert perfil["seguidores"] == 120000

    assert len(perfil["experiencias"]) == 3
    atual = perfil["experiencias"][0]
    assert atual["funcao"] == "Chief Executive Officer"
    assert atual["atual"] is True
    assert atual["inicio"] == date(2019, 4, 1)
    assert atual["fim"] is None

    # Datas em string ("Jan 2015") também são interpretadas
    ldc = perfil["experiencias"][2]
    assert ldc["inicio"] == date(2015, 1, 1)
    assert ldc["fim"] == date(2017, 12, 1)

    assert perfil["formacao"][0]["instituicao"] == "COPPE/UFRJ"
    assert perfil["certificacoes"] == ["Board Member Certification"]


def test_normalizar_perfil_camelcase_fallback():
    """Se o actor mudar para camelCase, o normalizador não quebra."""
    bruto = {
        "fullName": "Fulana de Tal",
        "headline": "CFO",
        "summary": "Resumo.",
        "profilePicture": "https://x/foto.jpg",
        "location": "São Paulo, Brazil",
        "experiences": [
            {"position": "CFO", "companyName": "Acme", "startDate": {"year": 2020}}
        ],
    }
    perfil = normalizar_perfil(bruto)
    assert perfil["nome_completo"] == "Fulana de Tal"
    assert perfil["sobre"] == "Resumo."
    assert perfil["localizacao"] == "São Paulo, Brazil"
    assert perfil["experiencias"][0]["empresa"] == "Acme"


def test_parse_data_li_variantes():
    assert _parse_data_li({"year": 2020, "month": 3}) == date(2020, 3, 1)
    assert _parse_data_li({"year": 2020}) == date(2020, 1, 1)
    assert _parse_data_li("Mar 2020") == date(2020, 3, 1)
    assert _parse_data_li("2020") == date(2020, 1, 1)
    assert _parse_data_li("Present") is None
    assert _parse_data_li(None) is None
    assert _parse_data_li({"month": 5}) is None
    assert _parse_data_li("sem ano nenhum") is None


# ---------- descoberta via SerpAPI ----------


class _RespostaFalsa:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_descobrir_linkedin_url(monkeypatch):
    payload = {
        "organic_results": [
            {"link": "https://br.linkedin.com/in/eduardobartolomeo?trk=abc"},
            {"link": "https://www.linkedin.com/pulse/artigo-qualquer"},
        ]
    }
    monkeypatch.setattr(
        apify_linkedin.httpx, "get", lambda *a, **kw: _RespostaFalsa(payload)
    )
    url = descobrir_linkedin_url("Eduardo Bartolomeo", "CEO da Vale")
    # Extrai a URL canônica /in/, sem query string
    assert url == "https://br.linkedin.com/in/eduardobartolomeo"


def test_descobrir_linkedin_url_sem_resultado(monkeypatch):
    monkeypatch.setattr(
        apify_linkedin.httpx,
        "get",
        lambda *a, **kw: _RespostaFalsa({"organic_results": []}),
    )
    assert descobrir_linkedin_url("Pessoa Inexistente") is None


def test_descobrir_linkedin_url_falha_de_rede(monkeypatch):
    def explode(*a, **kw):
        raise RuntimeError("rede caiu")

    monkeypatch.setattr(apify_linkedin.httpx, "get", explode)
    assert descobrir_linkedin_url("Qualquer Nome") is None


# ---------- coleta via Apify ----------


class _RunObjeto:
    """Simula o Run do apify-client >=2: objeto com atributos, não-subscriptável."""

    default_dataset_id = "ds123"


class _ClienteApifyFalso:
    """Simula ApifyClient o suficiente para o coletor."""

    def __init__(self, run, itens):
        self._run = run
        self._itens = itens

    def actor(self, actor_id):
        cliente = self

        class _Actor:
            def call(self, run_input):
                return cliente._run

        return _Actor()

    def dataset(self, dataset_id):
        assert dataset_id == "ds123"
        itens = self._itens

        class _Dataset:
            def iterate_items(self):
                return iter(itens)

        return _Dataset()


def test_coletar_perfil_com_run_objeto(monkeypatch):
    """Regressão: apify-client >=2 retorna Run objeto ('not subscriptable')."""
    monkeypatch.setattr(
        apify_linkedin,
        "ApifyClient",
        lambda token: _ClienteApifyFalso(_RunObjeto(), [PERFIL_BRUTO]),
    )
    bruto = apify_linkedin.coletar_perfil_linkedin("https://linkedin.com/in/x")
    assert bruto == PERFIL_BRUTO


def test_coletar_perfil_com_run_dict(monkeypatch):
    """apify-client <2 retorna dict com defaultDatasetId."""
    monkeypatch.setattr(
        apify_linkedin,
        "ApifyClient",
        lambda token: _ClienteApifyFalso({"defaultDatasetId": "ds123"}, [PERFIL_BRUTO]),
    )
    bruto = apify_linkedin.coletar_perfil_linkedin("https://linkedin.com/in/x")
    assert bruto == PERFIL_BRUTO


def test_coletar_perfil_dataset_vazio(monkeypatch):
    monkeypatch.setattr(
        apify_linkedin,
        "ApifyClient",
        lambda token: _ClienteApifyFalso(_RunObjeto(), []),
    )
    assert apify_linkedin.coletar_perfil_linkedin("https://linkedin.com/in/x") is None


# ---------- integração no worker ----------


@pytest.fixture(autouse=True)
def preparar(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_worker, "SessionLocal", TestingSession)
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: [])
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: None)
    monkeypatch.setattr(busca_worker, "sintetizar", lambda dados: "Briefing LinkedIn.")
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    session = TestingSession()
    yield session
    session.close()


def _rodar_job(db, termo="Eduardo Bartolomeo") -> JobColeta:
    job = JobColeta(termo_busca=termo)
    db.add(job)
    db.commit()
    busca_worker.executar_busca(job.id)
    db.expire_all()
    return db.get(JobColeta, job.id)


def test_worker_descobre_coleta_e_persiste(db, monkeypatch):
    chamadas = {"apify": 0}

    def coletar_falso(url):
        chamadas["apify"] += 1
        return PERFIL_BRUTO

    monkeypatch.setattr(
        busca_worker,
        "descobrir_linkedin_url",
        lambda nome, ctx=None: "https://www.linkedin.com/in/eduardobartolomeo",
    )
    monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar_falso)

    job = _rodar_job(db)
    assert job.status == StatusJob.DONE

    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.linkedin_url == "https://www.linkedin.com/in/eduardobartolomeo"
    assert pessoa.nome_completo == "Eduardo de Salles Bartolomeo"
    assert pessoa.localizacao == "Rio de Janeiro, Brazil"
    assert pessoa.cargo_atual == "Chief Executive Officer — Vale"
    assert pessoa.linkedin_dados is not None
    assert pessoa.linkedin_coletado_em is not None
    # Sem menções, mas com LinkedIn: briefing sai mesmo assim
    assert pessoa.briefing == "Briefing LinkedIn."

    # Histórico → Cargo/Empresa
    cargos = db.scalars(select(Cargo).where(Cargo.pessoa_id == pessoa.id)).all()
    assert len(cargos) == 3
    assert sum(1 for c in cargos if c.eh_atual) == 1
    assert db.scalar(select(Empresa).where(Empresa.slug == "vale")) is not None
    assert db.scalar(select(Empresa).where(Empresa.slug == "louis-dreyfus")) is not None

    assert chamadas["apify"] == 1


def test_worker_ttl_evita_recoleta_e_nao_duplica_cargos(db, monkeypatch):
    monkeypatch.setattr(
        busca_worker,
        "descobrir_linkedin_url",
        lambda nome, ctx=None: "https://www.linkedin.com/in/eduardobartolomeo",
    )
    chamadas = {"apify": 0}

    def coletar_falso(url):
        chamadas["apify"] += 1
        return PERFIL_BRUTO

    monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar_falso)

    _rodar_job(db)
    _rodar_job(db)  # force_refresh dentro do TTL

    assert chamadas["apify"] == 1  # segunda execução usou o cache
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    cargos = db.scalars(select(Cargo).where(Cargo.pessoa_id == pessoa.id)).all()
    assert len(cargos) == 3  # persistência idempotente


def test_worker_recoleta_apos_ttl_expirar(db, monkeypatch):
    monkeypatch.setattr(
        busca_worker,
        "descobrir_linkedin_url",
        lambda nome, ctx=None: "https://www.linkedin.com/in/eduardobartolomeo",
    )
    chamadas = {"apify": 0}

    def coletar_falso(url):
        chamadas["apify"] += 1
        return PERFIL_BRUTO

    monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar_falso)

    _rodar_job(db)

    # Envelhece a coleta para além do TTL
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    pessoa.linkedin_coletado_em = datetime.now(timezone.utc) - timedelta(days=90)
    db.commit()

    _rodar_job(db)
    assert chamadas["apify"] == 2


def test_worker_sem_linkedin_segue_normal(db, monkeypatch):
    """Descoberta falhou: job conclui sem enriquecimento e sem briefing."""
    monkeypatch.setattr(busca_worker, "descobrir_linkedin_url", lambda nome, ctx=None: None)
    monkeypatch.setattr(
        busca_worker, "coletar_perfil_linkedin", lambda url: pytest.fail("não deveria coletar")
    )

    job = _rodar_job(db, termo="Pessoa Sem LinkedIn")
    assert job.status == StatusJob.DONE
    pessoa = db.get(Pessoa, job.pessoa_id)
    assert pessoa.linkedin_url is None
    assert pessoa.briefing is None
