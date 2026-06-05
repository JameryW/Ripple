"""Base Protocol for all DataSource Providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DataSourceProvider(Protocol):
    """Common contract for external data source providers.

    Each provider supplies one category of data to the simulation engine.
    When a provider is unavailable or returns ``None``, the engine falls
    back to LLM-generated data (or defaults).
    """

    @property
    def name(self) -> str:
        """Human-readable identifier for this provider."""
        ...

    def is_available(self) -> bool:
        """Return ``True`` if the provider can service requests right now."""
        ...

    async def health_check(self) -> bool:
        """Lightweight probe — used by the registry to validate config."""
        ...
