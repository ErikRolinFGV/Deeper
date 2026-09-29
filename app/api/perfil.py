"""GET /perfil/{id} — retorna o dossiê completo de uma pessoa."""

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.models.cargo import Cargo
from app.models.evento import Evento, evento_participante
from app.models.mencao import Mencao
from app.models.pessoa import Pessoa
from pydantic import BaseModel, Field

from app.services.manutencao import excluir_pessoa, fundir_pessoas
from app.services.fotos import TAMANHO_MAXIMO, foto_publica, gravar_foto, guardar_foto


class FusaoRequest(BaseModel):
    """Diz que a pessoa `duplicada_id` é, na verdade, a pessoa da rota."""

    duplicada_id: int = Field(..., description="ID do registro a ser absorvido")

class FotoRequest(BaseModel):
    """Endereço de uma imagem pública escolhida pelo analista."""

    url: str = Field(..., min_length=8, max_length=2048)


router = APIRouter(prefix="/perfil", tags=["perfil"])


@router.post("/{pessoa_id}/desvincular")
def desvincular_identidade(pessoa_id: int, db: Session = Depends(get_db)) -> dict:
    """"Não é esta pessoa": desfaz a identidade atribuída ao registro.

    Apaga o que veio do perfil errado (LinkedIn, cargos, foto, briefing
    que misturou as duas pessoas) e PRESERVA o que veio da imprensa e do
    grafo: menções e conexões foram coletadas pelo nome, no contexto em
    que a pessoa certa foi citada. Depois disso o analista escolhe o
    perfil correto ou coleta só pela imprensa.
    """
    from sqlalchemy import delete

    from app.services.fotos import remover_foto

    pessoa = db.get(Pessoa, pessoa_id)
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    db.execute(delete(Cargo).where(Cargo.pessoa_id == pessoa.id))
    remover_foto(pessoa.id)
    for campo in ("linkedin_url", "linkedin_dados", "linkedin_coletado_em", "nome_completo",
                  "cargo_atual", "bio", "foto_url", "localizacao", "briefing"):
        setattr(pessoa, campo, None)
    # O cache do /busca não atrapalha: tanto a escolha de um LinkedIn quanto
    # o "só imprensa" forçam coleta nova para quem não tem identidade.
    pessoa.identidade_confirmada = False
    db.commit()
    return {"desvinculado": True, "pessoa_id": pessoa.id, "contexto_origem": pessoa.contexto_origem}


@router.post("/{pessoa_id}/foto")
def trocar_foto(pessoa_id: int, req: FotoRequest, db: Session = Depends(get_db)) -> dict:
    """Troca a foto pelo endereço de uma imagem (curadoria do analista).

    Para quando a coleta não acha retrato, ou acha um ruim. Baixamos a
    imagem na hora e guardamos a cópia, como na coleta.
    """
    pessoa = db.get(Pessoa, pessoa_id)
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    url = req.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Informe um endereço http(s) de imagem.")
    if not guardar_foto(pessoa.id, url):
        raise HTTPException(
            status_code=400,
            detail="Não consegui baixar uma imagem desse endereço. Use 'Copiar endereço "
            "da imagem' (não o da página) ou envie o arquivo.",
        )
    pessoa.foto_url = url
    db.commit()
    return {"foto_url": foto_publica(pessoa)}


@router.post("/{pessoa_id}/foto/arquivo")
async def enviar_foto(
    pessoa_id: int, arquivo: UploadFile = File(...), db: Session = Depends(get_db)
) -> dict:
    """Troca a foto por um arquivo enviado do computador do analista."""
    pessoa = db.get(Pessoa, pessoa_id)
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    conteudo = await arquivo.read(TAMANHO_MAXIMO + 1)
    if not gravar_foto(pessoa.id, conteudo, (arquivo.content_type or "").lower()):
        raise HTTPException(
            status_code=400, detail="Envie uma imagem (JPG, PNG ou WEBP) de até 5 MB."
        )
    db.commit()
    return {"foto_url": foto_publica(pessoa)}

# Tamanho do trecho exibido na interface. Deliberadamente curto: é citação
# para contexto e verificação, não substituto da matéria (ver nota no README
# sobre direitos autorais e paywall).
TRECHO_CHARS = 600


