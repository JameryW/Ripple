# ripple/backtest/runner.py
"""Offline backtest runner — R7.

Runs predictions against backtest cases without seeing ground truth,
then compares predictions to outcomes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Awaitable, Dict, List, Optional

from ripple.backtest.schema import BacktestCase, BacktestResult, BacktestReport
from ripple.backtest.metrics import (
    compute_numeric_metrics,
    compute_grade_metrics,
    compute_confidence_calibration,
    compute_prediction_errors,
    compute_brier_score,
)

logger = logging.getLogger(__name__)


async def run_backtest(
    cases: List[BacktestCase],
    simulate_fn: Callable[..., Awaitable[Dict[str, Any]]],
    *,
    accuracy_threshold_pct: float = 50.0,
    params_snapshot: Optional[Dict[str, float]] = None,
    simulate_kwargs: Optional[Dict[str, Any]] = None,
    persist: bool = False,
) -> BacktestReport:
    """Run backtest on a list of cases.

    Args:
        cases: Backtest cases with ground truth (NOT passed to simulate).
        simulate_fn: Async callable that takes simulation_input and returns result.
        accuracy_threshold_pct: Threshold for marking a prediction as "accurate".
        params_snapshot: Tunable parameter values used for this run.
        simulate_kwargs: Extra keyword arguments passed to simulate_fn on each call.
        persist: If True, save report to BacktestStore after completion.

    Returns:
        BacktestReport with aggregated metrics.
    """
    report = BacktestReport(total_cases=len(cases))
    if params_snapshot:
        report.params_snapshot = dict(params_snapshot)
    results: List[BacktestResult] = []
    extra_kwargs = simulate_kwargs or {}

    for case in cases:
        # Run simulation with ONLY prediction-time input (no ground truth)
        start = time.monotonic()
        try:
            result = await simulate_fn(case.simulation_input, **extra_kwargs)
            elapsed = time.monotonic() - start

            # Extract prediction
            prediction = result.get("prediction", {})
            if not isinstance(prediction, dict):
                prediction = {}

            # Compute errors against ground truth
            errors = compute_prediction_errors(prediction, case.ground_truth)

            # Determine confidence and accuracy
            predicted_confidence = result.get("confidence", "medium")
            if isinstance(predicted_confidence, str):
                predicted_confidence = predicted_confidence.lower()

            # Simple accuracy check: is MAPE within threshold?
            pct_errors = [e.percentage_error for e in errors if e.percentage_error is not None]
            actual_accuracy = None
            if pct_errors:
                avg_pct_error = sum(pct_errors) / len(pct_errors)
                actual_accuracy = avg_pct_error <= accuracy_threshold_pct

            bt_result = BacktestResult(
                case_id=case.case_id,
                prediction=prediction,
                errors=errors,
                predicted_confidence=predicted_confidence,
                actual_accuracy=actual_accuracy,
                elapsed_seconds=round(elapsed, 2),
            )
            results.append(bt_result)
            report.completed_cases += 1

        except Exception as exc:
            logger.warning("Backtest case %s failed: %s", case.case_id, exc)
            bt_result = BacktestResult(
                case_id=case.case_id,
                prediction={},
                elapsed_seconds=round(time.monotonic() - start, 2),
                error_message=str(exc),
            )
            results.append(bt_result)
            report.failed_cases += 1

    report.results = results

    # Compute aggregated metrics
    numeric = compute_numeric_metrics(results)
    report.mae = numeric.get("mae")
    report.mape = numeric.get("mape")
    report.signed_mape = numeric.get("signed_mape")
    report.rmse = numeric.get("rmse")

    grade = compute_grade_metrics(results)
    report.grade_confusion_matrix = grade.get("confusion_matrix", {})
    report.macro_f1 = grade.get("macro_f1")

    report.confidence_calibration = compute_confidence_calibration(results)

    # Brier score for probabilistic predictions
    report.brier_score = compute_brier_score(results)

    # Per-bucket breakdowns
    buckets: Dict[str, List[BacktestResult]] = {}
    for case, bt_res in zip(cases, results):
        for bucket_field in ("platform", "channel", "vertical", "skill_id",
                             "model", "prompt_hash", "skill_version"):
            val = getattr(case, bucket_field, "")
            if val:
                key = f"{bucket_field}={val}"
                buckets.setdefault(key, []).append(bt_res)

    for bucket_key, bucket_results in buckets.items():
        bucket_numeric = compute_numeric_metrics(bucket_results)
        bucket_grade = compute_grade_metrics(bucket_results)
        report.buckets[bucket_key] = {
            "count": len(bucket_results),
            "mae": bucket_numeric.get("mae"),
            "mape": bucket_numeric.get("mape"),
            "signed_mape": bucket_numeric.get("signed_mape"),
            "rmse": bucket_numeric.get("rmse"),
            "macro_f1": bucket_grade.get("macro_f1"),
        }

    logger.info(
        "Backtest complete: %d/%d cases, MAE=%s, MAPE=%s, run_id=%s",
        report.completed_cases,
        report.total_cases,
        report.mae,
        report.mape,
        report.run_id,
    )

    if persist:
        try:
            from ripple.backtest.store import BacktestStore
            BacktestStore().save(report)
        except Exception as exc:
            logger.warning("Failed to persist backtest report: %s", exc)

    return report
