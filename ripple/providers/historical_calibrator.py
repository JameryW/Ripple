# ripple/providers/historical_calibrator.py
"""Historical Calibrator — extends HistoricalValidator with percentile baselines,
bucketed comparisons, and structured calibration actions (R4).

While HistoricalValidator only logs deviations, the Calibrator produces
CalibrationAction objects that the runtime can apply to predictions.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calibration Action
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationAction:
    """A structured calibration action produced by the calibrator."""
    action_type: str  # "lower_confidence" | "calibrated_prediction" | "median_adjustment" | "flag_for_review"
    metric: str
    reason: str
    original_value: Optional[float] = None
    calibrated_value: Optional[float] = None
    deviation_pct: Optional[float] = None
    confidence_cap: Optional[str] = None  # "medium" or "low"


@dataclass(frozen=True)
class PercentileBaseline:
    """Historical baseline at multiple percentiles."""
    metric: str
    count: int
    avg: float
    median: float  # P50
    p75: float
    p90: float
    p95: float
    max_val: float


@dataclass(frozen=True)
class CalibratedMetric:
    """Result of calibrating a single metric against historical data."""
    metric: str
    predicted: float
    baseline: Optional[PercentileBaseline] = None
    deviation_from_avg_pct: float = 0.0
    deviation_from_median_pct: float = 0.0
    actions: List[CalibrationAction] = field(default_factory=list)
    within_range: bool = True


@dataclass
class CalibrationReport:
    """Aggregated calibration result with actions."""
    calibrated_metrics: List[CalibratedMetric] = field(default_factory=list)
    actions: List[CalibrationAction] = field(default_factory=list)
    bucket_key: str = ""  # e.g. "platform=xiaohongshu,channel=generic"
    warnings: List[str] = field(default_factory=list)

    @property
    def has_actions(self) -> bool:
        return bool(self.actions)

    def log(self) -> None:
        for cm in self.calibrated_metrics:
            if cm.baseline:
                logger.info(
                    "Calibration — %s: predicted=%.1f, avg=%.1f, P95=%.1f, "
                    "deviation=%+.1f%%, actions=%d",
                    cm.metric,
                    cm.predicted,
                    cm.baseline.avg,
                    cm.baseline.p95,
                    cm.deviation_from_avg_pct,
                    len(cm.actions),
                )
        for a in self.actions:
            logger.warning(
                "Calibration action — %s: %s (%s)",
                a.action_type,
                a.reason,
                a.metric,
            )
        for w in self.warnings:
            logger.warning("Calibration: %s", w)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_values: List[float], pct: float) -> float:
    """Compute percentile from a sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (n - 1) * pct / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


def _compute_baselines(
    values: List[float],
    metric: str,
) -> PercentileBaseline:
    """Compute percentile baselines from a list of historical values."""
    sorted_v = sorted(values)
    return PercentileBaseline(
        metric=metric,
        count=len(values),
        avg=sum(values) / len(values),
        median=_percentile(sorted_v, 50),
        p75=_percentile(sorted_v, 75),
        p90=_percentile(sorted_v, 90),
        p95=_percentile(sorted_v, 95),
        max_val=max(values),
    )


def _extract_numeric_fields(data: Dict[str, Any]) -> Dict[str, float]:
    """Extract top-level numeric fields from a prediction dict."""
    result: Dict[str, float] = {}
    skip = {"step", "tick", "t", "phase", "agent_id", "id", "timestamp"}
    for key, value in data.items():
        if key.lower() in skip:
            continue
        if isinstance(value, (int, float)):
            result[key] = float(value)
    return result


def _extract_metric_from_records(records: List[Dict[str, Any]], metric: str) -> List[float]:
    """Extract a specific metric's numeric values from historical records."""
    values: List[float] = []
    for rec in records:
        if metric in rec:
            val = rec[metric]
            if isinstance(val, (int, float)):
                values.append(float(val))
    return values


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def _build_bucket_key(
    record: Dict[str, Any],
    bucket_fields: List[str],
) -> str:
    """Build a composite bucket key from a record."""
    parts = []
    for f in bucket_fields:
        v = record.get(f)
        if v is not None:
            parts.append(f"{f}={v}")
    return ",".join(parts) if parts else "default"


