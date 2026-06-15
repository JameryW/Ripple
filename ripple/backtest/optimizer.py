# ripple/backtest/optimizer.py
"""ParameterOptimizer — propose new parameter values from deviation analysis.

Takes a DeviationReport and searches for parameter combinations that would
counteract the detected bias.  Uses a grid-search approach over the 3 tunable
threshold parameters:

- ``threshold`` (HistoricalCalibrator, default 100.0)
- ``p95_hard_cap`` (HistoricalCalibrator, default 200.0)
- ``historical_threshold_pct`` (ConfidenceGate, default 50.0)

Strategy: if bias is "over_predict", lower thresholds (make the system more
conservative).  If "under_predict", raise thresholds.  The magnitude of
adjustment is proportional to the bias magnitude.
"""

from __future__ import annotations

import itertools
import logging
from typing import Dict, List, Optional

from ripple.backtest.analyzer import DeviationReport
from ripple.backtest.schema import OptimizationResult

logger = logging.getLogger(__name__)

# Default tunable parameter values
DEFAULT_PARAMS: Dict[str, float] = {
    "threshold": 100.0,
    "p95_hard_cap": 200.0,
    "historical_threshold_pct": 50.0,
}

# Grid values per dimension — 4 values each (64 combinations total)
_GRID: Dict[str, List[float]] = {
    "threshold": [50.0, 75.0, 100.0, 150.0],
    "p95_hard_cap": [100.0, 150.0, 200.0, 300.0],
    "historical_threshold_pct": [25.0, 37.5, 50.0, 75.0],
}


def _score_candidate(
    candidate: Dict[str, float],
    deviation_report: DeviationReport,
) -> float:
    """Score a candidate parameter set against the deviation report.

    Lower score = better (represents expected residual deviation after
    applying the proposed parameters).

    Logic:
    - If bias is "over_predict": lower thresholds make the system more
      conservative, which should reduce over-prediction.  We compute how
      much the candidate's thresholds deviate from defaults in the
      *corrective* direction, and estimate the improvement.
    - If bias is "under_predict": higher thresholds allow more optimistic
      predictions.  We compute how much the candidate's thresholds
      deviate from defaults in the permissive direction.
    - If "neutral": prefer default values (no correction needed).
    """
    bias = deviation_report.overall_bias
    magnitude = abs(deviation_report.overall_signed_mape)

    if bias == "neutral" or magnitude < 1e-9:
        # Prefer defaults — score by distance from defaults
        dist = sum(
            abs(candidate[k] - DEFAULT_PARAMS[k]) / DEFAULT_PARAMS[k]
            for k in DEFAULT_PARAMS
        )
        return dist

    # Compute a correction factor based on how far the candidate params
    # deviate from defaults in the *corrective* direction.
    correction_score = 0.0

    for param_name, default_val in DEFAULT_PARAMS.items():
        candidate_val = candidate[param_name]

        if bias == "over_predict":
            # Lower thresholds = more conservative = corrects over-prediction
            # Fractional reduction from default
            if candidate_val < default_val:
                reduction = (default_val - candidate_val) / default_val
                correction_score += reduction
            else:
                # Moving wrong direction — penalty
                penalty = (candidate_val - default_val) / default_val
                correction_score -= penalty * 0.5

        elif bias == "under_predict":
            # Higher thresholds = more permissive = corrects under-prediction
            if candidate_val > default_val:
                increase = (candidate_val - default_val) / default_val
                correction_score += increase
            else:
                penalty = (default_val - candidate_val) / default_val
                correction_score -= penalty * 0.5

    # The optimal correction magnitude should be proportional to the
    # observed bias magnitude.  We model this as: the ideal correction
    # is roughly magnitude / 100 (a 100% bias needs full correction).
    ideal_correction = min(magnitude / 100.0, 1.5)  # cap at 150%

    # Score = |actual_correction - ideal_correction| + small penalty for
    # over-correction.  Lower is better.
    score = abs(correction_score - ideal_correction)

    # Add a small penalty for very aggressive changes (prefer conservative adjustments)
    max_change = max(
        abs(candidate[k] - DEFAULT_PARAMS[k]) / DEFAULT_PARAMS[k]
        for k in DEFAULT_PARAMS
    )
    if max_change > 0.5:
        score += max_change * 0.1

    return score


class ParameterOptimizer:
    """Propose new parameter values based on deviation analysis.

    Parameters
    ----------
    grid : dict or None
        Custom grid values for each parameter.  Defaults to ``_GRID``.
    """

    def __init__(
        self,
        grid: Optional[Dict[str, List[float]]] = None,
    ) -> None:
        self._grid = grid or dict(_GRID)

    def optimize(
        self,
        deviation_report: DeviationReport,
        current_params: Optional[Dict[str, float]] = None,
    ) -> OptimizationResult:
        """Find the best parameter set for the detected bias.

        Args:
            deviation_report: Analysis of recent backtest history.
            current_params: Current parameter values (defaults used if None).

        Returns:
            OptimizationResult with proposed params and scoring details.
        """
        warnings: List[str] = []

        if deviation_report.overall_bias == "neutral":
            return OptimizationResult(
                proposed_params=dict(DEFAULT_PARAMS),
                score=0.0,
                improvement_estimate=0.0,
                current_params=dict(current_params or DEFAULT_PARAMS),
                bias_direction="neutral",
                candidates_evaluated=0,
                warnings=["No systematic bias detected; default params recommended."],
            )

        effective_current = dict(current_params or DEFAULT_PARAMS)

        # Generate all candidate combinations
        param_names = list(self._grid.keys())
        param_values = [self._grid[name] for name in param_names]

        best_candidate: Dict[str, float] = dict(DEFAULT_PARAMS)
        best_score = float("inf")
        candidates_evaluated = 0

        for combo in itertools.product(*param_values):
            candidate = dict(zip(param_names, combo))
            score = _score_candidate(candidate, deviation_report)
            candidates_evaluated += 1
            if score < best_score:
                best_score = score
                best_candidate = dict(candidate)

        # Estimate improvement: compare best candidate score to the score
        # of current params
        current_score = _score_candidate(effective_current, deviation_report)
        if current_score > 0:
            improvement_estimate = max(
                0.0, (current_score - best_score) / current_score * 100
            )
        else:
            improvement_estimate = 0.0

        magnitude = abs(deviation_report.overall_signed_mape)
        # Cap improvement estimate at the bias magnitude (can't fix more than 100%)
        improvement_estimate = min(improvement_estimate, magnitude)

        return OptimizationResult(
            proposed_params=best_candidate,
            score=round(best_score, 4),
            improvement_estimate=round(improvement_estimate, 2),
            current_params=effective_current,
            bias_direction=deviation_report.overall_bias,
            candidates_evaluated=candidates_evaluated,
            warnings=warnings,
        )
