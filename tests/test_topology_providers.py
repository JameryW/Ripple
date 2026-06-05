"""Tests for TopologyProvider concrete implementations and validator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ripple.providers.topology import StubTopologyProvider, TopologyData
from ripple.providers.topology_loaders import (
    FileTopologyProvider,
    SyntheticTopologyProvider,
    _HAS_NETWORKX,
)
from ripple.providers.topology_validator import (
    ScaleCheck,
    StructCheck,
    TopologyValidator,
    TypeCheck,
    ValidationReport,
)

pytestmark = pytest.mark.skipif(not _HAS_NETWORKX, reason="networkx not installed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snap_edge_list(tmp_path: Path) -> Path:
    """Create a minimal SNAP edge list file."""
    p = tmp_path / "test_graph.txt"
    p.write_text(
        "# Test graph\n"
        "# Nodes: 5 Edges: 6\n"
        "0 1\n"
        "0 2\n"
        "1 2\n"
        "1 3\n"
        "2 4\n"
        "3 4\n"
    )
    return p


@pytest.fixture
def json_topology_file(tmp_path: Path) -> Path:
    """Create a JSON topology file in Ripple format."""
    data = {
        "nodes": [
            {"id": "a", "type": "star"},
            {"id": "b", "type": "sea"},
            {"id": "c", "type": "sea"},
        ],
        "edges": [
            {"source": "a", "target": "b", "weight": 0.8},
            {"source": "a", "target": "c", "weight": 0.5},
            {"source": "b", "target": "c", "weight": 0.3},
        ],
    }
    p = tmp_path / "test_graph.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def csv_edge_list(tmp_path: Path) -> Path:
    """Create a CSV edge list file."""
    p = tmp_path / "test_graph.csv"
    p.write_text("source,target,weight\na,b,0.8\nb,c,0.5\nc,a,0.3\n")
    return p


@pytest.fixture
def sample_topology() -> TopologyData:
    return {
        "nodes": [
            {"id": "a", "type": "star"},
            {"id": "b", "type": "sea"},
            {"id": "c", "type": "sea"},
            {"id": "d", "type": "sea"},
        ],
        "edges": [
            {"source": "a", "target": "b", "weight": 0.8},
            {"source": "a", "target": "c", "weight": 0.5},
            {"source": "b", "target": "d", "weight": 0.3},
        ],
    }


# ---------------------------------------------------------------------------
# FileTopologyProvider
# ---------------------------------------------------------------------------


class TestFileTopologyProvider:
    @pytest.mark.asyncio
    async def test_load_snap(self, snap_edge_list: Path):
        provider = FileTopologyProvider(snap_edge_list, format="snap")
        assert provider.is_available()
        result = await provider.get_topology()
        assert result is not None
        assert "nodes" in result and "edges" in result
        assert len(result["nodes"]) == 5
        assert len(result["edges"]) == 6

    @pytest.mark.asyncio
    async def test_load_snap_auto_format(self, snap_edge_list: Path):
        provider = FileTopologyProvider(snap_edge_list, format="auto")
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 5

    @pytest.mark.asyncio
    async def test_load_json(self, json_topology_file: Path):
        provider = FileTopologyProvider(json_topology_file, format="json")
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 3

    @pytest.mark.asyncio
    async def test_load_csv(self, csv_edge_list: Path):
        provider = FileTopologyProvider(csv_edge_list, format="csv")
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 3

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        provider = FileTopologyProvider("/nonexistent/file.txt")
        assert not provider.is_available()
        result = await provider.get_topology()
        assert result is None

    @pytest.mark.asyncio
    async def test_caching(self, snap_edge_list: Path):
        provider = FileTopologyProvider(snap_edge_list, format="snap")
        result1 = await provider.get_topology()
        result2 = await provider.get_topology()
        assert result1 is result2  # Same object (cached)

    @pytest.mark.asyncio
    async def test_node_type_map(self, snap_edge_list: Path):
        provider = FileTopologyProvider(
            snap_edge_list,
            format="snap",
            node_type_map={"agent_0": "star"},
        )
        result = await provider.get_topology()
        assert result is not None
        node_0 = next(n for n in result["nodes"] if n["id"] == "agent_0")
        assert node_0["type"] == "star"

    @pytest.mark.asyncio
    async def test_default_type(self, snap_edge_list: Path):
        provider = FileTopologyProvider(
            snap_edge_list, format="snap", default_type="sea"
        )
        result = await provider.get_topology()
        assert result is not None
        for node in result["nodes"]:
            assert node["type"] == "sea"

    def test_name(self, snap_edge_list: Path):
        provider = FileTopologyProvider(snap_edge_list)
        assert "file-topology" in provider.name

    @pytest.mark.asyncio
    async def test_health_check(self, snap_edge_list: Path):
        provider = FileTopologyProvider(snap_edge_list)
        assert await provider.health_check()

    @pytest.mark.asyncio
    async def test_graphml(self, tmp_path: Path):
        import networkx as nx

        G = nx.DiGraph()
        G.add_edges_from([("a", "b"), ("b", "c")])
        p = tmp_path / "test.graphml"
        nx.write_graphml(G, str(p))

        provider = FileTopologyProvider(p, format="graphml")
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 3

    @pytest.mark.asyncio
    async def test_gml(self, tmp_path: Path):
        import networkx as nx

        G = nx.DiGraph()
        G.add_edges_from([("a", "b"), ("b", "c")])
        p = tmp_path / "test.gml"
        nx.write_gml(G, str(p))

        provider = FileTopologyProvider(p, format="gml")
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 3


# ---------------------------------------------------------------------------
# SyntheticTopologyProvider
# ---------------------------------------------------------------------------


class TestSyntheticTopologyProvider:
    @pytest.mark.asyncio
    async def test_ba_model(self):
        provider = SyntheticTopologyProvider(model="ba", n=20, m=2, seed=42)
        assert provider.is_available()
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 20

    @pytest.mark.asyncio
    async def test_ws_model(self):
        provider = SyntheticTopologyProvider(model="ws", n=20, k=4, p=0.3, seed=42)
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 20

    @pytest.mark.asyncio
    async def test_sbm_model(self):
        provider = SyntheticTopologyProvider(
            model="sbm",
            n=30,
            sizes=[15, 15],
            p=[[0.3, 0.02], [0.02, 0.3]],
            seed=42,
        )
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 30

    @pytest.mark.asyncio
    async def test_er_model(self):
        provider = SyntheticTopologyProvider(model="er", n=20, p=0.2, seed=42)
        result = await provider.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 20

    @pytest.mark.asyncio
    async def test_caching(self):
        provider = SyntheticTopologyProvider(model="ba", n=10, seed=42)
        result1 = await provider.get_topology()
        result2 = await provider.get_topology()
        assert result1 is result2

    @pytest.mark.asyncio
    async def test_node_types_assigned(self):
        provider = SyntheticTopologyProvider(model="ba", n=20, m=2, seed=42)
        result = await provider.get_topology()
        assert result is not None
        types = {n["type"] for n in result["nodes"]}
        assert "star" in types or "sea" in types

    @pytest.mark.asyncio
    async def test_edge_weights_present(self):
        provider = SyntheticTopologyProvider(model="ba", n=10, m=2, seed=42)
        result = await provider.get_topology()
        assert result is not None
        for edge in result["edges"]:
            assert "weight" in edge

    def test_invalid_model(self):
        with pytest.raises(ValueError, match="Unknown model"):
            SyntheticTopologyProvider(model="invalid")

    def test_name(self):
        provider = SyntheticTopologyProvider(model="ba", n=50)
        assert "synthetic-topology" in provider.name
        assert "ba" in provider.name

    @pytest.mark.asyncio
    async def test_health_check(self):
        provider = SyntheticTopologyProvider(model="ba", n=10)
        assert await provider.health_check()


# ---------------------------------------------------------------------------
# TopologyValidator
# ---------------------------------------------------------------------------


class TestTopologyValidator:
    def test_scale_check_identical(self, sample_topology: TopologyData):
        validator = TopologyValidator()
        report = validator.validate(sample_topology, sample_topology)
        assert report.scale.node_deviation_pct == 0.0
        assert report.scale.edge_deviation_pct == 0.0
        assert report.scale.is_acceptable

    def test_scale_check_deviation(self, sample_topology: TopologyData):
        # Provider has more nodes/edges
        provider_data = {
            "nodes": [{"id": f"n{i}", "type": "sea"} for i in range(10)],
            "edges": [{"source": "n0", "target": "n1", "weight": 1.0}],
        }
        validator = TopologyValidator()
        report = validator.validate(sample_topology, provider_data)
        assert report.scale.llm_nodes == 4
        assert report.scale.provider_nodes == 10
        assert report.scale.node_deviation_pct < 0  # LLM has fewer

    def test_structure_check_connected(self, sample_topology: TopologyData):
        validator = TopologyValidator()
        report = validator.validate(sample_topology, sample_topology)
        assert report.structure.llm_isolated_count >= 0

    def test_type_dist_check(self, sample_topology: TopologyData):
        validator = TopologyValidator()
        report = validator.validate(sample_topology, sample_topology)
        assert report.type_dist.llm_star_ratio == 0.25  # 1/4
        assert report.type_dist.star_deviation_pct == 0.0

    def test_type_dist_mismatch(self, sample_topology: TopologyData):
        # Provider has all star nodes
        provider_data = {
            "nodes": [{"id": "a", "type": "star"}, {"id": "b", "type": "star"}],
            "edges": [{"source": "a", "target": "b", "weight": 1.0}],
        }
        validator = TopologyValidator()
        report = validator.validate(sample_topology, provider_data)
        assert report.type_dist.provider_star_ratio == 1.0
        assert report.type_dist.llm_star_ratio < 1.0

    def test_report_is_acceptable(self, sample_topology: TopologyData):
        validator = TopologyValidator()
        report = validator.validate(sample_topology, sample_topology)
        assert report.is_acceptable

    def test_report_log(self, sample_topology: TopologyData):
        """Ensure log() doesn't raise."""
        validator = TopologyValidator()
        report = validator.validate(sample_topology, sample_topology)
        report.log()  # Should not raise

    def test_auto_correct_not_implemented(self):
        with pytest.raises(NotImplementedError):
            TopologyValidator(auto_correct=True)

    def test_empty_topology(self):
        empty: TopologyData = {"nodes": [], "edges": []}
        validator = TopologyValidator()
        report = validator.validate(empty, empty)
        assert report.scale.llm_nodes == 0
        assert report.scale.provider_nodes == 0

    def test_scale_check_infinite_deviation(self):
        """When provider has 0 nodes, deviation is inf."""
        llm: TopologyData = {"nodes": [{"id": "a", "type": "star"}], "edges": []}
        empty: TopologyData = {"nodes": [], "edges": []}
        validator = TopologyValidator()
        report = validator.validate(llm, empty)
        assert report.scale.node_deviation_pct == float("inf")


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestTopologyRegistryIntegration:
    def test_lazy_import_resolves(self):
        from ripple.providers.registry import (
            _PROVIDER_IMPLEMENTATIONS,
            _ensure_lazy_imports,
        )

        _ensure_lazy_imports("topology")
        assert "file" in _PROVIDER_IMPLEMENTATIONS["topology"]
        assert "synthetic" in _PROVIDER_IMPLEMENTATIONS["topology"]

    def test_yaml_config_file_provider(self, snap_edge_list: Path):
        from ripple.providers.registry import ProviderRegistry

        cfg = {
            "topology": {
                "impl": "file",
                "path": str(snap_edge_list),
                "format": "snap",
            }
        }
        registry = ProviderRegistry(yaml_providers_cfg=cfg)
        topo = registry.topology
        assert not isinstance(topo, StubTopologyProvider)

    def test_yaml_config_synthetic_provider(self):
        from ripple.providers.registry import ProviderRegistry

        cfg = {
            "topology": {
                "impl": "synthetic",
                "model": "ba",
                "n": 20,
                "m": 2,
                "seed": 42,
            }
        }
        registry = ProviderRegistry(yaml_providers_cfg=cfg)
        topo = registry.topology
        assert not isinstance(topo, StubTopologyProvider)

    @pytest.mark.asyncio
    async def test_yaml_synthetic_get_topology(self):
        from ripple.providers.registry import ProviderRegistry

        cfg = {
            "topology": {
                "impl": "synthetic",
                "model": "ba",
                "n": 10,
                "m": 2,
                "seed": 42,
            }
        }
        registry = ProviderRegistry(yaml_providers_cfg=cfg)
        result = await registry.topology.get_topology()
        assert result is not None
        assert len(result["nodes"]) == 10
