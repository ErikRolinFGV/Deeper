"""Manutenção do acervo: limpar dados derivados e excluir pessoas.

Centraliza a remoção em cascata para que a troca de identidade (novo perfil
do LinkedIn) e a exclusão definitiva sigam exatamente a mesma regra — e para
não depender do ON DELETE do banco, que não é garantido no SQLite dos testes.
"""

from loguru import logger
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.alias import AliasPessoa
from app.models.cargo import Cargo
from app.models.evento import evento_participante
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao

# Tipos de relação simétricos seguem a convenção pessoa_a_id < pessoa_b_id
TIPOS_SIMETRICOS = {"co_mencionado", "co_evento", "co_board", "colega_empresa", "manual"}


def localizar_por_slug(db: Session, slug: str) -> Pessoa | None:
    """Acha a pessoa pelo slug OU por um apelido registrado numa fusão.

    É isso que impede a próxima coleta de recriar 'Dani Braun' depois que o
    analista já disse que ela é a 'Daniela Braun'.
    """
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == slug))
    if pessoa is not None:
        return pessoa
    alias = db.scalar(select(AliasPessoa).where(AliasPessoa.slug == slug))
    return db.get(Pessoa, alias.pessoa_id) if alias else None


def registrar_alias(db: Session, pessoa: Pessoa, slug: str, nome: str) -> None:
    """Guarda um nome alternativo, se ainda não existir e não colidir."""
    if slug == pessoa.slug:
        return
    if db.scalar(select(AliasPessoa).where(AliasPessoa.slug == slug)):
        return
    if db.scalar(select(Pessoa).where(Pessoa.slug == slug)):
        return  # o slug ainda pertence a outra pessoa viva
    db.add(AliasPessoa(slug=slug, nome=nome, pessoa_id=pessoa.id))
    db.flush()


def limpar_dados_derivados(db: Session, pessoa: Pessoa) -> dict:
    """Apaga tudo que veio de coleta (cargos, menções, relações, eventos).

    Mantém o registro da Pessoa. Retorna a contagem do que foi removido.
    """
    cargos = db.scalar(
        select(func.count()).select_from(Cargo).where(Cargo.pessoa_id == pessoa.id)
    )
    mencoes = db.scalar(
        select(func.count()).select_from(Mencao).where(Mencao.pessoa_id == pessoa.id)
    )
    relacoes = db.scalar(
        select(func.count()).select_from(Relacao).where(
            or_(Relacao.pessoa_a_id == pessoa.id, Relacao.pessoa_b_id == pessoa.id)
        )
    )

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
    return {"cargos": cargos or 0, "mencoes": mencoes or 0, "relacoes": relacoes or 0}


def _mesclar_relacoes(db: Session, principal: Pessoa, duplicada: Pessoa) -> int:
    """Repõe as arestas da duplicada na principal, somando as coincidentes."""
    por_chave: dict[tuple[int, str], Relacao] = {}
    for r in db.scalars(
        select(Relacao).where(
            or_(Relacao.pessoa_a_id == principal.id, Relacao.pessoa_b_id == principal.id)
        )
    ).all():
        outro = r.pessoa_b_id if r.pessoa_a_id == principal.id else r.pessoa_a_id
        por_chave[(outro, r.tipo)] = r

    movidas = 0
    for r in db.scalars(
        select(Relacao).where(
            or_(Relacao.pessoa_a_id == duplicada.id, Relacao.pessoa_b_id == duplicada.id)
        )
    ).all():
        outro = r.pessoa_b_id if r.pessoa_a_id == duplicada.id else r.pessoa_a_id
        if outro == principal.id:
            db.delete(r)  # a pessoa não se relaciona consigo mesma
            continue

        existente = por_chave.get((outro, r.tipo))
        if existente is not None:
            # Mesma dupla e mesmo tipo: soma peso e junta evidências.
            existente.peso += r.peso
            existente.evidencias = [*(existente.evidencias or []), *(r.evidencias or [])]
            existente.rotulo = existente.rotulo or r.rotulo
            existente.nota = existente.nota or r.nota
            db.delete(r)
        else:
            if r.tipo in TIPOS_SIMETRICOS:
                r.pessoa_a_id, r.pessoa_b_id = sorted((principal.id, outro))
            elif r.pessoa_a_id == duplicada.id:
                r.pessoa_a_id = principal.id
            else:
                r.pessoa_b_id = principal.id
            por_chave[(outro, r.tipo)] = r
        movidas += 1
    db.flush()
    return movidas


