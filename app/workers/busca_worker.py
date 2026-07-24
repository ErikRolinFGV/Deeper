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
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.busca import gerar_slug
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.cargo import Cargo
from app.models.empresa import Empresa
from app.models.evento import Evento, evento_participante
from app.models.job import JobColeta, StatusJob
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.collectors.apify_linkedin import (
    coletar_perfil_linkedin,
    descobrir_linkedin_url,
    normalizar_perfil,
)
from app.services.collectors.leitor_artigo import baixar_texto
from app.services.collectors.serpapi_news import (
    _data_da_url,
    _eh_pagina_indice,
    buscar_mencoes,
    limpar_url,
)
from app.services.graph.construtor import reforcar_relacao
from app.services.graph.inferidor_formal import inferir_relacoes_formais
from app.services.llm.extrator import extrair
from app.services.llm.sintetizador import sintetizar

# Versão do pipeline — aparece no log de cada job. Se o log mostrar uma
# versão antiga, o processo do worker precisa ser reiniciado.
PIPELINE_VERSAO = "2026-07-23.2"

# Limites de MVP: controlam custo de API por busca.
MAX_MENCOES = 30          # resultados pedidos ao SerpAPI
MAX_EXTRACOES = 20        # menções que passam pelo extrator LLM
MAX_COMENCIONADOS = 10    # pessoas co-mencionadas processadas por menção
MIN_TEXTO_COMPLETO = 500  # menção com texto menor que isso baixa o corpo da matéria


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
        url = limpar_url(item.get("url") or "")
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


def _persistir_perfil_linkedin(db: Session, pessoa: Pessoa, perfil: dict) -> None:
    """Grava na ficha e no histórico o perfil já normalizado. Idempotente.

    O LinkedIn é a fonte primária destes campos: quando presente, VENCE o
    valor antigo (que pode vir de coleta anterior ou de homônimo).
    """
    pessoa.nome_completo = perfil.get("nome_completo") or pessoa.nome_completo
    pessoa.bio = perfil.get("sobre") or pessoa.bio
    pessoa.foto_url = perfil.get("foto_url") or pessoa.foto_url
    pessoa.localizacao = perfil.get("localizacao") or pessoa.localizacao

    # Cargo atual: a experiência corrente do LinkedIn é a fonte mais
    # estruturada que temos — tem prioridade sobre menções de imprensa.
    atuais = [e for e in perfil.get("experiencias", []) if e.get("atual")]
    if atuais and atuais[0].get("funcao"):
        empresa_nome = atuais[0].get("empresa")
        pessoa.cargo_atual = (
            f"{atuais[0]['funcao']} — {empresa_nome}" if empresa_nome else atuais[0]["funcao"]
        )
    elif not pessoa.cargo_atual and perfil.get("headline"):
        pessoa.cargo_atual = perfil["headline"]

    # Histórico profissional → Cargo/Empresa (dedup por pessoa+empresa+função)
    novos = 0
    for exp in perfil.get("experiencias", []):
        if not exp.get("empresa") or not exp.get("funcao"):
            continue
        empresa = _get_or_create_empresa(db, exp["empresa"])
        ja_existe = db.scalar(
            select(Cargo).where(
                Cargo.pessoa_id == pessoa.id,
                Cargo.empresa_id == empresa.id,
                Cargo.funcao == exp["funcao"],
            )
        )
        if ja_existe:
            ja_existe.inicio = ja_existe.inicio or exp.get("inicio")
            ja_existe.fim = ja_existe.fim or exp.get("fim")
            ja_existe.eh_atual = bool(exp.get("atual"))
            continue
        db.add(
            Cargo(
                pessoa_id=pessoa.id,
                empresa_id=empresa.id,
                funcao=exp["funcao"],
                inicio=exp.get("inicio"),
                fim=exp.get("fim"),
                eh_atual=bool(exp.get("atual")),
            )
        )
        novos += 1
    db.flush()
    logger.info(f"Pessoa {pessoa.id}: LinkedIn aplicado ({novos} cargos novos)")


def _atualizar_com_linkedin(db: Session, pessoa: Pessoa) -> dict | None:
    """Descobre, coleta e aplica o perfil LinkedIn da pessoa.

    Fluxo: descoberta da URL via SerpAPI (1x, só se desconhecida) →
    cache TTL (payload bruto salvo em pessoa.linkedin_dados, evita pagar o
    actor de novo em force_refresh) → coleta Apify → normalização →
    persistência (ficha + cargos + empresas).

    Retorna o perfil normalizado (para o sintetizador) ou None.
    """
    if not pessoa.linkedin_url:
        pessoa.linkedin_url = descobrir_linkedin_url(pessoa.nome, pessoa.cargo_atual)
    if not pessoa.linkedin_url:
        return None

    agora = datetime.now(timezone.utc)

    # Cache: payload fresco não gera nova cobrança no Apify.
    if pessoa.linkedin_dados and pessoa.linkedin_coletado_em:
        coletado = pessoa.linkedin_coletado_em
        if coletado.tzinfo is None:
            coletado = coletado.replace(tzinfo=timezone.utc)
        if (agora - coletado).days < settings.LINKEDIN_TTL_DIAS:
            logger.info(f"Pessoa {pessoa.id}: LinkedIn em cache (TTL), sem recoleta")
            perfil = normalizar_perfil(pessoa.linkedin_dados)
            _persistir_perfil_linkedin(db, pessoa, perfil)
            return perfil

    bruto = coletar_perfil_linkedin(pessoa.linkedin_url)
    if not bruto:
        return None

    pessoa.linkedin_dados = bruto
    pessoa.linkedin_coletado_em = agora
    perfil = normalizar_perfil(bruto)
    _persistir_perfil_linkedin(db, pessoa, perfil)
    return perfil


