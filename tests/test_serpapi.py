"""Testes unitários das funções puras do coletor SerpAPI (sem rede)."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("CRUNCHBASE_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

from app.services.collectors.serpapi_news import (
    _data_da_url,
    _eh_pagina_indice,
    _extrair_fonte,
    _normalizar_resultado,
)


def test_data_da_url_extrai_padrao_dos_portais():
    url = "https://oglobo.globo.com/economia/noticia/2024/03/08/ceo-da-vale.ghtml"
    assert _data_da_url(url) == "2024-03-08"


def test_data_da_url_aceita_mes_dia_sem_zero():
    assert _data_da_url("https://exame.com/2025/7/3/materia") == "2025-07-03"


def test_data_da_url_sem_data_retorna_none():
    assert _data_da_url("https://exame.com/negocios/materia-sem-data/") is None


def test_paginas_indice_sao_detectadas():
    assert _eh_pagina_indice("https://www.estadao.com.br/tudo-sobre/eduardo-bartolomeo/")
    assert _eh_pagina_indice("https://neofeed.com.br/noticias-sobre/eduardo-bartolomeo/")
    assert _eh_pagina_indice("https://www.infomoney.com.br/tudo-sobre/eduardo-bartolomeo/")
    assert not _eh_pagina_indice("https://exame.com/negocios/vale-anuncia-investimento/")


def test_normalizar_usa_data_da_url_como_fallback():
    item = {
        "link": "https://oglobo.globo.com/economia/noticia/2024/10/02/despedida.ghtml",
        "title": "Despedida",
        "snippet": "…",
        # sem campo "date"
    }
    normalizado = _normalizar_resultado(item)
    assert normalizado["data_publicacao"] == "2024-10-02"
    assert normalizado["fonte"] == "oglobo"


def test_normalizar_prefere_data_da_url_sobre_date_do_serpapi():
    # O campo 'date' do SerpAPI vem em formato humano e não confiável;
    # a data embutida na URL tem prioridade.
    item = {
        "link": "https://exame.com/2024/01/15/materia",
        "title": "t",
        "date": "Jun 15, 2026",
    }
    assert _normalizar_resultado(item)["data_publicacao"] == "2024-01-15"


def test_normalizar_usa_date_do_serpapi_quando_url_nao_tem_data():
    item = {
        "link": "https://exame.com/negocios/materia-sem-data",
        "title": "t",
        "date": "2024-02-20",
    }
    assert _normalizar_resultado(item)["data_publicacao"] == "2024-02-20"


def test_extrair_fonte_conhecida_e_desconhecida():
    assert _extrair_fonte("https://valor.globo.com/x") == "valor"
    assert _extrair_fonte("https://siteobscuro.com/x") == "outros"
