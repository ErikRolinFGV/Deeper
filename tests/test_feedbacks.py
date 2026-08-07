"""Testes dos feedbacks de usuário: sugestões, busca com URL e evidências no grafo."""

import os
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import busca as busca_api
from app.api import sugestoes as sugestoes_api
from app.core.db import Base, get_db
from app.main import app
from app.models.pessoa import Pessoa
from app.models.relacao import Relacao

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def preparar(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(busca_api, "enfileirar_coleta", lambda job_id: None)
    monkeypatch.setattr(sugestoes_api, "sugerir_perfis_linkedin", lambda q, limite=5: [])
    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    s = TestingSession()
    yield s
    s.close()


# ---------- /sugestoes ----------


def test_sugestoes_locais_por_nome_e_cargo(client, db):
    db.add_all(
        [
            Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo",
                   cargo_atual="Conselheiro — Boston Metal", briefing="ok"),
            Pessoa(slug="gustavo-pimenta", nome="Gustavo Pimenta", cargo_atual="CEO da Vale"),
            Pessoa(slug="outra-pessoa", nome="Outra Pessoa"),
        ]
    )
    db.commit()

    corpo = client.get("/sugestoes?q=eduardo").json()
    assert [s["nome"] for s in corpo["locais"]] == ["Eduardo Bartolomeo"]
    assert corpo["locais"][0]["tem_briefing"] is True
    assert corpo["linkedin"] == []

    # Busca por cargo/empresa também encontra
    corpo = client.get("/sugestoes?q=vale").json()
    assert [s["nome"] for s in corpo["locais"]] == ["Gustavo Pimenta"]


def test_sugestoes_externas_sob_demanda(client, monkeypatch):
    chamadas = []

    def falso(q, limite=5):
        chamadas.append(q)
        return [{"nome": "Eduardo Bartolomeo", "headline": "Board Member — Boston Metal",
                 "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo"}]

    monkeypatch.setattr(sugestoes_api, "sugerir_perfis_linkedin", falso)

    # Sem externas: SerpAPI não é chamado (economia de cota)
    client.get("/sugestoes?q=eduardo+vale")
    assert chamadas == []

    corpo = client.get("/sugestoes?q=eduardo+vale&externas=true").json()
    assert len(chamadas) == 1
    assert corpo["linkedin"][0]["linkedin_url"].endswith("/in/eduardobartolomeo")


def test_sugestoes_exige_minimo_de_caracteres(client):
    assert client.get("/sugestoes?q=a").status_code == 422


# ---------- /busca com linkedin_url confirmada ----------


def test_busca_com_linkedin_url_cria_pessoa_com_url(client, db):
    corpo = client.post(
        "/busca",
        json={"nome": "Eduardo Bartolomeo",
              "linkedin_url": "https://br.linkedin.com/in/eduardobartolomeo"},
    ).json()
    assert corpo["job_id"] is not None

    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "eduardo-bartolomeo"))
    assert pessoa.linkedin_url == "https://br.linkedin.com/in/eduardobartolomeo"


