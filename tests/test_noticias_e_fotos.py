"""Coleta de notícias via Google Notícias, extrator tolerante e fotos da imprensa.

Regressões de 28/09/2026:
- Alberto Griselli (CEO da TIM) saía sem notícias: a busca orgânica com
  `site:` dos 10 portais perdia a imprensa setorial e não ordenava por data.
- Maria Antonietta Russo: o extrator devolveu eventos como {"name": ...} e a
  validação derrubava a extração inteira; o LinkedIn dela não expõe foto.
"""

import pytest

from app.services import fotos
from app.services.collectors import serpapi_news
from app.services.collectors.serpapi_news import (
    _data_google_news,
    _normalizar_noticia,
    buscar_mencoes,
)
from app.services.llm.extrator import EntidadesExtraidas


# ---------- Google Notícias ----------

NOTICIA = {
    "title": "“Temos regras injustas”, diz Alberto Griselli, presidente da TIM",
    "source": {"name": "VEJA"},
    "link": "https://veja.abril.com.br/economia/temos-regras-injustas?utm_source=x",
    "date": "05/29/2026, 07:00 AM, +0000 UTC",
}


def test_data_do_google_news_mes_dia_ano():
    assert _data_google_news({"date": "05/29/2026, 07:00 AM, +0000 UTC"}) == "2026-05-29"
    assert _data_google_news({"iso_date": "2026-07-28T07:00:00Z"}) == "2026-07-28"
    assert _data_google_news({"date": "ontem"}) is None


def test_normalizar_noticia_limpa_url_e_identifica_fonte():
    n = _normalizar_noticia(NOTICIA)
    assert n["url"] == "https://veja.abril.com.br/economia/temos-regras-injustas"
    assert n["fonte"] == "veja"  # portal conhecido usa a chave curta
    assert n["data_publicacao"] == "2026-05-29"
    setorial = _normalizar_noticia(
        {"title": "t", "link": "https://www.telesintese.com.br/x", "source": {"name": "TeleSíntese"}}
    )
    assert setorial["fonte"] == "TeleSíntese"


def _fake_consultar(respostas):
    chamadas = []

    def consultar(params):
        chamadas.append(params["engine"])
        return respostas[params["engine"]]

    return consultar, chamadas


def test_google_news_e_principal_ordena_por_data_e_nao_complementa(monkeypatch):
    itens = [
        {"title": f"m{i}", "link": f"https://site{i}.com/m", "source": {"name": "S"},
         "date": f"0{1 + i % 9}/10/2026, 07:00 AM, +0000 UTC"}
        for i in range(10)
    ]
    # Cobertura agrupada: matérias dentro de "stories" também contam.
    itens.append({"title": "grupo", "stories": [
        {"title": "g1", "link": "https://grupo.com/1", "source": {"name": "G"},
         "date": "12/01/2025, 07:00 AM, +0000 UTC"}]})
    consultar, chamadas = _fake_consultar({"google_news": {"news_results": itens}})
    monkeypatch.setattr(serpapi_news, "_consultar", consultar)

    r = buscar_mencoes("Alberto Griselli", limite=30)
    assert chamadas == ["google_news"]
    assert len(r) == 11
    datas = [m["data_publicacao"] for m in r]
    assert datas == sorted(datas, reverse=True)
    assert "https://grupo.com/1" in {m["url"] for m in r}


def test_pouca_noticia_complementa_com_portais_sem_duplicar(monkeypatch):
    consultar, chamadas = _fake_consultar({
        "google_news": {"news_results": [NOTICIA]},
        "google": {"organic_results": [
            {"link": NOTICIA["link"], "title": "dup"},
            {"link": "https://exame.com/2024/01/15/perfil", "title": "perfil"},
            {"link": "https://www.estadao.com.br/tudo-sobre/fulano/", "title": "índice"},
        ]},
    })
    monkeypatch.setattr(serpapi_news, "_consultar", consultar)

    r = buscar_mencoes("Alberto Griselli", limite=30)
    assert chamadas == ["google_news", "google"]
    assert [m["url"] for m in r] == [
        "https://veja.abril.com.br/economia/temos-regras-injustas",
        "https://exame.com/2024/01/15/perfil",
    ]


