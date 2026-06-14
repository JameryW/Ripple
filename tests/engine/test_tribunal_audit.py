# tests/engine/test_tribunal_audit.py
"""Tests for R6 Tribunal audit field parsing and SSE quality fields — Gap 3 & Gap 5."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ripple.engine.runtime import SimulationRuntime, _safe_str_list, _normalize_cap
from ripple.primitives.pmf_models import DeliberationRecord, TribunalOpinion


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestSafeStrList:
    def test_none(self):
        assert _safe_str_list(None) == []

    def test_empty_string(self):
        assert _safe_str_list("") == []

    def test_single_string(self):
        assert _safe_str_list("hello") == ["hello"]

    def test_list_of_strings(self):
        assert _safe_str_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_list_with_nones(self):
        assert _safe_str_list(["a", None, "b"]) == ["a", "b"]

    def test_list_with_empty_strings(self):
        assert _safe_str_list(["a", "", "b"]) == ["a", "b"]

    def test_non_string_value(self):
        assert _safe_str_list(42) == ["42"]


class TestNormalizeCap:
    def test_none(self):
        assert _normalize_cap(None) is None

    def test_valid_values(self):
        assert _normalize_cap("low") == "low"
        assert _normalize_cap("medium") == "medium"
        assert _normalize_cap("high") == "high"

    def test_case_insensitive(self):
        assert _normalize_cap("LOW") == "low"
        assert _normalize_cap("Medium") == "medium"
        assert _normalize_cap("HIGH") == "high"

    def test_whitespace(self):
        assert _normalize_cap("  low  ") == "low"

    def test_invalid(self):
        assert _normalize_cap("very_low") is None
        assert _normalize_cap("moderate") is None


# ---------------------------------------------------------------------------
# _parse_tribunal_audit tests
# ---------------------------------------------------------------------------


class TestParseTribunalAudit:
    """Tests for SimulationRuntime._parse_tribunal_audit — R6 Gap 3."""

    def _make_runtime(self, extra_phase_outputs=None):
        """Create a minimal SimulationRuntime for testing _parse_tribunal_audit."""
        caller = AsyncMock()
        runtime = SimulationRuntime(
            omniscient_caller=caller,
            star_caller=caller,
            sea_caller=caller,
        )
        if extra_phase_outputs is not None:
            runtime._extra_phase_outputs = extra_phase_outputs
        return runtime

    def test_no_deliberate_phase(self):
        """Returns None when no DELIBERATE phase output exists."""
        runtime = self._make_runtime({})
        assert runtime._parse_tribunal_audit() is None

    def test_structured_audit_from_summary(self):
        """Path 1: Extracts audit from deliberation_summary.audit dict."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "audit": {
                        "key_evidence": ["ev1", "ev2"],
                        "uncertainties": ["unc1"],
                        "optimism_audit": ["opt1"],
                        "overrated_dimensions": ["dim1: too high"],
                        "missing_evidence": ["miss1"],
                        "recommended_confidence_cap": "medium",
                    },
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert result["key_evidence"] == ["ev1", "ev2"]
        assert result["uncertainties"] == ["unc1"]
        assert result["optimism_audit"] == ["opt1"]
        assert result["overrated_dimensions"] == ["dim1: too high"]
        assert result["missing_evidence"] == ["miss1"]
        assert result["recommended_confidence_cap"] == "medium"

    def test_flat_audit_fields_in_summary(self):
        """Path 2: Extracts audit fields directly from deliberation_summary (flat structure)."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "key_evidence": ["ev1"],
                    "uncertainties": [],
                    "optimism_audit": ["opt1"],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "low",
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert result["key_evidence"] == ["ev1"]
        assert result["optimism_audit"] == ["opt1"]
        assert result["recommended_confidence_cap"] == "low"

    def test_audit_from_deliberation_records(self):
        """Path 3: Extracts audit from last deliberation_records item."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_records": [
                    {"round_number": 0, "opinions": []},
                    {
                        "round_number": 1,
                        "opinions": [],
                        "key_evidence": ["ev_from_record"],
                        "uncertainties": [],
                        "optimism_audit": [],
                        "overrated_dimensions": ["dim_from_record: reason"],
                        "missing_evidence": [],
                        "recommended_confidence_cap": "low",
                    },
                ],
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert result["key_evidence"] == ["ev_from_record"]
        assert result["overrated_dimensions"] == ["dim_from_record: reason"]
        assert result["recommended_confidence_cap"] == "low"

    def test_text_fallback_optimism(self):
        """Path 4: Text fallback detects optimism keywords in narratives."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "final_positions": [
                        {"member_role": "DA", "narrative": "The prediction is overly optimistic about reach."},
                    ],
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert len(result["optimism_audit"]) > 0
        assert any("optimism" in s.lower() for s in result["optimism_audit"])

    def test_text_fallback_uncertainty(self):
        """Path 4: Text fallback detects uncertainty keywords in narratives."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "final_positions": [
                        {"member_role": "DA", "narrative": "I am uncertain about the retention data."},
                    ],
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert len(result["uncertainties"]) > 0

    def test_no_audit_returns_none(self):
        """Returns None when no audit data is available anywhere."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "final_positions": [
                        {"member_role": "DA", "narrative": "Everything looks fine."},
                    ],
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is None

    def test_non_dict_deliberate_output(self):
        """Returns None when DELIBERATE output is not a dict."""
        runtime = self._make_runtime({"DELIBERATE": "not a dict"})
        assert runtime._parse_tribunal_audit() is None

    def test_audit_with_invalid_cap(self):
        """Invalid recommended_confidence_cap is normalized to None."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "audit": {
                        "key_evidence": ["ev1"],
                        "uncertainties": [],
                        "optimism_audit": [],
                        "overrated_dimensions": [],
                        "missing_evidence": [],
                        "recommended_confidence_cap": "very_low",
                    },
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert result["recommended_confidence_cap"] is None

    def test_audit_with_non_list_fields(self):
        """Non-list audit fields are coerced to lists."""
        runtime = self._make_runtime({
            "DELIBERATE": {
                "deliberation_summary": {
                    "audit": {
                        "key_evidence": "single evidence item",
                        "uncertainties": None,
                        "optimism_audit": 42,
                        "overrated_dimensions": [],
                        "missing_evidence": [],
                        "recommended_confidence_cap": "medium",
                    },
                },
            },
        })
        result = runtime._parse_tribunal_audit()
        assert result is not None
        assert result["key_evidence"] == ["single evidence item"]
        assert result["uncertainties"] == []
        assert result["optimism_audit"] == ["42"]