def test_busca_trocar_linkedin_url_zera_dossie_do_homonimo(client, db):
    """Trocar o perfil = outra pessoa física: TODO o dossiê antigo é apagado."""
    from app.models.cargo import Cargo
    from app.models.empresa import Empresa
    from app.models.mencao import Mencao

    homonimo = Pessoa(
        slug="renato-costa", nome="Renato Costa",
        linkedin_url="https://www.linkedin.com/in/renato-costa-cio",
        linkedin_dados={"basic_info": {"fullname": "Renato Costa (CIO)"}},
        linkedin_coletado_em=datetime.now(timezone.utc),
        nome_completo="Renato Costa CIO", bio="Bio do CIO errado",
        foto_url="https://x/cio.jpg", cargo_atual="CIO — Odontoprev",
        briefing="Briefing do homônimo errado.",
    )
    outra = Pessoa(slug="outra-pessoa", nome="Outra Pessoa")
    empresa = Empresa(slug="odontoprev", nome="Odontoprev")
    db.add_all([homonimo, outra, empresa])
    db.flush()
    db.add_all(
        [
            Cargo(pessoa_id=homonimo.id, empresa_id=empresa.id, funcao="CIO", eh_atual=True),
            Mencao(pessoa_id=homonimo.id, fonte="valor", url="https://valor.globo.com/cio",
                   titulo="Matéria do CIO", sentimento=0.2),
            Relacao(pessoa_a_id=homonimo.id, pessoa_b_id=outra.id,
                    tipo="co_mencionado", peso=1, evidencias=[]),
        ]
    )
    db.commit()

    client.post(
        "/busca",
        json={"nome": "Renato Costa",
              "linkedin_url": "https://www.linkedin.com/in/renato-costa-friboi"},
    )

    db.expire_all()
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "renato-costa"))
    assert pessoa.linkedin_url == "https://www.linkedin.com/in/renato-costa-friboi"
    # Identidade antiga completamente apagada
    assert pessoa.linkedin_dados is None
    assert pessoa.bio is None
    assert pessoa.foto_url is None
    assert pessoa.cargo_atual is None
    assert pessoa.briefing is None
    assert db.scalar(select(Cargo).where(Cargo.pessoa_id == pessoa.id)) is None
    assert db.scalar(select(Mencao).where(Mencao.pessoa_id == pessoa.id)) is None
    assert db.scalar(select(Relacao)) is None


def test_busca_sem_linkedin_url_so_para_pessoa_existente(client, db):
    """Pessoa já no banco pode ser re-buscada sem URL; nova é rejeitada."""
    resp = client.post("/busca", json={"nome": "Fulano Qualquer"})
    assert resp.status_code == 422  # pessoa nova sem seleção: bloqueada

    db.add(Pessoa(slug="fulano-qualquer", nome="Fulano Qualquer"))
    db.commit()
    corpo = client.post(
        "/busca", json={"nome": "Fulano Qualquer", "force_refresh": True}
    ).json()
    assert corpo["job_id"] is not None  # existente: re-coleta liberada


def test_confirmar_no_do_grafo_preserva_conexoes(client, db):
    """Pesquisar um nó do grafo NÃO pode apagar a conexão que levou até ele."""
    raiz = Pessoa(slug="marcelo-diego", nome="Marcelo Diego", briefing="x",
                  linkedin_url="https://l/in/marcelo", identidade_confirmada=True)
    no = Pessoa(slug="joao-pedro", nome="João Pedro",
                contexto_origem="filho do executivo")  # nasceu de co-menção
    db.add_all([raiz, no])
    db.flush()
    db.add(Relacao(pessoa_a_id=raiz.id, pessoa_b_id=no.id, tipo="co_mencionado",
                   peso=2, evidencias=[{"titulo": "M"}]))
    db.commit()
    raiz_id, no_id = raiz.id, no.id

    # o analista confirma quem é o João Pedro
    client.post("/busca", json={"nome": "João Pedro",
                                "linkedin_url": "https://l/in/joao-pedro-real"})

    db.expire_all()
    atualizado = db.get(Pessoa, no_id)
    assert atualizado.linkedin_url == "https://l/in/joao-pedro-real"
    assert atualizado.identidade_confirmada is True
    # a aresta com quem o trouxe continua de pé
    rel = db.scalar(select(Relacao))
    assert rel is not None
    assert {rel.pessoa_a_id, rel.pessoa_b_id} == {raiz_id, no_id}

    # e agora o grafo dele mostra o caminho de volta
    grafo = client.get(f"/grafo/{no_id}?peso_minimo=1").json()
    assert raiz_id in {n["id"] for n in grafo["nodes"]}


