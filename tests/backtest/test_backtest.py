# tests/backtest/test_backtest.py
"""Tests for offline backtesting framework — R7."""

import pytest

from ripple.backtest.schema import BacktestCase, BacktestResult, BacktestReport, PredictionError
from ripple.backtest.metrics import (
    compute_numeric_metrics,
    compute_grade_metrics,
    compute_confidence_calibration,
    compute_prediction_errors,
    compute_brier_score,
)


class TestBacktestCase:
    def test_creation(self):
        case = BacktestCase(
            case_id="case-001",
            skill_id="social-media",
            simulation_input={"event": {"title": "Test"}},
            ground_truth={"views": 1000, "engagement": 100},
            platform="xiaohongshu",
            time_window="48h",
        )
        assert case.case_id == "case-001"
        assert case.schema_version == "1.0"
        assert case.platform == "xiaohongshu"

    def test_defaults(self):
        case = BacktestCase(case_id="case-002")
        assert case.skill_id == "social-media"
        assert case.schema_version == "1.0"


class TestPredictionErrors:
    def test_basic(self):
        errors = compute_prediction_errors(
            {"views": 500, "engagement": 50},
            {"views": 1000, "engagement": 100},
        )
        assert len(errors) == 2
        views_err = next(e for e in errors if e.metric == "views")
        assert views_err.absolute_error == 500.0
        assert views_err.percentage_error == 50.0

    def test_zero_actual(self):
        errors = compute_prediction_errors(
            {"views": 100},
            {"views": 0},
        )
        assert len(errors) == 1
        assert errors[0].percentage_error is None  # can't compute when actual=0

    def test_exact_match(self):
        errors = compute_prediction_errors(
            {"views": 1000},
            {"views": 1000},
        )
        assert len(errors) == 1
        assert errors[0].absolute_error == 0.0
        assert errors[0].percentage_error == 0.0

    def test_skip_non_numeric(self):
        errors = compute_prediction_errors(
            {"views": 100, "verdict": "optimistic", "confidence": "high"},
            {"views": 200, "verdict": "conservative"},
        )
        assert len(errors) == 1
        assert errors[0].metric == "views"


class TestNumericMetrics:
    def test_mae(self):
        results = [
            BacktestResult(case_id="1", prediction={}, errors=[
                PredictionError(metric="views", predicted=500, actual=1000, absolute_error=500, percentage_error=50),
            ]),
            BacktestResult(case_id="2", prediction={}, errors=[
                PredictionError(metric="views", predicted=800, actual=1000, absolute_error=200, percentage_error=20),
            ]),
        ]
        metrics = compute_numeric_metrics(results)
        assert metrics["mae"] == 350.0

    def test_rmse(self):
        results = [
            BacktestResult(case_id="1", prediction={}, errors=[
                PredictionError(metric="views", predicted=0, actual=3, absolute_error=3, percentage_error=None),
                PredictionError(metric="views", predicted=0, actual=4, absolute_error=4, percentage_error=None),
            ]),
        ]
        metrics = compute_numeric_metrics(results)
        import math
        assert abs(metrics["rmse"] - math.sqrt((9 + 16) / 2)) < 0.01

    def test_empty(self):
        metrics = compute_numeric_metrics([])
        assert metrics["mae"] is None


class TestGradeMetrics:
    def test_confusion_matrix(self):
        from ripple.backtest.schema import GradeError
        results = [
            BacktestResult(case_id="1", prediction={}, grade_errors=[
                GradeError(dimension="overall", predicted_grade="A", actual_grade="A", correct=True),
                GradeError(dimension="overall", predicted_grade="B", actual_grade="A", correct=False),
            ]),
        ]
        metrics = compute_grade_metrics(results)
        assert metrics["confusion_matrix"]["A"]["A"] == 1
        assert metrics["confusion_matrix"]["B"]["A"] == 1
        assert metrics["macro_f1"] is not None

    def test_empty(self):
        metrics = compute_grade_metrics([])
        assert metrics["macro_f1"] is None


class TestConfidenceCalibration:
    def test_basic(self):
        results = [
            BacktestResult(case_id="1", prediction={}, predicted_confidence="high", actual_accuracy=True),
            BacktestResult(case_id="2", prediction={}, predicted_confidence="high", actual_accuracy=False),
            BacktestResult(case_id="3", prediction={}, predicted_confidence="low", actual_accuracy=False),
        ]
        cal = compute_confidence_calibration(results)
        assert cal["high"] == 0.5
        assert cal["low"] == 0.0


class TestBrierScore:
    def test_perfect_predictions(self):
        results = [
            BacktestResult(case_id="1", prediction={"virality_probability": 0.9}, errors=[
                PredictionError(metric="virality_probability", predicted=0.9, actual=1.0, absolute_error=0.1, percentage_error=None),
            ]),
        ]
        brier = compute_brier_score(results)
        assert brier is not None
        assert brier < 0.2  # close to 0 for good predictions

    def test_no_probability_fields(self):
        results = [
            BacktestResult(case_id="1", prediction={"impressions": 1000}, errors=[
                PredictionError(metric="impressions", predicted=1000.0, actual=1200.0, absolute_error=200.0, percentage_error=None),
            ]),
        ]
        brier = compute_brier_score(results)
        assert brier is None

    def test_empty_results(self):
        brier = compute_brier_score([])
        assert brier is None
