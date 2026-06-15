# tests/backtest/test_validator.py
"""Tests for ABValidator — A/B validation and rollback for parameter optimization."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

import pytest

from ripple.backtest.schema import (
    BacktestCase,
    ValidationResult,
)
from ripple.backtest.validator import ABValidator, _compute_mape_change


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_case(case_id: str = "case-1") -> BacktestCase:
    return BacktestCase(
        case_id=case_id,
        simulation_input={"event": {"title": "test"}, "_backtest_case_id": case_id},
        ground_truth={"impressions": 1000, "engagement": 100},
        platform="xiaohongshu",
    )


def _make_mock_simulate(
    predictions_by_case_id: Dict[str, Dict[str, Any]],
) -> Callable[..., Awaitable[Dict[str, Any]]]:
    """Create a mock simulate_fn that returns stored predictions per case."""

    async def _mock_simulate(simulation_input: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        case_id = simulation_input.get("_backtest_case_id", "")
        prediction = predictions_by_case_id.get(case_id, {})
        return {
            "prediction": prediction,
            "confidence": prediction.get("confidence", "medium"),
        }

    return _mock_simulate


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputeMapeChange:
    def test_improvement(self):
        # MAPE went from 50 to 40 => -20% change
        result = _compute_mape_change(50.0, 40.0)
        assert result is not None
        assert result == pytest.approx(-20.0)

    def test_degradation(self):
        # MAPE went from 50 to 60 => +20% change
        result = _compute_mape_change(50.0, 60.0)
        assert result is not None
        assert result == pytest.approx(20.0)

    def test_none_old(self):
        assert _compute_mape_change(None, 50.0) is None

    def test_none_new(self):
        assert _compute_mape_change(50.0, None) is None

    def test_zero_old(self):
        assert _compute_mape_change(0.0, 50.0) is None

    def test_no_change(self):
        result = _compute_mape_change(50.0, 50.0)
        assert result is not None
        assert result == pytest.approx(0.0)


class TestABValidatorValidation:
    @pytest.mark.asyncio
    async def test_validation_passes_when_no_degradation(self):
        """When new params produce equal or better metrics, validation passes."""
        cases = [_make_case("case-1")]
        # Both old and new produce same predictions => same metrics
        predictions = {"case-1": {"impressions": 500, "engagement": 50, "confidence": "high"}}
        mock_fn = _make_mock_simulate(predictions)

        validator = ABValidator()
        old_params = {"threshold": 100.0, "p95_hard_cap": 200.0, "historical_threshold_pct": 50.0}
        new_params = {"threshold": 75.0, "p95_hard_cap": 150.0, "historical_threshold_pct": 37.5}

        result = await validator.validate(cases, mock_fn, old_params, new_params)
        # Same predictions => same metrics => should pass
        assert result.passed is True
        assert result.degraded_metrics == []
        assert result.old_mape == result.new_mape

    @pytest.mark.asyncio
    async def test_validation_result_fields_populated(self):
        """ValidationResult should have both old and new metrics populated."""
        cases = [_make_case("case-1")]
        predictions = {"case-1": {"impressions": 500, "engagement": 50, "confidence": "medium"}}
        mock_fn = _make_mock_simulate(predictions)

        validator = ABValidator()
        old_params = {"threshold": 100.0}
        new_params = {"threshold": 75.0}

        result = await validator.validate(cases, mock_fn, old_params, new_params)
        assert result.old_params == old_params
        assert result.new_params == new_params
        assert result.old_mape is not None
        assert result.new_mape is not None
        assert result.old_signed_mape is not None
        assert result.new_signed_mape is not None

    @pytest.mark.asyncio
    async def test_mape_change_pct_populated(self):
        """mape_change_pct should be populated when both MAPs are non-None."""
        cases = [_make_case("case-1")]
        predictions = {"case-1": {"impressions": 500, "engagement": 50, "confidence": "medium"}}
        mock_fn = _make_mock_simulate(predictions)

        validator = ABValidator()
        result = await validator.validate(cases, mock_fn, {"threshold": 100.0}, {"threshold": 75.0})
        # Same predictions => 0% change
        assert result.mape_change_pct is not None
        assert result.mape_change_pct == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_rolled_back_defaults_false(self):
        """rolled_back should default to False."""
        cases = [_make_case("case-1")]
        predictions = {"case-1": {"impressions": 500, "engagement": 50, "confidence": "medium"}}
        mock_fn = _make_mock_simulate(predictions)

        validator = ABValidator()
        result = await validator.validate(cases, mock_fn, {"threshold": 100.0}, {"threshold": 75.0})
        assert result.rolled_back is False

    @pytest.mark.asyncio
    async def test_baseline_failure_returns_not_passed(self):
        """If baseline backtest fails, validation should not pass."""
        cases = [_make_case("case-1")]

        async def _failing_simulate(simulation_input: dict, **kwargs: Any) -> dict:
            raise RuntimeError("Simulate failed")

        validator = ABValidator()
        result = await validator.validate(
            cases, _failing_simulate, {"threshold": 100.0}, {"threshold": 75.0}
        )
        assert result.passed is False
        assert "baseline_failed" in result.degraded_metrics

    @pytest.mark.asyncio
    async def test_trial_failure_returns_not_passed(self):
        """If trial backtest fails but baseline succeeds, validation should not pass."""
        cases = [_make_case("case-1")]
        call_count = 0

        async def _fail_on_second(simulation_input: dict, **kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("Trial simulate failed")
            return {"prediction": {"impressions": 500}, "confidence": "medium"}

        validator = ABValidator()
        result = await validator.validate(
            cases, _fail_on_second, {"threshold": 100.0}, {"threshold": 75.0}
        )
        assert result.passed is False
        assert "trial_failed" in result.degraded_metrics

    @pytest.mark.asyncio
    async def test_multiple_cases(self):
        """Validation works with multiple cases."""
        cases = [_make_case("case-1"), _make_case("case-2")]
        predictions = {
            "case-1": {"impressions": 500, "engagement": 50, "confidence": "medium"},
            "case-2": {"impressions": 300, "engagement": 30, "confidence": "medium"},
        }
        mock_fn = _make_mock_simulate(predictions)

        validator = ABValidator()
        result = await validator.validate(cases, mock_fn, {"threshold": 100.0}, {"threshold": 75.0})
        assert result.passed is True


class TestABValidatorRollback:
    def test_should_rollback_when_not_passed(self):
        validator = ABValidator()
        result = ValidationResult(
            old_params={"threshold": 100.0},
            new_params={"threshold": 75.0},
            passed=False,
            degraded_metrics=["mape: +15.0%"],
        )
        assert validator.should_rollback(result) is True

    def test_should_not_rollback_when_passed(self):
        validator = ABValidator()
        result = ValidationResult(
            old_params={"threshold": 100.0},
            new_params={"threshold": 75.0},
            passed=True,
        )
        assert validator.should_rollback(result) is False

    def test_should_not_rollback_already_rolled_back(self):
        validator = ABValidator()
        result = ValidationResult(
            old_params={"threshold": 100.0},
            new_params={"threshold": 75.0},
            passed=False,
            degraded_metrics=["mape: +15.0%"],
            rolled_back=True,
        )
        assert validator.should_rollback(result) is False

    def test_rollback_returns_old_params_copy(self):
        validator = ABValidator()
        old_params = {"threshold": 100.0, "p95_hard_cap": 200.0, "historical_threshold_pct": 50.0}
        result = validator.rollback(old_params)
        assert result == old_params
        assert result is not old_params  # Should be a copy


class TestABValidatorCustomThreshold:
    def test_custom_degradation_threshold(self):
        """Validator with a higher threshold should be more lenient."""
        validator_strict = ABValidator(degradation_threshold_pct=5.0)
        validator_lenient = ABValidator(degradation_threshold_pct=50.0)
        # A 10% degradation would trigger the strict validator but not the lenient one
        assert validator_strict._degradation_threshold == 5.0
        assert validator_lenient._degradation_threshold == 50.0


class TestValidationResultStructure:
    def test_result_has_all_fields(self):
        result = ValidationResult(
            old_params={"threshold": 100.0},
            new_params={"threshold": 75.0},
            old_mape=50.0,
            new_mape=45.0,
            old_signed_mape=30.0,
            new_signed_mape=25.0,
            mape_change_pct=-10.0,
            passed=True,
            degraded_metrics=[],
            rolled_back=False,
            warnings=[],
        )
        assert result.old_params == {"threshold": 100.0}
        assert result.new_params == {"threshold": 75.0}
        assert result.old_mape == 50.0
        assert result.new_mape == 45.0
        assert result.mape_change_pct == -10.0
        assert result.passed is True
        assert result.degraded_metrics == []
        assert result.rolled_back is False
        assert result.warnings == []

    def test_defaults(self):
        result = ValidationResult()
        assert result.old_params == {}
        assert result.new_params == {}
        assert result.old_mape is None
        assert result.new_mape is None
        assert result.old_signed_mape is None
        assert result.new_signed_mape is None
        assert result.mape_change_pct is None
        assert result.passed is True
        assert result.degraded_metrics == []
        assert result.rolled_back is False
        assert result.warnings == []