def test_trocar_perfil_ja_confirmado_ainda_zera(client, db):
    """A proteção contra homônimo continua valendo para quem JÁ tinha perfil."""
    p = Pessoa(slug="renato-costa", nome="Renato Costa", briefing="do CIO",
               linkedin_url="https://l/in/renato-cio", identidade_confirmada=True)
    outro = Pessoa(slug="outro", nome="Outro")
    db.add_all([p, outro])
    db.flush()
    db.add(Relacao(pessoa_a_id=p.id, pessoa_b_id=outro.id, tipo="co_mencionado",
                   peso=1, evidencias=[]))
    db.commit()
    pid = p.id

    client.post("/busca", json={"nome": "Renato Costa",
                                "linkedin_url": "https://l/in/renato-friboi"})

    db.expire_all()
    assert db.get(Pessoa, pid).briefing is None
    assert db.scalar(select(Relacao)) is None


def test_acervo_total_e_real_mesmo_com_limite(client, db):
    db.add_all([Pessoa(slug=f"p{i}", nome=f"Pessoa {i}") for i in range(12)])
    db.commit()

    corpo = client.get("/acervo?limite=5").json()
    assert corpo["total"] == 12          # contagem real no banco
    assert corpo["exibindo"] == 5        # tamanho desta página
    assert len(corpo["pessoas"]) == 5


# ---------- exclusão de perfil ----------


def test_excluir_perfil_remove_tudo_que_dependia_dele(client, db):
    from app.models.cargo import Cargo
    from app.models.empresa import Empresa
    from app.models.mencao import Mencao

    empresa = Empresa(slug="vale", nome="Vale")
    alvo = Pessoa(slug="perfil-antigo", nome="Perfil Antigo",
                  linkedin_url="https://linkedin.com/in/antigo", briefing="x")
    outro = Pessoa(slug="outro-executivo", nome="Outro Executivo",
                   briefing="tem dossiê próprio", linkedin_url="https://linkedin.com/in/outro")
    db.add_all([empresa, alvo, outro])
    db.flush()
    db.add_all([
        Cargo(pessoa_id=alvo.id, empresa_id=empresa.id, funcao="CEO"),
        Mencao(pessoa_id=alvo.id, fonte="valor", url="https://v/1", titulo="M"),
        Relacao(pessoa_a_id=alvo.id, pessoa_b_id=outro.id, tipo="co_mencionado",
                peso=1, evidencias=[]),
    ])
    db.commit()
    alvo_id, outro_id = alvo.id, outro.id

    corpo = client.delete(f"/perfil/{alvo_id}").json()
    assert corpo["removido"] is True
    assert corpo["mencoes"] == 1 and corpo["relacoes"] == 1

    db.expire_all()
    assert db.get(Pessoa, alvo_id) is None
    assert db.scalars(select(Mencao)).all() == []
    assert db.scalars(select(Cargo)).all() == []
    assert db.scalar(select(Relacao)) is None
    # quem tem dossiê próprio permanece
    assert db.get(Pessoa, outro_id) is not None


def test_excluir_perfil_limpa_nos_orfaos(client, db):
    """Nó que só existia por co-menção some junto; quem tem dossiê fica."""
    alvo = Pessoa(slug="alvo", nome="Alvo", briefing="x",
                  linkedin_url="https://l/in/alvo")
    fantasma = Pessoa(slug="so-citado", nome="Só Citado")  # nasceu de co-menção
    com_dossie = Pessoa(slug="com-dossie", nome="Com Dossiê", briefing="tem",
                        identidade_confirmada=True)
    db.add_all([alvo, fantasma, com_dossie])
    db.flush()
    db.add_all([
        Relacao(pessoa_a_id=alvo.id, pessoa_b_id=fantasma.id,
                tipo="co_mencionado", peso=1, evidencias=[]),
        Relacao(pessoa_a_id=alvo.id, pessoa_b_id=com_dossie.id,
                tipo="co_mencionado", peso=1, evidencias=[]),
    ])
    db.commit()
    alvo_id, fantasma_id, com_dossie_id = alvo.id, fantasma.id, com_dossie.id

    corpo = client.delete(f"/perfil/{alvo_id}").json()
    assert corpo["orfaos_removidos"] == ["Só Citado"]

    db.expire_all()
    assert db.get(Pessoa, fantasma_id) is None
    assert db.get(Pessoa, com_dossie_id) is not None


