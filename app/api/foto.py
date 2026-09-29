"""Serve a cópia local da foto de perfil.

A URL do LinkedIn expira (ver app/services/fotos.py); esta rota entrega a
imagem que baixamos na coleta, que não expira.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.fotos import caminho_local

router = APIRouter(prefix="/foto", tags=["foto"])


@router.get("/{pessoa_id}")
def obter_foto(pessoa_id: int):
    caminho = caminho_local(pessoa_id)
    if not caminho:
        raise HTTPException(status_code=404, detail="Sem foto guardada para esta pessoa")
    # A foto muda raramente; o navegador pode segurar por um dia.
    return FileResponse(caminho, headers={"Cache-Control": "public, max-age=86400"})
