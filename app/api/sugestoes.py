"""GET /sugestoes — autocomplete de executivos (banco local + LinkedIn).

Duas camadas com custos diferentes:
- locais: pessoas já pesquisadas (consulta SQL, grátis, pode ser chamada a
  cada pausa de digitação);
- linkedin: candidatos reais via SerpAPI (1 busca por chamada — o frontend
  só pede com `externas=true`, acionado explicitamente pelo usuário).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa
from app.services.collectors.apify_linkedin import sugerir_perfis_linkedin

router = APIRouter(prefix="/sugestoes", tags=["sugestoes"])


@router.get("")
def sugerir(
    q: str = Query(..., min_length=2, max_length=120),
    externas: bool = Query(False, description="Também busca candidatos no LinkedIn (custa 1 busca SerpAPI)"),
    db: Session = Depends(get_db),
) -> dict:
    termo = f"%{q.strip()}%"
    locais = db.scalars(
        select(Pessoa)
        .where(
            or_(
                Pessoa.nome.ilike(termo),
                Pessoa.nome_completo.ilike(termo),
                Pessoa.cargo_atual.ilike(termo),
            )
        )
        .limit(5)
    ).all()

    return {
        "locais": [
            {
                "pessoa_id": p.id,
                "nome": p.nome,
                "cargo_atual": p.cargo_atual,
                "foto_url": p.foto_url,
                "tem_briefing": p.briefing is not None,
            }
            for p in locais
        ],
        "linkedin": sugerir_perfis_linkedin(q) if externas else [],
    }
