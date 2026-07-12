"""Coletor SerpAPI: Google search programático restrito a portais brasileiros.

Docs: https://serpapi.com/search-api

A estratégia é montar uma query do tipo `"Nome" (site:valor.com.br OR site:exame.com ...)`
para que o Google retorne só resultados dos portais de imprensa BR pré-aprovados.
Isso evita lixo de blogs, fóruns e sites duplicados.
"""

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from loguru import logger

from app.core.config import settings

SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT = 30.0

# Páginas-índice/tag dos portais: agregam links mas não são notícias.
# Descartadas antes de gastar extração LLM.
PADROES_INDICE = (
    "/tudo-sobre/",
    "/tudo-sobre-",
    "/noticias-sobre/",
    "/ultimas-noticias/",
    "/topico/",
    "/topicos/",
    "/assunto/",
    "/tag/",
    "/tags/",
    "/comentarios/",  # páginas de comentários dos leitores (ex: Folha)
)

# Datas embutidas no caminho da URL, padrão comum nos portais BR:
# .../2024/03/08/titulo-da-materia...
_DATA_NA_URL = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/")

# Portais de imprensa brasileira priorizados para inteligência sobre executivos.
# Ordem reflete relevância editorial percebida — pode ser ajustada.
PORTAIS_BR = [
    "valor.globo.com",
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


# Parâmetros de tracking que fazem a MESMA matéria parecer URLs diferentes
# (quebrariam o dedup por URL): ?srsltid= do Google, utm_* de campanhas, etc.
_PARAMS_TRACKING_PREFIXOS = ("utm_",)
_PARAMS_TRACKING = {"srsltid", "fbclid", "gclid", "igshid", "mc_cid", "mc_eid"}


def limpar_url(url: str) -> str:
    """Remove parâmetros de tracking e fragmento; preserva o resto da URL."""
    if not url:
        return url
    partes = urlsplit(url)
    params = [
        (k, v)
        for k, v in parse_qsl(partes.query, keep_blank_values=True)
        if k not in _PARAMS_TRACKING and not k.startswith(_PARAMS_TRACKING_PREFIXOS)
    ]
    return urlunsplit(
        (partes.scheme, partes.netloc, partes.path, urlencode(params), "")
    )


def _extrair_fonte(url: str) -> str:
    """Identifica o portal de origem a partir da URL."""
    for portal in PORTAIS_BR:
        if portal in url:
            return portal.split(".")[0]
    return "outros"


def _eh_pagina_indice(url: str) -> bool:
    """True para páginas de tag/índice ('Tudo Sobre', 'Últimas notícias')."""
    url_lower = url.lower()
    return any(p in url_lower for p in PADROES_INDICE)


def _data_da_url(url: str) -> str | None:
    """Extrai data AAAA-MM-DD do caminho da URL, se existir."""
    m = _DATA_NA_URL.search(url)
    if not m:
        return None
    ano, mes, dia = m.groups()
    return f"{ano}-{int(mes):02d}-{int(dia):02d}"


def _normalizar_resultado(item: dict[str, Any]) -> dict[str, Any]:
    """Converte um item do SerpAPI para o formato consumido pelo nosso pipeline."""
    url = limpar_url(item.get("link") or "")
    # A data da URL tem prioridade: formato garantido (AAAA-MM-DD). O campo
    # 'date' do SerpAPI vem em formato humano ("Jun 15, 2026") e é fallback.
    data = _data_da_url(url) or item.get("date")
    return {
        "fonte": _extrair_fonte(url),
        "url": url or None,
        "titulo": item.get("title"),
        "snippet": item.get("snippet"),
        "data_publicacao": data,
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
    noticias = [
        item for item in organicos if not _eh_pagina_indice(item.get("link") or "")
    ]
    descartadas = len(organicos) - len(noticias)
    if descartadas:
        logger.debug(f"SerpAPI: {descartadas} páginas-índice descartadas")

    mencoes = [_normalizar_resultado(item) for item in noticias[:limite]]

    logger.info(f"SerpAPI: {len(mencoes)} menções retornadas para '{nome}'")
    return mencoes
