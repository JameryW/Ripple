# tests/backtest/test_backtest.py
"""Tests for offline backtesting framework — R7."""


from ripple.backtest.schema import BacktestCase, BacktestResult, PredictionError, BacktestReport
from ripple.backtest.metrics import (
    compute_numeric_metrics,
    compute_grade_metrics,
    compute_confidence_calibration,
    compute_prediction_errors,
    compute_brier_score,
)
from ripple.backtest.runner import (
    _extract_quality_signals,
    _compute_input_completeness,
    _aggregate_quality_signals,
    _worst_stability,
    _worst_divergence,
    _classify_divergence,
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

    def test_signed_mape(self):
        """Signed symmetric MAPE: positive=over-predict, negative=under-predict."""
        results = [
            BacktestResult(case_id="1", prediction={}, errors=[
                PredictionError(
                    metric="views", predicted=1200, actual=1000,
                    absolute_error=200, percentage_error=20,
                    signed_percentage_error=18.18,  # (1200-1000)/((1200+1000)/2)*100
                ),
            ]),
            BacktestResult(case_id="2", prediction={}, errors=[
                PredictionError(
                    metric="views", predicted=800, actual=1000,
                    absolute_error=200, percentage_error=20,
                    signed_percentage_error=-22.22,  # (800-1000)/((800+1000)/2)*100
                ),
            ]),
        ]
        metrics = compute_numeric_metrics(results)
        assert metrics["signed_mape"] is not None
        # Average of 18.18 and -22.22 ≈ -2.02
        assert metrics["signed_mape"] < 0  # net under-prediction

    def test_signed_mape_empty(self):
        """Signed MAPE is None when no errors."""
        metrics = compute_numeric_metrics([])
        assert metrics["signed_mape"] is None

    def test_signed_percentage_error_in_compute_prediction_errors(self):
        """compute_prediction_errors should populate signed_percentage_error."""
        errors = compute_prediction_errors(
            {"views": 1200},
            {"views": 1000},
        )
        assert len(errors) == 1
        e = errors[0]
        assert e.signed_percentage_error is not None
        assert e.signed_percentage_error > 0  # over-prediction
        # (1200-1000)/((1200+1000)/2)*100 = 200/1100*100 ≈ 18.18
        assert abs(e.signed_percentage_error - 18.1818) < 0.01


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


# ── Quality signal extraction tests ──────────────────────────────────────


class TestExtractQualitySignals:
    """Test _extract_quality_signals with various simulate result dicts."""

    def test_full_result_dict(self):
        """Extract all quality signals from a rich simulate result."""
        result = {
            "prediction": {"views": 1000},
            "confidence": "high",
            "ensemble_stats": {
                "dimension_aggregates": {
                    "reach_realism": {"stability": "high", "score": 0.9},
                    "decay_realism": {"stability": "low", "score": 0.3},
                },
            },
            "deliberation_summary": {
                "dissent_points": ["reach_realism", "decay_realism"],
                "consensus_points": ["virality_plausibility"],
            },
            "evidence_balance": {"positive": 5, "negative": 2, "silent": 1},
            "calibration_report": {"max_deviation": 45.2},
            "quality_report": {
                "ensemble_stability": "low",
                "residual_risks": ["high_deviation", "low_stability"],
            },
        }
        signals = _extract_quality_signals(result)

        assert signals["ensemble_stability"] == "low"  # worst of high+low
        assert signals["tribunal_divergence"] == "high"  # 2 dissent, 1 consensus
        assert signals["evidence_balance"] == {"positive": 5, "negative": 2, "silent": 1}
        assert signals["historical_deviation"] == 45.2
        assert signals["quality_report_dict"]["ensemble_stability"] == "low"

    def test_minimal_result_dict(self):
        """Extract gracefully from a minimal mock result (no quality signals)."""
        result = {
            "prediction": {"views": 1000},
            "confidence": "medium",
        }
        signals = _extract_quality_signals(result)

        assert "ensemble_stability" not in signals
        assert "tribunal_divergence" not in signals
        assert "evidence_balance" not in signals
        assert "historical_deviation" not in signals
        assert "quality_report_dict" not in signals

    def test_empty_result_dict(self):
        """Empty dict produces no signals."""
        signals = _extract_quality_signals({})
        assert signals == {}

    def test_partial_quality_signals(self):
        """Result with only some quality signals present."""
        result = {
            "prediction": {"views": 500},
            "confidence": "low",
            "evidence_balance": {"positive": 3, "negative": 1},
        }
        signals = _extract_quality_signals(result)

        assert "ensemble_stability" not in signals
        assert "tribunal_divergence" not in signals
        assert signals["evidence_balance"] == {"positive": 3, "negative": 1}
        assert "historical_deviation" not in signals

    def test_evidence_balance_non_int_filtered(self):
        """Non-numeric evidence_balance values are filtered out."""
        result = {
            "prediction": {},
            "confidence": "medium",
            "evidence_balance": {"positive": 3, "negative": "many", "silent": 1.5},
        }
        signals = _extract_quality_signals(result)
        assert signals["evidence_balance"] == {"positive": 3, "silent": 1}

    def test_ensemble_stability_single_dimension(self):
        """Single dimension aggregate returns its stability level."""
        result = {
            "prediction": {},
            "confidence": "medium",
            "ensemble_stats": {
                "dimension_aggregates": {
                    "reach_realism": {"stability": "medium", "score": 0.6},
                },
            },
        }
        signals = _extract_quality_signals(result)
        assert signals["ensemble_stability"] == "medium"

    def test_deliberation_no_dissent(self):
        """Zero dissent points produces low divergence."""
        result = {
            "prediction": {},
            "confidence": "medium",
            "deliberation_summary": {
                "dissent_points": [],
                "consensus_points": ["reach_realism", "decay_realism"],
            },
        }
        signals = _extract_quality_signals(result)
        assert signals["tribunal_divergence"] == "low"

    def test_calibration_report_non_numeric_max_deviation(self):
        """Non-numeric max_deviation is ignored."""
        result = {
            "prediction": {},
            "confidence": "medium",
            "calibration_report": {"max_deviation": "high"},
        }
        signals = _extract_quality_signals(result)
        assert "historical_deviation" not in signals


class TestClassifyDivergence:
    """Test _classify_divergence helper."""

    def test_high_divergence(self):
        assert _classify_divergence(3, 3) == "high"  # 50%

    def test_medium_divergence(self):
        assert _classify_divergence(1, 3) == "medium"  # 25%

    def test_low_divergence(self):
        assert _classify_divergence(1, 5) == "low"  # ~17%

    def test_zero_total(self):
        assert _classify_divergence(0, 0) is None


class TestWorstStability:
    """Test _worst_stability helper."""

    def test_none_current(self):
        assert _worst_stability(None, "low") == "low"

    def test_none_candidate(self):
        assert _worst_stability("high", None) == "high"

    def test_both_present(self):
        assert _worst_stability("high", "low") == "low"
        assert _worst_stability("low", "high") == "low"
        assert _worst_stability("medium", "high") == "medium"

    def test_same_level(self):
        assert _worst_stability("medium", "medium") == "medium"


class TestWorstDivergence:
    """Test _worst_divergence helper."""

    def test_none_current(self):
        assert _worst_divergence(None, "high") == "high"

    def test_none_candidate(self):
        assert _worst_divergence("low", None) == "low"

    def test_both_present(self):
        assert _worst_divergence("low", "high") == "high"
        assert _worst_divergence("high", "low") == "high"
        assert _worst_divergence("medium", "low") == "medium"


class TestComputeInputCompleteness:
    """Test _compute_input_completeness helper."""

    def test_empty_input(self):
        assert _compute_input_completeness({}) == 0.0

    def test_full_input(self):
        inp = {
            "event": {"title": "test"},
            "source": "author",
            "platform": "xiaohongshu",
            "channel": "feed",
            "vertical": "beauty",
            "historical": {"baseline": 100},
            "simulation_horizon": "48h",
        }
        assert _compute_input_completeness(inp) == 1.0

    def test_partial_input(self):
        inp = {"event": {"title": "test"}, "platform": "weibo"}
        result = _compute_input_completeness(inp)
        assert 0.0 < result < 1.0


class TestAggregateQualitySignals:
    """Test _aggregate_quality_signals across multiple cases."""

    def test_empty_signals(self):
        result = _aggregate_quality_signals([], [])
        assert result == {}

    def test_worst_stability_across_cases(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={"event": "a"}),
            BacktestCase(case_id="2", simulation_input={"event": "b"}),
        ]
        per_case = [
            {"ensemble_stability": "high"},
            {"ensemble_stability": "low"},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["ensemble_stability"] == "low"

    def test_worst_divergence_across_cases(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={"event": "a"}),
            BacktestCase(case_id="2", simulation_input={"event": "b"}),
        ]
        per_case = [
            {"tribunal_divergence": "low"},
            {"tribunal_divergence": "high"},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["tribunal_divergence"] == "high"

    def test_evidence_balance_sums(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={"event": "a"}),
            BacktestCase(case_id="2", simulation_input={"event": "b"}),
        ]
        per_case = [
            {"evidence_balance": {"positive": 3, "negative": 1}},
            {"evidence_balance": {"positive": 2, "negative": 4}},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["evidence_balance"] == {"positive": 5, "negative": 5}

    def test_input_completeness_averaged(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={"event": "a", "platform": "xhs"}),
            BacktestCase(case_id="2", simulation_input={"event": "b", "platform": "xhs", "channel": "feed"}),
        ]
        per_case = [{}, {}]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["input_completeness"] is not None
        assert 0.0 < result["input_completeness"] <= 1.0

    def test_historical_deviation_max(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={}),
            BacktestCase(case_id="2", simulation_input={}),
        ]
        per_case = [
            {"historical_deviation": 30.0},
            {"historical_deviation": 55.0},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["historical_deviation"] == 55.0

    def test_residual_risks_deduplicated(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={}),
            BacktestCase(case_id="2", simulation_input={}),
        ]
        per_case = [
            {"quality_report_dict": {"residual_risks": ["high_deviation", "low_stability"]}},
            {"quality_report_dict": {"residual_risks": ["low_stability", "missing_data"]}},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["residual_risks"] == ["high_deviation", "low_stability", "missing_data"]

    def test_quality_report_dict_last_wins(self):
        cases = [
            BacktestCase(case_id="1", simulation_input={}),
            BacktestCase(case_id="2", simulation_input={}),
        ]
        per_case = [
            {"quality_report_dict": {"run": 1}},
            {"quality_report_dict": {"run": 2}},
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["quality_report_dict"] == {"run": 2}

    def test_mixed_signals_with_none(self):
        """Some cases have signals, others don't — aggregation handles gracefully."""
        cases = [
            BacktestCase(case_id="1", simulation_input={"event": "a"}),
            BacktestCase(case_id="2", simulation_input={}),
        ]
        per_case = [
            {"ensemble_stability": "high", "tribunal_divergence": "low"},
            {},  # mock simulate returns no quality signals
        ]
        result = _aggregate_quality_signals(per_case, cases)
        assert result["ensemble_stability"] == "high"
        assert result["tribunal_divergence"] == "low"


class TestBacktestReportQualityFields:
    """Test that BacktestReport has quality dimension fields with correct defaults."""

    def test_quality_defaults(self):
        report = BacktestReport()
        assert report.ensemble_stability is None
        assert report.tribunal_divergence is None
        assert report.evidence_balance == {}
        assert report.input_completeness is None
        assert report.historical_deviation is None
        assert report.residual_risks == []
        assert report.quality_report_dict is None

    def test_quality_fields_set(self):
        report = BacktestReport(
            ensemble_stability="low",
            tribunal_divergence="high",
            evidence_balance={"positive": 5, "negative": 2},
            input_completeness=0.73,
            historical_deviation=45.2,
            residual_risks=["high_deviation"],
            quality_report_dict={"key": "value"},
        )
        assert report.ensemble_stability == "low"
        assert report.tribunal_divergence == "high"
        assert report.evidence_balance == {"positive": 5, "negative": 2}
        assert report.input_completeness == 0.73
        assert report.historical_deviation == 45.2
        assert report.residual_risks == ["high_deviation"]
        assert report.quality_report_dict == {"key": "value"}

    def test_backward_compatible_dict_without_quality(self):
        """Old dicts without quality fields should deserialize correctly."""
        from dataclasses import asdict
        # Simulate an old-style dict (no quality keys)
        report = BacktestReport(total_cases=5)
        d = asdict(report)
        # Quality fields should be present with defaults
        assert d["ensemble_stability"] is None
        assert d["evidence_balance"] == {}
        assert d["residual_risks"] == []