def test_excluir_pode_preservar_orfaos(client, db):
    alvo = Pessoa(slug="alvo", nome="Alvo", briefing="x")
    fantasma = Pessoa(slug="so-citado", nome="Só Citado")
    db.add_all([alvo, fantasma])
    db.flush()
    db.add(Relacao(pessoa_a_id=alvo.id, pessoa_b_id=fantasma.id,
                   tipo="co_mencionado", peso=1, evidencias=[]))
    db.commit()

    alvo_id, fantasma_id = alvo.id, fantasma.id
    corpo = client.delete(f"/perfil/{alvo_id}?limpar_orfaos=false").json()
    assert corpo["orfaos_removidos"] == []
    assert db.get(Pessoa, fantasma_id) is not None


def test_excluir_perfil_inexistente_404(client):
    assert client.delete("/perfil/9999").status_code == 404


def test_pessoa_excluida_some_do_acervo(client, db):
    db.add(Pessoa(slug="temp", nome="Temporário", briefing="x"))
    db.commit()
    pid = db.scalar(select(Pessoa).where(Pessoa.slug == "temp")).id

    assert client.get("/acervo").json()["total"] == 1
    client.delete(f"/perfil/{pid}")
    assert client.get("/acervo").json()["total"] == 0


# ---------- busca por link do LinkedIn ----------


def test_sugestoes_por_url_resolve_o_perfil(client, monkeypatch):
    """Colar o link identifica a pessoa sem ambiguidade."""
    monkeypatch.setattr(
        sugestoes_api, "resolver_perfil_por_url",
        lambda url: {"nome": "Eduardo Bartolomeo", "headline": "Board Member",
                     "linkedin_url": url},
    )
    chamou_busca_texto = []
    monkeypatch.setattr(
        sugestoes_api, "sugerir_perfis_linkedin",
        lambda q, limite=5: (chamou_busca_texto.append(q), [])[1],
    )

    corpo = client.get(
        "/sugestoes?q=https://br.linkedin.com/in/eduardobartolomeo&externas=true"
    ).json()
    assert corpo["por_url"] is True
    assert corpo["linkedin"][0]["nome"] == "Eduardo Bartolomeo"
    assert chamou_busca_texto == []  # não gasta busca por texto


def test_sugestoes_por_url_de_quem_ja_esta_no_acervo(client, db, monkeypatch):
    """Se o perfil já foi coletado, abre do acervo sem custo de API."""
    db.add(Pessoa(slug="eduardo-bartolomeo", nome="Eduardo Bartolomeo",
                  linkedin_url="https://br.linkedin.com/in/eduardobartolomeo",
                  briefing="pronto", identidade_confirmada=True))
    db.commit()
    monkeypatch.setattr(
        sugestoes_api, "resolver_perfil_por_url",
        lambda url: pytest.fail("não deveria gastar SerpAPI"),
    )

    corpo = client.get(
        "/sugestoes?q=https://www.linkedin.com/in/eduardobartolomeo/?trk=abc"
    ).json()
    assert corpo["por_url"] is True
    assert corpo["locais"][0]["nome"] == "Eduardo Bartolomeo"
    assert corpo["linkedin"] == []


def test_url_com_parametros_longos_e_aceita(client, monkeypatch):
    monkeypatch.setattr(
        sugestoes_api, "resolver_perfil_por_url",
        lambda url: {"nome": "X", "headline": None, "linkedin_url": url},
    )
    url = "https://www.linkedin.com/in/paulo-jose-marinho-273927b/?originalSubdomain=br&trk=" + "a" * 120
    assert client.get(f"/sugestoes?q={url}").status_code == 200


def test_nome_derivado_da_url_quando_busca_falha():
    from app.services.collectors.apify_linkedin import nome_a_partir_da_url

    assert nome_a_partir_da_url("https://br.linkedin.com/in/eduardo-bartolomeo") == "Eduardo Bartolomeo"
    assert nome_a_partir_da_url("https://linkedin.com/in/paulo-jose-marinho-273927b") == "Paulo Jose Marinho"


