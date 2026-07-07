"""GET /grafo/{id} — retorna nós e arestas para visualização da rede de conexões."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa
from app.services.graph.queries import vizinhos_em_profundidade

router = APIRouter(prefix="/grafo", tags=["grafo"])


@router.get("/{pessoa_id}")
def obter_grafo(
    pessoa_id: int,
    profundidade: int = Query(2, ge=1, le=3),
    peso_minimo: int = Query(1, ge=1),
    db: Session = Depends(get_db),
) -> dict:
    """Retorna {'nodes': [...], 'edges': [...]} pronto para Cytoscape.js.

    Expande a rede a partir do nó raiz via CTE recursiva até `profundidade`
    saltos, filtrando arestas com peso >= peso_minimo. Os nós vêm enriquecidos
    com nome e cargo atual para renderização direta no frontend.
    """
    raiz = db.get(Pessoa, pessoa_id)
    if raiz is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")

    grafo = vizinhos_em_profundidade(
        db, pessoa_id=pessoa_id, profundidade=profundidade, peso_minimo=peso_minimo
    )

    # Garante que a raiz aparece mesmo sem nenhuma aresta.
    ids = {n["id"] for n in grafo["nodes"]} | {pessoa_id}

    pessoas = db.scalars(select(Pessoa).where(Pessoa.id.in_(ids))).all()
    por_id = {p.id: p for p in pessoas}

    nodes = []
    for node_id in sorted(ids):
        p = por_id.get(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": p.nome if p else f"#{node_id}",
                "cargo_atual": p.cargo_atual if p else None,
                "foto_url": p.foto_url if p else None,
                "raiz": node_id == pessoa_id,
            }
        )

    return {"nodes": nodes, "edges": grafo["edges"]}
