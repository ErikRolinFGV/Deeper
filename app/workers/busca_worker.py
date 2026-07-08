"""Worker que orquestra a coleta completa de um executivo.

Fluxo executado em segundo plano por RQ:
1. Atualiza JobColeta para "running".
2. Localiza ou cria a Pessoa alvo a partir do termo de busca.
3. Coleta menções na imprensa BR (SerpAPI) e, se houver URL, perfil LinkedIn (Apify).
4. Passa cada menção pelo extrator LLM (entidades, sentimento, temas).
5. Persiste Mencao/Empresa/Evento e reforça arestas em Relacao (co-menções).
6. Chama o sintetizador para gerar o briefing executivo.
7. Marca JobColeta como "done".

Cada coletor e cada chamada LLM é tolerante a falha: uma fonte fora do ar
degrada o dossiê, mas não derruba o job inteiro.
"""

import re
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.busca import gerar_slug
from app.core.db import SessionLocal
from app.models.empresa import Empresa
from app.models.evento import Evento, evento_participante
from app.models.job import JobColeta, StatusJob
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.services.collectors.apify_linkedin import coletar_perfil_linkedin
from app.services.collectors.serpapi_news import (
    _data_da_url,
    _eh_pagina_indice,
    buscar_mencoes,
)
from app.services.graph.construtor import reforcar_relacao
from app.services.llm.extrator import extrair
from app.services.llm.sintetizador import sintetizar

# Versão do pipeline — aparece no log de cada job. Se o log mostrar uma
# versão antiga, o processo do worker precisa ser reiniciado.
PIPELINE_VERSAO = "2026-07-08.1"

# Limites de MVP: controlam custo de API por busca.
MAX_MENCOES = 15          # resultados pedidos ao SerpAPI
MAX_EXTRACOES = 10        # menções que passam pelo extrator LLM
MAX_COMENCIONADOS = 5     # pessoas co-mencionadas processadas por menção


# ---------- helpers de persistência ----------


def _get_or_create_pessoa(db: Session, nome: str) -> Pessoa:
    slug = gerar_slug(nome)
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == slug))
    if pessoa is None:
        pessoa = Pessoa(slug=slug, nome=nome.strip())
        db.add(pessoa)
        db.flush()
        logger.info(f"Pessoa criada: '{nome}' (id={pessoa.id})")
    return pessoa


def _get_or_create_empresa(db: Session, nome: str) -> Empresa:
    slug = gerar_slug(nome)
    empresa = db.scalar(select(Empresa).where(Empresa.slug == slug))
    if empresa is None:
        empresa = Empresa(slug=slug, nome=nome.strip())
        db.add(empresa)
        db.flush()
    return empresa


_ANO_NO_NOME = re.compile(r"\b20\d{2}\b")


def _canonico(nome: str) -> str:
    """Nome de evento sem ano/edição, para dedup ('Executivo de Valor 2026' == 'Executivo de Valor')."""
    return _ANO_NO_NOME.sub("", nome).strip(" -–—").casefold()


def _get_or_create_evento(db: Session, nome: str, fonte_url: str | None) -> Evento:
    nome_limpo = nome.strip()
    alvo = _canonico(nome_limpo)
    # Varredura em Python: volume de eventos no MVP é pequeno e a comparação
    # canônica (sem ano) não é expressável em SQL portável de forma simples.
    for evento in db.scalars(select(Evento)).all():
        if _canonico(evento.nome) == alvo:
            return evento
    evento = Evento(nome=nome_limpo, fonte_url=fonte_url)
    db.add(evento)
    db.flush()
    return evento


def _vincular_participante(db: Session, evento: Evento, pessoa: Pessoa) -> None:
    ja_existe = db.execute(
        select(evento_participante).where(
            evento_participante.c.evento_id == evento.id,
            evento_participante.c.pessoa_id == pessoa.id,
        )
    ).first()
    if not ja_existe:
        db.execute(
            evento_participante.insert().values(evento_id=evento.id, pessoa_id=pessoa.id)
        )


