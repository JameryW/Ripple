# tests/providers/test_historical_calibrator.py
"""Tests for HistoricalCalibrator — R4 percentile baselines, bucketing, and calibration actions."""

import tempfile
from pathlib import Path

from ripple.providers.historical_calibrator import (
    CalibrationAction,
    CalibrationReport,
    HistoricalCalibrator,
    apply_calibration_feedback,
    apply_calibrator_feedback,
    _build_bucket_key,
    _bucket_records,
    _compute_baselines,
    _percentile,
)


# ---------------------------------------------------------------------------
# Percentile computation
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_single_value(self):
        assert _percentile([10.0], 50) == 10.0

    def test_two_values(self):
        assert _percentile([10.0, 20.0], 50) == 15.0

    def test_p50_median(self):
        values = [1, 2, 3, 4, 5]
        assert _percentile(values, 50) == 3.0

    def test_p75(self):
        values = [1, 2, 3, 4, 5]
        assert _percentile(values, 75) == 4.0

    def test_p95(self):
        values = list(range(1, 21))
        assert _percentile(values, 95) == 19.05

    def test_empty(self):
        assert _percentile([], 50) == 0.0


class TestComputeBaselines:
    def test_basic(self):
        bl = _compute_baselines([100, 200, 300, 400, 500], "views")
        assert bl.metric == "views"
        assert bl.count == 5
        assert bl.avg == 300.0
        assert bl.max_val == 500.0
        assert bl.p95 > 0


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


class TestBucketing:
    def test_build_bucket_key(self):
        rec = {"platform": "xiaohongshu", "channel": "generic"}
        assert _build_bucket_key(rec, ["platform", "channel"]) == "platform=xiaohongshu,channel=generic"

    def test_missing_field(self):
        rec = {"platform": "weibo"}
        assert _build_bucket_key(rec, ["platform", "vertical"]) == "platform=weibo"

    def test_bucket_records(self):
        records = [
            {"platform": "weibo", "views": 100},
            {"platform": "xiaohongshu", "views": 200},
            {"platform": "weibo", "views": 150},
        ]
        buckets = _bucket_records(records, ["platform"])
        assert len(buckets) == 2
        assert len(buckets["platform=weibo"]) == 2
        assert len(buckets["platform=xiaohongshu"]) == 1


# ---------------------------------------------------------------------------
# HistoricalCalibrator
# ---------------------------------------------------------------------------


class TestHistoricalCalibrator:
    def setup_method(self):
        self.calibrator = HistoricalCalibrator()

    def test_no_historical_data(self):
        report = self.calibrator.calibrate({"views": 500}, [])
        assert len(report.warnings) > 0
        assert not report.has_actions

    def test_within_threshold(self):
        historical = [{"views": 100}, {"views": 200}, {"views": 150}]
        report = self.calibrator.calibrate({"views": 160}, historical)
        # No lower_confidence actions — deviation is within threshold
        assert not any(a.action_type == "lower_confidence" for a in report.actions)
        # May still have median_adjustment if predicted > median and <= P95
        assert all(cm.within_range for cm in report.calibrated_metrics)

    def test_deviation_exceeds_threshold(self):
        historical = [{"views": 100}, {"views": 120}, {"views": 110}]
        report = self.calibrator.calibrate({"views": 500}, historical)
        assert report.has_actions
        # Should have a lower_confidence action
        assert any(a.action_type == "lower_confidence" for a in report.actions)

    def test_exceeds_p95_hard_cap(self):
        historical = [{"views": 100}, {"views": 150}, {"views": 200}]
        cal = HistoricalCalibrator(threshold=100, p95_hard_cap=200)
        report = cal.calibrate({"views": 800}, historical)
        # 800 vs avg ~150 → deviation > 400% → hard cap → confidence "low"
        assert any(
            a.action_type == "lower_confidence" and a.confidence_cap == "low"
            for a in report.actions
        )

    def test_calibrated_prediction_action(self):
        historical = [{"views": 100}, {"views": 200}, {"views": 300}, {"views": 400}, {"views": 500}]
        report = self.calibrator.calibrate({"views": 600}, historical)
        # 600 > P95 → calibrated_prediction action
        assert any(a.action_type == "calibrated_prediction" for a in report.actions)

    def test_flag_for_review_action(self):
        historical = [{"views": 100}, {"views": 150}, {"views": 200}]
        report = self.calibrator.calibrate({"views": 1000}, historical)
        # 1000 >> 2×P95 → flag_for_review
        assert any(a.action_type == "flag_for_review" for a in report.actions)

    def test_bucketed_calibration(self):
        historical = [
            {"platform": "weibo", "views": 100},
            {"platform": "weibo", "views": 200},
            {"platform": "xiaohongshu", "views": 500},
            {"platform": "xiaohongshu", "views": 600},
        ]
        bucket_context = {"platform": "weibo"}
        report = self.calibrator.calibrate(
            {"views": 500}, historical, bucket_context=bucket_context
        )
        assert report.bucket_key == "platform=weibo"
        assert report.has_actions  # 500 vs weibo avg ~150 → exceeds threshold

    def test_percentile_baselines_present(self):
        historical = [{"views": 100}, {"views": 200}, {"views": 300}]
        report = self.calibrator.calibrate({"views": 150}, historical)
        for cm in report.calibrated_metrics:
            if cm.baseline is not None:
                assert cm.baseline.median > 0
                assert cm.baseline.p75 > 0
                assert cm.baseline.p90 > 0

    def test_empty_prediction(self):
        historical = [{"views": 100}]
        report = self.calibrator.calibrate({}, historical)
        assert not report.has_actions

    def test_median_adjustment_action_generated(self):
        """When predicted is between median and P95, a median_adjustment action is generated."""
        historical = [{"views": 100}, {"views": 200}, {"views": 300}, {"views": 400}, {"views": 500}]
        report = self.calibrator.calibrate({"views": 350}, historical)
        # 350 is between median(300) and P95(~500) — should get median_adjustment
        action_types = [a.action_type for a in report.actions]
        assert "median_adjustment" in action_types
        adj = next(a for a in report.actions if a.action_type == "median_adjustment")
        assert adj.metric == "views"
        assert adj.original_value == 350.0
        assert adj.calibrated_value is not None

    def test_no_median_adjustment_when_predicted_below_median(self):
        """When predicted <= median, no median_adjustment action."""
        historical = [{"views": 100}, {"views": 200}, {"views": 300}, {"views": 400}, {"views": 500}]
        report = self.calibrator.calibrate({"views": 200}, historical)
        action_types = [a.action_type for a in report.actions]
        assert "median_adjustment" not in action_types

    def test_no_median_adjustment_when_predicted_above_p95(self):
        """When predicted > P95, calibrated_prediction (not median_adjustment) is generated."""
        historical = [{"views": 100}, {"views": 200}, {"views": 300}, {"views": 400}, {"views": 500}]
        report = self.calibrator.calibrate({"views": 600}, historical)
        action_types = [a.action_type for a in report.actions]
        assert "median_adjustment" not in action_types
        assert "calibrated_prediction" in action_types


