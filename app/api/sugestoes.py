"""GET /sugestoes — autocomplete de executivos (banco local + LinkedIn).

Três formas de chegar à pessoa certa:
- locais: quem já está no acervo (SQL, grátis);
- linkedin: candidatos reais via SerpAPI (1 busca — só com `externas=true`);
- URL colada: se a consulta contém um link de perfil, a identidade já está
  definida — resolvemos nome/headline e devolvemos um único candidato.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.pessoa import Pessoa
from app.services.collectors.apify_linkedin import (
    extrair_url_linkedin,
    resolver_perfil_por_url,
    sugerir_perfis_linkedin,
)
from app.services.fotos import foto_publica

router = APIRouter(prefix="/sugestoes", tags=["sugestoes"])


def _serializar(p: Pessoa) -> dict:
    return {
        "pessoa_id": p.id,
        "nome": p.nome,
        "cargo_atual": p.cargo_atual,
        "foto_url": foto_publica(p),
        "tem_briefing": p.briefing is not None,
        "contexto_origem": p.contexto_origem,
        "identidade_confirmada": bool(p.identidade_confirmada),
        "tem_linkedin": bool(p.linkedin_url),
    }


@router.get("")
def sugerir(
    q: str = Query(..., min_length=2, max_length=300),
    externas: bool = Query(False, description="Também busca candidatos no LinkedIn (custa 1 busca SerpAPI)"),
    contexto: str | None = Query(
        None, max_length=160,
        description=(
            "Pistas de identidade (cargo/empresa/vínculo) vindas do grafo. "
            "Presente (mesmo vazio) = pessoa vinda do grafo: só entram "
            "candidatos do LinkedIn com o mesmo nome, ordenados pelas pistas."
        ),
    ),
    db: Session = Depends(get_db),
) -> dict:
    # ── Caminho 1: o usuário colou um link de perfil ──
    url = extrair_url_linkedin(q)
    if url:
        # Já temos alguém no acervo com esse perfil? Abre direto, sem custo.
        usuario = url.rstrip("/").rsplit("/in/", 1)[-1]
        locais = db.scalars(
            select(Pessoa).where(Pessoa.linkedin_url.ilike(f"%/in/{usuario}%")).limit(5)
        ).all()
        candidato = resolver_perfil_por_url(url) if not locais else None
        return {
            "por_url": True,
            "locais": [_serializar(p) for p in locais],
            "linkedin": [candidato] if candidato else [],
        }

    # ── Caminho 2: busca por texto ──
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
        "por_url": False,
        "locais": [_serializar(p) for p in locais],
        "linkedin": (
            sugerir_perfis_linkedin(q, nome_exigido=q.strip(), contexto=contexto)
            if externas and contexto is not None
            else sugerir_perfis_linkedin(q) if externas else []
        ),
    }
