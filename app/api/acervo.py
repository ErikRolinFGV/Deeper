"""GET /acervo — lista os executivos já pesquisados (aba Acervo do frontend).

Diferente de /sugestoes (que filtra por termo), aqui devolvemos o acervo
inteiro, mais recentes primeiro, para a tela de listagem.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa

router = APIRouter(prefix="/acervo", tags=["acervo"])


@router.get("")
def listar_acervo(
    limite: int = Query(200, ge=1, le=1000, description="Quantos registros retornar"),
    db: Session = Depends(get_db),
) -> dict:
    # `total` é a contagem REAL no banco; `exibindo` é o tamanho desta página.
    # O limite afeta só esta listagem — nunca o grafo, que consulta o banco
    # inteiro.
    total = db.scalar(select(func.count()).select_from(Pessoa)) or 0
    pessoas = db.scalars(
        select(Pessoa).order_by(Pessoa.atualizado_em.desc()).limit(limite)
    ).all()
    return {
        "total": total,
        "exibindo": len(pessoas),
        "pessoas": [
            {
                "pessoa_id": p.id,
                "nome": p.nome,
                "cargo_atual": p.cargo_atual,
                "foto_url": p.foto_url,
                "tem_briefing": p.briefing is not None,
                "identidade_confirmada": bool(p.identidade_confirmada),
                "atualizado_em": p.atualizado_em.isoformat() if p.atualizado_em else None,
            }
            for p in pessoas
        ],
    }
