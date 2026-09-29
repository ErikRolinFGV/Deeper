"""Fotos de perfil: a URL do LinkedIn expira, a nossa cópia não.

Regressão do bug em que dossiês atualizados voltavam sem imagem: o cache
de 30 dias do payload do Apify reaplicava uma URL de CDN já vencida.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("CRUNCHBASE_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")
from datetime import datetime, timedelta, timezone

from app.services import fotos
from app.services.fotos import foto_expirada, foto_publica, guardar_foto, remover_foto


def _url(dias: int) -> str:
    """URL no formato da CDN do LinkedIn, vencendo em `dias` (pode ser negativo)."""
    ts = int((datetime.now(timezone.utc) + timedelta(days=dias)).timestamp())
    return f"https://media.licdn.com/dms/image/v2/abc/foto.jpg?e={ts}&v=beta&t=xyz"


class TestFotoExpirada:
    def test_url_vencida_e_detectada(self):
        assert foto_expirada(_url(-25)) is True

    def test_url_valida_passa(self):
        assert foto_expirada(_url(60)) is False

    def test_margem_de_seguranca(self):
        # Vence amanhã: já tratamos como expirada para não quebrar no meio do uso.
        assert foto_expirada(_url(1)) is True

    def test_sem_parametro_e_nao_recoleta(self):
        # Não dá para saber, e recoletar custa dinheiro no Apify.
        assert foto_expirada("https://exemplo.com/foto.jpg") is False

    def test_none_e_lixo_nao_quebram(self):
        assert foto_expirada(None) is False
        assert foto_expirada("") is False
        assert foto_expirada("https://x.com/f.jpg?e=abacaxi") is False
        assert foto_expirada("https://x.com/f.jpg?e=99999999999999999999") is False


class TestCopiaLocal:
    def test_guarda_serve_e_remove(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 40

        class Resp:
            content = png
            headers = {"content-type": "image/png"}

            def raise_for_status(self):
                pass

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                return Resp()

        monkeypatch.setattr(fotos.httpx, "Client", lambda **kw: Client())

        assert guardar_foto(7, _url(30)) is True
        assert fotos.caminho_local(7).read_bytes() == png
        assert fotos.caminho_local(7).suffix == ".png"

        pessoa = type("P", (), {"id": 7, "foto_url": _url(-5)})()
        assert "/foto/7?v=" in foto_publica(pessoa)  # ?v= invalida o cache do navegador

        remover_foto(7)
        assert fotos.caminho_local(7) is None
        # Sem cópia local, cai no original do LinkedIn (comportamento antigo).
        assert foto_publica(pessoa) == pessoa.foto_url

    def test_download_que_falha_nao_derruba_a_coleta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")

        def explode(**kw):
            raise RuntimeError("CDN fora do ar")

        monkeypatch.setattr(fotos.httpx, "Client", explode)
        assert guardar_foto(9, _url(30)) is False
        assert fotos.caminho_local(9) is None

    def test_resposta_que_nao_e_imagem_e_descartada(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")

        class Resp:
            content = b"<html>403</html>"
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                pass

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                return Resp()

        monkeypatch.setattr(fotos.httpx, "Client", lambda **kw: Client())
        assert guardar_foto(11, _url(30)) is False

    def test_sem_id_ou_sem_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")
        assert guardar_foto(None, _url(30)) is False
        assert guardar_foto(3, None) is False


# --------------------------------------------------------------------------
# Cache do LinkedIn x validade da foto
# --------------------------------------------------------------------------

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models.pessoa import Pessoa
from app.workers import busca_worker

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Sessao = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    sessao = Sessao()
    try:
        yield sessao
    finally:
        sessao.close()
        Base.metadata.drop_all(engine)


def _payload(url_foto: str) -> dict:
    return {"basic_info": {"fullname": "Fulano de Tal", "profile_picture_url": url_foto}}


def _pessoa_com_cache(db, url_foto: str) -> Pessoa:
    """Pessoa já coletada há 5 dias — bem dentro do TTL de 30 dias."""
    pessoa = Pessoa(
        nome="Fulano de Tal",
        slug="fulano-de-tal",
        linkedin_url="https://www.linkedin.com/in/fulano/",
        linkedin_dados=_payload(url_foto),
        linkedin_coletado_em=datetime.now(timezone.utc) - timedelta(days=5),
    )
    db.add(pessoa)
    db.flush()
    return pessoa


class TestCacheDoLinkedIn:
    def test_foto_vencida_fura_o_cache_e_recoleta(self, db, tmp_path, monkeypatch):
        """O bug: 'atualizar dossiê' reaplicava a URL morta e a foto sumia."""
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")
        monkeypatch.setattr(busca_worker, "guardar_foto", lambda *a, **k: True)
        pessoa = _pessoa_com_cache(db, _url(-25))

        chamadas = []
        nova = _url(30)

        def coletar(url):
            chamadas.append(url)
            return _payload(nova)

        monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar)
        perfil = busca_worker._atualizar_com_linkedin(db, pessoa)

        assert chamadas, "deveria ter recoletado no Apify"
        assert perfil["foto_url"] == nova
        assert pessoa.foto_url == nova

    def test_foto_valida_mantem_o_cache(self, db, tmp_path, monkeypatch):
        """Recoleta custa dinheiro: sem motivo, não paga."""
        monkeypatch.setattr(fotos, "DIR_FOTOS", tmp_path / "fotos")
        monkeypatch.setattr(busca_worker, "guardar_foto", lambda *a, **k: True)
        pessoa = _pessoa_com_cache(db, _url(90))

        def coletar(url):
            raise AssertionError("não deveria recoletar")

        monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar)
        assert busca_worker._atualizar_com_linkedin(db, pessoa)["foto_url"]

    def test_copia_local_dispensa_a_recoleta(self, db, tmp_path, monkeypatch):
        """Já temos os bytes: a URL vencida não importa mais."""
        dir_fotos = tmp_path / "fotos"
        dir_fotos.mkdir(parents=True)
        monkeypatch.setattr(fotos, "DIR_FOTOS", dir_fotos)
        monkeypatch.setattr(busca_worker, "guardar_foto", lambda *a, **k: True)
        pessoa = _pessoa_com_cache(db, _url(-25))
        (dir_fotos / f"{pessoa.id}.jpg").write_bytes(b"bytes-da-foto")

        def coletar(url):
            raise AssertionError("não deveria recoletar: a foto já é nossa")

        monkeypatch.setattr(busca_worker, "coletar_perfil_linkedin", coletar)
        assert busca_worker._atualizar_com_linkedin(db, pessoa) is not None