@router.post("/{pessoa_id}/fundir")
def fundir_perfil(
    pessoa_id: int, req: FusaoRequest, db: Session = Depends(get_db)
) -> dict:
    """Funde dois registros que são a mesma pessoa física.

    Caso típico: a imprensa cita "Dani Braun" e o LinkedIn diz "Daniela
    Braun". O analista aponta a equivalência e as duas redes viram uma só.
    """
    principal = db.get(Pessoa, pessoa_id)
    duplicada = db.get(Pessoa, req.duplicada_id)
    if principal is None or duplicada is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    if principal.id == duplicada.id:
        raise HTTPException(
            status_code=400, detail="Selecione duas pessoas diferentes."
        )

    resumo = fundir_pessoas(db, principal, duplicada)
    db.commit()
    return {"fundido": True, "pessoa_id": principal.id, **resumo}


@router.delete("/{pessoa_id}")
def excluir_perfil(
    pessoa_id: int,
    limpar_orfaos: bool = Query(
        True, description="Também remove nós que só existiam por causa desta pessoa"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Remove o dossiê e todos os dados coletados desta pessoa.

    Útil quando o registro nasceu de um erro de grafia, de um homônimo ou de
    um perfil do LinkedIn desativado. Ação definitiva.
    """
    pessoa = db.get(Pessoa, pessoa_id)
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")

    resumo = excluir_pessoa(db, pessoa, limpar_orfaos=limpar_orfaos)
    db.commit()
    return {"removido": True, **resumo}


@router.get("/{pessoa_id}")
def obter_perfil(
    pessoa_id: int,
    limite_mencoes: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Retorna pessoa, cargos, menções recentes, eventos e briefing executivo."""
    pessoa = db.scalar(
        select(Pessoa)
        .where(Pessoa.id == pessoa_id)
        .options(selectinload(Pessoa.cargos).selectinload(Cargo.empresa))
    )
    if pessoa is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")

    mencoes = db.scalars(
        select(Mencao)
        .where(Mencao.pessoa_id == pessoa_id)
        .order_by(Mencao.data_publicacao.desc().nullslast(), Mencao.coletado_em.desc())
        .limit(limite_mencoes)
    ).all()

    eventos = db.scalars(
        select(Evento)
        .join(evento_participante, Evento.id == evento_participante.c.evento_id)
        .where(evento_participante.c.pessoa_id == pessoa_id)
        .order_by(Evento.data.desc().nullslast())
    ).all()

    return {
        "pessoa": {
            "id": pessoa.id,
            "slug": pessoa.slug,
            "nome": pessoa.nome,
            "nome_completo": pessoa.nome_completo,
            "cargo_atual": pessoa.cargo_atual,
            "bio": pessoa.bio,
            "linkedin_url": pessoa.linkedin_url,
            "foto_url": foto_publica(pessoa),
            "identidade_confirmada": bool(pessoa.identidade_confirmada),
            "contexto_origem": pessoa.contexto_origem,
            "atualizado_em": pessoa.atualizado_em.isoformat() if pessoa.atualizado_em else None,
        },
        "briefing": pessoa.briefing,
        "cargos": [
            {
                "funcao": c.funcao,
                "empresa": c.empresa.nome if c.empresa else None,
                "empresa_id": c.empresa_id,
                "inicio": c.inicio.isoformat() if c.inicio else None,
                "fim": c.fim.isoformat() if c.fim else None,
                "eh_atual": c.eh_atual,
            }
            # Cargos atuais primeiro; dentro de cada grupo, mais recentes primeiro.
            for c in sorted(pessoa.cargos, key=lambda c: (not c.eh_atual, -(c.inicio or date.min).toordinal()))
        ],
        "mencoes": [
            {
                "id": m.id,
                "fonte": m.fonte,
                "url": m.url,
                "titulo": m.titulo,
                "data_publicacao": m.data_publicacao.isoformat() if m.data_publicacao else None,
                "sentimento": m.sentimento,
                "temas": m.temas.split(",") if m.temas else [],
                # Trecho do que foi coletado da matéria — contexto suficiente
                # para o analista julgar sem sair da ferramenta. A leitura
                # integral continua sendo no site do veículo (link acima).
                "trecho": (m.texto or "")[:TRECHO_CHARS].strip() or None,
                "trecho_truncado": len(m.texto or "") > TRECHO_CHARS,
                # "autor" = matéria assinada pela pessoa (ela era o repórter)
                "papel": m.papel,
            }
            for m in mencoes
        ],
        "eventos": [
            {
                "id": e.id,
                "nome": e.nome,
                "tipo": e.tipo,
                "data": e.data.isoformat() if e.data else None,
                "local": e.local,
                "fonte_url": e.fonte_url,
            }
            for e in eventos
        ],
    }