def _processar_mencao(
    db: Session, pessoa: Pessoa, mencao: Mencao, extras: dict
) -> None:
    """Roda o extrator LLM sobre uma menção e persiste o que ele encontrar.

    `extras` acumula o que NÃO é persistido em tabela própria (valores
    monetários, empresas citadas) para entrar no consolidado do sintetizador.
    """
    # Enriquecimento: snippet do buscador é curto (~200 chars) e manchete de
    # economia raramente nomeia pessoas — o corpo da matéria é onde o grafo
    # nasce. Baixa uma vez e persiste em mencao.texto (reuso sem re-download).
    if len(mencao.texto or "") < MIN_TEXTO_COMPLETO:
        corpo = baixar_texto(mencao.url)
        if corpo:
            mencao.texto = corpo
            logger.debug(f"Menção {mencao.id}: corpo baixado ({len(corpo)} chars)")

    texto = "\n".join(filter(None, [mencao.titulo, mencao.texto]))
    if not texto.strip():
        return

    # Contexto rico (nome + cargo/empresa) permite ao extrator detectar
    # homônimos: matéria sobre "outro" Renato Costa é descartada.
    contexto = (
        f"{pessoa.nome} — {pessoa.cargo_atual}" if pessoa.cargo_atual else pessoa.nome
    )
    entidades = extrair(texto, contexto)
    if entidades is None:
        return

    if not getattr(entidades, "texto_e_sobre_alvo", True):
        logger.info(
            f"Menção {mencao.id} descartada: homônimo detectado ({(mencao.titulo or mencao.url)[:60]})"
        )
        db.delete(mencao)
        return

    mencao.sentimento = entidades.sentimento
    mencao.temas = ",".join(entidades.temas[:10]) if entidades.temas else None

    # Nota: NÃO usamos as datas do extrator como data de publicação — o texto
    # cita datas de outros fatos (posse, anúncio) e rotulá-las como publicação
    # gera erro factual no briefing. Datas vêm só da URL/SerpAPI (higienização).

    # Cargo do alvo: preenche a ficha se ainda estiver vazia.
    if not pessoa.cargo_atual and entidades.cargo_pessoa_alvo:
        pessoa.cargo_atual = entidades.cargo_pessoa_alvo

    # Contexto da co-menção: lista/ranking ("50 mais ricos") não é relação
    # genuína — rotulamos a evidência para o grafo exibir a diferença.
    # Duas defesas: o rótulo do extrator LLM e a heurística de fan-out
    # (matéria que cita 6+ pessoas juntas é quase sempre lista).
    eh_lista = bool(getattr(entidades, "eh_lista_ou_ranking", False)) or (
        len(entidades.pessoas_mencionadas) > 5
    )
    evidencia = {
        "mencao_url": mencao.url,
        "titulo": mencao.titulo,
        "contexto": "lista" if eh_lista else "direta",
    }

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

    extras["empresas"].extend(entidades.empresas_mencionadas)
    extras["valores_monetarios"].extend(entidades.valores_monetarios)


def _unicos(itens: list) -> list:
    """Deduplica preservando a ordem."""
    return list(dict.fromkeys(itens))


