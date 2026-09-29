"""Coletor SerpAPI: menções na imprensa via Google Notícias + portais brasileiros.

Docs: https://serpapi.com/search-api

Busca principal no Google Notícias (engine google_news): cobre a imprensa
geral e a setorial, com data e veículo. Se vier pouca coisa, complementa com
`"Nome" (site:valor.com.br OR site:exame.com ...)` nos portais pré-aprovados.
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


# Mínimo de matérias do Google Notícias abaixo do qual complementamos com a
# busca orgânica restrita aos portais (cobre executivo pouco noticiado, em
# que a busca antiga ainda acha perfil ou entrevista antiga).
MIN_NOTICIAS_ANTES_DO_COMPLEMENTO = 8

_DATA_GOOGLE_NEWS = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def _data_google_news(item: dict[str, Any]) -> str | None:
    """Data do Google Notícias em AAAA-MM-DD.

    O campo `iso_date` é o mais confiável; `date` vem como
    "05/29/2026, 07:00 AM, +0000 UTC" (mês/dia/ano).
    """
    iso = item.get("iso_date")
    if isinstance(iso, str) and re.match(r"\d{4}-\d{2}-\d{2}", iso):
        return iso[:10]
    m = _DATA_GOOGLE_NEWS.search(item.get("date") or "")
    if not m:
        return None
    mes, dia, ano = m.groups()
    return f"{ano}-{mes}-{dia}"


def _nome_da_fonte(item: dict[str, Any], url: str) -> str:
    """Nome do veículo: portal conhecido usa a chave curta, os demais o nome exibido."""
    conhecida = _extrair_fonte(url)
    if conhecida != "outros":
        return conhecida
    fonte = item.get("source")
    nome = fonte.get("name") if isinstance(fonte, dict) else fonte
    if isinstance(nome, str) and nome.strip():
        return nome.strip()[:150]
    return urlsplit(url).netloc.removeprefix("www.")[:150] or "outros"


def _normalizar_noticia(item: dict[str, Any]) -> dict[str, Any]:
    """Converte um resultado do Google Notícias para o formato do pipeline."""
    url = limpar_url(item.get("link") or "")
    return {
        "fonte": _nome_da_fonte(item, url),
        "url": url or None,
        "titulo": item.get("title"),
        "snippet": item.get("snippet"),
        "data_publicacao": _data_da_url(url) or _data_google_news(item),
    }


def _consultar(params: dict[str, Any]) -> dict[str, Any] | None:
    """GET no SerpAPI com uma nova tentativa em caso de timeout/rede.

    O SerpAPI oscila: a mesma busca que leva 1 s às vezes passa de 30 s. Uma
    segunda tentativa resolve a maioria dos casos sem derrubar a coleta.
    """
    for tentativa in (1, 2):
        try:
            resp = httpx.get(SERPAPI_URL, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"SerpAPI HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(f"SerpAPI falha de conexão (tentativa {tentativa}/2): {exc}")
        except Exception as exc:
            logger.exception(f"SerpAPI erro inesperado: {exc}")
            return None
    return None


def _buscar_google_news(nome: str) -> list[dict[str, Any]]:
    """Google Notícias: cobre toda a imprensa indexada, com data e veículo.

    É a busca principal. A antiga (Google orgânico com `site:` dos 10 portais)
    perdia a imprensa setorial (TeleSíntese, Teletime, Você RH...) e trazia
    páginas antigas, porque o Google orgânico não ordena por recência.
    """
    dados = _consultar(
        {
            "engine": "google_news",
            "q": f'"{nome}"',
            "hl": "pt-br",
            "gl": "br",
            "api_key": settings.SERPAPI_KEY,
        }
    )
    if not dados:
        return []
    itens: list[dict[str, Any]] = []
    for item in dados.get("news_results", []) or []:
        # Resultados agrupados ("Cobertura completa") trazem as matérias em `stories`.
        if item.get("link"):
            itens.append(item)
        for historia in item.get("stories", []) or []:
            if historia.get("link"):
                itens.append(historia)
    return [_normalizar_noticia(i) for i in itens]


def _buscar_portais(nome: str, limite: int) -> list[dict[str, Any]]:
    """Busca orgânica restrita aos portais BR pré-aprovados (complemento)."""
    site_filter = " OR ".join(f"site:{p}" for p in PORTAIS_BR)
    dados = _consultar(
        {
            "engine": "google",
            "q": f'"{nome}" ({site_filter})',
            "hl": "pt-br",
            "gl": "br",
            "num": min(limite, 100),
            "api_key": settings.SERPAPI_KEY,
        }
    )
    if not dados:
        return []
    return [_normalizar_resultado(i) for i in dados.get("organic_results", []) or []]


def buscar_mencoes(nome: str, limite: int = 15) -> list[dict[str, Any]]:
    """Pesquisa menções do nome na imprensa, mais recentes primeiro.

    Estratégia: Google Notícias como fonte principal (1 busca SerpAPI) e,
    se vier pouca coisa, complemento com a busca orgânica nos portais BR
    (2ª busca). Páginas-índice são descartadas e URLs repetidas fundidas.

    Returns:
        Lista de dicts com {fonte, url, titulo, snippet, data_publicacao},
        ordenada da mais recente para a mais antiga (sem data vai ao fim).
        Em caso de falha, retorna lista vazia (não levanta exceção).
    """
    if not nome.strip():
        logger.warning("SerpAPI chamado com nome vazio")
        return []

    logger.info(f"SerpAPI: buscando menções de '{nome}' (limite={limite})")

    brutas = _buscar_google_news(nome)
    logger.info(f"SerpAPI: {len(brutas)} resultados no Google Notícias")
    if len(brutas) < MIN_NOTICIAS_ANTES_DO_COMPLEMENTO:
        complemento = _buscar_portais(nome, limite)
        logger.info(f"SerpAPI: +{len(complemento)} resultados da busca nos portais")
        brutas += complemento

    vistas: set[str] = set()
    mencoes: list[dict[str, Any]] = []
    descartadas = 0
    for m in brutas:
        url = m.get("url")
        if not url or url in vistas:
            continue
        vistas.add(url)
        if _eh_pagina_indice(url):
            descartadas += 1
            continue
        mencoes.append(m)
    if descartadas:
        logger.debug(f"SerpAPI: {descartadas} páginas-índice descartadas")

    # Recência primeiro: o analista quer o que mudou, e o limite de extração
    # do worker deve gastar LLM nas matérias novas, não em arquivo de 2019.
    mencoes.sort(key=lambda m: m.get("data_publicacao") or "", reverse=True)
    mencoes = mencoes[:limite]

    logger.info(f"SerpAPI: {len(mencoes)} menções retornadas para '{nome}'")
    return mencoes
