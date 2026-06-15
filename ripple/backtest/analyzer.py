# ripple/backtest/analyzer.py
"""DeviationAnalyzer — detect systematic prediction bias from backtest history.

Reads persisted BacktestReports and computes per-metric bias signals
(positive = over-predict, negative = under-predict) that the
ParameterOptimizer can act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

from ripple.backtest.schema import BacktestReport

logger = logging.getLogger(__name__)


@dataclass
class BiasSignal:
    """Systematic bias detected for a single metric."""
    metric: str
    bias_direction: str  # "over_predict" | "under_predict" | "neutral"
    magnitude: float  # absolute signed_mape value
    signed_mape: float  # positive=over-predict, negative=under-predict
    sample_count: int  # number of runs contributing to this signal


@dataclass
class DeviationReport:
    """Aggregated deviation analysis across recent backtest history."""
    overall_bias: str  # "over_predict" | "under_predict" | "neutral"
    overall_signed_mape: float
    per_metric: List[BiasSignal] = field(default_factory=list)
    sample_count: int = 0
    warnings: List[str] = field(default_factory=list)


class DeviationAnalyzer:
    """Analyze persisted backtest history for systematic bias.

    Parameters
    ----------
    min_runs : int
        Minimum number of past runs required to produce a signal.
        Below this threshold the analyzer returns a neutral report.
    """

    def __init__(self, min_runs: int = 2) -> None:
        self._min_runs = min_runs

    def analyze(self, reports: List[BacktestReport]) -> DeviationReport:
        """Compute bias signals from a list of BacktestReports.

        Args:
            reports: Recent backtest reports, typically from
                     ``BacktestStore.query_recent()``.

        Returns:
            DeviationReport with overall and per-metric bias signals.
        """
        warnings: List[str] = []

        if len(reports) < self._min_runs:
            warnings.append(
                f"Only {len(reports)} runs available (minimum {self._min_runs})"
            )
            return DeviationReport(
                overall_bias="neutral",
                overall_signed_mape=0.0,
                warnings=warnings,
            )

        # Aggregate overall signed_mape across runs
        signed_mapes = [
            r.signed_mape for r in reports
            if r.signed_mape is not None
        ]
        if not signed_mapes:
            warnings.append("No signed_mape data in recent runs")
            return DeviationReport(
                overall_bias="neutral",
                overall_signed_mape=0.0,
                sample_count=len(reports),
                warnings=warnings,
            )

        avg_signed_mape = sum(signed_mapes) / len(signed_mapes)
        overall_bias = _classify_bias(avg_signed_mape)

        # Per-metric analysis from individual case errors
        metric_errors: Dict[str, List[float]] = {}
        for report in reports:
            for result in report.results:
                for err in result.errors:
                    if err.signed_percentage_error is not None:
                        metric_errors.setdefault(err.metric, []).append(
                            err.signed_percentage_error
                        )

        per_metric: List[BiasSignal] = []
        for metric_name, errors in sorted(metric_errors.items()):
            avg_err = sum(errors) / len(errors)
            per_metric.append(BiasSignal(
                metric=metric_name,
                bias_direction=_classify_bias(avg_err),
                magnitude=abs(avg_err),
                signed_mape=round(avg_err, 2),
                sample_count=len(errors),
            ))

        return DeviationReport(
            overall_bias=overall_bias,
            overall_signed_mape=round(avg_signed_mape, 2),
            per_metric=per_metric,
            sample_count=len(reports),
            warnings=warnings,
        )


def _classify_bias(signed_mape: float, threshold: float = 5.0) -> str:
    if signed_mape > threshold:
        return "over_predict"
    elif signed_mape < -threshold:
        return "under_predict"
    return "neutral"
