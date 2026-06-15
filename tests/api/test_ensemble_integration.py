"""Integration tests for simulate() ensemble mode (P1).

Verifies:
- run boundaries are recorded via recorder.begin_ensemble_run/end_ensemble_run
- ensemble aggregation is attached to the returned result
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ripple.api.simulate import simulate, _run_ensemble


class TestSimulateEnsembleAggregation:
    @pytest.mark.asyncio
    async def test_ensemble_aggregation_and_run_boundaries(self):
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder") as MockRecorder:

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.version = "fixture-version"
            mock_skill.domain_profile = "PMF profile"
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            mock_skill.prompts = {
                "omniscient": "omniscient prompt",
                "star": "star prompt",
                "sea": "sea prompt",
                "tribunal": "tribunal prompt",
            }
            mock_skill.rubrics = {"scorecard-dimensions": "rubric"}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            recorder = MockRecorder.return_value

            mock_runtime = AsyncMock()
            mock_runtime.run.side_effect = [
                {
                    "total_waves": 1,
                    "grade": "A",
                    "scores": {"demand_resonance": 4, "propagation_potential": 4},
                },
                {
                    "total_waves": 1,
                    "grade": "A",
                    "scores": {"demand_resonance": 4, "propagation_potential": 3},
                },
                {
                    "total_waves": 1,
                    "grade": "B",
                    "scores": {"demand_resonance": 3, "propagation_potential": 3},
                },
            ]
            MockRuntime.return_value = mock_runtime

            result = await simulate(
                event={"description": "test"},
                skill="pmf-validation",
                ensemble_runs=3,
            )

            assert result["ensemble_runs_requested"] == 3
            assert result["ensemble_runs_completed"] == 3
            stats = result["ensemble_stats"]
            assert stats["grade_mode"] == "A"
            assert stats["grade_agreement_rate"] == pytest.approx(2 / 3, abs=1e-6)
            assert "dimension_aggregates" in stats
            assert stats["dimension_aggregates"]["demand_resonance"]["median"] == 4.0

            assert recorder.begin_ensemble_run.call_count == 3
            assert recorder.end_ensemble_run.call_count == 3


class TestEnsembleMedianMerge:
    """R1: Ensemble merge uses median values from numeric_distributions, not last-run values."""

    @pytest.mark.asyncio
    async def test_numeric_fields_replaced_with_median(self):
        """Numeric prediction fields should be replaced with ensemble medians."""
        with patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.variant_isolation.compute_variant_seeds") as mock_seeds:

            mock_seeds.return_value = [42, 43, 44]

            mock_runtime = AsyncMock()
            mock_runtime.run.side_effect = [
                {"prediction": {"views": 1000, "engagement": 50}, "grade": "A", "scores": {"demand": 4}},
                {"prediction": {"views": 2000, "engagement": 100}, "grade": "A", "scores": {"demand": 4}},
                {"prediction": {"views": 3000, "engagement": 150}, "grade": "B", "scores": {"demand": 3}},
            ]
            MockRuntime.return_value = mock_runtime

            result = await _run_ensemble(
                omniscient_caller=AsyncMock(),
                star_caller=AsyncMock(),
                sea_caller=AsyncMock(),
                skill_profile="test",
                skill_prompts={},
                on_progress=None,
                recorder=None,
                extra_phases=None,
                simulation_input={"event": {"title": "test"}},
                run_id="test",
                ensemble_runs=3,
                random_seed=42,
            )

            # Median of views: sorted [1000, 2000, 3000] → 2000
            assert result["prediction"]["views"] == 2000.0
            # Median of engagement: sorted [50, 100, 150] → 100
            assert result["prediction"]["engagement"] == 100.0

    @pytest.mark.asyncio
    async def test_post_ensemble_confidence_gate(self):
        """R2: Post-ensemble confidence gate should run when 2+ runs complete."""
        with patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.variant_isolation.compute_variant_seeds") as mock_seeds:

            mock_seeds.return_value = [42, 43]

            mock_runtime = AsyncMock()
            mock_runtime.run.side_effect = [
                {"prediction": {"views": 1000}, "confidence": "high", "grade": "A", "scores": {"demand": 4}},
                {"prediction": {"views": 3000}, "confidence": "high", "grade": "B", "scores": {"demand": 2}},
            ]
            MockRuntime.return_value = mock_runtime

            result = await _run_ensemble(
                omniscient_caller=AsyncMock(),
                star_caller=AsyncMock(),
                sea_caller=AsyncMock(),
                skill_profile="test",
                skill_prompts={},
                on_progress=None,
                recorder=None,
                extra_phases=None,
                simulation_input={"event": {"title": "test"}},
                run_id="test",
                ensemble_runs=2,
                random_seed=42,
            )

            # Post-ensemble confidence gate should have been applied
            assert "confidence_gate" in result
            assert isinstance(result["confidence_gate"], dict)
            # Serialized values must be strings, not enum objects
            assert isinstance(result["confidence_gate"]["original_confidence"], str)
            assert isinstance(result["confidence_gate"]["final_confidence"], str)
            # Factors must be list of dicts, not dataclass objects
            factors = result["confidence_gate"]["factors"]
            assert isinstance(factors, list)
            if factors:
                assert isinstance(factors[0], dict)
            # Quality sub-dict should also contain the gate result
            assert "quality" in result
            assert "confidence_gate_result" in result["quality"]

    @pytest.mark.asyncio
    async def test_no_post_ensemble_gate_for_single_run(self):
        """Post-ensemble gate should NOT run when only 1 run completes."""
        with patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.variant_isolation.compute_variant_seeds") as mock_seeds:

            mock_seeds.return_value = [42]

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {
                "prediction": {"views": 1000},
                "confidence": "high",
            }
            MockRuntime.return_value = mock_runtime

            result = await _run_ensemble(
                omniscient_caller=AsyncMock(),
                star_caller=AsyncMock(),
                sea_caller=AsyncMock(),
                skill_profile="test",
                skill_prompts={},
                on_progress=None,
                recorder=None,
                extra_phases=None,
                simulation_input={"event": {"title": "test"}},
                run_id="test",
                ensemble_runs=1,
                random_seed=42,
            )

            # No post-ensemble gate for single run
            assert "confidence_gate" not in result