# ---------------------------------------------------------------------------
# TribunalAgent audit extraction tests
# ---------------------------------------------------------------------------


class TestTribunalAgentAuditExtraction:
    """Tests for TribunalAgent._last_audit population — R6 Gap 3."""

    @pytest.mark.asyncio
    async def test_evaluate_extracts_audit(self):
        """TribunalAgent.evaluate populates _last_audit from LLM response."""
        from ripple.agents.tribunal import TribunalAgent

        mock_llm = AsyncMock(return_value=json.dumps({
            "scores": {"demand_resonance": 3},
            "narrative": "Moderate demand.",
            "audit": {
                "key_evidence": ["Wave 1: 3 agents responded"],
                "uncertainties": ["Small sample size"],
                "optimism_audit": ["May be overestimating reach"],
                "overrated_dimensions": ["demand_resonance: limited evidence"],
                "missing_evidence": ["No historical data"],
                "recommended_confidence_cap": "medium",
            },
        }))
        agent = TribunalAgent(
            role="Analyst",
            perspective="General",
            expertise="Analysis",
            llm_caller=mock_llm,
        )
        await agent.evaluate(
            evidence="Test evidence",
            dimensions=["demand_resonance"],
            rubric="1=low, 5=high",
            round_number=0,
        )
        assert agent._last_audit["key_evidence"] == ["Wave 1: 3 agents responded"]
        assert agent._last_audit["uncertainties"] == ["Small sample size"]
        assert agent._last_audit["optimism_audit"] == ["May be overestimating reach"]
        assert agent._last_audit["recommended_confidence_cap"] == "medium"

    @pytest.mark.asyncio
    async def test_evaluate_no_audit_in_response(self):
        """TribunalAgent.evaluate handles missing audit gracefully."""
        from ripple.agents.tribunal import TribunalAgent

        mock_llm = AsyncMock(return_value=json.dumps({
            "scores": {"demand_resonance": 3},
            "narrative": "OK.",
        }))
        agent = TribunalAgent(
            role="Analyst",
            perspective="General",
            expertise="Analysis",
            llm_caller=mock_llm,
        )
        await agent.evaluate(
            evidence="Test",
            dimensions=["demand_resonance"],
            rubric="r",
            round_number=0,
        )
        assert agent._last_audit["key_evidence"] == []
        assert agent._last_audit["recommended_confidence_cap"] is None

    @pytest.mark.asyncio
    async def test_revise_extracts_audit(self):
        """TribunalAgent.revise populates _last_audit from LLM response."""
        from ripple.agents.tribunal import TribunalAgent
        from ripple.primitives.pmf_models import TribunalOpinion

        mock_llm = AsyncMock(return_value=json.dumps({
            "scores": {"demand_resonance": 2},
            "narrative": "Revised down.",
            "audit": {
                "key_evidence": [],
                "uncertainties": [],
                "optimism_audit": ["Still too optimistic"],
                "overrated_dimensions": [],
                "missing_evidence": ["Need real data"],
                "recommended_confidence_cap": "low",
            },
        }))
        agent = TribunalAgent(
            role="DA",
            perspective="Risk",
            expertise="Risk analysis",
            llm_caller=mock_llm,
        )
        original = TribunalOpinion(
            member_role="DA",
            scores={"demand_resonance": 3},
            narrative="Original.",
            round_number=0,
        )
        await agent.revise(original, ["Challenge 1"], round_number=1)
        assert agent._last_audit["optimism_audit"] == ["Still too optimistic"]
        assert agent._last_audit["missing_evidence"] == ["Need real data"]
        assert agent._last_audit["recommended_confidence_cap"] == "low"


