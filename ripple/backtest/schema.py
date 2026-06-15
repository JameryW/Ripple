# ripple/backtest/schema.py
"""Versioned backtest case schema — R7.

Each case contains prediction-time input, ground truth outcome,
labels, and time window. The runner must NOT see the outcome
when generating predictions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class BacktestCase:
    """A single backtest case with ground truth.

    Fields:
        case_id: Unique identifier for this case.
        schema_version: Schema version for forward compatibility.
        skill_id: Skill used for prediction (e.g. "social-media", "pmf-validation").
        simulation_input: The input that would be passed to simulate().
        ground_truth: The actual outcome (NOT visible to the predictor).
        platform: Platform label (e.g. "xiaohongshu", "weibo").
        channel: Channel label.
        vertical: Vertical/industry label.
        time_window: Prediction time window (e.g. "48h").
        content_type: Content type (e.g. "video", "text").
        product_category: Product category for PMF.
        model: LLM model used.
        prompt_hash: Hash of prompt template version.
        skill_version: Skill version string.
        provider_version: Provider configuration version.
        engine_version: Engine version string.
        tags: Free-form tags for filtering.
    """
    case_id: str
    schema_version: str = SCHEMA_VERSION
    skill_id: str = "social-media"
    simulation_input: Dict[str, Any] = field(default_factory=dict)
    ground_truth: Dict[str, Any] = field(default_factory=dict)
    platform: str = ""
    channel: str = ""
    vertical: str = ""
    time_window: str = ""
    content_type: str = ""
    product_category: str = ""
    model: str = ""
    prompt_hash: str = ""
    skill_version: str = ""
    provider_version: str = ""
    engine_version: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PredictionError:
    """Error metrics for a single numeric field."""
    metric: str
    predicted: float
    actual: float
    absolute_error: float
    percentage_error: Optional[float] = None  # None when actual == 0
    signed_percentage_error: Optional[float] = None  # symmetric signed MAPE term; None when predicted+actual == 0


@dataclass(frozen=True)
class GradeError:
    """Error metrics for a grade/ordinal field."""
    dimension: str
    predicted_grade: str
    actual_grade: str
    correct: bool


@dataclass(frozen=True)
class BacktestResult:
    """Result of running a single backtest case."""
    case_id: str
    prediction: Dict[str, Any]
    errors: List[PredictionError] = field(default_factory=list)
    grade_errors: List[GradeError] = field(default_factory=list)
    predicted_confidence: str = ""
    actual_accuracy: Optional[bool] = None  # Was prediction accurate enough?
    elapsed_seconds: float = 0.0
    error_message: Optional[str] = None  # Non-None if case failed to run


@dataclass
class BacktestReport:
    """Aggregated backtest report across multiple cases."""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    params_snapshot: Dict[str, float] = field(default_factory=dict)  # tunable params used during this run

    schema_version: str = SCHEMA_VERSION
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0

    # Numeric metrics (aggregated across all cases)
    mae: Optional[float] = None  # Mean Absolute Error
    mape: Optional[float] = None  # Mean Absolute Percentage Error
    signed_mape: Optional[float] = None  # Symmetric signed MAPE (positive=over-predict, negative=under-predict)
    rmse: Optional[float] = None  # Root Mean Square Error

    # Grade metrics
    grade_confusion_matrix: Dict[str, Dict[str, int]] = field(default_factory=dict)
    macro_f1: Optional[float] = None

    # Confidence calibration
    confidence_calibration: Dict[str, float] = field(default_factory=dict)  # {"high": 0.6, "medium": 0.4, "low": 0.2}

    # Brier score for probabilistic predictions
    brier_score: Optional[float] = None

    # Quality dimensions (from runtime QualityReport, when available)
    ensemble_stability: Optional[str] = None  # "high"|"medium"|"low"|None
    tribunal_divergence: Optional[str] = None  # "high"|"medium"|"low"|None
    evidence_balance: Dict[str, int] = field(default_factory=dict)  # {"positive": N, "negative": N, "silent": N}
    input_completeness: Optional[float] = None  # 0.0-1.0
    historical_deviation: Optional[float] = None  # max deviation pct
    residual_risks: List[str] = field(default_factory=list)
    quality_report_dict: Optional[Dict[str, Any]] = None  # raw dump for future fields

    # Per-bucket breakdowns
    buckets: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Per-case results
    results: List[BacktestResult] = field(default_factory=list)


@dataclass
class OptimizationResult:
    """Result of parameter optimization based on deviation analysis."""
    proposed_params: Dict[str, float] = field(default_factory=dict)
    score: float = 0.0  # Lower is better (represents expected deviation after applying params)
    improvement_estimate: float = 0.0  # Estimated % improvement in signed_mape
    current_params: Dict[str, float] = field(default_factory=dict)
    bias_direction: str = "neutral"  # "over_predict" | "under_predict" | "neutral"
    candidates_evaluated: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Result of A/B validation comparing old vs new parameter sets."""
    old_params: Dict[str, float] = field(default_factory=dict)
    new_params: Dict[str, float] = field(default_factory=dict)
    old_mape: Optional[float] = None
    new_mape: Optional[float] = None
    old_signed_mape: Optional[float] = None
    new_signed_mape: Optional[float] = None
    mape_change_pct: Optional[float] = None  # % change in MAPE (negative = improvement)
    passed: bool = True  # True if new params don't degrade any metric > 10%
    degraded_metrics: List[str] = field(default_factory=list)  # Metrics that degraded > 10%
    rolled_back: bool = False  # True if rollback was triggered
    warnings: List[str] = field(default_factory=list)