def test_extrair_url_linkedin_reconhece_formatos():
    from app.services.collectors.apify_linkedin import extrair_url_linkedin

    assert extrair_url_linkedin("https://br.linkedin.com/in/fulano") == "https://br.linkedin.com/in/fulano"
    assert extrair_url_linkedin("veja https://www.linkedin.com/in/fulano/ aqui") == "https://www.linkedin.com/in/fulano"
    assert extrair_url_linkedin("Eduardo Bartolomeo") is None
    assert extrair_url_linkedin("https://www.linkedin.com/company/vale") is None


# ---------- identidade dos nós do grafo ----------


def test_no_do_grafo_carrega_contexto_e_status(client, db):
    """Quem foi pesquisado é 'confirmado'; quem veio de matéria, não."""
    alvo = Pessoa(slug="marcelo-diego", nome="Marcelo Diego",
                  linkedin_url="https://linkedin.com/in/marcelo",
                  identidade_confirmada=True, briefing="Dossiê pronto.")
    citado = Pessoa(slug="joao-pedro", nome="João Pedro",
                    contexto_origem="filho do executivo")
    db.add_all([alvo, citado])
    db.flush()
    db.add(Relacao(pessoa_a_id=alvo.id, pessoa_b_id=citado.id,
                   tipo="co_mencionado", peso=2, evidencias=[]))
    db.commit()

    nos = {n["label"]: n for n in client.get(f"/grafo/{alvo.id}?peso_minimo=1").json()["nodes"]}
    assert nos["Marcelo Diego"]["identidade_confirmada"] is True
    assert nos["Marcelo Diego"]["tem_dossie"] is True
    assert nos["João Pedro"]["identidade_confirmada"] is False
    assert nos["João Pedro"]["contexto_origem"] == "filho do executivo"
    assert nos["João Pedro"]["tem_dossie"] is False


def test_busca_com_linkedin_confirmado_marca_identidade(client, db):
    client.post("/busca", json={"nome": "Novo Executivo",
                                "linkedin_url": "https://linkedin.com/in/novo"})
    pessoa = db.scalar(select(Pessoa).where(Pessoa.slug == "novo-executivo"))
    assert pessoa.identidade_confirmada is True


def test_sugestoes_usam_contexto_na_query_do_linkedin(client, monkeypatch):
    consultas = []
    monkeypatch.setattr(
        sugestoes_api, "sugerir_perfis_linkedin",
        lambda q, limite=5: (consultas.append(q), [])[1],
    )

    client.get("/sugestoes?q=Jo%C3%A3o%20Pedro&externas=true&contexto=CFO%20da%20Vale")
    assert consultas == ["João Pedro CFO da Vale"]


# ---------- anotação humana nas relações ----------


def _par_com_relacao(db):
    a = Pessoa(slug="pai", nome="Pai Executivo")
    b = Pessoa(slug="joao-pedro", nome="João Pedro")
    db.add_all([a, b])
    db.flush()
    rel = Relacao(pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado",
                  peso=2, evidencias=[{"mencao_url": "https://x/1", "titulo": "M1"}])
    db.add(rel)
    db.commit()
    return a, b, rel


def test_anotar_relacao_salva_rotulo_e_nota(client, db):
    a, b, rel = _par_com_relacao(db)

    corpo = client.patch(
        f"/grafo/relacao/{rel.id}",
        json={"rotulo": "filho", "nota": "citados juntos em 2 matérias"},
    ).json()
    assert corpo["rotulo"] == "filho"
    assert corpo["anotado_em"] is not None

    # a anotação volta no grafo
    grafo = client.get(f"/grafo/{a.id}?profundidade=1&peso_minimo=1").json()
    aresta = grafo["edges"][0]
    assert aresta["id"] == rel.id
    assert aresta["rotulo"] == "filho"
    assert aresta["nota"] == "citados juntos em 2 matérias"


def test_anotacao_vazia_limpa_o_registro(client, db):
    a, b, rel = _par_com_relacao(db)
    client.patch(f"/grafo/relacao/{rel.id}", json={"rotulo": "sócio"})

    corpo = client.patch(f"/grafo/relacao/{rel.id}", json={"rotulo": "  ", "nota": ""}).json()
    assert corpo["rotulo"] is None
    assert corpo["anotado_em"] is None


