"""POST /busca — entrada do fluxo de coleta sobre um executivo."""

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.job import JobColeta, StatusJob
from app.models.pessoa import Pessoa
from app.schemas.busca import BuscaRequest, BuscaResponse

router = APIRouter(prefix="/busca", tags=["busca"])


def gerar_slug(nome: str) -> str:
    """Normaliza um nome em slug canônico: 'Eduardo Bartolomeo' -> 'eduardo-bartolomeo'."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def _resetar_dossie(db: Session, pessoa: Pessoa) -> None:
    """Apaga tudo que foi derivado de coletas anteriores desta pessoa.

    Usado quando o usuário confirma um LinkedIn diferente do salvo: o slug é
    o mesmo (homônimo), mas a pessoa física é outra — menções, cargos,
    relações e briefing antigos contaminariam o novo dossiê.
    """
    from app.models.cargo import Cargo
    from app.models.evento import evento_participante
    from app.models.mencao import Mencao
    from app.models.relacao import Relacao
    from sqlalchemy import delete, or_

    db.execute(delete(Cargo).where(Cargo.pessoa_id == pessoa.id))
    db.execute(delete(Mencao).where(Mencao.pessoa_id == pessoa.id))
    db.execute(
        delete(Relacao).where(
            or_(Relacao.pessoa_a_id == pessoa.id, Relacao.pessoa_b_id == pessoa.id)
        )
    )
    db.execute(
        evento_participante.delete().where(evento_participante.c.pessoa_id == pessoa.id)
    )
    pessoa.nome_completo = None
    pessoa.cargo_atual = None
    pessoa.bio = None
    pessoa.foto_url = None
    pessoa.localizacao = None
    pessoa.briefing = None
    pessoa.linkedin_dados = None
    pessoa.linkedin_coletado_em = None
    logger.info(f"Pessoa {pessoa.id}: dossiê zerado (troca de identidade LinkedIn)")


def enfileirar_coleta(job_id: int) -> None:
    """Enfileira o job de coleta no RQ. Isolado em função para facilitar mock em testes."""
    fila = Queue("coleta", connection=Redis.from_url(settings.REDIS_URL))
    fila.enqueue("app.workers.busca_worker.executar_busca", job_id, job_timeout=600)


@router.post("", response_model=BuscaResponse)
def criar_busca(req: BuscaRequest, db: Session = Depends(get_db)) -> BuscaResponse:
    """Cria ou recupera o perfil de um executivo.

    1. Normaliza o nome em slug canônico.
    2. Cache: se Pessoa existe e foi atualizada há menos de CACHE_TTL_DAYS, retorna direto.
    3. Caso contrário, cria JobColeta e enfileira no Redis (RQ).
    4. Retorna job_id para o cliente fazer polling em GET /job/{id}.
    """
    slug = gerar_slug(req.nome)
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == slug))

    # Busca livre desabilitada para pessoas novas: sem identidade confirmada,
    # "CEO do Nubank" acha um diretor qualquer e o dossiê nasce errado.
    # Pessoa nova só entra com linkedin_url escolhida nas sugestões.
    if pessoa is None and not req.linkedin_url:
        raise HTTPException(
            status_code=422,
            detail=(
                "Selecione uma das sugestões para pesquisar alguém novo — "
                "a busca precisa partir de um perfil LinkedIn confirmado."
            ),
        )

    # URL confirmada pelo usuário (fluxo de sugestões): fixa na pessoa ANTES
    # do worker rodar — a descoberta automática é pulada e homônimos somem.
    # Pessoa nova ou perfil trocado precisam de coleta: o cache não vale
    # (o registro recém-criado nasce com atualizado_em "fresco" mas vazio).
    pular_cache = False
    if req.linkedin_url:
        if pessoa is None:
            pessoa = Pessoa(slug=slug, nome=req.nome.strip(), linkedin_url=req.linkedin_url)
            db.add(pessoa)
            db.flush()
            pular_cache = True
        elif pessoa.linkedin_url != req.linkedin_url:
            # Perfil trocado = OUTRA pessoa física com o mesmo nome (homônimo).
            # Tudo que foi coletado pertence à identidade antiga — zera o
            # dossiê para recomeçar limpo com a identidade escolhida.
            _resetar_dossie(db, pessoa)
            pessoa.linkedin_url = req.linkedin_url
            pular_cache = True
        db.commit()

    # --- Cache hit: perfil existe e está fresco ---
    if pessoa is not None and not req.force_refresh and not pular_cache:
        atualizado = pessoa.atualizado_em
        if atualizado is not None and atualizado.tzinfo is None:
            atualizado = atualizado.replace(tzinfo=timezone.utc)
        limite = datetime.now(timezone.utc) - timedelta(days=settings.CACHE_TTL_DAYS)
        if atualizado is not None and atualizado >= limite:
            return BuscaResponse(
                pessoa_id=pessoa.id,
                cache_hit=True,
                mensagem=f"Perfil de '{pessoa.nome}' em cache (atualizado em {atualizado:%Y-%m-%d}).",
            )

    # --- Cache miss: cria job e enfileira ---
    job = JobColeta(
        termo_busca=req.nome,
        pessoa_id=pessoa.id if pessoa is not None else None,
        status=StatusJob.QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        enfileirar_coleta(job.id)
    except Exception as exc:  # Redis fora do ar não deve derrubar a API
        logger.error(f"Falha ao enfileirar job {job.id}: {exc}")
        job.status = StatusJob.FAILED
        job.erro = f"Fila indisponível: {exc}"
        job.finalizado_em = datetime.now(timezone.utc)
        db.commit()
        return BuscaResponse(
            job_id=job.id,
            pessoa_id=job.pessoa_id,
            cache_hit=False,
            mensagem="Job criado, mas a fila (Redis) está indisponível. Verifique o serviço e tente novamente.",
        )

    logger.info(f"Job {job.id} enfileirado para '{req.nome}' (slug: {slug})")
    return BuscaResponse(
        job_id=job.id,
        pessoa_id=job.pessoa_id,
        cache_hit=False,
        mensagem=f"Coleta de '{req.nome}' enfileirada. Acompanhe em GET /job/{job.id}.",
    )
