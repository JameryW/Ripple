"""Post-hoc validation of LLM SYNTHESIZE predictions against provider historical data.

Compares predicted metrics (views, shares, engagement) with historical baselines.
Logs deviations — never modifies the LLM output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricDeviation:
    """Deviation of a single predicted metric from historical baseline."""

    metric: str
    predicted: float
    historical_avg: float
    historical_max: float
    deviation_pct: float  # (predicted - avg) / avg * 100

    @property
    def is_acceptable(self, threshold: float = 100.0) -> bool:
        if self.historical_avg == 0:
            return self.predicted == 0
        return abs(self.deviation_pct) <= threshold


@dataclass
class HistoricalValidationReport:
    """Aggregated validation result."""

    metric_deviations: List[MetricDeviation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_acceptable(self) -> bool:
        return all(d.is_acceptable for d in self.metric_deviations)

    def log(self) -> None:
        for d in self.metric_deviations:
            logger.info(
                "Historical validation — %s: predicted=%.1f, historical_avg=%.1f, deviation=%+.1f%%",
                d.metric,
                d.predicted,
                d.historical_avg,
                d.deviation_pct,
            )
        unacceptable = [d for d in self.metric_deviations if not d.is_acceptable]
        if unacceptable:
            names = ", ".join(d.metric for d in unacceptable)
            logger.warning(
                "Historical validation — metrics exceeding threshold: %s",
                names,
            )
        for w in self.warnings:
            logger.warning("Historical validation: %s", w)


class HistoricalValidator:
    """Post-hoc validation of SYNTHESIZE predictions against historical data.

    Parameters
    ----------
    threshold : float
        Maximum acceptable deviation (%) for any metric. Default 100%.
    """

    def __init__(self, threshold: float = 100.0) -> None:
        self._threshold = threshold

    def validate(
        self,
        prediction: Dict[str, Any],
        historical: List[Dict[str, Any]],
    ) -> HistoricalValidationReport:
        """Compare a single LLM prediction with historical baseline records."""
        deviations: List[MetricDeviation] = []
        warnings: List[str] = []

        if not historical:
            warnings.append("No historical data available for validation")
            return HistoricalValidationReport(metric_deviations=deviations, warnings=warnings)

        # Extract numeric metrics from prediction
        numeric_fields = _extract_numeric_fields(prediction)

        # Compute historical baselines for matching fields
        for metric_name, predicted_value in numeric_fields.items():
            hist_values = _extract_metric_from_records(historical, metric_name)
            if not hist_values:
                continue

            avg = sum(hist_values) / len(hist_values)
            max_val = max(hist_values)
            dev = ((predicted_value - avg) / avg * 100) if avg else (0.0 if predicted_value == 0 else float("inf"))

            deviations.append(MetricDeviation(
                metric=metric_name,
                predicted=round(predicted_value, 2),
                historical_avg=round(avg, 2),
                historical_max=round(max_val, 2),
                deviation_pct=round(dev, 2),
            ))

        return HistoricalValidationReport(
            metric_deviations=deviations,
            warnings=warnings,
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
