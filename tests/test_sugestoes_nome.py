"""Filtro de nome nas sugestões do LinkedIn (caso Rafael Miotto, 28/09/2026).

Para "Rafael Miotto" o Google devolveu Helder Salomão, André Wingert... gente
que só MENCIONA o executivo no perfil. Nenhum deles pode virar candidato.
"""

from app.services.collectors import apify_linkedin
from app.services.collectors.apify_linkedin import nome_confere, sugerir_perfis_linkedin


def test_nome_confere():
    assert nome_confere("Rafael Miotto", "Rafael Miotto")
    assert nome_confere("Rafael Miotto", "Rafael Souza Miotto")
    assert nome_confere("João Pedro de Souza", "Joao Pedro Souza")
    assert not nome_confere("Rafael Miotto", "Helder Salomão Júnior")
    assert not nome_confere("Rafael Miotto", "Rafael Almeida")
    assert not nome_confere("Rafael Miotto", None)


def _resultado(nome, headline, usuario):
    return {"title": f"{nome} - {headline} | LinkedIn", "link": f"https://br.linkedin.com/in/{usuario}"}


class _Resp:
    def __init__(self, dados):
        self._d = dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_modo_grafo_so_devolve_quem_tem_o_nome_e_ordena_por_pistas(monkeypatch):
    consultas = []
    dados = {"organic_results": [
        _resultado("Helder Salomão Júnior", "Legal Manager at CNH", "helder"),
        _resultado("Rafael Miotto", "Analista Comercial", "rafael-analista"),
        _resultado("Rafael Miotto", "Presidente CNH Industrial América Latina", "rafael-cnh"),
    ]}

    def get(url, params, timeout):
        consultas.append(params["q"])
        return _Resp(dados)

    monkeypatch.setattr(apify_linkedin.httpx, "get", get)
    r = sugerir_perfis_linkedin("Rafael Miotto", nome_exigido="Rafael Miotto",
                                contexto="Presidente da CNH para a América Latina")
    assert consultas == ['site:linkedin.com/in "Rafael Miotto"']
    assert [c["linkedin_url"].rsplit("/", 1)[-1] for c in r] == ["rafael-cnh", "rafael-analista"]


def test_modo_grafo_sem_ninguem_com_o_nome_devolve_vazio(monkeypatch):
    dados = {"organic_results": [_resultado("André Wingert", "Executivo de Contas", "andre")]}
    monkeypatch.setattr(apify_linkedin.httpx, "get", lambda url, params, timeout: _Resp(dados))
    assert sugerir_perfis_linkedin("Rafael Miotto", nome_exigido="Rafael Miotto") == []


def test_busca_livre_mantem_todos_quando_a_consulta_nao_e_nome(monkeypatch):
    dados = {"organic_results": [
        _resultado("David Vélez", "CEO at Nubank", "davidvelez"),
        _resultado("Cristina Junqueira", "Co-founder Nubank", "cris"),
    ]}
    monkeypatch.setattr(apify_linkedin.httpx, "get", lambda url, params, timeout: _Resp(dados))
    assert len(sugerir_perfis_linkedin("CEO do Nubank")) == 2
