"""Configuração comum: nenhum teste deve gastar busca real no SerpAPI."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APIFY_TOKEN", "test")
os.environ.setdefault("SERPAPI_KEY", "test")
os.environ.setdefault("CRUNCHBASE_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test")

import pytest


@pytest.fixture(autouse=True)
def _sem_busca_de_foto_na_imprensa(monkeypatch):
    """O worker tenta a foto da imprensa quando falta foto; em teste, nada."""
    from app.services import fotos

    monkeypatch.setattr(fotos, "buscar_foto_imprensa", lambda nome, contexto=None: [])
