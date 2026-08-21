"""Pluggable embedding providers behind a Protocol.

  * DevEmbedder    - deterministic, offline (no key). NOT semantically meaningful;
                     for local runs/tests only.
  * VoyageEmbedder - voyage-3 etc. Recommended for life-science text.
  * OpenAIEmbedder - text-embedding-3-* alternative.

The matcher and the eval harness depend only on the `Embedder` Protocol, so the
provider is a one-line swap with zero downstream changes.

NOTE: EMBEDDING_DIM in config must match the provider's output dimension and the
Vector(dim) column in the schema. Changing providers/dims means a migration.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from astor.config import settings


def _batched(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class DevEmbedder:
    """Deterministic hash-based pseudo-embeddings. Dev/offline only."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in text.lower().split():
            h = int(hashlib.sha1(token.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class VoyageEmbedder:  # pragma: no cover - needs a live key
    def __init__(self, model: str = "voyage-3", batch_size: int = 128) -> None:
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        import voyageai  # type: ignore

        client = voyageai.Client(api_key=settings.voyage_api_key)
        out: list[list[float]] = []
        for chunk in _batched(texts, self.batch_size):
            res = client.embed(chunk, model=self.model, input_type="document")
            out.extend(res.embeddings)
        return out


class OpenAIEmbedder:  # pragma: no cover - needs a live key
    def __init__(self, model: str = "text-embedding-3-small", batch_size: int = 256) -> None:
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=settings.openai_api_key)
        out: list[list[float]] = []
        for chunk in _batched(texts, self.batch_size):
            res = client.embeddings.create(model=self.model, input=chunk)
            out.extend(d.embedding for d in res.data)
        return out


def get_embedder() -> Embedder:
    """Select the configured embedding provider.

    A configured real provider with no key RAISES rather than degrading to
    DevEmbedder: DevEmbedder's hash-based vectors are the right dimension, so
    Postgres accepts them without complaint, which would otherwise let a
    misconfigured deploy silently write junk embeddings (and junk `equivalences`
    rows) into the production database with a 200 response.
    """
    provider = settings.embeddings_provider.lower()
    if provider == "dev":
        return DevEmbedder()
    if provider == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError(
                "EMBEDDINGS_PROVIDER=voyage but VOYAGE_API_KEY is not set — refusing "
                "to fall back to DevEmbedder and write meaningless vectors.")
        return VoyageEmbedder()
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "EMBEDDINGS_PROVIDER=openai but OPENAI_API_KEY is not set — refusing "
                "to fall back to DevEmbedder and write meaningless vectors.")
        return OpenAIEmbedder()
    raise RuntimeError(
        f"Unrecognized EMBEDDINGS_PROVIDER={settings.embeddings_provider!r} "
        "(expected 'dev', 'voyage', or 'openai').")
