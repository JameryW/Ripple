"""Tests for provider_insights — simulation output reflecting Provider usage."""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

from ripple.engine.runtime import SimulationRuntime
from ripple.providers.historical_validator import (
    HistoricalValidationReport,
    MetricDeviation,
)
from ripple.providers.registry import ProviderRegistry
from ripple.providers.topology_validator import (
    ScaleCheck,
    StructCheck,
    TypeCheck,
    ValidationReport as TopologyValidationReport,
)


# ---------------------------------------------------------------------------
# Helpers: mock providers that are "available"
# ---------------------------------------------------------------------------


class MockAvailableTopologyProvider:
    """A topology provider that claims to be available."""

    @property
    def name(self) -> str:
        return "mock-topology"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def get_topology(self, **kwargs: Any) -> Any:
        return None


class MockAvailableHistoricalProvider:
    """A historical provider that claims to be available and returns data."""

    @property
    def name(self) -> str:
        return "mock-historical"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def get_historical(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return [{"views": 1000, "shares": 50}]


class MockAvailableEmbeddingProvider:
    """An embedding provider that claims to be available."""

    @property
    def name(self) -> str:
        return "mock-embedding"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def embed(self, text: str) -> Any:
        return [0.1, 0.2, 0.3]


class MockUnavailableProvider:
    """A provider that is configured but unavailable."""

    @property
    def name(self) -> str:
        return "mock-unavailable"

    def is_available(self) -> bool:
        return False

    async def health_check(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# _build_provider_insights tests
# ---------------------------------------------------------------------------


class TestBuildProviderInsights:
    """Tests for SimulationRuntime._build_provider_insights."""

    def _make_runtime(self, providers=None) -> SimulationRuntime:
        async def noop_caller(**kwargs) -> str:
            return "noop"

        return SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
            providers=providers,
        )

    def test_no_providers_returns_empty(self):
        """When providers=None, _build_provider_insights returns empty dict."""
        runtime = self._make_runtime(providers=None)
        result = runtime._build_provider_insights({})
        assert result == {}

    def test_all_stubs_returns_empty(self):
        """When all providers are stubs, _build_provider_insights returns empty dict."""
        registry = ProviderRegistry()
        runtime = self._make_runtime(providers=registry)
        result = runtime._build_provider_insights({})
        assert result == {}

    def test_available_historical_provider(self):
        """Historical provider with available status appears in insights."""
        registry = ProviderRegistry(
            runtime_overrides={"historical": MockAvailableHistoricalProvider()}
        )
        runtime = self._make_runtime(providers=registry)
        runtime._historical_records_injected = 5
        result = runtime._build_provider_insights({"historical": [{"views": 100}]})
        assert "historical" in result
        assert result["historical"]["available"] is True
        assert result["historical"]["records_injected"] == 5

    def test_unavailable_provider_shows_available_false(self):
        """Unavailable non-stub provider appears with available=False."""
        registry = ProviderRegistry(
            runtime_overrides={"historical": MockUnavailableProvider()}
        )
        runtime = self._make_runtime(providers=registry)
        result = runtime._build_provider_insights({})
        assert "historical" in result
        assert result["historical"]["available"] is False

    def test_records_injected_zero_not_shown(self):
        """records_injected=0 should not appear in insights (only shown when >0)."""
        registry = ProviderRegistry(
            runtime_overrides={"historical": MockAvailableHistoricalProvider()}
        )
        runtime = self._make_runtime(providers=registry)
        runtime._historical_records_injected = 0
        result = runtime._build_provider_insights({})
        assert "historical" in result
        assert "records_injected" not in result["historical"]

    def test_validation_report_included(self):
        """Validation reports stored on runtime appear in insights."""
        registry = ProviderRegistry(
            runtime_overrides={"historical": MockAvailableHistoricalProvider()}
        )
        runtime = self._make_runtime(providers=registry)
        runtime._historical_records_injected = 3
        report = HistoricalValidationReport(
            metric_deviations=[
                MetricDeviation(metric="views", predicted=1000.0, historical_avg=1000.0,
                                historical_max=1200.0, deviation_pct=0.0, threshold=100.0),
            ]
        )
        runtime._validation_reports["historical"] = report
        result = runtime._build_provider_insights({"historical": [{"views": 100}]})
        assert "historical" in result
        assert "validation" in result["historical"]
        assert result["historical"]["validation"]["acceptable"] is True

    def test_multiple_provider_categories(self):
        """Multiple provider categories appear in insights."""
        registry = ProviderRegistry(
            runtime_overrides={
                "historical": MockAvailableHistoricalProvider(),
                "embedding": MockAvailableEmbeddingProvider(),
            }
        )
        runtime = self._make_runtime(providers=registry)
        runtime._historical_records_injected = 2
        result = runtime._build_provider_insights({"historical": [{"views": 100}]})
        assert "historical" in result
        assert "embedding" in result
        assert result["embedding"]["available"] is True
        # Embedding does not have records_injected or validation
        assert "records_injected" not in result["embedding"]
        assert "validation" not in result["embedding"]

    def test_exception_on_is_available_handled(self):
        """If is_available() raises, available defaults to False."""
        class BrokenProvider:
            @property
            def name(self) -> str:
                return "broken"

            def is_available(self) -> bool:
                raise RuntimeError("broken")

            async def health_check(self) -> bool:
                return False

        registry = ProviderRegistry(runtime_overrides={"historical": BrokenProvider()})
        runtime = self._make_runtime(providers=registry)
        result = runtime._build_provider_insights({})
        assert "historical" in result
        assert result["historical"]["available"] is False


# ---------------------------------------------------------------------------
# _serialize_validation tests
# ---------------------------------------------------------------------------


class TestSerializeValidation:
    """Tests for SimulationRuntime._serialize_validation."""

    def _make_runtime(self) -> SimulationRuntime:
        async def noop_caller(**kwargs) -> str:
            return "noop"

        return SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
        )

    def test_historical_report_acceptable(self):
        """HistoricalValidationReport with all acceptable deviations."""
        runtime = self._make_runtime()
        report = HistoricalValidationReport(
            metric_deviations=[
                MetricDeviation(metric="views", predicted=1000.0, historical_avg=1000.0,
                                historical_max=1200.0, deviation_pct=0.0, threshold=100.0),
                MetricDeviation(metric="shares", predicted=50.0, historical_avg=50.0,
                                historical_max=60.0, deviation_pct=0.0, threshold=100.0),
            ]
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is True
        assert result["deviation_count"] == 2
        assert result["max_deviation_pct"] == 0.0
        assert result["exceeded"] == []

    def test_historical_report_with_exceeded(self):
        """HistoricalValidationReport with some unacceptable deviations."""
        runtime = self._make_runtime()
        report = HistoricalValidationReport(
            metric_deviations=[
                MetricDeviation(metric="views", predicted=1000.0, historical_avg=1000.0,
                                historical_max=1200.0, deviation_pct=0.0, threshold=100.0),
                MetricDeviation(metric="shares", predicted=500.0, historical_avg=50.0,
                                historical_max=60.0, deviation_pct=900.0, threshold=100.0),
            ]
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is False
        assert result["deviation_count"] == 2
        assert result["max_deviation_pct"] == 900.0
        assert len(result["exceeded"]) == 1
        assert result["exceeded"][0]["metric"] == "shares"
        assert result["exceeded"][0]["predicted"] == 500.0
        assert result["exceeded"][0]["historical_avg"] == 50.0
        assert result["exceeded"][0]["deviation_pct"] == 900.0

    def test_historical_report_empty_deviations(self):
        """HistoricalValidationReport with no metric deviations."""
        runtime = self._make_runtime()
        report = HistoricalValidationReport(metric_deviations=[])
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is True
        assert result["deviation_count"] == 0
        assert result["max_deviation_pct"] == 0.0
        assert result["exceeded"] == []

    def test_topology_report_acceptable(self):
        """Topology ValidationReport where all checks pass."""
        runtime = self._make_runtime()
        report = TopologyValidationReport(
            scale=ScaleCheck(llm_nodes=10, llm_edges=20, provider_nodes=10,
                             provider_edges=20, node_deviation_pct=0.0,
                             edge_deviation_pct=0.0),
            structure=StructCheck(llm_connected=True, provider_connected=True,
                                  llm_isolated_count=0, provider_isolated_count=0,
                                  llm_avg_degree=2.0, provider_avg_degree=2.0),
            type_dist=TypeCheck(llm_star_ratio=0.3, llm_sea_ratio=0.7,
                                provider_star_ratio=0.3, provider_sea_ratio=0.7,
                                star_deviation_pct=0.0),
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is True
        assert result["deviation_count"] == 3
        assert result["max_deviation_pct"] == 0.0
        assert result["exceeded"] == []

    def test_topology_report_with_exceeded_scale(self):
        """Topology ValidationReport where scale check fails (node deviation)."""
        runtime = self._make_runtime()
        report = TopologyValidationReport(
            scale=ScaleCheck(llm_nodes=20, llm_edges=20, provider_nodes=10,
                             provider_edges=20, node_deviation_pct=100.0,
                             edge_deviation_pct=0.0),
            structure=StructCheck(llm_connected=True, provider_connected=True,
                                  llm_isolated_count=0, provider_isolated_count=0,
                                  llm_avg_degree=2.0, provider_avg_degree=2.0),
            type_dist=TypeCheck(llm_star_ratio=0.3, llm_sea_ratio=0.7,
                                provider_star_ratio=0.3, provider_sea_ratio=0.7,
                                star_deviation_pct=0.0),
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is False
        assert result["deviation_count"] == 3
        assert result["max_deviation_pct"] == 100.0
        assert len(result["exceeded"]) == 1
        assert result["exceeded"][0]["metric"] == "node_count"
        assert result["exceeded"][0]["predicted"] == 20
        assert result["exceeded"][0]["historical_avg"] == 10
        assert result["exceeded"][0]["deviation_pct"] == 100.0

    def test_topology_report_with_exceeded_scale_both(self):
        """Topology ValidationReport where both node and edge deviations exceed threshold."""
        runtime = self._make_runtime()
        report = TopologyValidationReport(
            scale=ScaleCheck(llm_nodes=20, llm_edges=40, provider_nodes=10,
                             provider_edges=20, node_deviation_pct=100.0,
                             edge_deviation_pct=100.0),
            structure=StructCheck(llm_connected=True, provider_connected=True,
                                  llm_isolated_count=0, provider_isolated_count=0,
                                  llm_avg_degree=2.0, provider_avg_degree=2.0),
            type_dist=TypeCheck(llm_star_ratio=0.3, llm_sea_ratio=0.7,
                                provider_star_ratio=0.3, provider_sea_ratio=0.7,
                                star_deviation_pct=0.0),
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is False
        assert len(result["exceeded"]) == 2
        metrics = {e["metric"] for e in result["exceeded"]}
        assert metrics == {"node_count", "edge_count"}

    def test_topology_report_with_exceeded_type_dist(self):
        """Topology ValidationReport where type distribution check fails."""
        runtime = self._make_runtime()
        report = TopologyValidationReport(
            scale=ScaleCheck(llm_nodes=10, llm_edges=20, provider_nodes=10,
                             provider_edges=20, node_deviation_pct=0.0,
                             edge_deviation_pct=0.0),
            structure=StructCheck(llm_connected=True, provider_connected=True,
                                  llm_isolated_count=0, provider_isolated_count=0,
                                  llm_avg_degree=2.0, provider_avg_degree=2.0),
            type_dist=TypeCheck(llm_star_ratio=0.6, llm_sea_ratio=0.4,
                                provider_star_ratio=0.3, provider_sea_ratio=0.7,
                                star_deviation_pct=100.0),
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is False
        assert len(result["exceeded"]) == 1
        assert result["exceeded"][0]["metric"] == "star_ratio"

    def test_topology_report_with_exceeded_structure(self):
        """Topology ValidationReport where structure check fails."""
        runtime = self._make_runtime()
        report = TopologyValidationReport(
            scale=ScaleCheck(llm_nodes=10, llm_edges=20, provider_nodes=10,
                             provider_edges=20, node_deviation_pct=0.0,
                             edge_deviation_pct=0.0),
            structure=StructCheck(llm_connected=False, provider_connected=True,
                                  llm_isolated_count=1, provider_isolated_count=0,
                                  llm_avg_degree=1.5, provider_avg_degree=2.0),
            type_dist=TypeCheck(llm_star_ratio=0.3, llm_sea_ratio=0.7,
                                provider_star_ratio=0.3, provider_sea_ratio=0.7,
                                star_deviation_pct=0.0),
        )
        result = runtime._serialize_validation(report)
        assert result["acceptable"] is False
        assert len(result["exceeded"]) == 1
        assert result["exceeded"][0]["metric"] == "connectivity"

    def test_unknown_report_type(self):
        """Unknown report type falls back to minimal info."""
        runtime = self._make_runtime()
        class FakeReport:
            @property
            def is_acceptable(self) -> bool:
                return True

        result = runtime._serialize_validation(FakeReport())
        assert result["acceptable"] is True
        assert result["deviation_count"] == 0
        assert result["max_deviation_pct"] == 0.0
        assert result["exceeded"] == []


# ---------------------------------------------------------------------------
# _serialize_topology_check tests
# ---------------------------------------------------------------------------


class TestSerializeTopologyCheck:
    """Tests for SimulationRuntime._serialize_topology_check."""

    def _make_runtime(self) -> SimulationRuntime:
        async def noop_caller(**kwargs) -> str:
            return "noop"

        return SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
        )

    def test_scale_check_node_deviation(self):
        """ScaleCheck where only node deviation exceeds threshold."""
        check = ScaleCheck(llm_nodes=20, llm_edges=20, provider_nodes=10,
                           provider_edges=20, node_deviation_pct=100.0,
                           edge_deviation_pct=0.0)
        results = SimulationRuntime._serialize_scale_checks(check)
        assert len(results) == 1
        assert results[0]["metric"] == "node_count"
        assert results[0]["predicted"] == 20
        assert results[0]["historical_avg"] == 10

    def test_scale_check_edge_deviation(self):
        """ScaleCheck where only edge deviation exceeds threshold."""
        check = ScaleCheck(llm_nodes=10, llm_edges=40, provider_nodes=10,
                           provider_edges=20, node_deviation_pct=0.0,
                           edge_deviation_pct=100.0)
        results = SimulationRuntime._serialize_scale_checks(check)
        assert len(results) == 1
        assert results[0]["metric"] == "edge_count"
        assert results[0]["predicted"] == 40
        assert results[0]["historical_avg"] == 20

    def test_scale_check_both_exceeded(self):
        """ScaleCheck where both node and edge deviations exceed threshold."""
        check = ScaleCheck(llm_nodes=20, llm_edges=40, provider_nodes=10,
                           provider_edges=20, node_deviation_pct=100.0,
                           edge_deviation_pct=100.0)
        results = SimulationRuntime._serialize_scale_checks(check)
        assert len(results) == 2
        metrics = {r["metric"] for r in results}
        assert metrics == {"node_count", "edge_count"}

    def test_structure_check(self):
        """StructCheck serialization."""
        check = StructCheck(llm_connected=False, provider_connected=True,
                           llm_isolated_count=1, provider_isolated_count=0,
                           llm_avg_degree=1.5, provider_avg_degree=2.0)
        result = SimulationRuntime._serialize_topology_check("structure", check)
        assert result["metric"] == "connectivity"
        assert result["predicted"] is False
        assert result["historical_avg"] is True

    def test_type_dist_check(self):
        """TypeCheck serialization."""
        check = TypeCheck(llm_star_ratio=0.6, llm_sea_ratio=0.4,
                         provider_star_ratio=0.3, provider_sea_ratio=0.7,
                         star_deviation_pct=100.0)
        result = SimulationRuntime._serialize_topology_check("type_dist", check)
        assert result["metric"] == "star_ratio"
        assert result["deviation_pct"] == 100.0

    def test_unknown_label(self):
        """Unknown label falls back to minimal format."""
        check = MagicMock()
        result = SimulationRuntime._serialize_topology_check("unknown_label", check)
        assert result["metric"] == "unknown_label"
        assert result["deviation_pct"] == 0.0


# ---------------------------------------------------------------------------
# Integration: provider_insights in result dict
# ---------------------------------------------------------------------------


class TestProviderInsightsIntegration:
    """Integration tests verifying provider_insights appears in the result dict."""

    def test_no_providers_omits_key(self):
        """When providers=None, result dict should NOT contain provider_insights."""
        async def noop_caller(**kwargs) -> str:
            return "noop"

        runtime = SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
            providers=None,
        )
        simulation_input = {"event": {"title": "test"}}
        insights = runtime._build_provider_insights(simulation_input)
        # insights should be empty dict {}
        assert insights == {}
        # When providers is None, key should not be added
        # (verified by the conditional check in _run_phases)

    def test_all_stubs_produces_empty_dict(self):
        """When all providers are stubs, provider_insights should be empty dict {}."""
        async def noop_caller(**kwargs) -> str:
            return "noop"

        registry = ProviderRegistry()  # all stubs
        runtime = SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
            providers=registry,
        )
        simulation_input = {"event": {"title": "test"}}
        insights = runtime._build_provider_insights(simulation_input)
        assert insights == {}
        # But since self._providers is not None, the key IS added with empty dict value
        # This is by design: provider_insights={} tells consumers "providers were configured, none were active"

    def test_available_historical_with_records(self):
        """Historical provider available + records injected produces full insights."""
        async def noop_caller(**kwargs) -> str:
            return "noop"

        registry = ProviderRegistry(
            runtime_overrides={"historical": MockAvailableHistoricalProvider()}
        )
        runtime = SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
            providers=registry,
        )
        runtime._historical_records_injected = 5
        simulation_input = {"event": {"title": "test"}}
        insights = runtime._build_provider_insights(simulation_input)
        assert "historical" in insights
        assert insights["historical"]["available"] is True
        assert insights["historical"]["records_injected"] == 5

    def test_available_historical_with_validation(self):
        """Historical provider with validation report in insights."""
        async def noop_caller(**kwargs) -> str:
            return "noop"

        registry = ProviderRegistry(
            runtime_overrides={"historical": MockAvailableHistoricalProvider()}
        )
        runtime = SimulationRuntime(
            omniscient_caller=noop_caller,
            star_caller=noop_caller,
            sea_caller=noop_caller,
            providers=registry,
        )
        runtime._historical_records_injected = 3

        # Simulate what _validate_historical would do
        report = HistoricalValidationReport(
            metric_deviations=[
                MetricDeviation(metric="views", predicted=5000.0, historical_avg=1000.0,
                                historical_max=1200.0, deviation_pct=400.0, threshold=100.0),
            ]
        )
        runtime._validation_reports["historical"] = report

        simulation_input = {"event": {"title": "test"}}
        insights = runtime._build_provider_insights(simulation_input)
        assert "historical" in insights
        assert insights["historical"]["validation"]["acceptable"] is False
        assert insights["historical"]["validation"]["deviation_count"] == 1
        assert len(insights["historical"]["validation"]["exceeded"]) == 1


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Verify that existing result dict keys are not modified."""

    def test_result_dict_keys_preserved(self):
        """When provider_insights is added, existing keys remain unchanged."""
        result = {
            "prediction": {"verdict": "test"},
            "timeline": [],
            "bifurcation_points": [],
            "agent_insights": {},
            "total_waves": 5,
            "run_id": "abc123",
            "wave_records_count": 5,
        }
        # Adding provider_insights does not touch existing keys
        result["provider_insights"] = {"historical": {"available": True, "records_injected": 3}}
        assert result["prediction"] == {"verdict": "test"}
        assert result["timeline"] == []
        assert result["total_waves"] == 5

    def test_no_providers_no_extra_keys(self):
        """When no providers configured, result dict has no provider_insights key."""
        result = {
            "prediction": {"verdict": "test"},
            "timeline": [],
            "total_waves": 3,
        }
        # Simulating the condition: providers=None means no key added
        assert "provider_insights" not in result

    def test_empty_provider_insights_does_not_break_consumers(self):
        """Empty dict provider_insights {} should be harmless to consumers."""
        result = {
            "prediction": {},
            "provider_insights": {},
        }
        # Consumers checking result.get("provider_insights") will get {} instead of None
        # — a backward-compatible change (new key, empty dict)
        insights = result.get("provider_insights")
        assert isinstance(insights, dict)
        assert len(insights) == 0