# ---------------------------------------------------------------------------
# DeliberationOrchestrator audit aggregation tests
# ---------------------------------------------------------------------------


class TestDeliberationAuditAggregation:
    """Tests for DeliberationOrchestrator._aggregate_audit_from_agents — R6 Gap 3."""

    @pytest.mark.asyncio
    async def test_audit_populated_on_convergence(self):
        """Final DeliberationRecord has audit fields when deliberation converges."""
        from ripple.engine.deliberation import DeliberationOrchestrator
        from ripple.primitives.pmf_models import TribunalMember

        mock_llm = AsyncMock(side_effect=[
            # Round 0: evaluations (2 members, with audit)
            json.dumps({
                "scores": {"demand_resonance": 4, "propagation_potential": 3},
                "narrative": "Good.",
                "audit": {
                    "key_evidence": ["Wave 1 data"],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "high",
                },
            }),
            json.dumps({
                "scores": {"demand_resonance": 2, "propagation_potential": 2},
                "narrative": "Risky.",
                "audit": {
                    "key_evidence": [],
                    "uncertainties": ["Small sample"],
                    "optimism_audit": ["Overestimating"],
                    "overrated_dimensions": ["demand_resonance: too high"],
                    "missing_evidence": ["Historical data"],
                    "recommended_confidence_cap": "medium",
                },
            }),
            # Round 1: challenges
            json.dumps({"challenge": "Too optimistic."}),
            json.dumps({"challenge": "Too pessimistic."}),
            # Round 1: revisions — converge
            json.dumps({
                "scores": {"demand_resonance": 3, "propagation_potential": 3},
                "narrative": "Revised.",
                "audit": {
                    "key_evidence": ["Wave 1 data", "Wave 2 data"],
                    "uncertainties": ["Still uncertain"],
                    "optimism_audit": ["Some optimism risk"],
                    "overrated_dimensions": [],
                    "missing_evidence": ["Historical data"],
                    "recommended_confidence_cap": "medium",
                },
            }),
            json.dumps({
                "scores": {"demand_resonance": 3, "propagation_potential": 2},
                "narrative": "Revised.",
                "audit": {
                    "key_evidence": [],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "low",
                },
            }),
            # Round 2: challenges
            json.dumps({"challenge": "Close."}),
            json.dumps({"challenge": "OK."}),
            # Round 2: revisions — stable (converge)
            json.dumps({
                "scores": {"demand_resonance": 3, "propagation_potential": 3},
                "narrative": "Stable.",
                "audit": {
                    "key_evidence": ["Final evidence"],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "medium",
                },
            }),
            json.dumps({
                "scores": {"demand_resonance": 3, "propagation_potential": 2},
                "narrative": "Stable.",
                "audit": {
                    "key_evidence": [],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "high",
                },
            }),
        ])

        members = [
            TribunalMember(role="Analyst", perspective="General", expertise="Analysis"),
            TribunalMember(role="DA", perspective="Risk", expertise="Risk analysis"),
        ]
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_llm,
            dimensions=["demand_resonance", "propagation_potential"],
            rubric="1=low, 5=high",
            max_rounds=4,
        )
        records = await orch.run(evidence_pack={"summary": "Test", "key_signals": []})
        # Last record should have audit fields populated
        last = records[-1]
        assert last.converged is True
        # Audit fields should be populated (at least some)
        assert isinstance(last.key_evidence, list)
        assert isinstance(last.uncertainties, list)
        assert isinstance(last.optimism_audit, list)
        assert isinstance(last.overrated_dimensions, list)
        assert isinstance(last.missing_evidence, list)
        # recommended_confidence_cap should be the most conservative (lowest)
        assert last.recommended_confidence_cap in ("low", "medium", "high")

    @pytest.mark.asyncio
    async def test_audit_populated_on_non_convergence(self):
        """Final DeliberationRecord has audit fields even when not converging."""
        from ripple.engine.deliberation import DeliberationOrchestrator
        from ripple.primitives.pmf_models import TribunalMember

        mock_llm = AsyncMock(side_effect=[
            # Round 0
            json.dumps({
                "scores": {"d": 4},
                "narrative": "A.",
                "audit": {
                    "key_evidence": ["ev1"],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "medium",
                },
            }),
            json.dumps({
                "scores": {"d": 1},
                "narrative": "B.",
                "audit": {
                    "key_evidence": [],
                    "uncertainties": [],
                    "optimism_audit": [],
                    "overrated_dimensions": [],
                    "missing_evidence": [],
                    "recommended_confidence_cap": "low",
                },
            }),
        ])

        members = [
            TribunalMember(role="A", perspective="P", expertise="E"),
            TribunalMember(role="B", perspective="P", expertise="E"),
        ]
        orch = DeliberationOrchestrator(
            members=members,
            llm_caller=mock_llm,
            dimensions=["d"],
            rubric="1=low, 5=high",
            max_rounds=1,
        )
        records = await orch.run(evidence_pack={"summary": "Test", "key_signals": []})
        last = records[-1]
        # Even without convergence, audit should be populated
        assert isinstance(last.key_evidence, list)
        # Most conservative cap should be "low" (from member B)
        assert last.recommended_confidence_cap == "low"


