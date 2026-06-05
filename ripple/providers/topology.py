"""Topology Provider — supplies social-graph structure."""

from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

from .base import DataSourceProvider


class TopologyNode(Dict[str, Any]):
    """A single node in the social graph.

    Required keys: ``id`` (str), ``type`` (str — e.g. "star" / "sea").
    Additional keys are provider-specific (e.g. ``platform``, ``follower_count``).
    """


class TopologyEdge(Dict[str, Any]):
    """A directed, weighted edge in the social graph.

    Required keys: ``source`` (str), ``target`` (str), ``weight`` (float 0-1).
    """


class TopologyData(Dict[str, Any]):
    """Edge-list + node-metadata format — aligned with LLM INIT output.

    Keys: ``nodes`` (List[TopologyNode]), ``edges`` (List[TopologyEdge]).
    """


@runtime_checkable
class TopologyProvider(DataSourceProvider, Protocol):
    """Provides a social-graph topology for simulation INIT phase."""

    async def get_topology(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        constraints: Dict[str, Any] | None = None,
    ) -> TopologyData | None:
        """Return a topology dict, or ``None`` to let the engine fall back to LLM."""
        ...


class StubTopologyProvider:
    """No-op stub — always returns ``None`` (triggers LLM fallback)."""

    @property
    def name(self) -> str:
        return "stub-topology"

    def is_available(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return False

    async def get_topology(self, **kwargs: Any) -> TopologyData | None:
        return None
