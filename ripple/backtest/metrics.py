# ripple/backtest/metrics.py
"""Error metrics computation for backtesting — R7.

Numeric: MAE, MAPE, RMSE
Grade: confusion matrix, macro F1
Confidence calibration: accuracy per confidence level
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ripple.backtest.schema import BacktestResult, PredictionError, GradeError


def compute_numeric_metrics(
    results: List[BacktestResult],
) -> Dict[str, Optional[float]]:
    """Compute MAE, MAPE, RMSE across all numeric prediction errors."""
    all_errors: List[PredictionError] = []
    for r in results:
        all_errors.extend(r.errors)

    if not all_errors:
        return {"mae": None, "mape": None, "rmse": None}

    abs_errors = [e.absolute_error for e in all_errors]
    mae = sum(abs_errors) / len(abs_errors)

    # MAPE: only over cases where actual != 0
    pct_errors = [e.percentage_error for e in all_errors if e.percentage_error is not None]
    mape = (sum(pct_errors) / len(pct_errors)) if pct_errors else None

    # RMSE
    rmse = math.sqrt(sum(e ** 2 for e in abs_errors) / len(abs_errors))

    return {
        "mae": round(mae, 4),
        "mape": round(mape, 4) if mape is not None else None,
        "rmse": round(rmse, 4),
    }


def compute_grade_metrics(
    results: List[BacktestResult],
) -> Dict[str, Any]:
    """Compute confusion matrix and macro F1 for grade predictions."""
    all_grade_errors: List[GradeError] = []
    for r in results:
        all_grade_errors.extend(r.grade_errors)

    if not all_grade_errors:
        return {"confusion_matrix": {}, "macro_f1": None}

    # Build confusion matrix
    grades = set()
    for ge in all_grade_errors:
        grades.add(ge.predicted_grade)
        grades.add(ge.actual_grade)

    confusion: Dict[str, Dict[str, int]] = {}
    for pred_g in sorted(grades):
        confusion[pred_g] = {}
        for actual_g in sorted(grades):
            confusion[pred_g][actual_g] = 0

    for ge in all_grade_errors:
        confusion[ge.predicted_grade][ge.actual_grade] += 1

    # Macro F1
    f1_scores: List[float] = []
    for g in sorted(grades):
        tp = confusion.get(g, {}).get(g, 0)
        fp = sum(confusion.get(g, {}).get(other, 0) for other in grades if other != g)
        fn = sum(confusion.get(other, {}).get(g, 0) for other in grades if other != g)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1_scores.append(f1)

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    return {
        "confusion_matrix": confusion,
        "macro_f1": round(macro_f1, 4),
    }


def compute_confidence_calibration(
    results: List[BacktestResult],
) -> Dict[str, float]:
    """Compute actual accuracy rate per confidence level."""
    by_confidence: Dict[str, List[bool]] = {}
    for r in results:
        if r.actual_accuracy is not None:
            by_confidence.setdefault(r.predicted_confidence, []).append(r.actual_accuracy)

    calibration: Dict[str, float] = {}
    for level, accuracies in sorted(by_confidence.items()):
        calibration[level] = round(sum(accuracies) / len(accuracies), 4)

    return calibration


def compute_prediction_errors(
    prediction: Dict[str, Any],
    ground_truth: Dict[str, Any],
) -> List[PredictionError]:
    """Compute per-field prediction errors."""
    _SKIP = {"step", "tick", "t", "phase", "agent_id", "id", "timestamp",
             "confidence", "confidence_gate_reason", "verdict"}
    errors: List[PredictionError] = []

    for key, pred_val in prediction.items():
        if key.lower() in _SKIP:
            continue
        if not isinstance(pred_val, (int, float)):
            continue
        actual_val = ground_truth.get(key)
        if actual_val is None or not isinstance(actual_val, (int, float)):
            continue

        pred_f = float(pred_val)
        actual_f = float(actual_val)
        ae = abs(pred_f - actual_f)
        pe = (ae / abs(actual_f) * 100) if actual_f != 0 else None

        errors.append(PredictionError(
            metric=key,
            predicted=pred_f,
            actual=actual_f,
            absolute_error=round(ae, 4),
            percentage_error=round(pe, 4) if pe is not None else None,
        ))

    return errors


def compute_brier_score(
    results: List[BacktestResult],
) -> Optional[float]:
    """Compute Brier score for probabilistic predictions.

    Brier score = mean((predicted_prob - actual_outcome)^2) over all
    probability-annotated fields. Lower is better (0 = perfect).

    Identifies probability fields by name suffix: _probability, _prob,
    or fields containing "probability"/"prob" in the key.
    """
    _PROB_PATTERNS = ("probability", "prob")
    squared_errors: List[float] = []

    for r in results:
        pred = r.prediction if isinstance(r.prediction, dict) else {}
        # Find case's ground truth via errors — but we need actual outcome
        # Instead, use the case's stored ground truth indirectly:
        # each PredictionError gives us predicted & actual
        for e in r.errors:
            if any(p in e.metric.lower() for p in _PROB_PATTERNS):
                # For probabilities, actual should be 0 or 1 (event happened or not)
                actual_binary = 1.0 if e.actual > 0.5 else 0.0
                se = (e.predicted - actual_binary) ** 2
                squared_errors.append(se)

    if not squared_errors:
        return None

    return round(sum(squared_errors) / len(squared_errors), 4)
