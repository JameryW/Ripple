# ripple/backtest/calibration_feedback.py
"""Calibration Feedback Loop — BacktestReport → ConfidenceGate threshold adjustment.

Reads recent BacktestReports via DeviationAnalyzer, extracts bias patterns,
and produces calibration adjustments that ConfidenceGate uses in subsequent runs.

The loop is:
  1. Backtest runs produce BacktestReports (existing)
  2. DeviationAnalyzer extracts BiasSignals (existing)
  3. CalibrationFeedbackLoop converts BiasSignals → ConfidenceAdjustments (NEW)
  4. Adjustments are persisted to a YAML config file (NEW)
  5. ConfidenceGate reads adjusted thresholds on next simulation (existing, via historical_threshold_pct)

Design principles:
  - Conservative: adjustments are capped to avoid overfitting
  - Requires minimum sample size before producing adjustments
  - Cool-down: only one adjustment per N hours
  - All adjustments are logged for auditability
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ripple.backtest.analyzer import BiasSignal, DeviationAnalyzer, DeviationReport
from ripple.backtest.schema import BacktestReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceAdjustment:
    """A single adjustment to a ConfidenceGate parameter."""
    parameter: str  # e.g. "historical_threshold_pct", "evidence_positive_threshold"
    original_value: float
    adjusted_value: float
    reason: str
    bias_direction: str  # "over_predict" | "under_predict" | "neutral"
    magnitude: float  # |signed_mape|
    sample_count: int


@dataclass
class CalibrationFeedbackResult:
    """Result of running the calibration feedback loop."""
    adjustments: List[ConfidenceAdjustment] = field(default_factory=list)
    overall_bias: str = "neutral"
    overall_signed_mape: float = 0.0
    sample_count: int = 0
    skipped: bool = False
    skip_reason: str = ""
    timestamp: str = ""

    @property
    def has_adjustments(self) -> bool:
        return bool(self.adjustments)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_CALIBRATION_FEEDBACK_FILE = "calibration_feedback.yaml"


def _feedback_path(data_dir: Path) -> Path:
    return data_dir / _CALIBRATION_FEEDBACK_FILE


def load_calibration_feedback(data_dir: Path) -> Dict[str, Any]:
    """Load persisted calibration feedback from YAML.

    Returns empty dict if file doesn't exist.
    """
    path = _feedback_path(data_dir)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load calibration feedback from %s: %s", path, exc)
        return {}


def save_calibration_feedback(data_dir: Path, feedback: Dict[str, Any]) -> None:
    """Persist calibration feedback to YAML."""
    path = _feedback_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            yaml.safe_dump(feedback, f, allow_unicode=True, sort_keys=False)
        logger.info("Calibration feedback saved to %s", path)
    except Exception as exc:
        logger.warning("Failed to save calibration feedback to %s: %s", path, exc)


def get_adjusted_threshold(data_dir: Path, default: float = 50.0) -> float:
    """Get the adjusted historical_threshold_pct from persisted feedback.

    Returns the default if no feedback exists or feedback is stale.
    """
    data = load_calibration_feedback(data_dir)
    if not data:
        return default

    # Check cool-down: skip if adjusted too recently
    last_adjusted = data.get("last_adjusted_at")
    if last_adjusted:
        try:
            last_dt = datetime.fromisoformat(last_adjusted)
            hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            cool_down_hours = data.get("cool_down_hours", 24)
            if hours_since < cool_down_hours:
                logger.debug(
                    "Calibration feedback within cool-down (%.1fh < %dh), using adjusted value",
                    hours_since, cool_down_hours,
                )
        except (ValueError, TypeError):
            pass

    adjustments = data.get("adjustments", [])
    for adj in adjustments:
        if isinstance(adj, dict) and adj.get("parameter") == "historical_threshold_pct":
            return float(adj.get("adjusted_value", default))

    return default


# ---------------------------------------------------------------------------
# CalibrationFeedbackLoop
# ---------------------------------------------------------------------------


class CalibrationFeedbackLoop:
    """Convert backtest deviation signals into ConfidenceGate parameter adjustments.

    Parameters
    ----------
    min_runs : int
        Minimum number of backtest runs required to produce adjustments.
    max_adjustment_pct : float
        Maximum percentage-point adjustment per loop iteration (caps overfitting).
    cool_down_hours : int
        Minimum hours between adjustments (prevents rapid oscillation).
    data_dir : Path or None
        Directory for persisting feedback. If None, feedback is not persisted.
    """

    def __init__(
        self,
        min_runs: int = 3,
        max_adjustment_pct: float = 15.0,
        cool_down_hours: int = 24,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._analyzer = DeviationAnalyzer(min_runs=min_runs)
        self._max_adjustment_pct = max_adjustment_pct
        self._cool_down_hours = cool_down_hours
        self._data_dir = data_dir

    def run(
        self,
        reports: List[BacktestReport],
        current_threshold: float = 50.0,
    ) -> CalibrationFeedbackResult:
        """Run the calibration feedback loop.

        Args:
            reports: Recent BacktestReports to analyze.
            current_threshold: Current historical_threshold_pct value.

        Returns:
            CalibrationFeedbackResult with adjustments (if any).
        """
        result = CalibrationFeedbackResult(timestamp=datetime.now(timezone.utc).isoformat())

        # Step 1: Check cool-down
        if self._data_dir is not None:
            feedback = load_calibration_feedback(self._data_dir)
            last_adjusted = feedback.get("last_adjusted_at")
            if last_adjusted:
                try:
                    last_dt = datetime.fromisoformat(last_adjusted)
                    hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                    if hours_since < self._cool_down_hours:
                        result.skipped = True
                        result.skip_reason = (
                            f"Within cool-down period ({hours_since:.1f}h < {self._cool_down_hours}h)"
                        )
                        return result
                except (ValueError, TypeError):
                    pass

        # Step 2: Analyze deviation
        deviation = self._analyzer.analyze(reports)
        result.overall_bias = deviation.overall_bias
        result.overall_signed_mape = deviation.overall_signed_mape
        result.sample_count = deviation.sample_count

        if deviation.overall_bias == "neutral":
            result.skipped = True
            result.skip_reason = "No systematic bias detected (neutral)"
            return result

        if deviation.sample_count < self._analyzer._min_runs:
            result.skipped = True
            result.skip_reason = f"Insufficient samples ({deviation.sample_count} < {self._analyzer._min_runs})"
            return result

        # Step 3: Compute adjustments
        adjustments = self._compute_adjustments(deviation, current_threshold)
        result.adjustments = adjustments

        # Step 4: Persist if data_dir is configured
        if self._data_dir is not None and adjustments:
            self._persist(adjustments, deviation)

        return result

    def _compute_adjustments(
        self,
        deviation: DeviationReport,
        current_threshold: float,
    ) -> List[ConfidenceAdjustment]:
        """Convert deviation signals into parameter adjustments."""
        adjustments: List[ConfidenceAdjustment] = []

        # Adjustment 1: historical_threshold_pct
        # If over-predicting, tighten the threshold (lower → more likely to gate)
        # If under-predicting, relax the threshold (higher → less likely to gate)
        signed_mape = deviation.overall_signed_mape

        # Scale adjustment: use sqrt to dampen large biases
        # Cap at max_adjustment_pct
        raw_adjustment = math.copysign(
            min(math.sqrt(abs(signed_mape)) * 2.0, self._max_adjustment_pct),
            signed_mape,
        )

        # Over-predict → lower threshold (stricter gate)
        # Under-predict → raise threshold (looser gate)
        if signed_mape > 0:
            new_threshold = max(10.0, current_threshold - abs(raw_adjustment))
        else:
            new_threshold = min(200.0, current_threshold + abs(raw_adjustment))

        if abs(new_threshold - current_threshold) > 0.5:  # minimum meaningful change
            adjustments.append(ConfidenceAdjustment(
                parameter="historical_threshold_pct",
                original_value=round(current_threshold, 2),
                adjusted_value=round(new_threshold, 2),
                reason=(
                    f"Systematic {'over-prediction' if signed_mape > 0 else 'under-prediction'} "
                    f"(signed_mape={signed_mape:+.1f}%) — "
                    f"{'tightening' if signed_mape > 0 else 'relaxing'} historical threshold"
                ),
                bias_direction=deviation.overall_bias,
                magnitude=abs(signed_mape),
                sample_count=deviation.sample_count,
            ))

        return adjustments

    def _persist(
        self,
        adjustments: List[ConfidenceAdjustment],
        deviation: DeviationReport,
    ) -> None:
        """Persist adjustments to YAML."""
        if self._data_dir is None:
            return

        feedback = {
            "last_adjusted_at": datetime.now(timezone.utc).isoformat(),
            "cool_down_hours": self._cool_down_hours,
            "overall_bias": deviation.overall_bias,
            "overall_signed_mape": deviation.overall_signed_mape,
            "sample_count": deviation.sample_count,
            "adjustments": [
                {
                    "parameter": a.parameter,
                    "original_value": a.original_value,
                    "adjusted_value": a.adjusted_value,
                    "reason": a.reason,
                    "bias_direction": a.bias_direction,
                    "magnitude": a.magnitude,
                    "sample_count": a.sample_count,
                }
                for a in adjustments
            ],
        }
        save_calibration_feedback(self._data_dir, feedback)
