# tests/backtest/test_optimizer.py
"""Tests for ParameterOptimizer — propose new parameter values from deviation analysis."""

from __future__ import annotations

from ripple.backtest.analyzer import DeviationReport, BiasSignal
from ripple.backtest.optimizer import ParameterOptimizer, DEFAULT_PARAMS, _score_candidate


def _make_deviation_report(
    overall_bias: str = "over_predict",
    overall_signed_mape: float = 100.0,
    per_metric: list | None = None,
) -> DeviationReport:
    return DeviationReport(
        overall_bias=overall_bias,
        overall_signed_mape=overall_signed_mape,
        per_metric=per_metric or [
            BiasSignal("impressions", "over_predict", 100.0, 100.0, 3),
        ],
        sample_count=3,
    )


class TestDefaultParams:
    def test_default_params_keys(self):
        assert "threshold" in DEFAULT_PARAMS
        assert "p95_hard_cap" in DEFAULT_PARAMS
        assert "historical_threshold_pct" in DEFAULT_PARAMS

    def test_default_params_values(self):
        assert DEFAULT_PARAMS["threshold"] == 100.0
        assert DEFAULT_PARAMS["p95_hard_cap"] == 200.0
        assert DEFAULT_PARAMS["historical_threshold_pct"] == 50.0


class TestScoreCandidate:
    def test_neutral_bias_prefers_defaults(self):
        report = _make_deviation_report(overall_bias="neutral", overall_signed_mape=0.0)
        default_score = _score_candidate(DEFAULT_PARAMS, report)
        # Any deviation from defaults should score worse
        shifted = dict(DEFAULT_PARAMS)
        shifted["threshold"] = 50.0
        shifted_score = _score_candidate(shifted, report)
        assert default_score < shifted_score

    def test_over_predict_lower_thresholds_score_better(self):
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        conservative = {"threshold": 50.0, "p95_hard_cap": 100.0, "historical_threshold_pct": 25.0}
        permissive = {"threshold": 150.0, "p95_hard_cap": 300.0, "historical_threshold_pct": 75.0}
        conservative_score = _score_candidate(conservative, report)
        permissive_score = _score_candidate(permissive, report)
        assert conservative_score < permissive_score

    def test_under_predict_higher_thresholds_score_better(self):
        report = _make_deviation_report(overall_bias="under_predict", overall_signed_mape=-100.0)
        conservative = {"threshold": 50.0, "p95_hard_cap": 100.0, "historical_threshold_pct": 25.0}
        permissive = {"threshold": 150.0, "p95_hard_cap": 300.0, "historical_threshold_pct": 75.0}
        conservative_score = _score_candidate(conservative, report)
        permissive_score = _score_candidate(permissive, report)
        assert permissive_score < conservative_score


class TestParameterOptimizer:
    def test_neutral_bias_returns_defaults(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="neutral", overall_signed_mape=0.0)
        result = optimizer.optimize(report)
        assert result.proposed_params == DEFAULT_PARAMS
        assert result.bias_direction == "neutral"
        assert result.improvement_estimate == 0.0
        assert len(result.warnings) > 0

    def test_over_predict_proposes_lower_thresholds(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        # At least one threshold should be lower than default
        assert any(
            result.proposed_params[k] < DEFAULT_PARAMS[k]
            for k in DEFAULT_PARAMS
        ), f"Expected at least one lower param, got {result.proposed_params}"

    def test_under_predict_proposes_higher_thresholds(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="under_predict", overall_signed_mape=-100.0)
        result = optimizer.optimize(report)
        # At least one threshold should be higher than default
        assert any(
            result.proposed_params[k] > DEFAULT_PARAMS[k]
            for k in DEFAULT_PARAMS
        ), f"Expected at least one higher param, got {result.proposed_params}"

    def test_candidates_evaluated(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        # 4 values per dim * 3 dims = 64
        assert result.candidates_evaluated == 64

    def test_custom_grid(self):
        small_grid = {
            "threshold": [80.0, 100.0],
            "p95_hard_cap": [150.0, 200.0],
            "historical_threshold_pct": [40.0, 50.0],
        }
        optimizer = ParameterOptimizer(grid=small_grid)
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        # 2 * 2 * 2 = 8
        assert result.candidates_evaluated == 8

    def test_current_params_stored(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        current = {"threshold": 90.0, "p95_hard_cap": 180.0, "historical_threshold_pct": 45.0}
        result = optimizer.optimize(report, current_params=current)
        assert result.current_params == current

    def test_default_current_params(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        assert result.current_params == DEFAULT_PARAMS

    def test_score_is_non_negative(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        assert result.score >= 0.0

    def test_improvement_estimate_non_negative(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=100.0)
        result = optimizer.optimize(report)
        assert result.improvement_estimate >= 0.0

    def test_improvement_estimate_capped_at_magnitude(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=50.0)
        result = optimizer.optimize(report)
        # Improvement estimate should not exceed the bias magnitude
        assert result.improvement_estimate <= 50.0

    def test_small_bias_still_produces_result(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=10.0)
        result = optimizer.optimize(report)
        assert result.proposed_params is not None
        assert result.candidates_evaluated > 0

    def test_large_bias_produces_result(self):
        optimizer = ParameterOptimizer()
        report = _make_deviation_report(overall_bias="over_predict", overall_signed_mape=300.0)
        result = optimizer.optimize(report)
        assert result.proposed_params is not None
        # With large bias, should propose significantly lower thresholds
        assert result.proposed_params["threshold"] < DEFAULT_PARAMS["threshold"]


class TestOptimizationResultStructure:
    def test_result_has_all_fields(self):
        from ripple.backtest.schema import OptimizationResult
        result = OptimizationResult(
            proposed_params={"threshold": 75.0},
            score=0.5,
            improvement_estimate=20.0,
            current_params={"threshold": 100.0},
            bias_direction="over_predict",
            candidates_evaluated=64,
        )
        assert result.proposed_params == {"threshold": 75.0}
        assert result.score == 0.5
        assert result.improvement_estimate == 20.0
        assert result.current_params == {"threshold": 100.0}
        assert result.bias_direction == "over_predict"
        assert result.candidates_evaluated == 64
        assert result.warnings == []
