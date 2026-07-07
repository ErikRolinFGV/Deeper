"""Coletor SerpAPI: Google search programático restrito a portais brasileiros.

Docs: https://serpapi.com/search-api

A estratégia é montar uma query do tipo `"Nome" (site:valor.com.br OR site:exame.com ...)`
para que o Google retorne só resultados dos portais de imprensa BR pré-aprovados.
Isso evita lixo de blogs, fóruns e sites duplicados.
"""

from typing import Any

import httpx
from loguru import logger

from app.core.config import settings

SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT = 30.0

# Portais de imprensa brasileira priorizados para inteligência sobre executivos.
# Ordem reflete relevância editorial percebida — pode ser ajustada.
PORTAIS_BR = [
    "valor.com.br",
    "estadao.com.br",
    "folha.uol.com.br",
    "exame.com",
    "infomoney.com.br",
    "neofeed.com.br",
    "brazilianreport.com",
    "oglobo.globo.com",
    "veja.abril.com.br",
    "istoedinheiro.com.br",
]


def _extrair_fonte(url: str) -> str:
    """Identifica o portal de origem a partir da URL."""
    for portal in PORTAIS_BR:
        if portal in url:
            return portal.split(".")[0]
    return "outros"


def _normalizar_resultado(item: dict[str, Any]) -> dict[str, Any]:
    """Converte um item do SerpAPI para o formato consumido pelo nosso pipeline."""
    return {
        "fonte": _extrair_fonte(item.get("link", "")),
        "url": item.get("link"),
        "titulo": item.get("title"),
        "snippet": item.get("snippet"),
        "data_publicacao": item.get("date"),  # SerpAPI nem sempre devolve
    }


def buscar_mencoes(nome: str, limite: int = 15) -> list[dict[str, Any]]:
    """Pesquisa o nome restrito aos principais portais BR e retorna menções.

    Args:
        nome: nome completo do executivo a pesquisar.
        limite: número máximo de resultados (até 100 pelo Google).

    Returns:
        Lista de dicts com {fonte, url, titulo, snippet, data_publicacao}.
        Em caso de falha, retorna lista vazia (não levanta exceção).
    """
    if not nome.strip():
        logger.warning("SerpAPI chamado com nome vazio")
        return []

    logger.info(f"SerpAPI: buscando menções de '{nome}' (limite={limite})")

    site_filter = " OR ".join(f"site:{p}" for p in PORTAIS_BR)
    query = f'"{nome}" ({site_filter})'

    params = {
        "engine": "google",
        "q": query,
        "hl": "pt-br",
        "gl": "br",
        "num": min(limite, 100),
        "api_key": settings.SERPAPI_KEY,
    }

    try:
        resp = httpx.get(SERPAPI_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(f"SerpAPI HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return []
    except httpx.HTTPError as exc:
        logger.error(f"SerpAPI falha de conexão: {exc}")
        return []
    except Exception as exc:
        logger.exception(f"SerpAPI erro inesperado: {exc}")
        return []

    organicos = dados.get("organic_results", []) or []
    mencoes = [_normalizar_resultado(item) for item in organicos[:limite]]

    logger.info(f"SerpAPI: {len(mencoes)} menções retornadas para '{nome}'")
    return mencoes
