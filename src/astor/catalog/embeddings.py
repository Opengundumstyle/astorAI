"""Pluggable embedding providers behind a Protocol.

`DevEmbedder` is deterministic and offline so the pipeline + tests run with no
API key. It is NOT semantically meaningful -- swap to Voyage/OpenAI before
trusting match quality. The matcher depends only on the Protocol.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from astor.config import settings


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


class VoyageEmbedder:  # pragma: no cover - wired when a key is present
    def __init__(self, model: str = "voyage-3") -> None:
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import voyageai  # type: ignore

        client = voyageai.Client(api_key=settings.voyage_api_key)
        return client.embed(texts, model=self.model, input_type="document").embeddings


def get_embedder() -> Embedder:
    if settings.embeddings_provider == "voyage" and settings.voyage_api_key:
        return VoyageEmbedder()
    return DevEmbedder()