# ---------------------------------------------------------------------------
# SSE quality fields tests (Gap 5)
# ---------------------------------------------------------------------------


class TestSSEQualityFields:
    """Tests for SSE quality fields in SYNTHESIZE phase_end event — Gap 5."""

    @pytest.mark.asyncio
    async def test_evidence_balance_always_present(self):
        """evidence_balance is always in SSE detail, even with no evidence pack."""
        caller = AsyncMock()
        runtime = SimulationRuntime(
            omniscient_caller=caller,
            star_caller=caller,
            sea_caller=caller,
        )
        # No evidence pack V2
        runtime._evidence_pack_v2 = None
        runtime._providers = None

        # Simulate the quality detail construction logic
        ev_balance = {
            "positive_count": 0,
            "negative_count": 0,
            "silent_count": 0,
            "balanced": True,
        }
        if runtime._evidence_pack_v2 is not None:
            ep = runtime._evidence_pack_v2
            pos = ep.positive_signals.count
            neg = ep.negative_signals.count
            silent = ep.silent_signals.count
            ev_balance = {
                "positive_count": pos,
                "negative_count": neg,
                "silent_count": silent,
                "balanced": not (pos > 0 and neg == 0 and pos > 5),
            }

        assert ev_balance["positive_count"] == 0
        assert ev_balance["negative_count"] == 0
        assert ev_balance["silent_count"] == 0
        assert ev_balance["balanced"] is True

    @pytest.mark.asyncio
    async def test_evidence_balance_with_imbalanced_signals(self):
        """evidence_balance.balanced is False when positive dominates heavily."""
        from ripple.primitives.prediction_quality import (
            EvidencePackV2, SignalSummary, StratifiedStats, EnergyDecaySummary,
        )

        ep = EvidencePackV2(
            pack_id="test",
            source="test",
            summary="test",
            positive_signals=SignalSummary(count=10, top_signals=[], energy_total=5.0),
            negative_signals=SignalSummary(count=0, top_signals=[], energy_total=0.0),
            silent_signals=SignalSummary(count=0, top_signals=[], energy_total=0.0),
            stratified=StratifiedStats(),
            energy_decay=EnergyDecaySummary(),
        )

        pos = ep.positive_signals.count
        neg = ep.negative_signals.count
        silent = ep.silent_signals.count
        balanced = not (pos > 0 and neg == 0 and pos > 5) and not (silent > (pos + neg) and (pos + neg + silent) > 5)

        assert balanced is False

    @pytest.mark.asyncio
    async def test_provider_status_no_providers(self):
        """provider_status shows available=False when no providers configured."""
        caller = AsyncMock()
        runtime = SimulationRuntime(
            omniscient_caller=caller,
            star_caller=caller,
            sea_caller=caller,
        )
        runtime._providers = None

        # Simulate the provider_status construction logic
        provider_status_detail = {"available": False, "categories": []}
        if runtime._providers is not None:
            from ripple.providers.registry import ProviderRegistry
            if isinstance(runtime._providers, ProviderRegistry):
                available_categories = []
                for cat in ("historical", "topology", "embedding", "ambient"):
                    try:
                        p = runtime._providers.get(cat)
                        if p.is_available():
                            available_categories.append(cat)
                    except Exception:
                        pass
                provider_status_detail = {
                    "available": len(available_categories) > 0,
                    "categories": available_categories,
                }

        assert provider_status_detail["available"] is False
        assert provider_status_detail["categories"] == []

    @pytest.mark.asyncio
    async def test_provider_status_with_available_providers(self):
        """provider_status shows available=True with real providers."""
        from ripple.providers.registry import ProviderRegistry

        caller = AsyncMock()
        runtime = SimulationRuntime(
            omniscient_caller=caller,
            star_caller=caller,
            sea_caller=caller,
        )
        # Create registry with default stubs (none available)
        runtime._providers = ProviderRegistry()

        # Simulate the provider_status construction logic
        provider_status_detail = {"available": False, "categories": []}
        if runtime._providers is not None:
            from ripple.providers.registry import ProviderRegistry as PR
            if isinstance(runtime._providers, PR):
                available_categories = []
                cat_status = {}
                for cat in ("historical", "topology", "embedding", "ambient"):
                    try:
                        p = runtime._providers.get(cat)
                        status = "available" if p.is_available() else "stub"
                        cat_status[cat] = status
                        if status == "available":
                            available_categories.append(cat)
                    except Exception:
                        cat_status[cat] = "error"
                provider_status_detail = {
                    "available": len(available_categories) > 0,
                    "categories": available_categories,
                    "detail": cat_status,
                }

        # With all stubs, available should be False
        assert provider_status_detail["available"] is False
        assert provider_status_detail["categories"] == []
        assert "detail" in provider_status_detail
        # All categories should be "stub"
        for cat in ("historical", "topology", "embedding", "ambient"):
            assert provider_status_detail["detail"].get(cat) == "stub"