def test_anotacao_sobrevive_a_recoleta(client, db):
    """O pipeline reforça a aresta existente — a anotação não pode se perder."""
    from app.services.graph.construtor import reforcar_relacao

    a, b, rel = _par_com_relacao(db)
    client.patch(f"/grafo/relacao/{rel.id}", json={"rotulo": "filho"})

    # nova coleta encontra a dupla de novo
    reforcar_relacao(db, a.id, b.id, "co_mencionado", {"mencao_url": "https://x/2"})
    db.commit()

    db.expire_all()
    atualizada = db.get(Relacao, rel.id)
    assert atualizada.peso == 3
    assert atualizada.rotulo == "filho"  # anotação preservada


def test_ocultar_relacao_some_do_grafo(client, db):
    """O analista marca a conexão falsa como incorreta e ela sai da rede."""
    a, b, rel = _par_com_relacao(db)
    assert len(client.get(f"/grafo/{a.id}?peso_minimo=1").json()["edges"]) == 1

    corpo = client.patch(f"/grafo/relacao/{rel.id}", json={"oculta": True}).json()
    assert corpo["oculta"] is True

    grafo = client.get(f"/grafo/{a.id}?peso_minimo=1").json()
    assert grafo["edges"] == []
    # o registro permanece no banco (não é ressuscitado sem ninguém ver)
    assert db.get(Relacao, rel.id) is not None


def test_ocultar_nao_apaga_anotacao_existente(client, db):
    a, b, rel = _par_com_relacao(db)
    client.patch(f"/grafo/relacao/{rel.id}", json={"rotulo": "filho"})

    corpo = client.patch(f"/grafo/relacao/{rel.id}", json={"oculta": True}).json()
    assert corpo["rotulo"] == "filho"  # PATCH parcial não zera o que não veio


def test_anotar_relacao_inexistente_404(client):
    assert client.patch("/grafo/relacao/9999", json={"rotulo": "x"}).status_code == 404


# ---------- trecho da matéria no perfil ----------


def test_perfil_expoe_trecho_da_materia(client, db):
    from app.api.perfil import TRECHO_CHARS
    from app.models.mencao import Mencao

    pessoa = Pessoa(slug="alvo", nome="Alvo")
    db.add(pessoa)
    db.flush()
    db.add_all([
        Mencao(pessoa_id=pessoa.id, fonte="valor", url="https://valor/1",
               titulo="Curta", texto="Texto curto da matéria."),
        Mencao(pessoa_id=pessoa.id, fonte="exame", url="https://exame/2",
               titulo="Longa", texto="P" * (TRECHO_CHARS + 500)),
        Mencao(pessoa_id=pessoa.id, fonte="oglobo", url="https://oglobo/3",
               titulo="Sem texto", texto=None),
    ])
    db.commit()

    mencoes = {m["titulo"]: m for m in client.get(f"/perfil/{pessoa.id}").json()["mencoes"]}
    assert mencoes["Curta"]["trecho"] == "Texto curto da matéria."
    assert mencoes["Curta"]["trecho_truncado"] is False
    assert len(mencoes["Longa"]["trecho"]) == TRECHO_CHARS
    assert mencoes["Longa"]["trecho_truncado"] is True
    assert mencoes["Sem texto"]["trecho"] is None


# ---------- /acervo ----------


def test_acervo_lista_pessoas_com_flag_de_briefing(client, db):
    db.add_all(
        [
            Pessoa(slug="com-dossie", nome="Com Dossiê", cargo_atual="CEO",
                   briefing="Briefing pronto."),
            Pessoa(slug="sem-dossie", nome="Sem Dossiê"),
        ]
    )
    db.commit()

    corpo = client.get("/acervo").json()
    assert corpo["total"] == 2
    por_nome = {p["nome"]: p for p in corpo["pessoas"]}
    assert por_nome["Com Dossiê"]["tem_briefing"] is True
    assert por_nome["Sem Dossiê"]["tem_briefing"] is False
    assert por_nome["Com Dossiê"]["cargo_atual"] == "CEO"


