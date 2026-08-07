"""Testes do leitor de artigos (extração do corpo da matéria)."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

from app.services.collectors import leitor_artigo
from app.services.collectors.leitor_artigo import baixar_artigo, baixar_texto, eh_autor

PARAGRAFO = (
    "O executivo afirmou que a companhia seguirá investindo em descarbonização "
    "ao lado de parceiros estratégicos do setor, segundo apuração da reportagem."
)

HTML_MATERIA = f"""
<html><head><script>var lixo=1;</script><style>.x{{}}</style></head><body>
<nav><p>Menu de navegação do portal com muitos caracteres irrelevantes aqui</p></nav>
<article>
  <p>Crédito: Agência</p>
  <p>{PARAGRAFO}</p>
  <p>{PARAGRAFO} Complemento adicional da segunda passagem do texto da matéria.</p>
</article>
<footer><p>Rodapé institucional do portal com links e políticas de privacidade</p></footer>
</body></html>
"""


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_extrai_corpo_do_article(monkeypatch):
    monkeypatch.setattr(leitor_artigo.httpx, "get", lambda *a, **kw: _Resp(HTML_MATERIA))
    texto = baixar_texto("https://valor.globo.com/materia")
    assert texto is not None
    assert PARAGRAFO in texto
    assert "Menu de navegação" not in texto  # nav descartada
    assert "Rodapé institucional" not in texto  # footer descartado
    assert "Crédito: Agência" not in texto  # migalha (<60 chars) descartada


def test_pagina_sem_conteudo_retorna_none(monkeypatch):
    monkeypatch.setattr(
        leitor_artigo.httpx, "get", lambda *a, **kw: _Resp("<html><body><p>curto</p></body></html>")
    )
    assert baixar_texto("https://x.com/vazio") is None


def test_falha_de_rede_retorna_none(monkeypatch):
    def explode(*a, **kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr(leitor_artigo.httpx, "get", explode)
    assert baixar_texto("https://x.com/fora") is None


# ---------- autoria (caso Marcelo Diego: repórter virou "conexão") ----------


def test_extrai_autor_de_meta_tag(monkeypatch):
    html = f"""<html><head><meta name="author" content="Marcelo Diego"></head><body>
    <article><p>{PARAGRAFO}</p><p>{PARAGRAFO} Segundo trecho da matéria.</p></article>
    </body></html>"""
    monkeypatch.setattr(leitor_artigo.httpx, "get", lambda *a, **kw: _Resp(html))
    art = baixar_artigo("https://folha/materia")
    assert art["autor"] == "Marcelo Diego"


def test_extrai_autor_de_assinatura_no_texto(monkeypatch):
    html = f"""<html><body><article>
    <p>Por Marcelo Diego, enviado especial a Washington, para a Folha de S.Paulo</p>
    <p>{PARAGRAFO}</p><p>{PARAGRAFO} Continuação do texto da reportagem.</p>
    </article></body></html>"""
    monkeypatch.setattr(leitor_artigo.httpx, "get", lambda *a, **kw: _Resp(html))
    art = baixar_artigo("https://folha/materia")
    assert art["autor"] and "Marcelo Diego" in art["autor"]


def test_materia_sem_autor(monkeypatch):
    monkeypatch.setattr(leitor_artigo.httpx, "get", lambda *a, **kw: _Resp(HTML_MATERIA))
    assert baixar_artigo("https://valor.globo.com/materia")["autor"] is None


def test_eh_autor_casa_nome_com_variacoes():
    assert eh_autor("Marcelo Diego", "Marcelo Diego") is True
    assert eh_autor("Marcelo Diego", "Marcelo Diego, da Folha de S.Paulo") is True
    assert eh_autor("Marcelo Diego", "MARCELO DIEGO") is True
    assert eh_autor("Marcelo Diego", "Diego Marcelo") is True  # ordem invertida
    # Outra pessoa não pode ser confundida com o alvo
    assert eh_autor("Marcelo Diego", "Marcelo Rubens Paiva") is False
    assert eh_autor("Marcelo Diego", "Redação") is False
    assert eh_autor("Marcelo Diego", None) is False
    assert eh_autor("Marcelo", "Marcelo Diego") is False  # nome único não basta


def test_respeita_teto_de_caracteres(monkeypatch):
    gigante = "<article>" + "".join(f"<p>{PARAGRAFO} bloco {i}.</p>" for i in range(100)) + "</article>"
    monkeypatch.setattr(leitor_artigo.httpx, "get", lambda *a, **kw: _Resp(gigante))
    texto = baixar_texto("https://x.com/longa")
    assert texto is not None
    assert len(texto) <= leitor_artigo.MAX_CHARS