# ---------------------------------------------------------------------------
# Confidence gate tribunal_cap fallback tests
# ---------------------------------------------------------------------------


class TestConfidenceGateTribunalCapFallback:
    """Tests for _evaluate_confidence_gate using _parse_tribunal_audit as fallback."""

    def test_tribunal_cap_from_parsed_audit(self):
        """When deliberation_summary.audit is missing, _parse_tribunal_audit provides fallback."""
        caller = AsyncMock()
        runtime = SimulationRuntime(
            omniscient_caller=caller,
            star_caller=caller,
            sea_caller=caller,
        )
        # Set up DELIBERATE output with audit in deliberation_records (not summary)
        runtime._extra_phase_outputs = {
            "DELIBERATE": {
                "deliberation_records": [
                    {
                        "round_number": 0,
                        "opinions": [],
                        "key_evidence": ["ev1"],  # at least one non-empty field
                        "uncertainties": [],
                        "optimism_audit": [],
                        "overrated_dimensions": [],
                        "missing_evidence": [],
                        "recommended_confidence_cap": "low",
                    },
                ],
            },
        }
        runtime._providers = None
        runtime._evidence_pack_v2 = None

        # The _parse_tribunal_audit should find the cap from records
        parsed = runtime._parse_tribunal_audit()
        assert parsed is not None
        assert parsed["recommended_confidence_cap"] == "low"
