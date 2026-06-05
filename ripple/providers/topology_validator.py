"""Post-hoc validation of LLM-generated topology against provider data.

Compares LLM INIT-phase topology output with real/synthetic provider data.
Logs deviations as info/warning — never modifies the LLM output.
Designed with ``auto_correct=False`` extension point for future use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .topology import TopologyData

logger = logging.getLogger(__name__)


@dataclass
class ScaleCheck:
    """Node and edge count deviation between LLM and provider topologies."""

    llm_nodes: int
    llm_edges: int
    provider_nodes: int
    provider_edges: int
    node_deviation_pct: float  # (llm - provider) / provider * 100
    edge_deviation_pct: float
    threshold: float = 50.0

    @property
    def is_acceptable(self) -> bool:
        return abs(self.node_deviation_pct) <= self.threshold and abs(self.edge_deviation_pct) <= self.threshold


@dataclass
class StructCheck:
    """Structural comparison: connectivity, isolated nodes, degree distribution."""

    llm_connected: bool
    provider_connected: bool
    llm_isolated_count: int
    provider_isolated_count: int
    llm_avg_degree: float
    provider_avg_degree: float
    degree_ks_stat: Optional[float] = None  # Kolmogorov-Smirnov if scipy available

    @property
    def is_acceptable(self) -> bool:
        if self.llm_connected and not self.provider_connected:
            return True  # LLM more connected is fine
        if not self.llm_connected and self.provider_connected:
            return False  # LLM disconnected when provider is connected
        return True


@dataclass
class TypeCheck:
    """Node type distribution comparison (star vs sea ratio)."""

    llm_star_ratio: float
    llm_sea_ratio: float
    provider_star_ratio: float
    provider_sea_ratio: float
    star_deviation_pct: float
    threshold: float = 30.0

    @property
    def is_acceptable(self) -> bool:
        return abs(self.star_deviation_pct) <= self.threshold


@dataclass
class ValidationReport:
    """Aggregated validation result."""

    scale: ScaleCheck
    structure: StructCheck
    type_dist: TypeCheck
    warnings: List[str] = field(default_factory=list)

    @property
    def is_acceptable(self) -> bool:
        return self.scale.is_acceptable and self.structure.is_acceptable and self.type_dist.is_acceptable

    def log(self) -> None:
        """Log the validation report."""
        logger.info(
            "Topology validation — nodes: %d vs %d (%+.1f%%), edges: %d vs %d (%+.1f%%)",
            self.scale.llm_nodes,
            self.scale.provider_nodes,
            self.scale.node_deviation_pct,
            self.scale.llm_edges,
            self.scale.provider_edges,
            self.scale.edge_deviation_pct,
        )
        logger.info(
            "Topology validation — connected: %s vs %s, isolated: %d vs %d, avg_degree: %.2f vs %.2f",
            self.structure.llm_connected,
            self.structure.provider_connected,
            self.structure.llm_isolated_count,
            self.structure.provider_isolated_count,
            self.structure.llm_avg_degree,
            self.structure.provider_avg_degree,
        )
        logger.info(
            "Topology validation — star ratio: %.2f vs %.2f (%+.1f%%)",
            self.type_dist.llm_star_ratio,
            self.type_dist.provider_star_ratio,
            self.type_dist.star_deviation_pct,
        )

        if not self.scale.is_acceptable:
            logger.warning(
                "Topology scale deviation exceeds threshold: nodes %+.1f%%, edges %+.1f%%",
                self.scale.node_deviation_pct,
                self.scale.edge_deviation_pct,
            )
        if not self.structure.is_acceptable:
            logger.warning("Topology structural mismatch: LLM disconnected when provider is connected")
        if not self.type_dist.is_acceptable:
            logger.warning(
                "Topology type distribution deviation: star ratio %+.1f%%",
                self.type_dist.star_deviation_pct,
            )
        for w in self.warnings:
            logger.warning("Topology validation: %s", w)


class TopologyValidator:
    """Post-hoc validation of LLM-generated topology against provider data.

    Parameters
    ----------
    scale_threshold : float
        Maximum acceptable deviation (%) for node/edge counts. Default 50%.
    type_threshold : float
        Maximum acceptable deviation (%) for star/sea ratio. Default 30%.
    auto_correct : bool
        Reserved for future use — currently always ``False``.
    """

    def __init__(
        self,
        scale_threshold: float = 50.0,
        type_threshold: float = 30.0,
        auto_correct: bool = False,
    ) -> None:
        self._scale_threshold = scale_threshold
        self._type_threshold = type_threshold
        self._auto_correct = auto_correct
        if auto_correct:
            raise NotImplementedError("auto_correct is reserved for future use")

    def validate(
        self,
        llm_topology: TopologyData,
        provider_topology: TopologyData,
    ) -> ValidationReport:
        """Compare LLM topology with provider data and return a report."""
        warnings: List[str] = []

        scale = self._check_scale(llm_topology, provider_topology)
        structure = self._check_structure(llm_topology, provider_topology, warnings)
        type_dist = self._check_type_dist(llm_topology, provider_topology, warnings)

        return ValidationReport(
            scale=scale,
            structure=structure,
            type_dist=type_dist,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Scale
    # ------------------------------------------------------------------

    def _check_scale(self, llm: TopologyData, provider: TopologyData) -> ScaleCheck:
        llm_n = len(llm.get("nodes", []))
        llm_e = len(llm.get("edges", []))
        prov_n = len(provider.get("nodes", []))
        prov_e = len(provider.get("edges", []))

        node_dev = ((llm_n - prov_n) / prov_n * 100) if prov_n else (0.0 if llm_n == 0 else float("inf"))
        edge_dev = ((llm_e - prov_e) / prov_e * 100) if prov_e else (0.0 if llm_e == 0 else float("inf"))

        return ScaleCheck(
            llm_nodes=llm_n,
            llm_edges=llm_e,
            provider_nodes=prov_n,
            provider_edges=prov_e,
            node_deviation_pct=round(node_dev, 2),
            edge_deviation_pct=round(edge_dev, 2),
            threshold=self._scale_threshold,
        )

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------

    @staticmethod
    def _check_structure(
        llm: TopologyData,
        provider: TopologyData,
        warnings: List[str],
    ) -> StructCheck:
        def _adjacency(topo: TopologyData) -> Dict[str, set]:
            adj: Dict[str, set] = {}
            for node in topo.get("nodes", []):
                nid = node.get("id", "")
                adj.setdefault(nid, set())
            for edge in topo.get("edges", []):
                src = edge.get("source", "")
                tgt = edge.get("target", "")
                if src in adj:
                    adj[src].add(tgt)
                if tgt not in adj:
                    adj[tgt] = set()
            return adj

        def _is_connected(adj: Dict[str, set]) -> bool:
            if not adj:
                return True
            start = next(iter(adj))
            visited = {start}
            stack = [start]
            while stack:
                node = stack.pop()
                for nb in adj.get(node, set()):
                    if nb not in visited:
                        visited.add(nb)
                        stack.append(nb)
                # Check reverse edges
                for other, nbs in adj.items():
                    if node in nbs and other not in visited:
                        visited.add(other)
                        stack.append(other)
            return len(visited) == len(adj)

        def _isolated_count(adj: Dict[str, set]) -> int:
            has_incoming: set = set()
            for nbs in adj.values():
                has_incoming.update(nbs)
            count = 0
            for node in adj:
                if not adj[node] and node not in has_incoming:
                    count += 1
            return count

        def _avg_degree(adj: Dict[str, set]) -> float:
            if not adj:
                return 0.0
            total = sum(len(nbs) for nbs in adj.values())
            return total / len(adj)

        llm_adj = _adjacency(llm)
        prov_adj = _adjacency(provider)

        return StructCheck(
            llm_connected=_is_connected(llm_adj),
            provider_connected=_is_connected(prov_adj),
            llm_isolated_count=_isolated_count(llm_adj),
            provider_isolated_count=_isolated_count(prov_adj),
            llm_avg_degree=round(_avg_degree(llm_adj), 2),
            provider_avg_degree=round(_avg_degree(prov_adj), 2),
        )

    # ------------------------------------------------------------------
    # Type distribution
    # ------------------------------------------------------------------

    def _check_type_dist(
        self,
        llm: TopologyData,
        provider: TopologyData,
        warnings: List[str],
    ) -> TypeCheck:
        def _ratios(topo: TopologyData) -> tuple[float, float]:
            nodes = topo.get("nodes", [])
            total = len(nodes)
            if total == 0:
                return 0.0, 0.0
            star = sum(1 for n in nodes if n.get("type") == "star")
            sea = total - star
            return star / total, sea / total

        llm_star, llm_sea = _ratios(llm)
        prov_star, prov_sea = _ratios(provider)
        if prov_star == 0:
            # Both have 0 star ratio -> no deviation; LLM has stars but provider
            # doesn't -> infinite deviation
            star_dev = 0.0 if llm_star == 0 else float("inf")
        else:
            star_dev = ((llm_star - prov_star) / prov_star * 100)

        return TypeCheck(
            llm_star_ratio=round(llm_star, 4),
            llm_sea_ratio=round(llm_sea, 4),
            provider_star_ratio=round(prov_star, 4),
            provider_sea_ratio=round(prov_sea, 4),
            star_deviation_pct=round(star_dev, 2),
            threshold=self._type_threshold,
        )
