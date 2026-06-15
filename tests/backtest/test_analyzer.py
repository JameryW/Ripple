# tests/backtest/test_analyzer.py
"""Tests for DeviationAnalyzer — systematic bias detection from backtest history."""

from __future__ import annotations

from ripple.backtest.schema import BacktestReport, BacktestResult, PredictionError
from ripple.backtest.analyzer import DeviationAnalyzer, DeviationReport, BiasSignal


def _make_report(
    run_id: str = "testrun01",
    signed_mape: float = 30.0,
    metric_errors: list | None = None,
) -> BacktestReport:
    """Build a BacktestReport with optional per-metric errors."""
    errors = metric_errors or [
        PredictionError("impressions", 500.0, 400.0, 100.0, 25.0, 22.2),
    ]
    return BacktestReport(
        run_id=run_id,
        total_cases=1,
        completed_cases=1,
        mae=100.0,
        mape=45.0,
        signed_mape=signed_mape,
        rmse=120.0,
        results=[
            BacktestResult(
                case_id="case-1",
                prediction={"impressions": 500},
                errors=errors,
                predicted_confidence="high",
                actual_accuracy=True,
                elapsed_seconds=0.1,
            ),
        ],
    )


class TestDeviationAnalyzerMinRuns:
    def test_below_min_runs_returns_neutral(self):
        analyzer = DeviationAnalyzer(min_runs=2)
        reports = [_make_report(run_id="r1", signed_mape=50.0)]
        result = analyzer.analyze(reports)
        assert result.overall_bias == "neutral"
        assert result.overall_signed_mape == 0.0
        assert len(result.warnings) > 0

    def test_exactly_min_runs(self):
        analyzer = DeviationAnalyzer(min_runs=2)
        reports = [
            _make_report(run_id="r1", signed_mape=50.0),
            _make_report(run_id="r2", signed_mape=60.0),
        ]
        result = analyzer.analyze(reports)
        assert result.overall_bias == "over_predict"
        assert result.sample_count == 2

    def test_no_runs(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        result = analyzer.analyze([])
        assert result.overall_bias == "neutral"
        assert len(result.warnings) > 0


class TestDeviationAnalyzerOverallBias:
    def test_over_predict_detected(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        reports = [
            _make_report(run_id="r1", signed_mape=100.0),
            _make_report(run_id="r2", signed_mape=150.0),
        ]
        result = analyzer.analyze(reports)
        assert result.overall_bias == "over_predict"
        assert result.overall_signed_mape > 0

    def test_under_predict_detected(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        reports = [
            _make_report(run_id="r1", signed_mape=-80.0),
            _make_report(run_id="r2", signed_mape=-60.0),
        ]
        result = analyzer.analyze(reports)
        assert result.overall_bias == "under_predict"
        assert result.overall_signed_mape < 0

    def test_neutral_detected(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        reports = [
            _make_report(run_id="r1", signed_mape=2.0),
            _make_report(run_id="r2", signed_mape=-1.0),
        ]
        result = analyzer.analyze(reports)
        assert result.overall_bias == "neutral"

    def test_none_signed_mape_skipped(self):
        """Reports with None signed_mape should be skipped."""
        analyzer = DeviationAnalyzer(min_runs=1)
        report_none = BacktestReport(
            run_id="none_report",
            total_cases=1,
            completed_cases=1,
            mae=10.0,
            mape=10.0,
            signed_mape=None,  # None
            rmse=12.0,
            results=[],
        )
        report_valid = _make_report(run_id="r1", signed_mape=50.0)
        result = analyzer.analyze([report_none, report_valid])
        assert result.overall_bias == "over_predict"
        assert result.sample_count == 2

    def test_all_none_signed_mape(self):
        """If all reports have None signed_mape, return neutral."""
        analyzer = DeviationAnalyzer(min_runs=1)
        report = BacktestReport(
            run_id="none_report",
            total_cases=1,
            completed_cases=1,
            mae=10.0,
            mape=10.0,
            signed_mape=None,
            rmse=12.0,
            results=[],
        )
        result = analyzer.analyze([report])
        assert result.overall_bias == "neutral"
        assert len(result.warnings) > 0


class TestDeviationAnalyzerPerMetric:
    def test_per_metric_bias_signals(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        errors = [
            PredictionError("impressions", 400.0, 100.0, 300.0, 300.0, 120.0),
            PredictionError("engagement", 80.0, 50.0, 30.0, 60.0, 46.15),
        ]
        report = _make_report(run_id="r1", signed_mape=80.0, metric_errors=errors)
        result = analyzer.analyze([report])
        assert len(result.per_metric) == 2
        metric_names = [m.metric for m in result.per_metric]
        assert "impressions" in metric_names
        assert "engagement" in metric_names

    def test_per_metric_bias_direction(self):
        analyzer = DeviationAnalyzer(min_runs=1)
        # Over-predicted impressions
        errors = [
            PredictionError("impressions", 400.0, 100.0, 300.0, 300.0, 120.0),
        ]
        report = _make_report(run_id="r1", signed_mape=120.0, metric_errors=errors)
        result = analyzer.analyze([report])
        impressions = next(m for m in result.per_metric if m.metric == "impressions")
        assert impressions.bias_direction == "over_predict"

    def test_per_metric_aggregation_across_runs(self):
        """Per-metric signals should aggregate signed_percentage_error across runs."""
        analyzer = DeviationAnalyzer(min_runs=1)
        errors1 = [PredictionError("impressions", 400.0, 100.0, 300.0, 300.0, 120.0)]
        errors2 = [PredictionError("impressions", 200.0, 100.0, 100.0, 100.0, 66.7)]
        reports = [
            _make_report(run_id="r1", signed_mape=90.0, metric_errors=errors1),
            _make_report(run_id="r2", signed_mape=66.7, metric_errors=errors2),
        ]
        result = analyzer.analyze(reports)
        impressions = next(m for m in result.per_metric if m.metric == "impressions")
        assert impressions.sample_count == 2
        # Average of 120.0 and 66.7
        assert abs(impressions.signed_mape - (120.0 + 66.7) / 2) < 1.0


class TestDeviationReportStructure:
    def test_default_fields(self):
        report = DeviationReport(overall_bias="neutral", overall_signed_mape=0.0)
        assert report.per_metric == []
        assert report.sample_count == 0
        assert report.warnings == []

    def test_bias_signal_fields(self):
        signal = BiasSignal(
            metric="impressions",
            bias_direction="over_predict",
            magnitude=80.0,
            signed_mape=80.0,
            sample_count=3,
        )
        assert signal.metric == "impressions"
        assert signal.bias_direction == "over_predict"
        assert signal.magnitude == 80.0
