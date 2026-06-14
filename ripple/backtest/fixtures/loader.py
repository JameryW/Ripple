# ripple/backtest/fixtures/loader.py
"""Load backtest seed fixtures from YAML and produce BacktestCase objects.

Each YAML case defines simulation_input and ground_truth.  The loader also
generates a *simulated prediction* that mimics typical LLM bias patterns
(optimistic over-prediction, conservative under-prediction, or well-calibrated).
These predictions are used by the backtest runner to exercise the backtest
without calling a real LLM.

This module lives in ``ripple.backtest.fixtures`` so that the CLI command
can import it in production builds (the ``tests/`` package is not installed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from ripple.backtest.schema import BacktestCase

_FIXTURES_DIR = Path(__file__).parent

# Bias multipliers applied to ground-truth numeric fields to simulate
# different LLM prediction patterns.
_BIAS_PROFILES: Dict[str, Dict[str, float]] = {
    "optimistic": {
        # Count fields: 3-5x over-prediction
        "impressions": 4.0,
        "engagement": 3.5,
        "reach": 3.8,
        "likes": 3.2,
        "comments": 3.0,
        "shares": 3.5,
        "conversion": 4.0,
        # Probability fields: inflate toward 1.0
        "virality_probability": 3.0,
        "breakout_probability": 4.0,
        "long_tail_probability": 1.5,
    },
    "conservative": {
        # Count fields: 0.3-0.5x under-prediction
        "impressions": 0.4,
        "engagement": 0.35,
        "reach": 0.4,
        "likes": 0.35,
        "comments": 0.3,
        "shares": 0.35,
        "conversion": 0.3,
        # Probability fields: deflate toward 0.0
        "virality_probability": 0.3,
        "breakout_probability": 0.25,
        "long_tail_probability": 0.6,
    },
    "calibrated": {
        # Count fields: within ~20-30% of truth
        "impressions": 1.2,
        "engagement": 0.85,
        "reach": 1.1,
        "likes": 0.9,
        "comments": 1.15,
        "shares": 0.95,
        "conversion": 1.1,
        # Probability fields: close to truth
        "virality_probability": 1.1,
        "breakout_probability": 0.9,
        "long_tail_probability": 1.05,
    },
}

# Confidence levels typically assigned by LLM for each bias pattern
_BIAS_CONFIDENCE: Dict[str, str] = {
    "optimistic": "high",
    "conservative": "medium",
    "calibrated": "medium",
}


def _detect_bias(tags: List[str]) -> str:
    """Extract bias category from fixture tags."""
    for tag in tags:
        if tag in _BIAS_PROFILES:
            return tag
    return "calibrated"


def _generate_prediction(
    ground_truth: Dict[str, Any],
    bias: str,
) -> Dict[str, Any]:
    """Generate a synthetic prediction by applying bias multipliers to ground truth."""
    multipliers = _BIAS_PROFILES.get(bias, _BIAS_PROFILES["calibrated"])
    prediction: Dict[str, Any] = {}

    for key, value in ground_truth.items():
        if not isinstance(value, (int, float)):
            continue
        mult = multipliers.get(key, 1.0)
        predicted = value * mult
        # Clamp probability fields to [0, 1]
        if "probability" in key.lower():
            predicted = max(0.0, min(1.0, predicted))
        # Round count fields to integers
        if "probability" not in key.lower():
            predicted = round(predicted)
        prediction[key] = predicted

    prediction["confidence"] = _BIAS_CONFIDENCE.get(bias, "medium")
    return prediction


def load_seed_cases() -> List[BacktestCase]:
    """Load all seed fixture cases from YAML.

    Returns:
        List of BacktestCase objects ready for the runner.
    """
    yaml_path = _FIXTURES_DIR / "seed_cases.yaml"
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw_cases = yaml.safe_load(f)

    cases: List[BacktestCase] = []
    for raw in raw_cases:
        case = BacktestCase(
            case_id=raw["case_id"],
            schema_version=raw.get("schema_version", "1.0"),
            skill_id=raw.get("skill_id", "social-media"),
            simulation_input=raw.get("simulation_input", {}),
            ground_truth=raw.get("ground_truth", {}),
            platform=raw.get("platform", ""),
            channel=raw.get("channel", ""),
            vertical=raw.get("vertical", ""),
            time_window=raw.get("time_window", ""),
            content_type=raw.get("content_type", ""),
            tags=raw.get("tags", []),
        )
        cases.append(case)
    return cases


def load_seed_cases_with_predictions() -> List[Tuple[BacktestCase, Dict[str, Any]]]:
    """Load seed cases paired with synthetic biased predictions.

    Returns:
        List of (BacktestCase, prediction_dict) tuples.
    """
    cases = load_seed_cases()
    result: List[Tuple[BacktestCase, Dict[str, Any]]] = []
    for case in cases:
        bias = _detect_bias(case.tags)
        prediction = _generate_prediction(case.ground_truth, bias)
        result.append((case, prediction))
    return result
