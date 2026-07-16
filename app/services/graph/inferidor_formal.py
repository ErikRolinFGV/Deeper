"""Inferidor de relações formais a partir do histórico de cargos (LinkedIn).

Diferencial FSB: além de co-menções na imprensa, o grafo ganha laços
verificáveis — duas pessoas que passaram pela MESMA empresa em períodos
sobrepostos viram aresta `colega_empresa`; se as duas funções são de
conselho, `co_board`.

Regras:
- Sobreposição usa intervalos abertos: data nula = "não sabemos quando
  começou/terminou", então só descartamos o par quando os dois períodos são
  datados e comprovadamente disjuntos.
- As arestas só surgem entre pessoas JÁ pesquisadas (que têm cargos no
  banco) — o grafo formal engrossa com o uso da ferramenta.
- Idempotente: a mesma empresa não gera evidência duplicada nem inflaciona
  o peso em force_refresh.
"""

import re
from datetime import date

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cargo import Cargo
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.graph.construtor import reforcar_relacao

_CONSELHO = re.compile(r"conselh|board", re.IGNORECASE)


def _intervalo(c: Cargo) -> tuple[date, date]:
    return (c.inicio or date.min, c.fim or date.max)


def _sobrepoe(a: Cargo, b: Cargo) -> bool:
    ai, af = _intervalo(a)
    bi, bf = _intervalo(b)
    return ai <= bf and bi <= af


def inferir_relacoes_formais(db: Session, pessoa: Pessoa) -> int:
    """Cria/reforça arestas formais da pessoa com quem compartilha empresas.

    Retorna o número de evidências novas adicionadas.
    """
    novas = 0
    meus = db.scalars(select(Cargo).where(Cargo.pessoa_id == pessoa.id)).all()

    for meu in meus:
        colegas = db.scalars(
            select(Cargo).where(
                Cargo.empresa_id == meu.empresa_id,
                Cargo.pessoa_id != pessoa.id,
            )
        ).all()
        for outro in colegas:
            if not _sobrepoe(meu, outro):
                continue  # períodos datados e disjuntos: não eram colegas

            tipo = (
                "co_board"
                if _CONSELHO.search(meu.funcao or "") and _CONSELHO.search(outro.funcao or "")
                else "colega_empresa"
            )
            empresa_nome = meu.empresa.nome if meu.empresa else f"empresa#{meu.empresa_id}"

            # Idempotência: se a aresta já tem evidência desta empresa, pula.
            a_id, b_id = sorted((pessoa.id, outro.pessoa_id))
            rel = (
                db.query(Relacao)
                .filter_by(pessoa_a_id=a_id, pessoa_b_id=b_id, tipo=tipo)
                .one_or_none()
            )
            if rel and any(ev.get("empresa") == empresa_nome for ev in rel.evidencias):
                continue

            evidencia = {
                "fonte": "linkedin_cargos",
                "empresa": empresa_nome,
                "funcao_a": meu.funcao,
                "funcao_b": outro.funcao,
            }
            reforcar_relacao(db, pessoa.id, outro.pessoa_id, tipo, evidencia)
            novas += 1
            logger.debug(
                f"Relação formal: {pessoa.id} <-> {outro.pessoa_id} ({tipo} via {empresa_nome})"
            )

    return novas