def test_acervo_vazio(client):
    corpo = client.get("/acervo").json()
    assert corpo["total"] == 0 and corpo["pessoas"] == []


# ---------- /grafo com evidências ----------


def test_grafo_expoe_evidencias_com_contexto(client, db):
    a = Pessoa(slug="a", nome="Pessoa A")
    b = Pessoa(slug="b", nome="Pessoa B")
    db.add_all([a, b])
    db.flush()
    db.add(
        Relacao(
            pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado", peso=2,
            evidencias=[
                {"mencao_url": "https://valor.globo.com/x", "titulo": "Matéria real",
                 "contexto": "direta"},
                {"mencao_url": "https://exame.com/50-mais-ricos",
                 "titulo": "50 mais ricos do Brasil", "contexto": "lista"},
            ],
        )
    )
    db.commit()

    corpo = client.get(f"/grafo/{a.id}?profundidade=1&peso_minimo=1").json()
    assert len(corpo["edges"]) == 1
    evs = corpo["edges"][0]["evidencias"]
    assert len(evs) == 2
    assert evs[0]["contexto"] == "direta"
    assert evs[1]["contexto"] == "lista"
    assert evs[1]["titulo"] == "50 mais ricos do Brasil"


def test_layout_do_grafo_e_salvo_e_devolvido(client, db):
    """A disposição arrumada pelo analista fica no servidor, para a equipe."""
    a = Pessoa(slug="a", nome="A")
    b = Pessoa(slug="b", nome="B")
    db.add_all([a, b])
    db.flush()
    db.add(Relacao(pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado",
                   peso=2, evidencias=[]))
    db.commit()
    ids = {"a": a.id, "b": b.id}

    posicoes = {str(ids["a"]): {"x": 0.0, "y": 0.0},
                str(ids["b"]): {"x": 220.0, "y": -140.0}}
    corpo = client.put(f"/grafo/{ids['a']}/layout", json={"posicoes": posicoes}).json()
    assert corpo["salvo"] is True and corpo["nos"] == 2

    grafo = client.get(f"/grafo/{ids['a']}?peso_minimo=1").json()
    assert grafo["layout"][str(ids["b"])]["x"] == 220.0


def test_layout_vazio_limpa_a_disposicao(client, db):
    p = Pessoa(slug="p", nome="P", grafo_layout={"1": {"x": 5.0, "y": 5.0}})
    db.add(p)
    db.commit()
    pid = p.id

    client.put(f"/grafo/{pid}/layout", json={"posicoes": {}})

    db.expire_all()
    assert db.get(Pessoa, pid).grafo_layout is None
    assert client.get(f"/grafo/{pid}").json()["layout"] is None


def test_layout_de_pessoa_inexistente_404(client):
    assert client.put("/grafo/9999/layout", json={"posicoes": {}}).status_code == 404


def test_grafo_nao_traz_ilha_ligada_por_aresta_oculta(client, db):
    """Aresta marcada como incorreta não pode arrastar um grupo para o grafo."""
    raiz = Pessoa(slug="raiz", nome="Raiz")
    ponte = Pessoa(slug="ponte", nome="Ponte")     # só se liga à raiz pela oculta
    ilha = Pessoa(slug="ilha", nome="Ilha")        # pendurada na ponte
    db.add_all([raiz, ponte, ilha])
    db.flush()
    db.add_all([
        Relacao(pessoa_a_id=raiz.id, pessoa_b_id=ponte.id, tipo="co_mencionado",
                peso=2, evidencias=[], oculta=True),      # o analista removeu
        Relacao(pessoa_a_id=ponte.id, pessoa_b_id=ilha.id, tipo="co_mencionado",
                peso=2, evidencias=[]),
    ])
    db.commit()

    corpo = client.get(f"/grafo/{raiz.id}?profundidade=3&peso_minimo=1").json()
    ids = {n["id"] for n in corpo["nodes"]}
    assert ids == {raiz.id}          # nada além da raiz
    assert corpo["edges"] == []


