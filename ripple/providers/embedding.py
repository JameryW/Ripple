"""Embedding Provider — supplies vector embeddings for Ripple content."""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from .base import DataSourceProvider


@runtime_checkable
class EmbeddingProvider(DataSourceProvider, Protocol):
    """Provides vector embeddings for text content."""

    async def embed(self, text: str) -> List[float] | None:
        """Return an embedding vector for the given text, or ``None`` on failure."""
        ...

    async def embed_batch(self, texts: List[str]) -> List[List[float] | None]:
        """Return embeddings for a batch of texts. Individual items may be ``None``."""
        ...


class StubEmbeddingProvider:
    """No-op stub — always returns ``None`` (content_embedding stays empty)."""

    @property
    def name(self) -> str:
        return "stub-embedding"

    def is_available(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return False

    async def embed(self, text: str) -> List[float] | None:
        return None

    async def embed_batch(self, texts: List[str]) -> List[List[float] | None]:
        return [None] * len(texts)
