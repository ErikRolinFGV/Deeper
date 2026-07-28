"""GET /acervo — lista os executivos já pesquisados (aba Acervo do frontend).

Diferente de /sugestoes (que filtra por termo), aqui devolvemos o acervo
inteiro, mais recentes primeiro, para a tela de listagem.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa

router = APIRouter(prefix="/acervo", tags=["acervo"])


@router.get("")
def listar_acervo(
    limite: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    pessoas = db.scalars(
        select(Pessoa).order_by(Pessoa.atualizado_em.desc()).limit(limite)
    ).all()
    return {
        "total": len(pessoas),
        "pessoas": [
            {
                "pessoa_id": p.id,
                "nome": p.nome,
                "cargo_atual": p.cargo_atual,
                "foto_url": p.foto_url,
                "tem_briefing": p.briefing is not None,
                "atualizado_em": p.atualizado_em.isoformat() if p.atualizado_em else None,
            }
            for p in pessoas
        ],
    }
