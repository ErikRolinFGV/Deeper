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
    monkeypatch.setattr(busca_worker, "descobrir_linkedin_url", lambda nome, ctx=None: None)
    monkeypatch.setattr(busca_worker, "baixar_texto", lambda url: None)
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


def test_dedup_ignora_parametros_de_tracking(db, monkeypatch):
    """A mesma matéria com ?srsltid= diferente não vira menção duplicada."""
    base = "https://www.estadao.com.br/economia/vale-discute-saida/"
    duplicadas = [
        {**MENCOES_FALSAS[0], "url": f"{base}?srsltid=AAA"},
        {**MENCOES_FALSAS[0], "url": f"{base}?srsltid=BBB"},
        {**MENCOES_FALSAS[0], "url": f"{base}?utm_source=x&utm_campaign=y"},
    ]
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: duplicadas)

    busca_worker.executar_busca(_criar_job(db))

    mencoes = db.scalars(select(Mencao)).all()
    assert len(mencoes) == 1
    assert mencoes[0].url == base


def test_higienizacao_funde_duplicatas_antigas(db):
    """Duplicatas com tracking gravadas antes da correção são fundidas."""
    pessoa = Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo")
    db.add(pessoa)
    db.flush()
    base = "https://www.estadao.com.br/economia/vale-discute-saida/"
    db.add_all(
        [
            Mencao(pessoa_id=pessoa.id, fonte="estadao", url=f"{base}?srsltid=AAA",
                   sentimento=None, data_publicacao=None),
            Mencao(pessoa_id=pessoa.id, fonte="estadao", url=f"{base}?srsltid=BBB",
                   sentimento=-0.4, temas="governança"),
        ]
    )
    db.commit()

    busca_worker.executar_busca(_criar_job(db))

    db.expire_all()
    sobreviventes = db.scalars(
        select(Mencao).where(Mencao.url.like(f"{base}%"))
    ).all()
    assert len(sobreviventes) == 1
    assert sobreviventes[0].sentimento == -0.4  # mantém a versão processada
    assert sobreviventes[0].url == base


def test_briefing_usa_estado_completo_do_banco(db, monkeypatch):
    """Menções processadas em execuções ANTERIORES entram no consolidado."""
    pessoa = Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo")
    db.add(pessoa)
    db.flush()
    db.add(
        Mencao(
            pessoa_id=pessoa.id,
            fonte="valor",
            url="https://valor.globo.com/antiga",
            titulo="Matéria antiga já processada",
            sentimento=0.5,
            temas="ESG,mineração",
        )
    )
    db.commit()

    # Nova execução sem nada novo: sem menções coletadas, sem LinkedIn
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: [])
    capturado = {}

    def sintetizar_espiao(dados):
        capturado.update(dados)
        return "Briefing com histórico."

    monkeypatch.setattr(busca_worker, "sintetizar", sintetizar_espiao)

    busca_worker.executar_busca(_criar_job(db))

    # O consolidado veio do banco, não do lote (vazio) desta execução
    assert len(capturado["mencoes"]) == 1
    assert capturado["mencoes"][0]["titulo"] == "Matéria antiga já processada"
    assert "ESG" in capturado["temas"]
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.briefing == "Briefing com histórico."


def test_higienizacao_remove_eventos_de_pagina_indice(db):
    pessoa = Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo")
    db.add(pessoa)
    db.flush()
    db.add_all(
        [
            Evento(nome="Evento legítimo", fonte_url="https://valor.globo.com/materia/2024/01/01/x"),
            Evento(nome="Evento de índice", fonte_url="https://www.estadao.com.br/tudo-sobre/eduardo-bartolomeo/"),
        ]
    )
    db.commit()

    busca_worker.executar_busca(_criar_job(db))

    db.expire_all()
    nomes = {e.nome for e in db.scalars(select(Evento)).all()}
    assert "Evento de índice" not in nomes


def test_pipeline_falha_marca_job_failed(db, monkeypatch):
    def explode(nome, limite=15):
        raise RuntimeError("SerpAPI caiu")

    monkeypatch.setattr(busca_worker, "buscar_mencoes", explode)
    job_id = _criar_job(db)

    busca_worker.executar_busca(job_id)

    job = db.get(JobColeta, job_id)
    assert job.status == StatusJob.FAILED
    assert "SerpAPI caiu" in job.erro


def test_mencao_de_homonimo_e_descartada(db, monkeypatch):
    """Extrator diz que o texto é sobre OUTRA pessoa → menção deletada."""
    homonimo = EXTRACAO_FALSA.model_copy(update={"texto_e_sobre_alvo": False})
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: homonimo)

    busca_worker.executar_busca(_criar_job(db))

    assert db.scalars(select(Mencao)).all() == []  # nada do homônimo sobrou
    assert db.scalar(select(Relacao)) is None
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.briefing is None  # sem material legítimo, sem briefing


def test_extrator_recebe_contexto_com_cargo(db, monkeypatch):
    """Pessoa com cargo conhecido → contexto rico chega ao extrator."""
    pessoa = Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo",
                    cargo_atual="CEO da Vale")
    db.add(pessoa)
    db.commit()

    contextos = []

    def espiao(texto, ctx):
        contextos.append(ctx)
        return EXTRACAO_FALSA

    monkeypatch.setattr(busca_worker, "extrair", espiao)
    busca_worker.executar_busca(_criar_job(db))

    assert contextos
    assert all(c == "Eduardo Bartolomeo — CEO da Vale" for c in contextos)


# ---------- contexto das co-menções (feedback: "top 50 não é conexão") ----------


def test_comencao_normal_recebe_contexto_direta(db):
    busca_worker.executar_busca(_criar_job(db))

    rel = db.scalar(select(Relacao))
    assert rel is not None
    assert all(ev["contexto"] == "direta" for ev in rel.evidencias)


def test_materia_lista_ranking_rotula_evidencia(db, monkeypatch):
    extracao_lista = EXTRACAO_FALSA.model_copy(update={"eh_lista_ou_ranking": True})
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: extracao_lista)

    busca_worker.executar_busca(_criar_job(db))

    rel = db.scalar(select(Relacao))
    assert all(ev["contexto"] == "lista" for ev in rel.evidencias)


def test_fanout_alto_tambem_vira_lista(db, monkeypatch):
    """Matéria citando 6+ pessoas juntas é lista mesmo sem rótulo do LLM."""
    muitos = EXTRACAO_FALSA.model_copy(
        update={"pessoas_mencionadas": [f"Pessoa Num {i}" for i in range(7)]}
    )
    monkeypatch.setattr(busca_worker, "extrair", lambda texto, ctx: muitos)

    busca_worker.executar_busca(_criar_job(db))

    rels = db.scalars(select(Relacao)).all()
    assert rels
    for rel in rels:
        assert all(ev["contexto"] == "lista" for ev in rel.evidencias)
