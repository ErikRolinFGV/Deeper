"""Consultas de grafo: CTE recursiva para alcance + ORM para as arestas.

A CTE resolve só a *alcançabilidade* (quais pessoas estão a até N saltos da
raiz). As arestas em si vêm por ORM na sequência — o que permite devolver a
coluna JSON `evidencias` sem esbarrar no DISTINCT sobre JSON (sem operador de
igualdade no Postgres) e mantém um único formato de saída.
"""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.relacao import Relacao

# Evidências por aresta enviadas ao frontend (as mais recentes)
MAX_EVIDENCIAS = 8


def vizinhos_em_profundidade(
    db: Session,
    pessoa_id: int,
    profundidade: int = 2,
    peso_minimo: int = 2,
) -> dict:
    """Retorna {'nodes': [...], 'edges': [...]} expandindo até `profundidade` saltos.

    Cada edge inclui `evidencias` (matérias/cargos que comprovam a relação),
    limitadas às MAX_EVIDENCIAS mais recentes.
    """
    sql = text(
        """
        WITH RECURSIVE rede AS (
            SELECT pessoa_a_id AS a, pessoa_b_id AS b, 1 AS nivel
            FROM relacao
            WHERE (pessoa_a_id = :raiz OR pessoa_b_id = :raiz)
              AND peso >= :peso_minimo

            UNION

            SELECT r.pessoa_a_id, r.pessoa_b_id, rede.nivel + 1
            FROM relacao r
            JOIN rede ON (r.pessoa_a_id = rede.a OR r.pessoa_a_id = rede.b
                       OR r.pessoa_b_id = rede.a OR r.pessoa_b_id = rede.b)
            WHERE rede.nivel < :profundidade
              AND r.peso >= :peso_minimo
        )
        SELECT DISTINCT a, b FROM rede;
        """
    )
    rows = db.execute(
        sql,
        {"raiz": pessoa_id, "profundidade": profundidade, "peso_minimo": peso_minimo},
    ).fetchall()

    nodes: set[int] = set()
    for a, b in rows:
        nodes.update({a, b})

    if not nodes:
        return {"nodes": [], "edges": []}

    relacoes = db.scalars(
        select(Relacao).where(
            Relacao.pessoa_a_id.in_(nodes),
            Relacao.pessoa_b_id.in_(nodes),
            Relacao.peso >= peso_minimo,
        )
    ).all()

    edges = [
        {
            "source": r.pessoa_a_id,
            "target": r.pessoa_b_id,
            "tipo": r.tipo,
            "peso": r.peso,
            "evidencias": (r.evidencias or [])[-MAX_EVIDENCIAS:],
        }
        for r in relacoes
    ]

    return {"nodes": [{"id": n} for n in nodes], "edges": edges}
