"""Ambient Provider — supplies real-time environment / trending data."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable

from .base import DataSourceProvider


@runtime_checkable
class AmbientProvider(DataSourceProvider, Protocol):
    """Provides real-time ambient context (trending topics, sentiment baseline, etc.)."""

    async def get_ambient(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
    ) -> Dict[str, Any] | None:
        """Return ambient context dict for ``Field.ambient``, or ``None`` for LLM fallback."""
        ...

    async def get_trending_memes(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        """Return trending meme data for ``Field.meme_pool``, or ``None`` for LLM fallback."""
        ...


class StubAmbientProvider:
    """No-op stub — always returns ``None`` (triggers LLM fallback)."""

    @property
    def name(self) -> str:
        return "stub-ambient"

    def is_available(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return False

    async def get_ambient(self, **kwargs: Any) -> Dict[str, Any] | None:
        return None

    async def get_trending_memes(self, **kwargs: Any) -> List[Dict[str, Any]] | None:
        return None
