import pytest

from astor.catalog.embeddings import DevEmbedder, OpenAIEmbedder, VoyageEmbedder, get_embedder
from astor.config import settings


def test_dev_provider_returns_dev_embedder(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "dev")
    assert isinstance(get_embedder(), DevEmbedder)


def test_voyage_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "voyage")
    monkeypatch.setattr(settings, "voyage_api_key", None)
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        get_embedder()


def test_openai_without_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_embedder()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "not-a-real-provider")
    with pytest.raises(RuntimeError, match="Unrecognized EMBEDDINGS_PROVIDER"):
        get_embedder()


def test_voyage_with_key_selects_voyage_embedder(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "voyage")
    monkeypatch.setattr(settings, "voyage_api_key", "fake-key")
    assert isinstance(get_embedder(), VoyageEmbedder)


def test_openai_with_key_selects_openai_embedder(monkeypatch):
    monkeypatch.setattr(settings, "embeddings_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "fake-key")
    assert isinstance(get_embedder(), OpenAIEmbedder)
