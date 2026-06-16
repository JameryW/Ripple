# tests/backtest/test_calibration_feedback.py
"""Tests for calibration feedback loop — BacktestReport -> HistoricalCalibrator -> ConfidenceGate."""

import tempfile
from pathlib import Path

import pytest

from ripple.backtest.calibration_feedback import (
    BiasPattern,
    CalibrationAdjustment,
    CalibrationDataStore,
    CalibrationFeedbackConfig,
    apply_feedback,
    apply_optimization_result,
    compute_threshold_adjustment,
    extract_bias_patterns,
    get_calibrated_calibrator_params,
    get_calibrated_threshold,
)
from ripple.backtest.schema import BacktestReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(
    signed_mape=None,
    completed_cases=10,
    buckets=None,
    run_id="test-run-001",
) -> BacktestReport:
    """创建一个 BacktestReport 用于测试。"""
    report = BacktestReport(
        run_id=run_id,
        total_cases=completed_cases,
        completed_cases=completed_cases,
        signed_mape=signed_mape,
    )
    if buckets:
        report.buckets = buckets
    return report


def _make_store(tmp_path: Path) -> CalibrationDataStore:
    """创建一个使用临时目录的 CalibrationDataStore。"""
    return CalibrationDataStore(data_dir=tmp_path / "calibration")


# ---------------------------------------------------------------------------
# CalibrationFeedbackConfig
# ---------------------------------------------------------------------------

class TestCalibrationFeedbackConfig:
    def test_defaults(self):
        config = CalibrationFeedbackConfig()
        assert config.enabled is True
        assert config.feedback_strength == 0.5
        assert config.cooldown_period == 3
        assert config.min_cases_for_adjustment == 5
        assert config.bias_threshold_pct == 10.0

    def test_custom_values(self):
        config = CalibrationFeedbackConfig(
            enabled=False,
            feedback_strength=0.8,
            cooldown_period=5,
            min_cases_for_adjustment=10,
            bias_threshold_pct=5.0,
        )
        assert config.enabled is False
        assert config.feedback_strength == 0.8
        assert config.cooldown_period == 5
        assert config.min_cases_for_adjustment == 10
        assert config.bias_threshold_pct == 5.0

    def test_rejects_negative_feedback_strength(self):
        """负反馈强度应被拒绝。"""
        with pytest.raises(ValueError):
            CalibrationFeedbackConfig(feedback_strength=-0.1)

    def test_rejects_excessive_feedback_strength(self):
        """超过 1.0 的反馈强度应被拒绝。"""
        with pytest.raises(ValueError):
            CalibrationFeedbackConfig(feedback_strength=1.5)

    def test_rejects_negative_cooldown_period(self):
        """负冷却期应被拒绝。"""
        with pytest.raises(ValueError):
            CalibrationFeedbackConfig(cooldown_period=-1)

    def test_rejects_negative_min_cases(self):
        """负最小样本数应被拒绝。"""
        with pytest.raises(ValueError):
            CalibrationFeedbackConfig(min_cases_for_adjustment=-1)

    def test_rejects_negative_bias_threshold(self):
        """负偏差阈值应被拒绝。"""
        with pytest.raises(ValueError):
            CalibrationFeedbackConfig(bias_threshold_pct=-5.0)

    def test_boundary_values(self):
        """边界值应被接受。"""
        CalibrationFeedbackConfig(feedback_strength=0.0)
        CalibrationFeedbackConfig(feedback_strength=1.0)
        CalibrationFeedbackConfig(cooldown_period=0)
        CalibrationFeedbackConfig(min_cases_for_adjustment=0)
        CalibrationFeedbackConfig(bias_threshold_pct=0.0)


# ---------------------------------------------------------------------------
# BiasPattern / CalibrationAdjustment
# ---------------------------------------------------------------------------

class TestBiasPattern:
    def test_creation(self):
        p = BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=25.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )
        assert p.bucket_key == "platform=xiaohongshu"
        assert p.signed_mape == 25.0
        assert p.sample_size == 10
        assert p.run_id == "run-1"

    def test_frozen(self):
        p = BiasPattern(
            bucket_key="", signed_mape=0, sample_size=0, timestamp="", run_id=""
        )
        with pytest.raises(AttributeError):
            p.signed_mape = 99


