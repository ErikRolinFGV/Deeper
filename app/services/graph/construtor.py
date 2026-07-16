"""Constrói arestas em Relacao a partir das extrações do LLM.

Lógica principal: para cada pessoa co-mencionada em uma extração,
cria (ou reforça) uma aresta entre a pessoa alvo e a co-mencionada.
"""

from loguru import logger
from sqlalchemy.orm import Session

from app.models.relacao import Relacao


def reforcar_relacao(
    db: Session,
    pessoa_a_id: int,
    pessoa_b_id: int,
    tipo: str,
    evidencia: dict,
) -> Relacao:
    """Cria a aresta se não existe; se existe, soma +1 ao peso e anexa evidência."""
    # Convenção: garantir A < B para arestas simétricas
    if pessoa_a_id > pessoa_b_id and tipo in {"co_mencionado", "co_evento", "co_board", "colega_empresa"}:
        pessoa_a_id, pessoa_b_id = pessoa_b_id, pessoa_a_id

    rel = (
        db.query(Relacao)
        .filter_by(pessoa_a_id=pessoa_a_id, pessoa_b_id=pessoa_b_id, tipo=tipo)
        .one_or_none()
    )

    if rel is None:
        rel = Relacao(
            pessoa_a_id=pessoa_a_id,
            pessoa_b_id=pessoa_b_id,
            tipo=tipo,
            peso=1,
            evidencias=[evidencia],
        )
        db.add(rel)
        # Flush imediato: com autoflush desligado na sessão, sem isso uma
        # segunda chamada no mesmo lote não encontraria esta aresta e criaria
        # uma duplicata, violando uq_relacao_tripla.
        db.flush()
        logger.debug(f"Nova relação {tipo}: {pessoa_a_id} <-> {pessoa_b_id}")
    else:
        rel.peso += 1
        rel.evidencias = [*rel.evidencias, evidencia]
        logger.debug(f"Reforço relação {tipo}: {pessoa_a_id} <-> {pessoa_b_id} (peso {rel.peso})")

    return rel
