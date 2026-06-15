# ripple/backtest/validator.py
"""A/B validation and rollback for parameter optimization.

Runs backtests with old and new parameter sets, compares metrics, and
triggers rollback if the new parameters degrade any metric by more than
10%.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ripple.backtest.schema import (
    BacktestCase,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# Degradation threshold: if any metric worsens by more than this %,
# rollback is triggered.
_DEGRADATION_THRESHOLD_PCT = 10.0


def _compute_mape_change(old_val: Optional[float], new_val: Optional[float]) -> Optional[float]:
    """Compute percentage change from old to new value.

    Returns None if either value is None or old_val is 0.
    Positive = new is worse (higher MAPE), Negative = improvement.
    """
    if old_val is None or new_val is None or old_val == 0:
        return None
    return (new_val - old_val) / abs(old_val) * 100


class ABValidator:
    """Validate proposed parameters by running A/B backtest comparison.

    Parameters
    ----------
    degradation_threshold_pct : float
        Maximum acceptable metric degradation (in %) before triggering
        rollback.  Default 10%.
    """

    def __init__(
        self,
        degradation_threshold_pct: float = _DEGRADATION_THRESHOLD_PCT,
    ) -> None:
        self._degradation_threshold = degradation_threshold_pct

    async def validate(
        self,
        cases: List[BacktestCase],
        simulate_fn: Callable[..., Awaitable[Dict[str, Any]]],
        old_params: Dict[str, float],
        new_params: Dict[str, float],
    ) -> ValidationResult:
        """Run A/B validation: backtest with old vs new params.

        Args:
            cases: Backtest cases to run against.
            simulate_fn: Async callable for simulation.
            old_params: Current (baseline) parameter set.
            new_params: Proposed (optimized) parameter set.

        Returns:
            ValidationResult with comparison metrics and pass/fail status.
        """
        from ripple.backtest.runner import run_backtest

        warnings: List[str] = []

        # Run baseline with old params
        try:
            old_report = await run_backtest(
                cases, simulate_fn, params_snapshot=old_params,
            )
        except Exception as exc:
            logger.warning("Baseline backtest failed: %s", exc)
            return ValidationResult(
                old_params=old_params,
                new_params=new_params,
                passed=False,
                degraded_metrics=["baseline_failed"],
                warnings=[f"Baseline backtest failed: {exc}"],
            )

        # Check if baseline had all cases fail (runner catches per-case exceptions)
        if old_report.failed_cases == old_report.total_cases:
            return ValidationResult(
                old_params=old_params,
                new_params=new_params,
                passed=False,
                degraded_metrics=["baseline_failed"],
                warnings=["Baseline backtest: all cases failed."],
            )

        # Run trial with new params
        try:
            new_report = await run_backtest(
                cases, simulate_fn, params_snapshot=new_params,
            )
        except Exception as exc:
            logger.warning("Trial backtest failed: %s", exc)
            return ValidationResult(
                old_params=old_params,
                new_params=new_params,
                old_mape=old_report.mape,
                old_signed_mape=old_report.signed_mape,
                passed=False,
                degraded_metrics=["trial_failed"],
                warnings=[f"Trial backtest failed: {exc}"],
            )

        # Check if trial had all cases fail
        if new_report.failed_cases == new_report.total_cases:
            return ValidationResult(
                old_params=old_params,
                new_params=new_params,
                old_mape=old_report.mape,
                old_signed_mape=old_report.signed_mape,
                passed=False,
                degraded_metrics=["trial_failed"],
                warnings=["Trial backtest: all cases failed."],
            )

        # Compare metrics
        mape_change = _compute_mape_change(old_report.mape, new_report.mape)

        degraded_metrics: List[str] = []

        # Check MAPE degradation
        if mape_change is not None and mape_change > self._degradation_threshold:
            degraded_metrics.append(
                f"mape: +{mape_change:.1f}%"
            )

        # Check signed_mape degradation: for signed_mape, "worse" depends
        # on direction.  Increase in absolute value = degradation.
        if (
            old_report.signed_mape is not None
            and new_report.signed_mape is not None
        ):
            old_abs = abs(old_report.signed_mape)
            new_abs = abs(new_report.signed_mape)
            if old_abs > 0:
                abs_change_pct = (new_abs - old_abs) / old_abs * 100
                if abs_change_pct > self._degradation_threshold:
                    degraded_metrics.append(
                        f"signed_mape: +{abs_change_pct:.1f}% (absolute)"
                    )

        # Check MAE degradation
        mae_change = _compute_mape_change(old_report.mae, new_report.mae)
        if mae_change is not None and mae_change > self._degradation_threshold:
            degraded_metrics.append(
                f"mae: +{mae_change:.1f}%"
            )

        # Check RMSE degradation
        rmse_change = _compute_mape_change(old_report.rmse, new_report.rmse)
        if rmse_change is not None and rmse_change > self._degradation_threshold:
            degraded_metrics.append(
                f"rmse: +{rmse_change:.1f}%"
            )

        passed = len(degraded_metrics) == 0

        if degraded_metrics:
            warnings.append(
                f"Metrics degraded beyond {self._degradation_threshold}%: "
                + "; ".join(degraded_metrics)
            )

        return ValidationResult(
            old_params=old_params,
            new_params=new_params,
            old_mape=old_report.mape,
            new_mape=new_report.mape,
            old_signed_mape=old_report.signed_mape,
            new_signed_mape=new_report.signed_mape,
            mape_change_pct=round(mape_change, 2) if mape_change is not None else None,
            passed=passed,
            degraded_metrics=degraded_metrics,
            rolled_back=False,  # Rollback is a separate explicit action
            warnings=warnings,
        )

    def should_rollback(self, result: ValidationResult) -> bool:
        """Determine whether rollback should be triggered.

        Rollback is triggered when:
        - Validation did NOT pass (some metric degraded > threshold)
        - No prior rollback has already occurred
        """
        return not result.passed and not result.rolled_back

    def rollback(self, old_params: Dict[str, float]) -> Dict[str, float]:
        """Restore previous parameter snapshot.

        Returns a copy of the old params to restore.

        Args:
            old_params: The parameter set to restore.

        Returns:
            Copy of old_params (the restored values).
        """
        logger.info(
            "Rolling back to previous parameters: %s", old_params,
        )
        return dict(old_params)