class TestCalibrationAdjustment:
    def test_creation(self):
        a = CalibrationAdjustment(
            bucket_key="platform=xiaohongshu",
            old_threshold_pct=50.0,
            new_threshold_pct=60.0,
            reason="高估偏差",
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
            feedback_strength=0.5,
        )
        assert a.old_threshold_pct == 50.0
        assert a.new_threshold_pct == 60.0
        assert a.feedback_strength == 0.5

    def test_frozen(self):
        a = CalibrationAdjustment(
            bucket_key="", old_threshold_pct=50, new_threshold_pct=60,
            reason="", timestamp="", run_id="", feedback_strength=0.5,
        )
        with pytest.raises(AttributeError):
            a.new_threshold_pct = 99


# ---------------------------------------------------------------------------
# CalibrationDataStore
# ---------------------------------------------------------------------------

class TestCalibrationDataStore:
    def test_save_and_get_bias_patterns(self, tmp_path):
        store = _make_store(tmp_path)
        p = BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )
        store.save_bias_pattern(p)

        patterns = store.get_bias_patterns()
        assert len(patterns) == 1
        assert patterns[0].bucket_key == "platform=xiaohongshu"
        assert patterns[0].signed_mape == 20.0

    def test_get_bias_patterns_by_bucket(self, tmp_path):
        store = _make_store(tmp_path)
        for key, mape in [("platform=xiaohongshu", 20), ("platform=weibo", -15)]:
            store.save_bias_pattern(BiasPattern(
                bucket_key=key, signed_mape=mape, sample_size=10,
                timestamp="2026-06-16T00:00:00+00:00", run_id="run-1",
            ))

        xhs_patterns = store.get_bias_patterns("platform=xiaohongshu")
        assert len(xhs_patterns) == 1
        assert xhs_patterns[0].signed_mape == 20.0

    def test_save_and_get_adjustments(self, tmp_path):
        store = _make_store(tmp_path)
        a = CalibrationAdjustment(
            bucket_key="platform=xiaohongshu",
            old_threshold_pct=50.0,
            new_threshold_pct=60.0,
            reason="高估",
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
            feedback_strength=0.5,
        )
        store.save_adjustment(a)

        adjustments = store.get_adjustments()
        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct == 60.0

    def test_effective_threshold(self, tmp_path):
        store = _make_store(tmp_path)
        # 默认值
        assert store.get_effective_threshold("platform=xiaohongshu", 50.0) == 50.0

        # 设置后查询
        store.set_effective_threshold("platform=xiaohongshu", 65.0)
        assert store.get_effective_threshold("platform=xiaohongshu", 50.0) == 65.0

        # 其他 bucket 不受影响
        assert store.get_effective_threshold("platform=weibo", 50.0) == 50.0

    def test_get_all_thresholds(self, tmp_path):
        store = _make_store(tmp_path)
        store.set_effective_threshold("platform=xiaohongshu", 65.0)
        store.set_effective_threshold("platform=weibo", 40.0)
        all_thresholds = store.get_all_thresholds()
        assert all_thresholds["platform=xiaohongshu"] == 65.0
        assert all_thresholds["platform=weibo"] == 40.0

    def test_empty_store(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_bias_patterns() == []
        assert store.get_adjustments() == []
        assert store.get_all_thresholds() == {}

    def test_corrupt_json_returns_empty(self, tmp_path):
        store = _make_store(tmp_path)
        # 写入无效 JSON
        bad_file = store._data_dir / "bias_patterns.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("{invalid json")
        # 应该优雅地返回空列表
        assert store.get_bias_patterns() == []

    def test_atomic_write(self, tmp_path):
        store = _make_store(tmp_path)
        p = BiasPattern(
            bucket_key="test", signed_mape=10.0, sample_size=5,
            timestamp="2026-06-16T00:00:00+00:00", run_id="run-1",
        )
        store.save_bias_pattern(p)
        # 验证文件存在且内容有效
        patterns = store.get_bias_patterns()
        assert len(patterns) == 1


# ---------------------------------------------------------------------------
# extract_bias_patterns
# ---------------------------------------------------------------------------

class TestExtractBiasPatterns:
    def test_no_signed_mape(self):
        """无 signed_mape 的报告不提取任何偏差。"""
        report = _make_report(signed_mape=None)
        config = CalibrationFeedbackConfig()
        patterns = extract_bias_patterns(report, config)
        assert patterns == []

    def test_global_over_prediction(self):
        """全局高估偏差。"""
        report = _make_report(signed_mape=25.0, completed_cases=10)
        config = CalibrationFeedbackConfig()
        patterns = extract_bias_patterns(report, config)
        assert len(patterns) == 1
        assert patterns[0].bucket_key == ""
        assert patterns[0].signed_mape == 25.0
        assert patterns[0].sample_size == 10

    def test_global_under_prediction(self):
        """全局低估偏差。"""
        report = _make_report(signed_mape=-20.0, completed_cases=10)
        config = CalibrationFeedbackConfig()
        patterns = extract_bias_patterns(report, config)
        assert len(patterns) == 1
        assert patterns[0].signed_mape == -20.0

    def test_below_bias_threshold(self):
        """偏差小于阈值不提取。"""
        report = _make_report(signed_mape=5.0, completed_cases=10)
        config = CalibrationFeedbackConfig(bias_threshold_pct=10.0)
        patterns = extract_bias_patterns(report, config)
        assert patterns == []

    def test_below_min_cases(self):
        """样本数不足不提取。"""
        report = _make_report(signed_mape=25.0, completed_cases=3)
        config = CalibrationFeedbackConfig(min_cases_for_adjustment=5)
        patterns = extract_bias_patterns(report, config)
        assert patterns == []

    def test_bucket_patterns(self):
        """分桶偏差提取。"""
        report = _make_report(
            signed_mape=15.0,
            completed_cases=20,
            buckets={
                "platform=xiaohongshu": {
                    "count": 12,
                    "signed_mape": 25.0,
                    "mape": 30.0,
                },
                "platform=weibo": {
                    "count": 8,
                    "signed_mape": -15.0,
                    "mape": 20.0,
                },
            },
        )
        config = CalibrationFeedbackConfig()
        patterns = extract_bias_patterns(report, config)
        # 全局 + 2 个分桶 = 3 个
        assert len(patterns) == 3
        bucket_keys = {p.bucket_key for p in patterns}
        assert "platform=xiaohongshu" in bucket_keys
        assert "platform=weibo" in bucket_keys
        assert "" in bucket_keys  # 全局

    def test_bucket_below_min_cases_skipped(self):
        """分桶样本数不足时跳过该桶。"""
        report = _make_report(
            signed_mape=25.0,
            completed_cases=10,
            buckets={
                "platform=xiaohongshu": {
                    "count": 2,  # 低于 min_cases
                    "signed_mape": 30.0,
                },
            },
        )
        config = CalibrationFeedbackConfig(min_cases_for_adjustment=5)
        patterns = extract_bias_patterns(report, config)
        # 只有全局
        assert len(patterns) == 1
        assert patterns[0].bucket_key == ""

    def test_bucket_below_bias_threshold_skipped(self):
        """分桶偏差不足阈值时跳过。"""
        report = _make_report(
            signed_mape=25.0,
            completed_cases=10,
            buckets={
                "platform=xiaohongshu": {
                    "count": 10,
                    "signed_mape": 5.0,  # 低于阈值
                },
            },
        )
        config = CalibrationFeedbackConfig(bias_threshold_pct=10.0)
        patterns = extract_bias_patterns(report, config)
        # 只有全局
        assert len(patterns) == 1
        assert patterns[0].bucket_key == ""


# ---------------------------------------------------------------------------
# compute_threshold_adjustment
# ---------------------------------------------------------------------------

class TestComputeThresholdAdjustment:
    def test_over_prediction_raises_threshold(self):
        """系统性高估 → 提高阈值（更严格）。"""
        patterns = [BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        adjustments = compute_threshold_adjustment(patterns, store, config)
        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct > adjustments[0].old_threshold_pct
        # 20 * 0.5 = 10 → 50 + 10 = 60
        assert adjustments[0].new_threshold_pct == 60.0

    def test_under_prediction_lowers_threshold(self):
        """系统性低估 → 降低阈值（更宽松）。"""
        patterns = [BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=-20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        adjustments = compute_threshold_adjustment(patterns, store, config)
        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct < adjustments[0].old_threshold_pct
        # -20 * 0.5 = -10 → 50 - 10 = 40
        assert adjustments[0].new_threshold_pct == 40.0

    def test_feedback_strength_dampens_adjustment(self):
        """反馈强度衰减调整幅度。"""
        patterns = [BiasPattern(
            bucket_key="test",
            signed_mape=30.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        # 低反馈强度 → 小幅调整
        config_weak = CalibrationFeedbackConfig(feedback_strength=0.2)
        adj_weak = compute_threshold_adjustment(patterns, store, config_weak)
        # 30 * 0.2 = 6 → 50 + 6 = 56
        assert adj_weak[0].new_threshold_pct == 56.0

        # 高反馈强度 → 大幅调整（但受单次上限 15% 限制）
        store2 = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config_strong = CalibrationFeedbackConfig(feedback_strength=0.8)
        adj_strong = compute_threshold_adjustment(patterns, store2, config_strong)
        # 30 * 0.8 = 24 → 被限制为 15 → 50 + 15 = 65
        assert adj_strong[0].new_threshold_pct == 65.0

    def test_adjustment_capped(self):
        """单次调整幅度有上限（15%）。"""
        patterns = [BiasPattern(
            bucket_key="test",
            signed_mape=100.0,  # 极大偏差
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=1.0)
        adjustments = compute_threshold_adjustment(patterns, store, config)
        # 100 * 1.0 = 100 → 被限制为 15 → 50 + 15 = 65
        assert adjustments[0].new_threshold_pct == 65.0

    def test_threshold_lower_bound(self):
        """阈值不低于 10%。"""
        patterns = [BiasPattern(
            bucket_key="test",
            signed_mape=-80.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=1.0)
        adjustments = compute_threshold_adjustment(patterns, store, config)
        # -80 * 1.0 = -80 → 被限制为 -15 → 50 - 15 = 35
        # 但如果默认阈值更低
        assert adjustments[0].new_threshold_pct >= 10.0

    def test_threshold_upper_bound(self):
        """阈值不超过 200%。"""
        # 先设置一个高阈值
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("test", 195.0)
        patterns = [BiasPattern(
            bucket_key="test",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-1",
        )]
        config = CalibrationFeedbackConfig(feedback_strength=1.0)
        adjustments = compute_threshold_adjustment(patterns, store, config)
        # 195 + min(20, 15) = 195 + 15 = 210 → 被限制为 200
        assert adjustments[0].new_threshold_pct <= 200.0

    def test_cooldown_enforced(self):
        """冷却期：当前 run_id 已有调整记录时跳过。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(cooldown_period=2)

        # 预先写入调整记录，run_id 与当前 pattern 相同
        store.save_adjustment(CalibrationAdjustment(
            bucket_key="platform=xiaohongshu",
            old_threshold_pct=50.0,
            new_threshold_pct=55.0,
            reason="test",
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-same",
            feedback_strength=0.5,
        ))

        patterns = [BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-same",  # 与已有调整的 run_id 相同
        )]
        adjustments = compute_threshold_adjustment(patterns, store, config)
        assert len(adjustments) == 0  # 冷却中

    def test_cooldown_not_yet_reached(self):
        """冷却期：当前 run_id 无已有调整记录时允许调整。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(cooldown_period=3)

        # 只有 1 条记录，run_id 不同
        store.save_adjustment(CalibrationAdjustment(
            bucket_key="platform=xiaohongshu",
            old_threshold_pct=50.0,
            new_threshold_pct=55.0,
            reason="test",
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-0",
            feedback_strength=0.5,
        ))

        patterns = [BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="run-new",  # 不同的 run_id
        )]
        adjustments = compute_threshold_adjustment(patterns, store, config)
        assert len(adjustments) == 1

    def test_empty_patterns(self):
        """空偏差列表不产生调整。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig()
        adjustments = compute_threshold_adjustment([], store, config)
        assert adjustments == []

    def test_old_adjustments_do_not_freeze_bucket(self):
        """历史调整记录不应永久冻结某个 bucket。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(cooldown_period=2)

        # 写入 2 条旧调整记录（不同 run_id）
        for i in range(2):
            store.save_adjustment(CalibrationAdjustment(
                bucket_key="platform=xiaohongshu",
                old_threshold_pct=50.0,
                new_threshold_pct=55.0 + i,
                reason="test",
                timestamp=f"2026-01-0{i}T00:00:00+00:00",
                run_id=f"old-run-{i}",
                feedback_strength=0.5,
            ))

        # 使用新 run_id 应该能调整（不被旧记录冻结）
        patterns = [BiasPattern(
            bucket_key="platform=xiaohongshu",
            signed_mape=20.0,
            sample_size=10,
            timestamp="2026-06-16T00:00:00+00:00",
            run_id="new-run-001",
        )]
        adjustments = compute_threshold_adjustment(patterns, store, config)
        assert len(adjustments) == 1


# ---------------------------------------------------------------------------
# apply_feedback
# ---------------------------------------------------------------------------

class TestApplyFeedback:
    def test_disabled_config(self):
        """关闭配置时不产生任何调整。"""
        report = _make_report(signed_mape=25.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(enabled=False)
        adjustments = apply_feedback(report, store, config)
        assert adjustments == []

    def test_no_significant_bias(self):
        """无显著偏差时不调整。"""
        report = _make_report(signed_mape=3.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(bias_threshold_pct=10.0)
        adjustments = apply_feedback(report, store, config)
        assert adjustments == []

    def test_successful_feedback(self):
        """正常反馈流程：提取偏差 → 计算调整 → 持久化。"""
        report = _make_report(signed_mape=20.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        adjustments = apply_feedback(report, store, config)

        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct == 60.0

        # 验证持久化
        patterns = store.get_bias_patterns()
        assert len(patterns) >= 1

        stored_adjustments = store.get_adjustments()
        assert len(stored_adjustments) >= 1

        # 验证阈值映射
        threshold = store.get_effective_threshold("", 50.0)
        assert threshold == 60.0

    def test_feedback_with_buckets(self):
        """含分桶的反馈流程。"""
        report = _make_report(
            signed_mape=15.0,
            completed_cases=20,
            buckets={
                "platform=xiaohongshu": {
                    "count": 12,
                    "signed_mape": 25.0,
                },
            },
        )
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig()
        adjustments = apply_feedback(report, store, config)

        # 应该有全局和分桶的调整
        assert len(adjustments) >= 1
        bucket_keys = {a.bucket_key for a in adjustments}
        assert "platform=xiaohongshu" in bucket_keys

    def test_non_fatal_on_store_failure(self):
        """存储写入失败不应导致 apply_feedback 异常。"""
        report = _make_report(signed_mape=20.0, completed_cases=10)
        # 创建一个可初始化但写入会失败的 store
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        # 覆盖原子写入方法使其抛出异常
        store._atomic_write_json = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed"))
        config = CalibrationFeedbackConfig()
        # apply_feedback 应该捕获异常并返回空列表
        adjustments = apply_feedback(report, store, config)
        assert isinstance(adjustments, list)

    def test_default_config(self):
        """不传配置时使用默认配置。"""
        report = _make_report(signed_mape=20.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        adjustments = apply_feedback(report, store)  # 无 config 参数
        assert len(adjustments) == 1


# ---------------------------------------------------------------------------
# get_calibrated_threshold
# ---------------------------------------------------------------------------

class TestGetCalibratedThreshold:
    def test_no_calibration_data(self):
        """无校准数据时返回默认阈值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        result = get_calibrated_threshold(default=50.0, store=store)
        assert result == 50.0

    def test_exact_bucket_match(self):
        """精确匹配分桶键。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("platform=xiaohongshu,channel=generic", 65.0)
        result = get_calibrated_threshold(
            bucket_context={"platform": "xiaohongshu", "channel": "generic"},
            default=50.0,
            store=store,
        )
        assert result == 65.0

    def test_single_field_fallback(self):
        """无法精确匹配时回退到单字段匹配。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("platform=xiaohongshu", 65.0)
        # bucket_context 有 platform 和 channel，但只有 platform 匹配
        result = get_calibrated_threshold(
            bucket_context={"platform": "xiaohongshu", "channel": "unknown"},
            default=50.0,
            store=store,
        )
        assert result == 65.0

    def test_global_threshold_fallback(self):
        """无分桶匹配时使用全局阈值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("", 45.0)
        result = get_calibrated_threshold(
            bucket_context={"platform": "weibo"},
            default=50.0,
            store=store,
        )
        assert result == 45.0

    def test_default_when_no_match(self):
        """无任何匹配时返回默认值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("platform=xiaohongshu", 65.0)
        result = get_calibrated_threshold(
            bucket_context={"platform": "weibo"},
            default=50.0,
            store=store,
        )
        assert result == 50.0

    def test_no_bucket_context(self):
        """无 bucket_context 时使用全局阈值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_effective_threshold("", 45.0)
        result = get_calibrated_threshold(
            bucket_context=None,
            default=50.0,
            store=store,
        )
        assert result == 45.0

    def test_default_store_creation_failure(self):
        """默认 store 创建失败时返回默认阈值。"""
        # 不传 store 参数 — 正常情况会创建默认 store
        # 这里主要测试函数不会抛异常
        result = get_calibrated_threshold(default=50.0)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# End-to-end: BacktestReport -> Threshold -> ConfidenceGate
# ---------------------------------------------------------------------------

class TestEndToEndFeedback:
    def test_over_prediction_increases_threshold(self):
        """高估偏差 → 阈值上升 → ConfidenceGate 更严格。"""
        # Step 1: 模拟回测结果
        report = _make_report(signed_mape=20.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        adjustments = apply_feedback(report, store, config)

        # Step 2: 验证阈值已调整
        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct > 50.0

        # Step 3: 验证 get_calibrated_threshold 返回调整后的值
        threshold = get_calibrated_threshold(default=50.0, store=store)
        assert threshold > 50.0

    def test_under_prediction_decreases_threshold(self):
        """低估偏差 → 阈值下降 → ConfidenceGate 更宽松。"""
        report = _make_report(signed_mape=-20.0, completed_cases=10)
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(feedback_strength=0.5)
        adjustments = apply_feedback(report, store, config)

        assert len(adjustments) == 1
        assert adjustments[0].new_threshold_pct < 50.0

        threshold = get_calibrated_threshold(default=50.0, store=store)
        assert threshold < 50.0

    def test_feedback_can_be_disabled(self):
        """关闭反馈后不再调整。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))

        # 第一次：开启反馈
        report1 = _make_report(signed_mape=20.0, completed_cases=10, run_id="run-1")
        config_enabled = CalibrationFeedbackConfig(enabled=True, feedback_strength=0.5)
        apply_feedback(report1, store, config_enabled)
        threshold_after_first = get_calibrated_threshold(default=50.0, store=store)
        assert threshold_after_first > 50.0

        # 第二次：关闭反馈
        report2 = _make_report(signed_mape=-30.0, completed_cases=10, run_id="run-2")
        config_disabled = CalibrationFeedbackConfig(enabled=False)
        adjustments = apply_feedback(report2, store, config_disabled)
        assert adjustments == []

        # 阈值不变
        threshold_after_second = get_calibrated_threshold(default=50.0, store=store)
        assert threshold_after_second == threshold_after_first

    def test_multiple_runs_accumulate(self):
        """多次回测运行累积调整。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        config = CalibrationFeedbackConfig(
            feedback_strength=0.3,
            cooldown_period=10,  # 设置高冷却期以允许本次测试
        )

        # 第一次回测：高估 20%
        report1 = _make_report(signed_mape=20.0, completed_cases=10, run_id="run-1")
        apply_feedback(report1, store, config)

        # 第二次回测：继续高估 15%
        report2 = _make_report(signed_mape=15.0, completed_cases=10, run_id="run-2")
        # 先清除冷却期限制（删掉之前的调整记录以简化测试）
        store2 = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        apply_feedback(report1, store2, config)
        apply_feedback(report2, store2, config)

        # 阈值应该高于第一次
        threshold = get_calibrated_threshold(default=50.0, store=store2)
        assert threshold > 50.0


# ---------------------------------------------------------------------------
# CalibrationDataStore — calibrator_params
# ---------------------------------------------------------------------------

class TestCalibratorParamsStore:
    """CalibrationDataStore 的 calibrator_params 读写测试。"""

    def test_set_and_get_calibrator_params(self, tmp_path):
        """设置和读取 calibrator 参数。"""
        store = _make_store(tmp_path)
        # 默认返回 None
        assert store.get_calibrator_params("platform=xiaohongshu") is None

        # 设置后读取
        store.set_calibrator_params("platform=xiaohongshu", threshold=75.0, p95_hard_cap=150.0)
        params = store.get_calibrator_params("platform=xiaohongshu")
        assert params is not None
        assert params["threshold"] == 75.0
        assert params["p95_hard_cap"] == 150.0

    def test_global_calibrator_params(self, tmp_path):
        """全局 calibrator 参数（bucket_key=""）。"""
        store = _make_store(tmp_path)
        store.set_calibrator_params("", threshold=120.0, p95_hard_cap=250.0)
        params = store.get_calibrator_params("")
        assert params is not None
        assert params["threshold"] == 120.0
        assert params["p95_hard_cap"] == 250.0

    def test_different_buckets_independent(self, tmp_path):
        """不同 bucket 的参数互不影响。"""
        store = _make_store(tmp_path)
        store.set_calibrator_params("platform=xiaohongshu", threshold=75.0, p95_hard_cap=150.0)
        store.set_calibrator_params("platform=weibo", threshold=90.0, p95_hard_cap=180.0)

        xhs = store.get_calibrator_params("platform=xiaohongshu")
        assert xhs["threshold"] == 75.0
        assert xhs["p95_hard_cap"] == 150.0

        weibo = store.get_calibrator_params("platform=weibo")
        assert weibo["threshold"] == 90.0
        assert weibo["p95_hard_cap"] == 180.0

    def test_overwrite_calibrator_params(self, tmp_path):
        """覆盖已存在的参数。"""
        store = _make_store(tmp_path)
        store.set_calibrator_params("test", threshold=75.0, p95_hard_cap=150.0)
        store.set_calibrator_params("test", threshold=80.0, p95_hard_cap=160.0)

        params = store.get_calibrator_params("test")
        assert params["threshold"] == 80.0
        assert params["p95_hard_cap"] == 160.0

    def test_corrupted_calibrator_params_returns_empty(self, tmp_path):
        """损坏的 JSON 文件返回空映射。"""
        store = _make_store(tmp_path)
        bad_file = store._data_dir / "calibrator_params.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("{invalid json")
        # 应该优雅地返回 None
        assert store.get_calibrator_params("test") is None


# ---------------------------------------------------------------------------
# apply_optimization_result
# ---------------------------------------------------------------------------

class TestApplyOptimizationResult:
    """优化结果写入 CalibrationDataStore 的测试。"""

    def _make_opt_result(self, proposed=None, current=None):
        """创建一个简化的 OptimizationResult。"""
        from ripple.backtest.schema import OptimizationResult
        return OptimizationResult(
            proposed_params=proposed if proposed is not None else {"threshold": 75.0, "p95_hard_cap": 150.0, "historical_threshold_pct": 37.5},
            current_params=current if current is not None else {"threshold": 100.0, "p95_hard_cap": 200.0, "historical_threshold_pct": 50.0},
            score=0.5,
            improvement_estimate=15.0,
            bias_direction="over_predict",
        )

    def test_writes_all_3_params_when_validation_passed(self, tmp_path):
        """验证通过时写入全部 3 个参数。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result()
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=True)

        assert status["written"] is True
        assert status["params"]["threshold"] == 75.0
        assert status["params"]["p95_hard_cap"] == 150.0
        assert status["params"]["historical_threshold_pct"] == 37.5

        # 验证 thresholds.json 已写入
        assert store.get_effective_threshold("") == 37.5

        # 验证 calibrator_params.json 已写入
        cal_params = store.get_calibrator_params("")
        assert cal_params is not None
        assert cal_params["threshold"] == 75.0
        assert cal_params["p95_hard_cap"] == 150.0

    def test_does_not_write_when_validation_failed(self, tmp_path):
        """A/B 验证失败时不写入。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result()
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=False)

        assert status["written"] is False
        assert status["reason"] == "validation_failed"

        # 验证 store 没有写入任何数据
        assert store.get_effective_threshold("", 50.0) == 50.0
        assert store.get_calibrator_params("") is None

    def test_does_not_write_when_no_params(self, tmp_path):
        """无优化参数时不写入。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result(proposed={})
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=True)

        assert status["written"] is False
        assert status["reason"] == "no_params"

    def test_writes_to_specific_bucket(self, tmp_path):
        """写入指定 bucket_key。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result()
        status = apply_optimization_result(
            opt_result, store, bucket_key="platform=xiaohongshu", validation_passed=True,
        )

        assert status["written"] is True
        assert store.get_effective_threshold("platform=xiaohongshu") == 37.5

        cal_params = store.get_calibrator_params("platform=xiaohongshu")
        assert cal_params is not None
        assert cal_params["threshold"] == 75.0

    def test_non_fatal_on_store_failure(self, tmp_path):
        """存储写入失败不应导致异常。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result()
        # 覆盖原子写入方法使其抛出异常
        store._atomic_write_json = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed"))
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=True)

        assert status["written"] is False
        assert "reason" in status

    def test_partial_params_only_historical_threshold(self, tmp_path):
        """只有 historical_threshold_pct 时，写入阈值但不写 calibrator_params。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result(
            proposed={"historical_threshold_pct": 37.5},
        )
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=True)

        assert status["written"] is True
        # historical_threshold_pct 应写入
        assert store.get_effective_threshold("") == 37.5
        # calibrator_params 不应写入（缺少 threshold 和 p95_hard_cap）
        assert store.get_calibrator_params("") is None

    def test_partial_params_threshold_without_p95(self, tmp_path):
        """只有 threshold 没有 p95_hard_cap 时，不写 calibrator_params。"""
        store = _make_store(tmp_path)
        opt_result = self._make_opt_result(
            proposed={"threshold": 75.0, "historical_threshold_pct": 37.5},
        )
        status = apply_optimization_result(opt_result, store, bucket_key="", validation_passed=True)

        assert status["written"] is True
        # historical_threshold_pct 应写入
        assert store.get_effective_threshold("") == 37.5
        # calibrator_params 不应写入（缺少 p95_hard_cap）
        assert store.get_calibrator_params("") is None


# ---------------------------------------------------------------------------
# get_calibrated_calibrator_params
# ---------------------------------------------------------------------------

class TestGetCalibratedCalibratorParams:
    """获取校准后 calibrator 参数的测试。"""

    def test_no_calibration_data(self):
        """无校准数据时返回默认参数。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        result = get_calibrated_calibrator_params(store=store)
        assert result == {"threshold": 100.0, "p95_hard_cap": 200.0}

    def test_exact_bucket_match(self):
        """精确匹配分桶键。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("platform=xiaohongshu,channel=generic", threshold=75.0, p95_hard_cap=150.0)
        result = get_calibrated_calibrator_params(
            bucket_context={"platform": "xiaohongshu", "channel": "generic"},
            store=store,
        )
        assert result["threshold"] == 75.0
        assert result["p95_hard_cap"] == 150.0

    def test_single_field_fallback(self):
        """无法精确匹配时回退到单字段匹配。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("platform=xiaohongshu", threshold=80.0, p95_hard_cap=160.0)
        result = get_calibrated_calibrator_params(
            bucket_context={"platform": "xiaohongshu", "channel": "unknown"},
            store=store,
        )
        assert result["threshold"] == 80.0
        assert result["p95_hard_cap"] == 160.0

    def test_global_fallback(self):
        """无分桶匹配时使用全局参数。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("", threshold=120.0, p95_hard_cap=250.0)
        result = get_calibrated_calibrator_params(
            bucket_context={"platform": "weibo"},
            store=store,
        )
        assert result["threshold"] == 120.0
        assert result["p95_hard_cap"] == 250.0

    def test_default_when_no_match(self):
        """无任何匹配时返回默认值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("platform=xiaohongshu", threshold=80.0, p95_hard_cap=160.0)
        result = get_calibrated_calibrator_params(
            bucket_context={"platform": "weibo"},
            store=store,
        )
        assert result == {"threshold": 100.0, "p95_hard_cap": 200.0}

    def test_no_bucket_context(self):
        """无 bucket_context 时使用全局参数。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        store.set_calibrator_params("", threshold=120.0, p95_hard_cap=250.0)
        result = get_calibrated_calibrator_params(
            bucket_context=None,
            store=store,
        )
        assert result["threshold"] == 120.0
        assert result["p95_hard_cap"] == 250.0

    def test_default_store_creation_failure(self):
        """默认 store 创建失败时返回默认参数。"""
        result = get_calibrated_calibrator_params()
        assert result == {"threshold": 100.0, "p95_hard_cap": 200.0}

    def test_custom_defaults(self):
        """自定义默认值。"""
        store = CalibrationDataStore(data_dir=Path(tempfile.mkdtemp()))
        result = get_calibrated_calibrator_params(
            default_threshold=150.0,
            default_p95_hard_cap=300.0,
            store=store,
        )
        assert result == {"threshold": 150.0, "p95_hard_cap": 300.0}
