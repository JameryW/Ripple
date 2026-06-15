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

# ── Quality signal extraction ───────────────────────────────────────────

_STABILITY_ORDER = {"low": 0, "medium": 1, "high": 2}
_DIVERGENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _worst_stability(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """Pick the worse (lower-confidence) stability level."""
    if current is None:
        return candidate
    if candidate is None:
        return current
    c_rank = _STABILITY_ORDER.get(current, 1)
    n_rank = _STABILITY_ORDER.get(candidate, 1)
    return current if c_rank <= n_rank else candidate


def _worst_divergence(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """Pick the worse (higher-divergence) level."""
    if current is None:
        return candidate
    if candidate is None:
        return current
    c_rank = _DIVERGENCE_ORDER.get(current, 1)
    n_rank = _DIVERGENCE_ORDER.get(candidate, 1)
    return current if c_rank <= n_rank else candidate


def _classify_divergence(dissent: int, consensus: int) -> Optional[str]:
    """Classify tribunal divergence from dissent/consensus counts."""
    total = dissent + consensus
    if total == 0:
        return None
    ratio = dissent / total
    if ratio >= 0.5:
        return "high"
    if ratio >= 0.25:
        return "medium"
    return "low"


def _extract_stability_from_dimension_aggregates(
    dim_aggs: Dict[str, Any],
) -> Optional[str]:
    """Extract worst stability level from ensemble dimension_aggregates."""
    worst: Optional[str] = None
    if not isinstance(dim_aggs, dict):
        return None
    for dim_data in dim_aggs.values():
        if not isinstance(dim_data, dict):
            continue
        level = str(dim_data.get("stability", "")).strip().lower()
        if level in _STABILITY_ORDER:
            worst = _worst_stability(worst, level)
    return worst


def _extract_quality_signals(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract quality signals from a single simulate_fn result dict.

    Returns a dict with keys: ensemble_stability, tribunal_divergence,
    evidence_balance, historical_deviation, quality_report_dict.
    All values default to None/empty when not present in the result.
    """
    signals: Dict[str, Any] = {}

    # ensemble_stability: from ensemble_stats.dimension_aggregates
    ensemble_stats = result.get("ensemble_stats")
    if isinstance(ensemble_stats, dict):
        dim_aggs = ensemble_stats.get("dimension_aggregates")
        signals["ensemble_stability"] = _extract_stability_from_dimension_aggregates(
            dim_aggs if isinstance(dim_aggs, dict) else {}
        )

    # tribunal_divergence: from deliberation_summary dissent vs consensus
    delib = result.get("deliberation_summary")
    if isinstance(delib, dict):
        dissent_points = list(delib.get("dissent_points") or [])
        consensus_points = list(delib.get("consensus_points") or [])
        signals["tribunal_divergence"] = _classify_divergence(
            len(dissent_points), len(consensus_points)
        )

    # evidence_balance: from top-level or extras
    eb = result.get("evidence_balance")
    if isinstance(eb, dict):
        signals["evidence_balance"] = {
            k: int(v) for k, v in eb.items() if isinstance(v, (int, float))
        }

    # historical_deviation: from calibration_report
    cal = result.get("calibration_report")
    if isinstance(cal, dict):
        max_dev = cal.get("max_deviation")
        if isinstance(max_dev, (int, float)):
            signals["historical_deviation"] = float(max_dev)

    # quality_report_dict: raw dump if present
    qr = result.get("quality_report")
    if isinstance(qr, dict):
        signals["quality_report_dict"] = qr

    return signals


def _compute_input_completeness(simulation_input: Dict[str, Any]) -> float:
    """Estimate input completeness from field presence in simulation_input.

    Checks for key signal fields that indicate rich input. Returns 0.0-1.0.
    """
    if not simulation_input:
        return 0.0
    important_fields = [
        "event", "source", "platform", "channel", "vertical",
        "historical", "simulation_horizon",
    ]
    present = sum(1 for f in important_fields if simulation_input.get(f))
    return round(present / len(important_fields), 2)


def _aggregate_quality_signals(
    per_case_signals: List[Dict[str, Any]],
    cases: List[BacktestCase],
) -> Dict[str, Any]:
    """Aggregate quality signals across all cases into report-level values."""
    if not per_case_signals:
        return {}

    # ensemble_stability: worst across all cases
    worst_stab: Optional[str] = None
    for sig in per_case_signals:
        worst_stab = _worst_stability(worst_stab, sig.get("ensemble_stability"))

    # tribunal_divergence: worst across all cases
    worst_div: Optional[str] = None
    for sig in per_case_signals:
        worst_div = _worst_divergence(worst_div, sig.get("tribunal_divergence"))

    # evidence_balance: sum counts across cases
    total_balance: Dict[str, int] = {}
    for sig in per_case_signals:
        eb = sig.get("evidence_balance")
        if isinstance(eb, dict):
            for k, v in eb.items():
                total_balance[k] = total_balance.get(k, 0) + v

    # input_completeness: mean across cases
    completeness_values = [
        _compute_input_completeness(c.simulation_input) for c in cases
    ]
    avg_completeness = (
        round(sum(completeness_values) / len(completeness_values), 2)
        if completeness_values
        else None
    )

    # historical_deviation: max across cases
    max_deviation: Optional[float] = None
    for sig in per_case_signals:
        dev = sig.get("historical_deviation")
        if isinstance(dev, (int, float)):
            if max_deviation is None or dev > max_deviation:
                max_deviation = float(dev)

    # residual_risks: collect from quality_report_dict entries
    risks: List[str] = []
    seen_risks: set[str] = set()
    for sig in per_case_signals:
        qr = sig.get("quality_report_dict")
        if isinstance(qr, dict):
            rr = qr.get("residual_risks")
            if isinstance(rr, list):
                for item in rr:
                    text = str(item).strip()
                    if text and text not in seen_risks:
                        risks.append(text)
                        seen_risks.add(text)

    # quality_report_dict: last case's quality report
    last_qr: Optional[Dict[str, Any]] = None
    for sig in reversed(per_case_signals):
        qr = sig.get("quality_report_dict")
        if isinstance(qr, dict):
            last_qr = qr
            break

    return {
        "ensemble_stability": worst_stab,
        "tribunal_divergence": worst_div,
        "evidence_balance": total_balance,
        "input_completeness": avg_completeness,
        "historical_deviation": max_deviation,
        "residual_risks": risks,
        "quality_report_dict": last_qr,
    }


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
    per_case_quality_signals: List[Dict[str, Any]] = []
    extra_kwargs = simulate_kwargs or {}

    for case in cases:
        # Run simulation with ONLY prediction-time input (no ground truth)
        start = time.monotonic()
        try:
            result = await simulate_fn(case.simulation_input, **extra_kwargs)
            elapsed = time.monotonic() - start

            # Extract quality signals from simulate result (graceful)
            per_case_quality_signals.append(_extract_quality_signals(result))

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
            per_case_quality_signals.append({})
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

    # Aggregate quality dimensions from per-case signals
    if per_case_quality_signals:
        quality = _aggregate_quality_signals(per_case_quality_signals, cases)
        report.ensemble_stability = quality.get("ensemble_stability")
        report.tribunal_divergence = quality.get("tribunal_divergence")
        report.evidence_balance = quality.get("evidence_balance") or {}
        report.input_completeness = quality.get("input_completeness")
        report.historical_deviation = quality.get("historical_deviation")
        report.residual_risks = quality.get("residual_risks") or []
        report.quality_report_dict = quality.get("quality_report_dict")

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
