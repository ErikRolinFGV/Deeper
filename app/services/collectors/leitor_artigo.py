"""Leitor de artigos: baixa a matéria e extrai o texto principal.

Motivação: o extrator LLM via só título+snippet (~200 chars) — manchetes de
economia raramente nomeiam outras pessoas, deixando o grafo ralo. O corpo da
matéria multiplica as entidades visíveis. Paywalls bloqueiam parte do texto,
mas o trecho aberto (lead + primeiros parágrafos) já ajuda muito.

Tolerante a falha: qualquer erro → None, o pipeline segue com o snippet.
"""

import httpx
from bs4 import BeautifulSoup
from loguru import logger

TIMEOUT = 15.0
MAX_CHARS = 4000  # teto por matéria: controla custo do extrator LLM

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Tags cujo conteúdo nunca é texto de matéria
_TAGS_LIXO = ("script", "style", "nav", "header", "footer", "aside", "form", "iframe")


def baixar_texto(url: str) -> str | None:
    """Baixa a página e devolve o texto principal (até MAX_CHARS) ou None."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug(f"Leitor: falha ao baixar {url[:60]}: {exc}")
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(_TAGS_LIXO):
            tag.decompose()

        # Preferência: <article> (padrão dos portais BR); fallback: <p> do body.
        raiz = soup.find("article") or soup.body or soup
        paragrafos = [
            p.get_text(" ", strip=True)
            for p in raiz.find_all("p")
        ]
        # Filtra migalhas (créditos, legendas, chamadas de 1 linha)
        texto = "\n".join(p for p in paragrafos if len(p) >= 60)
        if len(texto) < 200:
            logger.debug(f"Leitor: texto curto demais em {url[:60]} ({len(texto)} chars)")
            return None
        return texto[:MAX_CHARS]
    except Exception as exc:
        logger.debug(f"Leitor: falha ao extrair texto de {url[:60]}: {exc}")
        return None