class TestCalibrationAction:
    def test_creation(self):
        a = CalibrationAction(
            action_type="lower_confidence",
            metric="views",
            reason="Deviation > 100%",
            original_value=500,
            confidence_cap="medium",
        )
        assert a.action_type == "lower_confidence"
        assert a.confidence_cap == "medium"


class TestCalibrationReport:
    def test_has_actions_false_when_empty(self):
        report = CalibrationReport()
        assert not report.has_actions

    def test_has_actions_true(self):
        report = CalibrationReport(actions=[
            CalibrationAction(action_type="lower_confidence", metric="views", reason="test")
        ])
        assert report.has_actions


# ---------------------------------------------------------------------------
# apply_calibration_feedback
# ---------------------------------------------------------------------------

class TestApplyCalibrationFeedback:
    """Test apply_calibration_feedback function that bridges backtest feedback to runtime."""

    def test_returns_default_when_no_data(self):
        """无校准数据时返回默认阈值。"""
        result = apply_calibration_feedback(
            bucket_context=None,
            default_threshold=50.0,
        )
        assert result == 50.0

    def test_returns_calibrated_threshold(self):
        """有校准数据时返回调整后的阈值。"""
        from ripple.backtest.calibration_feedback import (
            CalibrationDataStore,
            CalibrationFeedbackConfig,
        )
        from ripple.backtest.schema import BacktestReport

        # 创建 store 并写入校准数据
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        report = BacktestReport(
            signed_mape=20.0,
            completed_cases=10,
            total_cases=10,
        )
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        from ripple.backtest.calibration_feedback import apply_feedback
        apply_feedback(report, store, config)

        # 传入同一个 store 实例，验证校准后的阈值
        result = apply_calibration_feedback(
            bucket_context=None,
            default_threshold=50.0,
            store=store,
        )
        assert result == 60.0  # 50 + 20 * 0.5 = 60

    def test_with_bucket_context(self):
        """带 bucket_context 时正常返回。"""
        result = apply_calibration_feedback(
            bucket_context={"platform": "xiaohongshu"},
            default_threshold=50.0,
        )
        assert isinstance(result, float)

    def test_non_fatal_on_failure(self):
        """异常时返回默认阈值。"""
        # 传入异常的 bucket_context 类型
        result = apply_calibration_feedback(
            bucket_context="invalid",
            default_threshold=50.0,
        )
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# apply_calibrator_feedback
# ---------------------------------------------------------------------------

class TestApplyCalibratorFeedback:
    """Test apply_calibrator_feedback function for Path C integration."""

    def test_returns_defaults_when_no_data(self):
        """无校准数据时返回默认参数。"""
        result = apply_calibrator_feedback(
            bucket_context=None,
        )
        assert result == {"threshold": 100.0, "p95_hard_cap": 200.0}

    def test_returns_calibrated_params(self):
        """有校准数据时返回调整后的参数。"""
        from ripple.backtest.calibration_feedback import (
            CalibrationDataStore,
        )

        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("", threshold=75.0, p95_hard_cap=150.0)

        result = apply_calibrator_feedback(
            bucket_context=None,
            default_threshold=100.0,
            default_p95_hard_cap=200.0,
            store=store,
        )
        assert result["threshold"] == 75.0
        assert result["p95_hard_cap"] == 150.0

    def test_with_bucket_context(self):
        """带 bucket_context 时正常返回。"""
        result = apply_calibrator_feedback(
            bucket_context={"platform": "xiaohongshu"},
        )
        assert isinstance(result, dict)
        assert "threshold" in result
        assert "p95_hard_cap" in result

    def test_non_fatal_on_failure(self):
        """异常时返回默认参数。"""
        result = apply_calibrator_feedback(
            bucket_context="invalid",
        )
        assert isinstance(result, dict)
        assert result == {"threshold": 100.0, "p95_hard_cap": 200.0}