def _montar_consolidado(
    db: Session, pessoa: Pessoa, perfil_linkedin: dict | None, extras: dict
) -> dict:
    """Monta o payload do sintetizador a partir do ESTADO COMPLETO do banco.

    Antes o consolidado só continha as menções processadas na execução
    corrente — se todas já tinham sido extraídas antes, o briefing saía
    dizendo "sem menções na imprensa" com dezenas delas no banco.
    """
    mencoes = db.scalars(
        select(Mencao).where(
            Mencao.pessoa_id == pessoa.id, Mencao.sentimento.is_not(None)
        )
    ).all()
    mencoes.sort(key=lambda m: m.data_publicacao or date.min, reverse=True)

    lista_mencoes, temas = [], []
    for m in mencoes:
        temas_m = [t for t in (m.temas or "").split(",") if t]
        temas.extend(temas_m)
        lista_mencoes.append(
            {
                "fonte": m.fonte,
                "titulo": m.titulo,
                "data": m.data_publicacao,
                "sentimento": m.sentimento,
                "temas": temas_m,
            }
        )

    eventos = db.scalars(
        select(Evento)
        .join(evento_participante, evento_participante.c.evento_id == Evento.id)
        .where(evento_participante.c.pessoa_id == pessoa.id)
    ).all()

    relacoes = db.scalars(
        select(Relacao).where(
            or_(Relacao.pessoa_a_id == pessoa.id, Relacao.pessoa_b_id == pessoa.id)
        )
    ).all()
    pessoas_relacionadas = []
    for rel in relacoes:
        outro_id = rel.pessoa_b_id if rel.pessoa_a_id == pessoa.id else rel.pessoa_a_id
        outro = db.get(Pessoa, outro_id)
        if outro:
            pessoas_relacionadas.append(
                {"nome": outro.nome, "tipo": rel.tipo, "forca": rel.peso}
            )
    pessoas_relacionadas.sort(key=lambda p: p["forca"], reverse=True)

    cargos = db.scalars(select(Cargo).where(Cargo.pessoa_id == pessoa.id)).all()

    consolidado: dict = {
        "nome": pessoa.nome,
        "cargo_atual": pessoa.cargo_atual,
        "localizacao": pessoa.localizacao,
        "mencoes": lista_mencoes,
        "temas": _unicos(temas),
        "empresas": _unicos(extras.get("empresas", [])),
        "eventos": _unicos([e.nome for e in eventos]),
        "valores_monetarios": _unicos(extras.get("valores_monetarios", [])),
        "pessoas_relacionadas": pessoas_relacionadas[:10],
        "historico_profissional": [
            {
                "funcao": c.funcao,
                "empresa": c.empresa.nome if c.empresa else None,
                "inicio": c.inicio,
                "fim": "atual" if c.eh_atual else c.fim,
            }
            for c in cargos
        ],
    }

    if perfil_linkedin:
        consolidado["linkedin"] = {
            "headline": perfil_linkedin.get("headline"),
            "localizacao": perfil_linkedin.get("localizacao"),
            "resumo": (perfil_linkedin.get("sobre") or "")[:600] or None,
            "seguidores": perfil_linkedin.get("seguidores"),
            "formacao": [
                {"instituicao": f.get("instituicao"), "curso": f.get("area") or f.get("grau")}
                for f in perfil_linkedin.get("formacao", [])[:5]
            ],
        }

    return consolidado


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
        # remove menções de páginas-índice, normaliza URLs (tracking params),
        # funde duplicatas da mesma matéria e preenche datas via URL.
        vistos: dict[str, Mencao] = {}
        for m in db.scalars(select(Mencao).where(Mencao.pessoa_id == pessoa.id)).all():
            url_limpa = limpar_url(m.url)
            if _eh_pagina_indice(url_limpa):
                db.delete(m)
                logger.info(f"Job {job_id}: menção índice removida ({m.url[:60]})")
                continue
            m.url = url_limpa
            anterior = vistos.get(url_limpa)
            if anterior is not None:
                # Mesma matéria com tracking diferente: mantém a mais completa.
                manter, apagar = anterior, m
                if anterior.sentimento is None and m.sentimento is not None:
                    manter, apagar = m, anterior
                manter.data_publicacao = manter.data_publicacao or apagar.data_publicacao
                db.delete(apagar)
                logger.info(f"Job {job_id}: menção duplicada fundida ({url_limpa[:60]})")
            vistos[url_limpa] = manter if anterior is not None else m
            alvo = vistos[url_limpa]
            if alvo.data_publicacao is None:
                alvo.data_publicacao = _parse_data(_data_da_url(alvo.url))
        # Eventos herdados de menções-índice antigas também saem.
        for evento in db.scalars(select(Evento)).all():
            if evento.fonte_url and _eh_pagina_indice(evento.fonte_url):
                db.execute(
                    evento_participante.delete().where(
                        evento_participante.c.evento_id == evento.id
                    )
                )
                db.delete(evento)
                logger.info(f"Job {job_id}: evento de página-índice removido ({evento.nome[:50]})")
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

        perfil_linkedin = _atualizar_com_linkedin(db, pessoa)
        db.commit()

        # Relações formais: cargos sobrepostos na mesma empresa → arestas
        # colega_empresa/co_board (só entre pessoas já pesquisadas).
        formais = inferir_relacoes_formais(db, pessoa)
        if formais:
            logger.info(f"Job {job_id}: {formais} relações formais inferidas via cargos")
        db.commit()

        # 3. Extração LLM + grafo
        extras: dict = {"empresas": [], "valores_monetarios": []}
        for mencao in pendentes[:MAX_EXTRACOES]:
            _processar_mencao(db, pessoa, mencao, extras)
        db.commit()

        # 4. Briefing executivo — sintetiza o estado completo do banco
        # (menções/eventos/relações/cargos de TODAS as execuções, não só desta)
        consolidado = _montar_consolidado(db, pessoa, perfil_linkedin, extras)
        if consolidado["mencoes"] or perfil_linkedin:
            briefing = sintetizar(consolidado)
            if briefing:
                pessoa.briefing = briefing
        else:
            logger.warning(f"Job {job_id}: sem menções nem LinkedIn, briefing mantido")

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