def test_falha_de_rede_devolve_lista_vazia(monkeypatch):
    monkeypatch.setattr(serpapi_news, "_consultar", lambda params: None)
    assert buscar_mencoes("Alguém Qualquer") == []


def test_consultar_tenta_de_novo_apos_timeout(monkeypatch):
    import httpx

    tentativas = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def get(*a, **k):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise httpx.ReadTimeout("The read operation timed out")
        return Resp()

    monkeypatch.setattr(serpapi_news.httpx, "get", get)
    assert serpapi_news._consultar({"engine": "google_news"}) == {"ok": True}
    assert len(tentativas) == 2


# ---------- extrator tolerante ----------

def test_extrator_aceita_evento_como_objeto():
    e = EntidadesExtraidas(
        eventos=[{"name": "Época Negócios 360°"}, "Web Summit", {"x": 1}],
        temas=[{"nome": "ESG"}],
        empresas_mencionadas=["TIM"],
    )
    assert e.eventos == ["Época Negócios 360°", "Web Summit"]
    assert e.temas == ["ESG"]


# ---------- foto da imprensa ----------

IMAGENS = [
    {"original": "https://lookaside.instagram.com/seo/x?media_id=1", "title": "Maria Antonietta Russo",
     "original_width": 1080, "original_height": 1350},
    {"original": "https://veja.abril.com.br/wp/2024/05/tim-sede.jpg", "title": "Maria Antonietta Russo fala",
     "original_width": 1212, "original_height": 909},
    {"original": "https://classic.exame.com/wp/2024/02/Maria-Antonietta-Russo-tim.jpg", "title": "Na TIM Brasil",
     "original_width": 1380, "original_height": 920},
    {"original": "https://site.com/logo-maria-russo.png", "title": "x",
     "original_width": 400, "original_height": 400},
]


def test_pontuacao_exige_nome_no_arquivo():
    notas = [fotos._pontuar_candidata(i, "Maria Antonietta Russo") for i in IMAGENS]
    assert notas[0] == 0  # instagram
    assert notas[1] == 0  # nome só no título: pode ser foto de outra coisa
    assert notas[2] > 0
    assert notas[3] == 0  # logo


def test_foto_do_linkedin_indexada_tem_prioridade():
    li = {"original": "https://media.licdn.com/dms/image/v2/X/profile-displayphoto-shrink_200_200/0?e=1",
          "title": "Alberto Griselli - CEO at TIM Brasil | LinkedIn",
          "original_width": 200, "original_height": 200}
    imprensa = {"original": "https://infomoney.com.br/2024/02/Alberto-Griselli-1.jpg", "title": "t",
                "original_width": 1280, "original_height": 853}
    assert fotos._pontuar_candidata(li, "Alberto Griselli") > fotos._pontuar_candidata(
        imprensa, "Alberto Griselli")


def test_completar_foto_usa_primeira_que_baixa(monkeypatch, tmp_path):
    monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path)
    monkeypatch.setattr(fotos, "buscar_foto_imprensa",
                        lambda nome, contexto=None: ["https://a/quebrada.jpg", "https://b/boa.jpg"])
    baixadas = []

    def guardar(pid, url):
        baixadas.append(url)
        return url.endswith("boa.jpg")

    monkeypatch.setattr(fotos, "guardar_foto", guardar)

    class P:
        id, nome, nome_completo, foto_url = 227, "maria antonietta russo", "Maria Antonietta Russo", None

    p = P()
    assert fotos.completar_foto_pela_imprensa(p, "TIM Brasil") is True
    assert p.foto_url == "https://b/boa.jpg"
    assert baixadas == ["https://a/quebrada.jpg", "https://b/boa.jpg"]