def _parse_data(valor: str | None) -> date | None:
    """Tenta interpretar a data do SerpAPI; devolve None se não for ISO."""
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except (ValueError, TypeError):
        return None


def _persistir_mencoes(db: Session, pessoa: Pessoa, brutas: list[dict]) -> list[Mencao]:
    """Grava menções novas (deduplicadas por URL) e retorna as criadas."""
    novas: list[Mencao] = []
    for item in brutas:
        url = item.get("url")
        if not url:
            continue
        ja_existe = db.scalar(
            select(Mencao).where(Mencao.pessoa_id == pessoa.id, Mencao.url == url)
        )
        if ja_existe:
            continue
        mencao = Mencao(
            pessoa_id=pessoa.id,
            fonte=item.get("fonte") or "outros",
            url=url,
            titulo=item.get("titulo"),
            texto=item.get("snippet"),
            data_publicacao=_parse_data(item.get("data_publicacao")),
        )
        db.add(mencao)
        novas.append(mencao)
    db.flush()
    return novas


# ---------- etapas do pipeline ----------


def _atualizar_com_linkedin(pessoa: Pessoa) -> None:
    """Enriquece a Pessoa com dados do LinkedIn, se houver URL conhecida."""
    if not pessoa.linkedin_url:
        return
    perfil = coletar_perfil_linkedin(pessoa.linkedin_url)
    if not perfil:
        return
    pessoa.nome_completo = pessoa.nome_completo or perfil.get("fullName")
    pessoa.cargo_atual = pessoa.cargo_atual or perfil.get("headline")
    pessoa.bio = pessoa.bio or perfil.get("summary") or perfil.get("about")
    pessoa.foto_url = pessoa.foto_url or perfil.get("profilePicture")
    logger.info(f"Pessoa {pessoa.id}: enriquecida com LinkedIn")


def _processar_mencao(
    db: Session, pessoa: Pessoa, mencao: Mencao, consolidado: dict
) -> None:
    """Roda o extrator LLM sobre uma menção e persiste o que ele encontrar."""
    texto = "\n".join(filter(None, [mencao.titulo, mencao.texto]))
    if not texto.strip():
        return

    entidades = extrair(texto, pessoa.nome)
    if entidades is None:
        return

    mencao.sentimento = entidades.sentimento
    mencao.temas = ",".join(entidades.temas[:10]) if entidades.temas else None

    # Nota: NÃO usamos as datas do extrator como data de publicação — o texto
    # cita datas de outros fatos (posse, anúncio) e rotulá-las como publicação
    # gera erro factual no briefing. Datas vêm só da URL/SerpAPI (higienização).

    # Cargo do alvo: preenche a ficha se ainda estiver vazia.
    if not pessoa.cargo_atual and entidades.cargo_pessoa_alvo:
        pessoa.cargo_atual = entidades.cargo_pessoa_alvo
        consolidado["cargo_atual"] = pessoa.cargo_atual

    evidencia = {"mencao_url": mencao.url, "titulo": mencao.titulo}

    for nome_empresa in entidades.empresas_mencionadas:
        _get_or_create_empresa(db, nome_empresa)

    for nome_evento in entidades.eventos:
        evento = _get_or_create_evento(db, nome_evento, mencao.url)
        _vincular_participante(db, evento, pessoa)

    for nome_pessoa in entidades.pessoas_mencionadas[:MAX_COMENCIONADOS]:
        if gerar_slug(nome_pessoa) == pessoa.slug:
            continue  # o próprio alvo citado com variação do nome
        co_pessoa = _get_or_create_pessoa(db, nome_pessoa)
        reforcar_relacao(db, pessoa.id, co_pessoa.id, "co_mencionado", evidencia)

    consolidado["mencoes"].append(
        {
            "fonte": mencao.fonte,
            "titulo": mencao.titulo,
            "data": mencao.data_publicacao,
            "sentimento": entidades.sentimento,
            "temas": entidades.temas,
        }
    )
    consolidado["temas"].extend(entidades.temas)
    consolidado["empresas"].extend(entidades.empresas_mencionadas)
    consolidado["eventos"].extend(entidades.eventos)
    consolidado["valores_monetarios"].extend(entidades.valores_monetarios)
    consolidado["pessoas_relacionadas"].extend(entidades.pessoas_mencionadas)


