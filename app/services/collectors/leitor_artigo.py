"""Leitor de artigos: baixa a matéria, extrai o texto principal e a autoria.

Motivação do texto: o extrator LLM via só título+snippet (~200 chars) —
manchetes de economia raramente nomeiam outras pessoas, deixando o grafo ralo.

Motivação da autoria: se a pessoa-alvo ASSINOU a matéria, ela não é assunto —
é repórter. Sem essa distinção, um executivo com passado de jornalista fica
"conectado" a todo mundo sobre quem ele escreveu (caso real: um sócio da FSB
ligado a Bush e Saddam Hussein por uma reportagem de 2002 na Folha).

Tolerante a falha: qualquer erro → None, o pipeline segue com o snippet.
"""

import re
import unicodedata

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

# Meta tags usadas pelos portais BR para identificar o autor
_METAS_AUTOR = (
    {"name": "author"},
    {"property": "article:author"},
    {"name": "article:author"},
    {"property": "og:article:author"},
    {"name": "cXenseParse:author"},
    {"name": "twitter:creator"},
)

# Assinatura no corpo: "Por Fulano de Tal", "POR FULANO", "Fulano de Tal, de São Paulo"
_ASSINATURA = re.compile(
    r"^\s*(?:por|texto de|reportagem de)\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÀ-ÿ.'-]+(?:\s+[A-Za-zÀ-ÿ.'-]+){0,4})",
    re.IGNORECASE | re.MULTILINE,
)


def _normalizar(txt: str) -> str:
    """minúsculas, sem acento — para comparar nomes."""
    if not txt:
        return ""
    sem_acento = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento.lower()).strip()


def eh_autor(nome_alvo: str, autor: str | None) -> bool:
    """True se `autor` corresponde à pessoa-alvo.

    Casa por tokens para tolerar "Marcelo Diego" vs "Marcelo Diego, da Folha"
    e ordens diferentes, exigindo pelo menos nome e sobrenome em comum.
    """
    if not autor or not nome_alvo:
        return False
    toks_alvo = [t for t in _normalizar(nome_alvo).split() if len(t) > 2]
    toks_autor = set(_normalizar(autor).split())
    if len(toks_alvo) < 2:
        return False
    return sum(1 for t in toks_alvo if t in toks_autor) >= 2


def _extrair_autor(soup: BeautifulSoup, texto: str) -> str | None:
    for attrs in _METAS_AUTOR:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            valor = tag["content"].strip()
            # ignora nome do veículo em vez de pessoa
            if valor and len(valor) < 120:
                return valor
    # rel="author" / classes comuns dos portais
    for sel in ('[rel="author"]', ".author", ".autor", ".byline", '[itemprop="author"]'):
        el = soup.select_one(sel)
        if el:
            valor = el.get_text(" ", strip=True)
            if valor and len(valor) < 120:
                return valor
    m = _ASSINATURA.search(texto[:400])
    return m.group(1).strip() if m else None


def baixar_artigo(url: str) -> dict | None:
    """Baixa a página e devolve {'texto': ..., 'autor': ...} ou None."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug(f"Leitor: falha ao baixar {url[:60]}: {exc}")
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        autor = _extrair_autor(soup, soup.get_text(" ", strip=True)[:400])

        for tag in soup(_TAGS_LIXO):
            tag.decompose()

        # Preferência: <article> (padrão dos portais BR); fallback: <p> do body.
        raiz = soup.find("article") or soup.body or soup
        paragrafos = [p.get_text(" ", strip=True) for p in raiz.find_all("p")]
        # Filtra migalhas (créditos, legendas, chamadas de 1 linha)
        texto = "\n".join(p for p in paragrafos if len(p) >= 60)
        if len(texto) < 200:
            logger.debug(f"Leitor: texto curto demais em {url[:60]} ({len(texto)} chars)")
            return None
        if autor is None:
            m = _ASSINATURA.search(texto[:400])
            autor = m.group(1).strip() if m else None
        return {"texto": texto[:MAX_CHARS], "autor": autor}
    except Exception as exc:
        logger.debug(f"Leitor: falha ao extrair texto de {url[:60]}: {exc}")
        return None


def baixar_texto(url: str) -> str | None:
    """Compatibilidade: só o texto da matéria."""
    art = baixar_artigo(url)
    return art["texto"] if art else None
