# tests/backtest/test_calibration_feedback.py
"""Tests for CalibrationFeedbackLoop — backtest → ConfidenceGate threshold adjustment."""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from ripple.backtest.calibration_feedback import (
    CalibrationFeedbackLoop,
    CalibrationFeedbackResult,
    ConfidenceAdjustment,
    load_calibration_feedback,
    save_calibration_feedback,
    get_adjusted_threshold,
)
from ripple.backtest.schema import BacktestReport


def _make_report(signed_mape: float, **kwargs) -> BacktestReport:
    """Create a minimal BacktestReport with given signed_mape."""
    return BacktestReport(
        signed_mape=signed_mape,
        completed_cases=1,
        total_cases=1,
        **kwargs,
    )


class TestCalibrationFeedbackLoop:
    def test_no_adjustment_when_neutral(self):
        """No adjustment when signed_mape is within neutral range."""
        reports = [_make_report(3.0), _make_report(-2.0), _make_report(4.0)]
        loop = CalibrationFeedbackLoop(min_runs=2)
        result = loop.run(reports, current_threshold=50.0)
        assert result.skipped
        assert "neutral" in result.skip_reason.lower() or "No systematic bias" in result.skip_reason

    def test_over_predict_lowers_threshold(self):
        """Over-prediction bias should lower historical_threshold_pct."""
        reports = [_make_report(30.0), _make_report(25.0), _make_report(35.0)]
        loop = CalibrationFeedbackLoop(min_runs=2, max_adjustment_pct=15.0)
        result = loop.run(reports, current_threshold=50.0)
        assert result.has_adjustments
        adj = result.adjustments[0]
        assert adj.parameter == "historical_threshold_pct"
        assert adj.adjusted_value < adj.original_value
        assert adj.bias_direction == "over_predict"

    def test_under_predict_raises_threshold(self):
        """Under-prediction bias should raise historical_threshold_pct."""
        reports = [_make_report(-30.0), _make_report(-25.0), _make_report(-35.0)]
        loop = CalibrationFeedbackLoop(min_runs=2, max_adjustment_pct=15.0)
        result = loop.run(reports, current_threshold=50.0)
        assert result.has_adjustments
        adj = result.adjustments[0]
        assert adj.parameter == "historical_threshold_pct"
        assert adj.adjusted_value > adj.original_value
        assert adj.bias_direction == "under_predict"

    def test_insufficient_runs_skips(self):
        """Below min_runs, no adjustment is made."""
        reports = [_make_report(30.0)]
        loop = CalibrationFeedbackLoop(min_runs=3)
        result = loop.run(reports, current_threshold=50.0)
        assert result.skipped

    def test_max_adjustment_capped(self):
        """Adjustment is capped at max_adjustment_pct."""
        # Very large bias (100%) — should cap
        reports = [_make_report(100.0), _make_report(90.0), _make_report(110.0)]
        loop = CalibrationFeedbackLoop(min_runs=2, max_adjustment_pct=10.0)
        result = loop.run(reports, current_threshold=50.0)
        assert result.has_adjustments
        adj = result.adjustments[0]
        assert abs(adj.adjusted_value - adj.original_value) <= 10.0

    def test_threshold_lower_bound(self):
        """Threshold should not go below 10.0."""
        reports = [_make_report(100.0)] * 5
        loop = CalibrationFeedbackLoop(min_runs=2, max_adjustment_pct=50.0)
        result = loop.run(reports, current_threshold=15.0)
        if result.has_adjustments:
            adj = result.adjustments[0]
            assert adj.adjusted_value >= 10.0

    def test_threshold_upper_bound(self):
        """Threshold should not exceed 200.0."""
        reports = [_make_report(-100.0)] * 5
        loop = CalibrationFeedbackLoop(min_runs=2, max_adjustment_pct=50.0)
        result = loop.run(reports, current_threshold=180.0)
        if result.has_adjustments:
            adj = result.adjustments[0]
            assert adj.adjusted_value <= 200.0


class TestCalibrationFeedbackPersistence:
    def test_save_and_load(self, tmp_path: Path):
        """Save and load feedback YAML."""
        feedback = {
            "last_adjusted_at": datetime.now(timezone.utc).isoformat(),
            "adjustments": [
                {
                    "parameter": "historical_threshold_pct",
                    "original_value": 50.0,
                    "adjusted_value": 40.0,
                    "reason": "Systematic over-prediction",
                    "bias_direction": "over_predict",
                    "magnitude": 30.0,
                    "sample_count": 3,
                }
            ],
        }
        save_calibration_feedback(tmp_path, feedback)
        loaded = load_calibration_feedback(tmp_path)
        assert loaded["adjustments"][0]["adjusted_value"] == 40.0

    def test_load_missing_file(self, tmp_path: Path):
        """Missing file returns empty dict."""
        loaded = load_calibration_feedback(tmp_path / "nonexistent")
        assert loaded == {}

    def test_get_adjusted_threshold(self, tmp_path: Path):
        """get_adjusted_threshold returns adjusted value from persisted feedback."""
        feedback = {
            "last_adjusted_at": datetime.now(timezone.utc).isoformat(),
            "cool_down_hours": 24,
            "adjustments": [
                {
                    "parameter": "historical_threshold_pct",
                    "original_value": 50.0,
                    "adjusted_value": 42.0,
                    "reason": "test",
                    "bias_direction": "over_predict",
                    "magnitude": 25.0,
                    "sample_count": 3,
                }
            ],
        }
        save_calibration_feedback(tmp_path, feedback)
        result = get_adjusted_threshold(tmp_path, default=50.0)
        assert result == 42.0

    def test_get_adjusted_threshold_no_feedback(self, tmp_path: Path):
        """No feedback file → return default."""
        result = get_adjusted_threshold(tmp_path, default=50.0)
        assert result == 50.0

    def test_cool_down_prevents_rapid_re_adjustment(self, tmp_path: Path):
        """Cool-down prevents running adjustments too frequently."""
        # Save feedback with recent timestamp
        feedback = {
            "last_adjusted_at": datetime.now(timezone.utc).isoformat(),
            "cool_down_hours": 24,
            "adjustments": [],
        }
        save_calibration_feedback(tmp_path, feedback)

        # Run loop with same data_dir — should skip due to cool-down
        reports = [_make_report(30.0), _make_report(25.0), _make_report(35.0)]
        loop = CalibrationFeedbackLoop(min_runs=2, cool_down_hours=24, data_dir=tmp_path)
        result = loop.run(reports, current_threshold=50.0)
        assert result.skipped
        assert "cool-down" in result.skip_reason.lower()

    def test_persist_on_adjustment(self, tmp_path: Path):
        """Loop persists adjustments to data_dir."""
        reports = [_make_report(30.0), _make_report(25.0), _make_report(35.0)]
        loop = CalibrationFeedbackLoop(min_runs=2, data_dir=tmp_path)
        result = loop.run(reports, current_threshold=50.0)

        if result.has_adjustments:
            loaded = load_calibration_feedback(tmp_path)
            assert "adjustments" in loaded
            assert loaded["adjustments"][0]["parameter"] == "historical_threshold_pct"