def fundir_pessoas(db: Session, principal: Pessoa, duplicada: Pessoa) -> dict:
    """Funde `duplicada` em `principal` — são a mesma pessoa física.

    Resolve o caso em que a imprensa cita "Dani Braun" e o LinkedIn diz
    "Daniela Braun": nenhum algoritmo liga os dois com segurança, mas o
    analista liga em dois segundos. Tudo da duplicada passa para a principal,
    o nome antigo vira apelido e o registro duplicado é removido.
    """
    if principal.id == duplicada.id:
        raise ValueError("Não é possível fundir uma pessoa com ela mesma.")

    resumo = {
        "principal": principal.nome,
        "absorvida": duplicada.nome,
        "cargos": 0,
        "mencoes": 0,
        "relacoes": 0,
        "eventos": 0,
    }

    # ---- cargos (sem repetir empresa+função) ----
    ja_tem = {
        (c.empresa_id, c.funcao)
        for c in db.scalars(select(Cargo).where(Cargo.pessoa_id == principal.id)).all()
    }
    for c in db.scalars(select(Cargo).where(Cargo.pessoa_id == duplicada.id)).all():
        if (c.empresa_id, c.funcao) in ja_tem:
            db.delete(c)
        else:
            c.pessoa_id = principal.id
            resumo["cargos"] += 1

    # ---- menções (dedup por URL) ----
    urls = {
        m.url
        for m in db.scalars(select(Mencao).where(Mencao.pessoa_id == principal.id)).all()
    }
    for m in db.scalars(select(Mencao).where(Mencao.pessoa_id == duplicada.id)).all():
        if m.url in urls:
            db.delete(m)
        else:
            m.pessoa_id = principal.id
            resumo["mencoes"] += 1

    # ---- participações em eventos ----
    eventos_principal = {
        row.evento_id
        for row in db.execute(
            select(evento_participante).where(
                evento_participante.c.pessoa_id == principal.id
            )
        )
    }
    for row in db.execute(
        select(evento_participante).where(
            evento_participante.c.pessoa_id == duplicada.id
        )
    ).all():
        if row.evento_id in eventos_principal:
            continue
        db.execute(
            evento_participante.update()
            .where(
                evento_participante.c.pessoa_id == duplicada.id,
                evento_participante.c.evento_id == row.evento_id,
            )
            .values(pessoa_id=principal.id)
        )
        resumo["eventos"] += 1
    db.execute(
        evento_participante.delete().where(
            evento_participante.c.pessoa_id == duplicada.id
        )
    )

    resumo["relacoes"] = _mesclar_relacoes(db, principal, duplicada)

    # ---- campos da ficha: a principal manda, a duplicada preenche buracos ----
    for campo in (
        "nome_completo", "cargo_atual", "bio", "foto_url", "localizacao",
        "linkedin_url", "linkedin_dados", "linkedin_coletado_em",
        "contexto_origem", "briefing",
    ):
        if getattr(principal, campo, None) in (None, ""):
            valor = getattr(duplicada, campo, None)
            if valor not in (None, ""):
                setattr(principal, campo, valor)
    principal.identidade_confirmada = bool(
        principal.identidade_confirmada or duplicada.identidade_confirmada
    )

    # ---- apelidos: o nome antigo e os apelidos que ela já tinha ----
    for alias in db.scalars(
        select(AliasPessoa).where(AliasPessoa.pessoa_id == duplicada.id)
    ).all():
        alias.pessoa_id = principal.id
    slug_antigo, nome_antigo = duplicada.slug, duplicada.nome

    db.delete(duplicada)
    db.flush()
    registrar_alias(db, principal, slug_antigo, nome_antigo)

    logger.info(
        f"Fusão: '{nome_antigo}' absorvida por '{principal.nome}' "
        f"({resumo['mencoes']} menções, {resumo['relacoes']} relações)"
    )
    return resumo


def _eh_orfa(db: Session, pessoa: Pessoa) -> bool:
    """Nó fantasma: nasceu de uma co-menção e não sobrou nada que o sustente."""
    if pessoa.briefing or pessoa.linkedin_url or pessoa.identidade_confirmada:
        return False
    tem_mencao = db.scalar(
        select(func.count()).select_from(Mencao).where(Mencao.pessoa_id == pessoa.id)
    )
    tem_relacao = db.scalar(
        select(func.count()).select_from(Relacao).where(
            or_(Relacao.pessoa_a_id == pessoa.id, Relacao.pessoa_b_id == pessoa.id)
        )
    )
    return not tem_mencao and not tem_relacao


def excluir_pessoa(db: Session, pessoa: Pessoa, limpar_orfaos: bool = True) -> dict:
    """Remove a pessoa e tudo que dependia dela.

    Com `limpar_orfaos`, também apaga os nós que só existiam por causa dela
    (pessoas criadas a partir de co-menções e que ficaram sem nenhum vínculo).
    """
    nome = pessoa.nome
    # Candidatos a órfão: quem estava ligado a esta pessoa no grafo.
    vizinhos_ids: set[int] = set()
    if limpar_orfaos:
        for rel in db.scalars(
            select(Relacao).where(
                or_(Relacao.pessoa_a_id == pessoa.id, Relacao.pessoa_b_id == pessoa.id)
            )
        ).all():
            vizinhos_ids.add(
                rel.pessoa_b_id if rel.pessoa_a_id == pessoa.id else rel.pessoa_a_id
            )
    vizinhos_ids.discard(pessoa.id)

    resumo = limpar_dados_derivados(db, pessoa)
    db.delete(pessoa)
    db.flush()

    orfaos = []
    for vid in vizinhos_ids:
        vizinho = db.get(Pessoa, vid)
        if vizinho is not None and _eh_orfa(db, vizinho):
            orfaos.append(vizinho.nome)
            db.delete(vizinho)
    db.flush()

    resumo["nome"] = nome
    resumo["orfaos_removidos"] = orfaos
    logger.info(
        f"Pessoa '{nome}' excluída ({resumo['mencoes']} menções, "
        f"{resumo['relacoes']} relações, {len(orfaos)} nós órfãos)"
    )
    return resumo
