"""Nomes do grafo e extração em paralelo (28/09/2026).

- No dossiê do Gustavo Pimenta apareceram "Lula" e "Luiz Inácio Lula da
  Silva" como duas pessoas: apelido solto não pode virar nó.
- Com 30 matérias do Google Notícias e teto de 20 extrações, parte das
  notícias ficava sem sentimento.
"""

import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.job import JobColeta, StatusJob
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.llm.extrator import EntidadesExtraidas, PessoaCitada
from app.services.manutencao import localizar_por_slug
from app.workers import busca_worker

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def preparar(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_worker, "SessionLocal", TestingSession)
    monkeypatch.setattr(busca_worker, "sintetizar", lambda dados: "Briefing.")
    monkeypatch.setattr(busca_worker, "baixar_artigo", lambda url: None)
    monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", lambda url: None)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    s = TestingSession()
    yield s
    s.close()


def _rodar(db, n_mencoes, pessoas):
    db.add(Pessoa(nome="Gustavo Pimenta", slug="gustavo-pimenta", identidade_confirmada=True))
    db.commit()
    mencoes = [
        {"fonte": "valor", "url": f"https://valor.globo.com/m{i}", "titulo": f"Vale {i}",
         "snippet": "texto " * 200, "data_publicacao": f"2026-08-{1 + i % 28:02d}"}
        for i in range(n_mencoes)
    ]
    busca_worker.buscar_mencoes = busca_worker.buscar_mencoes  # noqa (fixture restaura)
    return mencoes


def test_todas_as_30_materias_ganham_sentimento_e_rodam_em_paralelo(db, monkeypatch):
    mencoes = _rodar(db, 30, [])
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: mencoes)
    threads = set()

    def extrair(texto, ctx):
        threads.add(threading.get_ident())
        return EntidadesExtraidas(sentimento=0.3, papel_pessoa_alvo="protagonista")

    monkeypatch.setattr(busca_worker, "extrair", extrair)
    job = JobColeta(termo_busca="Gustavo Pimenta")
    db.add(job)
    db.commit()
    busca_worker.executar_busca(job.id)
    db.expire_all()
    assert db.get(JobColeta, job.id).status == StatusJob.DONE
    sem = db.scalars(select(Mencao).where(Mencao.sentimento.is_(None))).all()
    assert len(db.scalars(select(Mencao)).all()) == 30
    assert sem == []
    assert len(threads) > 1  # extração realmente concorrente


def test_apelido_solto_nao_vira_no_e_variacao_de_vizinho_e_reconhecida(db, monkeypatch):
    mencoes = _rodar(db, 2, [])
    monkeypatch.setattr(busca_worker, "buscar_mencoes", lambda nome, limite=15: mencoes)
    respostas = {
        "https://valor.globo.com/m0": [PessoaCitada(nome="Luiz Inácio Lula da Silva", descritor="presidente"),
                                       PessoaCitada(nome="Lula")],
        "https://valor.globo.com/m1": [PessoaCitada(nome="Luiz Lula da Silva", descritor="presidente"),
                                       PessoaCitada(nome="Gustavo Rodrigues Pimenta")],
    }

    def extrair(texto, ctx):
        chave = "https://valor.globo.com/m0" if "Vale 0" in texto else "https://valor.globo.com/m1"
        return EntidadesExtraidas(sentimento=0.1, pessoas_mencionadas=respostas[chave])

    monkeypatch.setattr(busca_worker, "extrair", extrair)
    # 1ª menção cria o nó completo; a 2ª traz variação do mesmo nome
    monkeypatch.setattr(busca_worker, "EXTRACOES_PARALELAS", 1)
    job = JobColeta(termo_busca="Gustavo Pimenta")
    db.add(job)
    db.commit()
    busca_worker.executar_busca(job.id)
    db.expire_all()

    nomes = sorted(p.nome for p in db.scalars(select(Pessoa)).all())
    assert nomes == ["Gustavo Pimenta", "Luiz Inácio Lula da Silva"]  # sem "Lula", sem 2º Lula, sem o próprio alvo
    lula = localizar_por_slug(db, "luiz-lula-da-silva")
    assert lula is not None and lula.nome == "Luiz Inácio Lula da Silva"  # virou apelido
    rel = db.scalars(select(Relacao)).all()
    assert len(rel) == 1 and rel[0].peso == 2
