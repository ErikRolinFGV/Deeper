"""Coletor LinkedIn via Apify.

Actor: apimaestro/linkedin-profile-detail ($5/1.000 perfis, sem cookies).
Docs: https://apify.com/apimaestro/linkedin-profile-detail

Três responsabilidades:
1. descobrir_linkedin_url — acha a URL pública do perfil via SerpAPI
   (site:linkedin.com/in "Nome"), 1 busca barata por pessoa.
2. coletar_perfil_linkedin — dispara o actor e retorna o JSON bruto.
3. normalizar_perfil — converte o JSON bruto (cujo schema pode variar entre
   versões do actor) em um dict estável consumido pelo worker.
"""

import re
from datetime import date
from typing import Any

import httpx
from apify_client import ApifyClient
from loguru import logger

from app.core.config import settings

SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT = 30.0

_LINKEDIN_IN = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[^/?#]+", re.I)


# ---------- 1. descoberta da URL do perfil ----------


def descobrir_linkedin_url(nome: str, contexto: str | None = None) -> str | None:
    """Busca no Google (via SerpAPI) a URL pública do LinkedIn do executivo.

    Args:
        nome: nome do executivo.
        contexto: cargo/empresa conhecidos, para desambiguar homônimos
            (ex.: "CEO da Vale").

    Returns:
        URL canônica do perfil (https://.../in/usuario) ou None.
    """
    if not nome.strip():
        return None

    query = f'site:linkedin.com/in "{nome}"'
    if contexto:
        query += f" {contexto}"

    logger.info(f"LinkedIn descoberta: '{query}'")
    params = {
        "engine": "google",
        "q": query,
        "hl": "pt-br",
        "gl": "br",
        "num": 5,
        "api_key": settings.SERPAPI_KEY,
    }
    try:
        resp = httpx.get(SERPAPI_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
    except Exception as exc:
        logger.error(f"LinkedIn descoberta falhou para '{nome}': {exc}")
        return None

    for item in dados.get("organic_results", []) or []:
        m = _LINKEDIN_IN.search(item.get("link") or "")
        if m:
            url = m.group(0)
            logger.info(f"LinkedIn descoberto para '{nome}': {url}")
            return url

    logger.warning(f"LinkedIn não encontrado para '{nome}'")
    return None


# ---------- 2. coleta via actor Apify ----------


def coletar_perfil_linkedin(linkedin_url: str) -> dict | None:
    """Dispara o actor do Apify e retorna o JSON bruto do perfil.

    Retorna None em caso de falha (perfil privado, actor sem créditos, etc.).
    """
    logger.info(f"Apify: coletando {linkedin_url}")
    client = ApifyClient(settings.APIFY_TOKEN)

    # O actor aceita username, URL completa ou URN no campo "username".
    run_input = {"username": linkedin_url, "includeEmail": False}

    try:
        run = client.actor(settings.APIFY_ACTOR_LINKEDIN).call(run_input=run_input)
        # apify-client <2 retorna dict; >=2 retorna objeto Run com atributos.
        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", None) or getattr(
                run, "defaultDatasetId", None
            )
        if not dataset_id:
            logger.error(f"Apify: run sem dataset id para {linkedin_url} ({run!r})")
            return None
        items = list(client.dataset(dataset_id).iterate_items())
        if not items:
            logger.warning(f"Apify: dataset vazio para {linkedin_url}")
            return None
        return items[0]
    except Exception as exc:
        logger.error(f"Apify falhou para {linkedin_url}: {exc}")
        return None


# ---------- 3. normalização ----------

_MESES = {
    "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5,
    "mai": 5, "jun": 6, "jul": 7, "aug": 8, "ago": 8, "sep": 9, "set": 9,
    "oct": 10, "out": 10, "nov": 11, "dec": 12, "dez": 12,
}
_ANO = re.compile(r"\b(19|20)\d{2}\b")


def _get(d: dict, *chaves: str, default: Any = None) -> Any:
    """Primeiro valor não-vazio entre variantes de nome de campo."""
    if not isinstance(d, dict):
        return default
    for chave in chaves:
        valor = d.get(chave)
        if valor not in (None, "", [], {}):
            return valor
    return default


def _parse_data_li(valor: Any) -> date | None:
    """Interpreta datas do actor: {'year': 2020, 'month': 3} ou 'Mar 2020'."""
    if not valor or (isinstance(valor, str) and valor.strip().lower() in {"present", "atual"}):
        return None
    if isinstance(valor, dict):
        ano = valor.get("year")
        if not ano:
            return None
        try:
            return date(int(ano), int(valor.get("month") or 1), 1)
        except (ValueError, TypeError):
            return None
    if isinstance(valor, str):
        m = _ANO.search(valor)
        if not m:
            return None
        mes = 1
        for nome_mes, num in _MESES.items():
            if nome_mes in valor.lower():
                mes = num
                break
        return date(int(m.group(0)), mes, 1)
    return None


def _normalizar_experiencia(exp: dict) -> dict:
    fim_bruto = _get(exp, "end_date", "endDate", "ends_at")
    fim = _parse_data_li(fim_bruto)
    atual = bool(_get(exp, "is_current", "isCurrent", default=False)) or (
        fim is None and fim_bruto in (None, "", "Present", "present")
        and _get(exp, "start_date", "startDate", "starts_at") is not None
    )
    return {
        "funcao": _get(exp, "title", "position", "funcao"),
        "empresa": _get(exp, "company", "company_name", "companyName", "empresa"),
        "local": _get(exp, "location"),
        "inicio": _parse_data_li(_get(exp, "start_date", "startDate", "starts_at")),
        "fim": fim,
        "atual": atual,
        "descricao": _get(exp, "description"),
    }


def _normalizar_formacao(edu: dict) -> dict:
    return {
        "instituicao": _get(edu, "school", "school_name", "instituicao"),
        "grau": _get(edu, "degree", "degree_name"),
        "area": _get(edu, "field_of_study", "fieldOfStudy", "area"),
        "inicio": _parse_data_li(_get(edu, "start_date", "startDate")),
        "fim": _parse_data_li(_get(edu, "end_date", "endDate")),
    }


def normalizar_perfil(bruto: dict) -> dict:
    """Converte o JSON do actor em dict estável para o pipeline.

    Estrutura de saída:
        nome_completo, headline, sobre, foto_url, localizacao, empresa_atual,
        seguidores, conexoes, experiencias[], formacao[], certificacoes[].
    """
    basico = _get(bruto, "basic_info", "basicInfo", default=bruto)

    localizacao = _get(basico, "location")
    if isinstance(localizacao, dict):
        localizacao = _get(localizacao, "full", "default", "city", "country")

    experiencias = [
        _normalizar_experiencia(e)
        for e in _get(bruto, "experience", "experiences", "positions", default=[])
        if isinstance(e, dict)
    ]
    formacao = [
        _normalizar_formacao(e)
        for e in _get(bruto, "education", "educations", default=[])
        if isinstance(e, dict)
    ]
    certificacoes = [
        _get(c, "name", "title") or str(c)
        for c in _get(bruto, "certifications", "certificates", default=[])
        if c
    ]

    return {
        "nome_completo": _get(basico, "fullname", "fullName", "full_name", "name"),
        "headline": _get(basico, "headline"),
        "sobre": _get(basico, "about", "summary"),
        "foto_url": _get(basico, "profile_picture_url", "profilePicture", "profile_pic_url"),
        "localizacao": localizacao,
        "empresa_atual": _get(basico, "current_company", "currentCompany"),
        "seguidores": _get(basico, "follower_count", "followers"),
        "conexoes": _get(basico, "connection_count", "connections"),
        "experiencias": [e for e in experiencias if e["funcao"] or e["empresa"]],
        "formacao": [f for f in formacao if f["instituicao"]],
        "certificacoes": certificacoes[:10],
    }
