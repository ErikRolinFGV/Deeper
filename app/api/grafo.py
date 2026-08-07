"""GET /grafo/{id} — nós e arestas da rede + anotação humana das relações."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao
from app.services.graph.queries import vizinhos_em_profundidade

router = APIRouter(prefix="/grafo", tags=["grafo"])


class ConexaoManualRequest(BaseModel):
    """Conexão registrada pelo analista — conhecimento da casa, não da coleta."""

    pessoa_a_id: int
    pessoa_b_id: int
    rotulo: str = Field(
        ..., min_length=2, max_length=80,
        description="Natureza do vínculo: 'sócios', 'conselho X', 'amigos'...",
    )
    nota: str = Field(
        ..., min_length=3,
        description=(
            "Justificativa — é a evidência desta conexão. Obrigatória: sem "
            "fonte externa, o registro do porquê é o que a torna auditável."
        ),
    )


class LayoutRequest(BaseModel):
    """Posições dos nós arrumadas pelo analista, em coordenadas do grafo."""

    posicoes: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description='{"12": {"x": 140.5, "y": -80.2}, ...} — vazio limpa a disposição',
    )


class AnotacaoRequest(BaseModel):
    """Qualificação humana de uma relação inferida pela máquina."""

    rotulo: str | None = Field(
        None, max_length=80,
        description="Rótulo curto do vínculo: 'filho', 'sócio', 'mentor'...",
    )
    nota: str | None = Field(None, description="Observação livre do analista")
    oculta: bool | None = Field(
        None, description="Marca a conexão como incorreta — some do grafo"
    )


def _pesquisada(p: Pessoa) -> bool:
    """A pessoa foi de fato apurada (não é um nome solto vindo de co-menção)."""
    return bool(p.briefing or p.identidade_confirmada or p.linkedin_url)


@router.post("/relacao")
def criar_conexao_manual(
    req: ConexaoManualRequest, db: Session = Depends(get_db)
) -> dict:
    """Registra uma conexão que o analista conhece e a imprensa não mostrou.

    Fica marcada como `manual` para nunca ser confundida com uma relação
    apurada: no grafo ela aparece com traço próprio e leva a justificativa
    junto, que é a evidência possível quando a fonte é o conhecimento da casa.
    """
    if req.pessoa_a_id == req.pessoa_b_id:
        raise HTTPException(status_code=400, detail="Selecione duas pessoas diferentes.")

    a = db.get(Pessoa, req.pessoa_a_id)
    b = db.get(Pessoa, req.pessoa_b_id)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    if not (_pesquisada(a) and _pesquisada(b)):
        raise HTTPException(
            status_code=400,
            detail=(
                "Só é possível conectar pessoas já pesquisadas. "
                "Pesquise a pessoa antes de registrar a conexão."
            ),
        )

    a_id, b_id = sorted((a.id, b.id))
    evidencia = {
        "fonte": "analista",
        "justificativa": req.nota.strip(),
        "registrado_em": datetime.now(timezone.utc).isoformat(),
    }

    rel = db.scalar(
        select(Relacao).where(
            Relacao.pessoa_a_id == a_id,
            Relacao.pessoa_b_id == b_id,
            Relacao.tipo == "manual",
        )
    )
    if rel is None:
        rel = Relacao(
            pessoa_a_id=a_id, pessoa_b_id=b_id, tipo="manual", peso=1,
            evidencias=[evidencia],
        )
        db.add(rel)
    else:
        rel.peso += 1
        rel.evidencias = [*(rel.evidencias or []), evidencia]
        rel.oculta = False
    rel.rotulo = req.rotulo.strip()
    rel.nota = req.nota.strip()
    rel.anotado_em = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rel)

    logger.info(f"Conexão manual: {a.nome} ↔ {b.nome} ({rel.rotulo})")
    return {
        "id": rel.id,
        "pessoa_a_id": rel.pessoa_a_id,
        "pessoa_b_id": rel.pessoa_b_id,
        "tipo": rel.tipo,
        "rotulo": rel.rotulo,
        "nota": rel.nota,
        "peso": rel.peso,
    }


@router.patch("/relacao/{relacao_id}")
def anotar_relacao(
    relacao_id: int, req: AnotacaoRequest, db: Session = Depends(get_db)
) -> dict:
    """Salva (ou limpa) a anotação humana de uma relação.

    A anotação é do analista, não da coleta: sobrevive a recoletas porque o
    pipeline reforça arestas existentes em vez de recriá-las.
    """
    rel = db.get(Relacao, relacao_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Relação não encontrada")

    if req.oculta is not None:
        rel.oculta = req.oculta
    # rotulo/nota só são tocados quando vieram na requisição
    if req.rotulo is not None or req.nota is not None:
        rotulo = (req.rotulo or "").strip() or None
        nota = (req.nota or "").strip() or None
        rel.rotulo = rotulo
        rel.nota = nota
        rel.anotado_em = datetime.now(timezone.utc) if (rotulo or nota) else None
    db.commit()

    return {
        "id": rel.id,
        "rotulo": rel.rotulo,
        "nota": rel.nota,
        "oculta": rel.oculta,
        "anotado_em": rel.anotado_em.isoformat() if rel.anotado_em else None,
    }


@router.put("/{pessoa_id}/layout")
def salvar_layout(
    pessoa_id: int, req: LayoutRequest, db: Session = Depends(get_db)
) -> dict:
    """Guarda (ou limpa) a disposição do grafo desta pessoa."""
    pessoa = db.get(Pessoa, pessoa_id)
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")

    pessoa.grafo_layout = req.posicoes or None
    db.commit()
    return {"salvo": True, "nos": len(req.posicoes or {})}


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
                # Identidade: quem nunca foi pesquisado é só um nome extraído
                # de uma matéria — o descritor diz de qual pessoa se tratava.
                "contexto_origem": p.contexto_origem if p else None,
                "identidade_confirmada": bool(p.identidade_confirmada) if p else False,
                "tem_dossie": bool(p.briefing) if p else False,
            }
        )

    return {
        "nodes": nodes,
        "edges": grafo["edges"],
        "layout": raiz.grafo_layout or None,  # disposição salva pelo analista
    }
