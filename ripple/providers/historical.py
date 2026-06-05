"""Historical Provider — supplies past propagation data."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable

from .base import DataSourceProvider


@runtime_checkable
class HistoricalProvider(DataSourceProvider, Protocol):
    """Provides historical propagation records for calibration."""

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        """Return historical event records, or ``None`` for LLM fallback."""
        ...


class StubHistoricalProvider:
    """No-op stub — always returns ``None`` (triggers LLM fallback)."""

    @property
    def name(self) -> str:
        return "stub-historical"

    def is_available(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return False

    async def get_historical(self, **kwargs: Any) -> List[Dict[str, Any]] | None:
        return None
