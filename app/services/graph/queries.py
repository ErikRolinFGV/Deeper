"""Consultas de grafo: busca em largura pelas arestas efetivamente visíveis.

A expansão é feita em níveis (BFS), caminhando SOMENTE por relações que serão
desenhadas — peso suficiente e não ocultas. Isso garante que todo nó do
resultado seja alcançável a partir da raiz.

Histórico: a versão anterior usava uma CTE recursiva que ignorava `oculta`.
Uma relação marcada como incorreta ainda trazia seus nós para o grafo, mas
não era desenhada — e o grupo aparecia flutuando, sem ligação com a raiz.
Um aglomerado solto sugere ao analista uma relação que não existe, então
o custo do bug era de interpretação, não só estético.
"""

from sqlalchemy import or_, select
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
    visiveis = (
        Relacao.peso >= peso_minimo,
        Relacao.oculta.is_(False),
    )

    # ---- BFS: no máximo `profundidade` consultas ----
    alcancados: set[int] = {pessoa_id}
    fronteira: set[int] = {pessoa_id}
    for _ in range(profundidade):
        if not fronteira:
            break
        rels = db.scalars(
            select(Relacao).where(
                or_(
                    Relacao.pessoa_a_id.in_(fronteira),
                    Relacao.pessoa_b_id.in_(fronteira),
                ),
                *visiveis,
            )
        ).all()
        novos = {
            lado
            for r in rels
            for lado in (r.pessoa_a_id, r.pessoa_b_id)
            if lado not in alcancados
        }
        alcancados |= novos
        fronteira = novos

    if len(alcancados) <= 1:
        return {"nodes": [], "edges": []}

    # Arestas entre os nós alcançados — inclui as que "fecham" ciclos dentro
    # do conjunto, que são legítimas porque todos os pontos já são acessíveis.
    relacoes = db.scalars(
        select(Relacao).where(
            Relacao.pessoa_a_id.in_(alcancados),
            Relacao.pessoa_b_id.in_(alcancados),
            *visiveis,
        )
    ).all()

    edges = [
        {
            "id": r.id,  # necessário para o frontend anotar a relação
            "source": r.pessoa_a_id,
            "target": r.pessoa_b_id,
            "tipo": r.tipo,
            "peso": r.peso,
            "evidencias": (r.evidencias or [])[-MAX_EVIDENCIAS:],
            "rotulo": r.rotulo,
            "nota": r.nota,
            "anotado_em": r.anotado_em.isoformat() if r.anotado_em else None,
        }
        for r in relacoes
    ]

    return {"nodes": [{"id": n} for n in sorted(alcancados)], "edges": edges}
