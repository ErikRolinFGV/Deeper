"""Teste do pipeline completo do worker com coletores e LLM mockados.

Valida a orquestração de ponta a ponta — pessoa criada, menções persistidas,
entidades extraídas, grafo reforçado, briefing gravado, job concluído —
sem tocar em nenhuma API externa.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("CRUNCHBASE_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.empresa import Empresa
from app.models.evento import Evento
from app.models.job import JobColeta, StatusJob
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.llm.extrator import EntidadesExtraidas
from app.workers import busca_worker

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

MENCOES_FALSAS = [
    {
        "fonte": "valor",
        "url": "https://valor.globo.com/noticia-1",
        "titulo": "Vale anuncia investimento bilionário em Carajás",
        "snippet": "O CEO Eduardo Bartolomeo anunciou ao lado de Gustavo Pimenta...",
        "data_publicacao": "2026-06-01",
    },
    {
        "fonte": "exame",
        "url": "https://exame.com/noticia-2",
        "titulo": "Executivos debatem ESG no Fórum de Davos",
        "snippet": "Bartolomeo participou de painel sobre mineração sustentável...",
        "data_publicacao": None,  # sem data: worker deve usar a do extrator
    },
]

EXTRACAO_FALSA = EntidadesExtraidas(
    eventos=["Fórum Econômico Mundial de Davos"],
    empresas_mencionadas=["Vale"],
    cargo_pessoa_alvo="CEO da Vale",
    pessoas_mencionadas=["Gustavo Pimenta"],
    valores_monetarios=["R$ 10 bilhões"],
    datas=["2026-05-20"],
    sentimento=0.5,
    temas=["ESG", "mineração"],
)

BRIEFING_FALSO = "Parágrafo 1.\n\nParágrafo 2.\n\nParágrafo 3."


@pytest.fixture(autouse=True)
def preparar(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_worker, "SessionLocal", TestingSession)
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: MENCOES_FALSAS)
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: EXTRACAO_FALSA)
    monkeypatch.setattr(busca_worker, "sintetizar", lambda dados: BRIEFING_FALSO)
    monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", lambda url: None)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    session = TestingSession()
    yield session
    session.close()


def _criar_job(db, termo="Eduardo Bartolomeo") -> int:
    job = JobColeta(termo_busca=termo)
    db.add(job)
    db.commit()
    return job.id


def test_pipeline_completo(db):
    job_id = _criar_job(db)

    busca_worker.executar_busca(job_id)

    job = db.get(JobColeta, job_id)
    assert job.status == StatusJob.DONE
    assert job.finalizado_em is not None
    assert job.erro is None

    # Pessoa alvo criada e vinculada ao job
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa is not None
    assert job.pessoa_id == pessoa.id
    assert pessoa.briefing == BRIEFING_FALSO

    # Cargo inferido pelo extrator preenche a ficha
    assert pessoa.cargo_atual == "CEO da Vale"

    # Menções persistidas com dados do extrator
    mencoes = db.scalars(select(Mencao).where(Mencao.pessoa_id == pessoa.id)).all()
    assert len(mencoes) == 2
    assert all(m.sentimento == 0.5 for m in mencoes)
    assert all("ESG" in (m.temas or "") for m in mencoes)

    # Datas: a 1ª veio do coletor; a 2ª não tem data confiável e fica nula
    # (datas do extrator NÃO viram data de publicação — risco de erro factual)
    por_url = {m.url: m for m in mencoes}
    assert str(por_url["https://valor.globo.com/noticia-1"].data_publicacao) == "2026-06-01"
    assert por_url["https://exame.com/noticia-2"].data_publicacao is None

    # Empresa e evento criados
    assert db.scalar(select(Empresa).where(Empresa.slug == "vale")) is not None
    evento = db.scalar(select(Evento))
    assert evento.nome == "Fórum Econômico Mundial de Davos"

    # Grafo: co-mencionado vira Pessoa + Relacao com peso 2 (uma por menção)
    co = db.scalar(select(Pessoa).where(Pessoa.slug == "gustavo-pimenta"))
    assert co is not None
    rel = db.scalar(select(Relacao))
    assert rel.tipo == "co_mencionado"
    assert rel.peso == 2
    assert {rel.pessoa_a_id, rel.pessoa_b_id} == {pessoa.id, co.id}
    assert len(rel.evidencias) == 2


def test_pipeline_reexecucao_nao_duplica(db):
    """Rodar duas buscas do mesmo nome não duplica menções nem pessoas."""
    busca_worker.executar_busca(_criar_job(db))
    busca_worker.executar_busca(_criar_job(db))

    assert len(db.scalars(select(Mencao)).all()) == 2  # dedup por URL
    assert len(db.scalars(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo")).all()) == 1


def test_pipeline_sem_mencoes_conclui_sem_briefing(db, monkeypatch):
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: [])
    job_id = _criar_job(db, termo="Pessoa Sem Noticias")

    busca_worker.executar_busca(job_id)

    job = db.get(JobColeta, job_id)
    assert job.status == StatusJob.DONE
    pessoa = db.get(Pessoa, job.pessoa_id)
    assert pessoa.briefing is None


def test_eventos_com_ano_diferente_nao_duplicam(db):
    """'Executivo de Valor 2026' e 'Executivo de Valor' são o mesmo evento."""
    e1 = busca_worker._get_or_create_evento(db, "Executivo de Valor 2026", "https://a")
    e2 = busca_worker._get_or_create_evento(db, "Executivo de Valor", "https://b")
    e3 = busca_worker._get_or_create_evento(db, "executivo de valor 2025", "https://c")
    db.commit()

    assert e1.id == e2.id == e3.id
    assert len(db.scalars(select(Evento)).all()) == 1


def test_higienizacao_remove_indices_e_preenche_datas(db):
    """Registros antigos: página-índice some, data nula vem da URL."""
    pessoa = Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo")
    db.add(pessoa)
    db.flush()
    db.add_all(
        [
            Mencao(  # página-índice gravada antes do filtro existir
                pessoa_id=pessoa.id,
                fonte="estadao",
                url="https://www.estadao.com.br/tudo-sobre/eduardo-bartolomeo/",
                sentimento=0.1,
            ),
            Mencao(  # sem data, mas a URL tem
                pessoa_id=pessoa.id,
                fonte="oglobo",
                url="https://oglobo.globo.com/economia/noticia/2025/12/10/materia.ghtml",
                sentimento=0.2,
            ),
        ]
    )
    db.commit()

    busca_worker.executar_busca(_criar_job(db))

    db.expire_all()
    urls = {m.url: m for m in db.scalars(select(Mencao)).all()}
    assert "https://www.estadao.com.br/tudo-sobre/eduardo-bartolomeo/" not in urls
    backfilled = urls["https://oglobo.globo.com/economia/noticia/2025/12/10/materia.ghtml"]
    assert str(backfilled.data_publicacao) == "2025-12-10"


def test_reprocessa_mencoes_que_ficaram_sem_extracao(db, monkeypatch):
    """Se o LLM falhou na 1ª busca, a 2ª (force_refresh) recupera as menções."""
    # 1ª execução: extrator fora do ar — menções gravadas sem sentimento/temas
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: None)
    busca_worker.executar_busca(_criar_job(db))

    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.briefing is None
    assert all(m.sentimento is None for m in db.scalars(select(Mencao)).all())

    # 2ª execução: extrator voltou — pendentes são reprocessadas
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: EXTRACAO_FALSA)
    busca_worker.executar_busca(_criar_job(db))

    db.expire_all()
    mencoes = db.scalars(select(Mencao)).all()
    assert len(mencoes) == 2  # nada duplicado
    assert all(m.sentimento == 0.5 for m in mencoes)
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.briefing == BRIEFING_FALSO


def test_pipeline_falha_marca_job_failed(db, monkeypatch):
    def explode(nome, limite=15):
        raise RuntimeError("SerpAPI caiu")

    monkeypatch.setattr(busca_worker, "buscar_mencoes", explode)
    job_id = _criar_job(db)

    busca_worker.executar_busca(job_id)

    job = db.get(JobColeta, job_id)
    assert job.status == StatusJob.FAILED
    assert "SerpAPI caiu" in job.erro