def _bucket_records(
    records: List[Dict[str, Any]],
    bucket_fields: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Partition records into buckets by specified fields."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        key = _build_bucket_key(rec, bucket_fields)
        buckets.setdefault(key, []).append(rec)
    return buckets


# ---------------------------------------------------------------------------
# HistoricalCalibrator
# ---------------------------------------------------------------------------

class HistoricalCalibrator:
    """Calibrate predictions against historical data with percentile baselines.

    Unlike HistoricalValidator which only logs, this produces CalibrationAction
    objects that the runtime applies.

    Parameters
    ----------
    threshold : float
        Maximum acceptable deviation (%) from avg. Default 100%.
    p95_hard_cap : float
        Deviation above P95 triggers a hard confidence cap to "low". Default 200%.
    bucket_fields : list[str]
        Fields to bucket historical data by. Default: platform, channel, vertical.
    """

    def __init__(
        self,
        threshold: float = 100.0,
        p95_hard_cap: float = 200.0,
        bucket_fields: Optional[List[str]] = None,
    ) -> None:
        self._threshold = threshold
        self._p95_hard_cap = p95_hard_cap
        self._bucket_fields = bucket_fields or ["platform", "channel", "vertical"]

    def calibrate(
        self,
        prediction: Dict[str, Any],
        historical: List[Dict[str, Any]],
        bucket_context: Optional[Dict[str, Any]] = None,
    ) -> CalibrationReport:
        """Calibrate a prediction against historical data.

        If bucket_context is provided, only uses records matching that bucket.
        Otherwise, uses all records.
        """
        warnings: List[str] = []

        if not historical:
            warnings.append("No historical data available for calibration")
            return CalibrationReport(warnings=warnings)

        # Select records for this bucket
        if bucket_context:
            bucket_key = _build_bucket_key(bucket_context, self._bucket_fields)
            bucketed = _bucket_records(historical, self._bucket_fields)
            records = bucketed.get(bucket_key, historical)
            if records is not historical and len(records) < len(historical):
                logger.info(
                    "Calibrator using bucket '%s': %d of %d records",
                    bucket_key, len(records), len(historical),
                )
        else:
            bucket_key = "default"
            records = historical

        numeric_fields = _extract_numeric_fields(prediction)
        calibrated_metrics: List[CalibratedMetric] = []
        all_actions: List[CalibrationAction] = []

        for metric_name, predicted_value in numeric_fields.items():
            hist_values = _extract_metric_from_records(records, metric_name)
            if not hist_values:
                continue

            baseline = _compute_baselines(hist_values, metric_name)

            # Deviation from avg (same as HistoricalValidator for compat)
            dev_avg = (
                ((predicted_value - baseline.avg) / baseline.avg * 100)
                if baseline.avg
                else (0.0 if predicted_value == 0 else float("inf"))
            )

            # Deviation from median
            dev_median = (
                ((predicted_value - baseline.median) / baseline.median * 100)
                if baseline.median
                else (0.0 if predicted_value == 0 else float("inf"))
            )

            actions: List[CalibrationAction] = []

            # Action: deviation exceeds threshold → lower_confidence
            if abs(dev_avg) > self._p95_hard_cap:
                actions.append(CalibrationAction(
                    action_type="lower_confidence",
                    metric=metric_name,
                    reason=f"Deviation {dev_avg:.1f}% >> P95 cap {self._p95_hard_cap:.1f}%",
                    original_value=predicted_value,
                    deviation_pct=round(dev_avg, 2),
                    confidence_cap="low",
                ))
            elif abs(dev_avg) > self._threshold:
                actions.append(CalibrationAction(
                    action_type="lower_confidence",
                    metric=metric_name,
                    reason=f"Deviation {dev_avg:.1f}% > threshold {self._threshold:.1f}%",
                    original_value=predicted_value,
                    deviation_pct=round(dev_avg, 2),
                    confidence_cap="medium",
                ))

            # Action: predicted > P95 → calibrated_prediction
            if baseline.p95 > 0 and predicted_value > baseline.p95:
                actions.append(CalibrationAction(
                    action_type="calibrated_prediction",
                    metric=metric_name,
                    reason=f"Predicted {predicted_value:.1f} exceeds P95 {baseline.p95:.1f}",
                    original_value=predicted_value,
                    calibrated_value=round(baseline.p95, 2),
                    deviation_pct=round(dev_avg, 2),
                ))

            # Action: median < predicted <= P95 → median_adjustment
            elif baseline.median is not None and baseline.p95 > 0 and predicted_value > baseline.median and predicted_value <= baseline.p95:
                actions.append(CalibrationAction(
                    action_type="median_adjustment",
                    metric=metric_name,
                    reason=f"Predicted {predicted_value:.1f} between median {baseline.median:.1f} and P95 {baseline.p95:.1f}",
                    original_value=predicted_value,
                    calibrated_value=round(baseline.median, 2),
                    deviation_pct=round(dev_avg, 2),
                ))

            # Action: extremely optimistic → flag_for_review
            if baseline.p95 > 0 and predicted_value > baseline.p95 * 2:
                actions.append(CalibrationAction(
                    action_type="flag_for_review",
                    metric=metric_name,
                    reason=f"Predicted {predicted_value:.1f} > 2×P95 {baseline.p95:.1f}",
                    original_value=predicted_value,
                    calibrated_value=round(baseline.p95, 2),
                    deviation_pct=round(dev_avg, 2),
                ))

            within = abs(dev_avg) <= self._threshold
            cm = CalibratedMetric(
                metric=metric_name,
                predicted=round(predicted_value, 2),
                baseline=baseline,
                deviation_from_avg_pct=round(dev_avg, 2),
                deviation_from_median_pct=round(dev_median, 2),
                actions=actions,
                within_range=within,
            )
            calibrated_metrics.append(cm)
            all_actions.extend(actions)

        return CalibrationReport(
            calibrated_metrics=calibrated_metrics,
            actions=all_actions,
            bucket_key=bucket_key,
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# 校准反馈集成
# ---------------------------------------------------------------------------

def apply_calibration_feedback(
    bucket_context: Optional[Dict[str, Any]] = None,
    default_threshold: float = 50.0,
    store: Any = None,
) -> float:
    """从 CalibrationDataStore 获取校准后的 historical_threshold_pct。

    供 runtime 调用，将回测反馈闭环的阈值调整注入模拟流程。
    此函数是非致命的 — 任何异常都会被捕获并返回默认阈值。

    Args:
        bucket_context: 分桶上下文（如 {"platform": "xiaohongshu"}）
        default_threshold: 默认阈值
        store: CalibrationDataStore 实例，None 使用默认路径

    Returns:
        校准后的 historical_threshold_pct
    """
    try:
        from ripple.backtest.calibration_feedback import get_calibrated_threshold
        return get_calibrated_threshold(
            bucket_context=bucket_context,
            default=default_threshold,
            store=store,
        )
    except Exception as exc:
        logger.debug("获取校准阈值失败，使用默认值 %.1f%%: %s", default_threshold, exc)
        return default_threshold


def apply_calibrator_feedback(
    bucket_context: Optional[Dict[str, Any]] = None,
    default_threshold: float = 100.0,
    default_p95_hard_cap: float = 200.0,
    store: Any = None,
) -> Dict[str, float]:
    """从 CalibrationDataStore 获取校准后的 HistoricalCalibrator 参数。

    供 runtime._calibrate_historical() 调用，将 Path C 优化闭环的
    threshold 和 p95_hard_cap 注入 HistoricalCalibrator。
    此函数是非致命的 — 任何异常都会被捕获并返回默认参数。

    Args:
        bucket_context: 分桶上下文（如 {"platform": "xiaohongshu"}）
        default_threshold: 默认 threshold
        default_p95_hard_cap: 默认 p95_hard_cap
        store: CalibrationDataStore 实例，None 使用默认路径

    Returns:
        {"threshold": float, "p95_hard_cap": float}
    """
    try:
        from ripple.backtest.calibration_feedback import get_calibrated_calibrator_params
        return get_calibrated_calibrator_params(
            bucket_context=bucket_context,
            default_threshold=default_threshold,
            default_p95_hard_cap=default_p95_hard_cap,
            store=store,
        )
    except Exception as exc:
        logger.debug(
            "获取校准 calibrator 参数失败，使用默认值: %s",
            exc,
        )
        return {"threshold": default_threshold, "p95_hard_cap": default_p95_hard_cap}