# ---------- entry point ----------


def executar_busca(job_id: int) -> None:
    """Entry point invocado pelo RQ."""
    db: Session = SessionLocal()
    job: JobColeta | None = None
    try:
        job = db.get(JobColeta, job_id)
        if job is None:
            logger.error(f"Job {job_id} não encontrado")
            return

        job.status = StatusJob.RUNNING
        db.commit()
        logger.info(
            f"Job {job_id}: iniciando coleta de '{job.termo_busca}' (pipeline v{PIPELINE_VERSAO})"
        )

        # 1. Pessoa alvo
        pessoa = _get_or_create_pessoa(db, job.termo_busca)
        job.pessoa_id = pessoa.id
        db.commit()

        # 2. Coleta — imprensa BR e LinkedIn (tolerantes a falha)
        brutas = buscar_mencoes(pessoa.nome, limite=MAX_MENCOES)
        novas = _persistir_mencoes(db, pessoa, brutas)
        logger.info(f"Job {job_id}: {len(novas)} menções novas de {len(brutas)} coletadas")

        # Higienização (sem custo de LLM), cobre registros de execuções antigas:
        # remove menções de páginas-índice/comentários e preenche datas via URL.
        for m in db.scalars(select(Mencao).where(Mencao.pessoa_id == pessoa.id)).all():
            if _eh_pagina_indice(m.url):
                db.delete(m)
                logger.info(f"Job {job_id}: menção índice removida ({m.url[:60]})")
            elif m.data_publicacao is None:
                m.data_publicacao = _parse_data(_data_da_url(m.url))
        db.commit()

        # Auto-recuperação: menções gravadas em execuções anteriores que nunca
        # passaram pelo extrator (sentimento nulo) entram na fila de novo —
        # um force_refresh conserta dossiês que falharam no meio.
        pendentes = db.scalars(
            select(Mencao).where(
                Mencao.pessoa_id == pessoa.id, Mencao.sentimento.is_(None)
            )
        ).all()
        if len(pendentes) > len(novas):
            logger.info(
                f"Job {job_id}: reprocessando {len(pendentes) - len(novas)} menções pendentes de execuções anteriores"
            )

        _atualizar_com_linkedin(pessoa)
        db.commit()

        # 3. Extração LLM + grafo
        consolidado: dict = {
            "nome": pessoa.nome,
            "cargo_atual": pessoa.cargo_atual,
            "mencoes": [],
            "temas": [],
            "empresas": [],
            "eventos": [],
            "valores_monetarios": [],
            "pessoas_relacionadas": [],
        }
        for mencao in pendentes[:MAX_EXTRACOES]:
            _processar_mencao(db, pessoa, mencao, consolidado)
        db.commit()

        # 4. Briefing executivo
        if consolidado["mencoes"]:
            briefing = sintetizar(consolidado)
            if briefing:
                pessoa.briefing = briefing
        else:
            logger.warning(f"Job {job_id}: sem menções processadas, briefing mantido")

        # Marca o perfil como fresco para o cache de /busca
        pessoa.atualizado_em = datetime.now(timezone.utc)

        job.status = StatusJob.DONE
        job.finalizado_em = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Job {job_id}: concluído — pessoa_id={pessoa.id}")

    except Exception as exc:
        logger.exception(f"Job {job_id} falhou")
        db.rollback()
        if job is not None:
            job.status = StatusJob.FAILED
            job.erro = str(exc)
            job.finalizado_em = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