def test_grafo_nao_traz_ilha_ligada_por_aresta_fraca(client, db):
    """Mesma regra para o filtro de peso mínimo: sem ponte, sem grupo."""
    raiz = Pessoa(slug="raiz", nome="Raiz")
    ponte = Pessoa(slug="ponte", nome="Ponte")
    ilha = Pessoa(slug="ilha", nome="Ilha")
    db.add_all([raiz, ponte, ilha])
    db.flush()
    db.add_all([
        Relacao(pessoa_a_id=raiz.id, pessoa_b_id=ponte.id, tipo="co_mencionado",
                peso=1, evidencias=[]),                    # abaixo do mínimo
        Relacao(pessoa_a_id=ponte.id, pessoa_b_id=ilha.id, tipo="co_mencionado",
                peso=5, evidencias=[]),
    ])
    db.commit()

    corpo = client.get(f"/grafo/{raiz.id}?profundidade=3&peso_minimo=2").json()
    assert {n["id"] for n in corpo["nodes"]} == {raiz.id}


def test_grafo_respeita_a_profundidade(client, db):
    """Cadeia raiz→a→b→c: profundidade 2 para em b."""
    pessoas = [Pessoa(slug=f"p{i}", nome=f"P{i}") for i in range(4)]
    db.add_all(pessoas)
    db.flush()
    ids = [p.id for p in pessoas]
    db.add_all([
        Relacao(pessoa_a_id=min(ids[i], ids[i + 1]), pessoa_b_id=max(ids[i], ids[i + 1]),
                tipo="co_mencionado", peso=2, evidencias=[])
        for i in range(3)
    ])
    db.commit()

    corpo = client.get(f"/grafo/{ids[0]}?profundidade=2&peso_minimo=1").json()
    assert {n["id"] for n in corpo["nodes"]} == {ids[0], ids[1], ids[2]}

    corpo = client.get(f"/grafo/{ids[0]}?profundidade=3&peso_minimo=1").json()
    assert {n["id"] for n in corpo["nodes"]} == set(ids)


def test_grafo_mantem_aresta_que_fecha_ciclo(client, db):
    """Ligação entre dois nós já alcançados continua sendo desenhada."""
    a = Pessoa(slug="a", nome="A")
    b = Pessoa(slug="b", nome="B")
    c = Pessoa(slug="c", nome="C")
    db.add_all([a, b, c])
    db.flush()
    ids = sorted([a.id, b.id, c.id])
    db.add_all([
        Relacao(pessoa_a_id=ids[0], pessoa_b_id=ids[1], tipo="co_mencionado", peso=2, evidencias=[]),
        Relacao(pessoa_a_id=ids[0], pessoa_b_id=ids[2], tipo="co_mencionado", peso=2, evidencias=[]),
        Relacao(pessoa_a_id=ids[1], pessoa_b_id=ids[2], tipo="co_mencionado", peso=2, evidencias=[]),
    ])
    db.commit()

    corpo = client.get(f"/grafo/{ids[0]}?profundidade=2&peso_minimo=1").json()
    assert len(corpo["edges"]) == 3


def test_grafo_limita_evidencias_por_aresta(client, db):
    from app.services.graph.queries import MAX_EVIDENCIAS

    a = Pessoa(slug="a", nome="Pessoa A")
    b = Pessoa(slug="b", nome="Pessoa B")
    db.add_all([a, b])
    db.flush()
    db.add(
        Relacao(
            pessoa_a_id=a.id, pessoa_b_id=b.id, tipo="co_mencionado", peso=20,
            evidencias=[{"mencao_url": f"https://x.com/{i}", "titulo": f"M{i}"} for i in range(20)],
        )
    )
    db.commit()

    corpo = client.get(f"/grafo/{a.id}?profundidade=1&peso_minimo=1").json()
    evs = corpo["edges"][0]["evidencias"]
    assert len(evs) == MAX_EVIDENCIAS
    assert evs[-1]["titulo"] == "M19"  # mantém as mais recentes
