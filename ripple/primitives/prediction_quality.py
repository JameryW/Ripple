# ripple/primitives/prediction_quality.py
"""Prediction quality data models — structured contracts, confidence gates, and evidence packs.

Defines the data layer for R1 (Prediction Contract), R2 (EvidencePack Upgrade),
and the multi-factor ConfidenceGate (R3/R4/R5/R6 integration point).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Confidence level — canonical enum
# ---------------------------------------------------------------------------

class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]

    def __lt__(self, other: ConfidenceLevel) -> bool:
        return self.rank < other.rank

    def __le__(self, other: ConfidenceLevel) -> bool:
        return self.rank <= other.rank

    @classmethod
    def min_of(cls, *levels: ConfidenceLevel) -> ConfidenceLevel:
        return min(levels, key=lambda l: l.rank)


def normalize_confidence(raw: Any) -> ConfidenceLevel:
    """Normalize inconsistent LLM confidence output to ConfidenceLevel.

    Handles: "High"/"high"/"HIGH", "Medium"/"medium", "Low"/"low",
    numeric 0.0-1.0, percentage 0-100.
    """
    if isinstance(raw, ConfidenceLevel):
        return raw
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in ("high", "h"):
            return ConfidenceLevel.HIGH
        if cleaned in ("medium", "med", "m", "moderate"):
            return ConfidenceLevel.MEDIUM
        if cleaned in ("low", "l"):
            return ConfidenceLevel.LOW
        # Try numeric parse
        try:
            val = float(cleaned.rstrip("%"))
            if "%" in cleaned:
                val = val / 100.0
            return _numeric_to_level(val)
        except ValueError:
            pass
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1.0:
            val = val / 100.0
        return _numeric_to_level(val)
    return ConfidenceLevel.MEDIUM  # safe default


def _numeric_to_level(val: float) -> ConfidenceLevel:
    if val >= 0.7:
        return ConfidenceLevel.HIGH
    if val >= 0.4:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


# ---------------------------------------------------------------------------
# R1: Prediction Contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NumericPrediction:
    """A single numeric prediction with quantile expression.

    Supports p50/p80/p95 intervals for social-media metrics:
    impressions, engagement, conversion, virality_probability,
    breakout_probability, long_tail_probability.
    """
    target: str                    # metric name (e.g. "impressions")
    unit: str                      # unit (e.g. "count", "probability")
    time_window: str               # e.g. "48h", "7d"
    p50: Optional[float] = None    # median prediction
    p80: Optional[float] = None    # optimistic bound
    p95: Optional[float] = None    # tail bound
    point: Optional[float] = None  # single-point prediction (when quantiles unavailable)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_reason: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    unverifiable_claims: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GradePrediction:
    """An ordinal/grade prediction for PMF validation.

    Supports grade distributions, dimension scores, and tribunal divergence.
    """
    target: str                    # dimension or overall grade
    grade: str                     # e.g. "B+", "Strong"
    grade_distribution: Dict[str, float] = field(default_factory=dict)  # {"A": 0.1, "B": 0.6, "C": 0.3}
    dimension_scores: Dict[str, int] = field(default_factory=dict)  # {"retention": 4, "utility": 3}
    time_window: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    confidence_reason: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    dissent_points: List[str] = field(default_factory=list)
    unverifiable_claims: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PredictionContract:
    """Structured prediction output contract (R1).

    Unifies numeric and grade predictions across social-media and PMF skills.
    """
    skill_id: str
    predictions: List[NumericPrediction] = field(default_factory=list)
    grades: List[GradePrediction] = field(default_factory=list)
    overall_confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    overall_confidence_reason: str = ""
    assumptions_to_verify: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "predictions": [
                {
                    "target": p.target,
                    "unit": p.unit,
                    "time_window": p.time_window,
                    **({k: v for k, v in [("p50", p.p50), ("p80", p.p80), ("p95", p.p95), ("point", p.point)] if v is not None}),
                    "confidence": p.confidence.value,
                    "confidence_reason": p.confidence_reason,
                    "evidence_ids": p.evidence_ids,
                    "assumptions": p.assumptions,
                    "unverifiable_claims": p.unverifiable_claims,
                }
                for p in self.predictions
            ],
            "grades": [
                {
                    "target": g.target,
                    "grade": g.grade,
                    "grade_distribution": g.grade_distribution,
                    "dimension_scores": g.dimension_scores,
                    "time_window": g.time_window,
                    "confidence": g.confidence.value,
                    "confidence_reason": g.confidence_reason,
                    "evidence_ids": g.evidence_ids,
                    "assumptions": g.assumptions,
                    "dissent_points": g.dissent_points,
                    "unverifiable_claims": g.unverifiable_claims,
                }
                for g in self.grades
            ],
            "overall_confidence": self.overall_confidence.value,
            "overall_confidence_reason": self.overall_confidence_reason,
            "assumptions_to_verify": self.assumptions_to_verify,
            "evidence_ids": self.evidence_ids,
            "validation_warnings": self.validation_warnings,
        }


# ---------------------------------------------------------------------------
# R1: Prediction Contract Parser
# ---------------------------------------------------------------------------

# Probability-suffixed fields → numeric predictions
_PROBABILITY_FIELDS = {
    "virality_probability", "breakout_probability", "long_tail_probability",
    "conversion_probability",
}
# Count/metric fields → numeric predictions
_NUMERIC_FIELDS = {
    "impressions", "reach", "engagement", "conversion", "shares",
    "comments", "likes", "views", "followers_gained",
}
_SKIP_PREDICTION_FIELDS = {
    "step", "tick", "t", "phase", "agent_id", "id", "timestamp",
    "confidence", "confidence_gate_reason", "verdict", "reasoning",
    "summary", "assumptions", "assumptions_to_verify",
    "unverifiable_claims", "evidence_ids", "grade", "grade_distribution",
    "dimension_scores", "dissent_points",
}


def _is_numeric_field(key: str) -> bool:
    """Check if a prediction key represents a numeric metric."""
    k = key.lower()
    if k in _SKIP_PREDICTION_FIELDS:
        return False
    if k in _NUMERIC_FIELDS or k in _PROBABILITY_FIELDS:
        return True
    if "probability" in k or "_prob" in k:
        return True
    return False


def parse_prediction_contract(
    prediction: Any,
    *,
    skill_id: str = "",
    evidence_pack_v2: Any = None,
) -> PredictionContract:
    """Parse a LLM prediction output into a structured PredictionContract.

    Handles both dict-based and string-based predictions gracefully.
    Non-fatal: unknown structures result in an empty contract.
    """
    if not isinstance(prediction, dict):
        return PredictionContract(
            skill_id=skill_id or "unknown",
            validation_warnings=[f"Prediction is not a dict: {type(prediction).__name__}"],
        )

    # Collect evidence_ids from EvidencePackV2 key_signals
    available_evidence_ids: List[str] = []
    if evidence_pack_v2 is not None:
        for sig in getattr(evidence_pack_v2, "key_signals", []):
            eid = sig.get("evidence_id") if isinstance(sig, dict) else None
            if eid:
                available_evidence_ids.append(eid)

    numeric_preds: List[NumericPrediction] = []
    grade_preds: List[GradePrediction] = []
    assumptions: List[str] = []
    unverifiable: List[str] = []
    all_evidence_ids: List[str] = []

    # Extract numeric fields
    for key, val in prediction.items():
        if not _is_numeric_field(key):
            continue
        if not isinstance(val, (int, float)):
            continue

        # Determine unit
        unit = "probability" if key.lower() in _PROBABILITY_FIELDS or "probability" in key.lower() else "count"
        time_window = prediction.get("time_window", "") or prediction.get("simulation_horizon", "") or ""

        # Check for quantile variants in the prediction dict
        p50 = prediction.get(f"{key}_p50")
        p80 = prediction.get(f"{key}_p80")
        p95 = prediction.get(f"{key}_p95")

        conf = normalize_confidence(prediction.get("confidence", "medium"))
        # High confidence requires evidence_ids
        ev_ids = available_evidence_ids[:3] if conf == ConfidenceLevel.HIGH and available_evidence_ids else []

        numeric_preds.append(NumericPrediction(
            target=key,
            unit=unit,
            time_window=str(time_window),
            p50=float(p50) if isinstance(p50, (int, float)) else None,
            p80=float(p80) if isinstance(p80, (int, float)) else None,
            p95=float(p95) if isinstance(p95, (int, float)) else None,
            point=float(val),
            confidence=conf,
            evidence_ids=ev_ids,
        ))

    # Extract grade prediction (PMF validation)
    grade_val = prediction.get("grade")
    if grade_val and isinstance(grade_val, str):
        grade_dist = prediction.get("grade_distribution", {})
        if isinstance(grade_dist, dict):
            grade_dist = {k: float(v) for k, v in grade_dist.items() if isinstance(v, (int, float))}
        dim_scores = prediction.get("dimension_scores", {})
        if isinstance(dim_scores, dict):
            dim_scores = {k: int(v) for k, v in dim_scores.items() if isinstance(v, (int, float))}

        dissent = prediction.get("dissent_points", [])
        if not isinstance(dissent, list):
            dissent = []

        conf = normalize_confidence(prediction.get("confidence", "medium"))
        ev_ids = available_evidence_ids[:3] if conf == ConfidenceLevel.HIGH and available_evidence_ids else []

        grade_preds.append(GradePrediction(
            target="overall",
            grade=grade_val,
            grade_distribution=grade_dist,
            dimension_scores=dim_scores,
            time_window=str(prediction.get("time_window", "") or prediction.get("simulation_horizon", "") or ""),
            confidence=conf,
            evidence_ids=ev_ids,
            dissent_points=dissent,
        ))

    # Extract assumptions and unverifiable claims
    raw_assumptions = prediction.get("assumptions") or prediction.get("assumptions_to_verify")
    if isinstance(raw_assumptions, list):
        assumptions = [str(a) for a in raw_assumptions if a]

    raw_unverifiable = prediction.get("unverifiable_claims")
    if isinstance(raw_unverifiable, list):
        unverifiable = [str(u) for u in raw_unverifiable if u]

    # Collect all evidence_ids from predictions
    for p in numeric_preds:
        all_evidence_ids.extend(p.evidence_ids)
    for g in grade_preds:
        all_evidence_ids.extend(g.evidence_ids)

    # Validation
    warnings: List[str] = []
    for p in numeric_preds:
        if p.p50 is not None and p.p80 is not None and p.p50 > p.p80:
            warnings.append(f"{p.target}: p50 ({p.p50}) > p80 ({p.p80})")
        if p.p80 is not None and p.p95 is not None and p.p80 > p.p95:
            warnings.append(f"{p.target}: p80 ({p.p80}) > p95 ({p.p95})")
        if p.confidence == ConfidenceLevel.HIGH and not p.evidence_ids:
            warnings.append(f"{p.target}: high confidence without evidence_ids")

    for g in grade_preds:
        if g.grade_distribution:
            total = sum(g.grade_distribution.values())
            if abs(total - 1.0) > 0.15:
                warnings.append(f"{g.target}: grade distribution sums to {total:.2f}, expected ~1.0")
        if g.confidence == ConfidenceLevel.HIGH and not g.evidence_ids:
            warnings.append(f"{g.target}: high confidence without evidence_ids")

    overall_conf = normalize_confidence(prediction.get("confidence", "medium"))

    return PredictionContract(
        skill_id=skill_id or "unknown",
        predictions=numeric_preds,
        grades=grade_preds,
        overall_confidence=overall_conf,
        overall_confidence_reason=prediction.get("confidence_reason", ""),
        assumptions_to_verify=assumptions,
        evidence_ids=list(dict.fromkeys(all_evidence_ids)),
        validation_warnings=warnings,
    )


# ---------------------------------------------------------------------------
# R2: EvidencePack Upgrade
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalSummary:
    """Summary of signals in one direction (positive/negative/silent)."""
    count: int
    top_signals: List[Dict[str, Any]]  # ≤5, each with {id, type, energy, agent_id, wave}
    energy_total: float = 0.0


@dataclass(frozen=True)
class StratifiedStats:
    """Star/Sea stratified statistics."""
    star_count: int = 0
    sea_count: int = 0
    star_energy_total: float = 0.0
    sea_energy_total: float = 0.0
    star_response_types: Dict[str, int] = field(default_factory=dict)
    sea_response_types: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EnergyDecaySummary:
    """Energy decay across waves."""
    wave_energies: List[float] = field(default_factory=list)  # total energy per wave
    peak_wave: int = 0
    decay_rate: float = 0.0  # exponential decay estimate


@dataclass(frozen=True)
class EvidencePackV2:
    """Upgraded evidence pack (R2) with balanced signals and evidence ids.

    Backward compatible: can be constructed from the original EvidencePack fields.
    """
    pack_id: str                           # unique evidence pack id for cross-referencing
    source: str                            # evidence source
    summary: str                           # evidence summary (≤500 words)
    positive_signals: SignalSummary = field(default_factory=lambda: SignalSummary(count=0, top_signals=[]))
    negative_signals: SignalSummary = field(default_factory=lambda: SignalSummary(count=0, top_signals=[]))
    silent_signals: SignalSummary = field(default_factory=lambda: SignalSummary(count=0, top_signals=[]))
    stratified: StratifiedStats = field(default_factory=StratifiedStats)
    response_type_distribution: Dict[str, int] = field(default_factory=dict)
    energy_decay: EnergyDecaySummary = field(default_factory=EnergyDecaySummary)
    cross_layer_depth: int = 0             # max propagation depth across layers
    statistics: Dict[str, Any] = field(default_factory=dict)
    full_records_ref: str = ""             # JSON Pointer to full records
    # Legacy compat
    key_signals: List[Dict[str, Any]] = field(default_factory=list)


def upgrade_evidence_pack(
    legacy: Any,
    pack_id: str = "",
) -> EvidencePackV2:
    """Upgrade a legacy EvidencePack (v3) to EvidencePackV2.

    Preserves all existing fields; new fields default to empty/zero.
    """
    return EvidencePackV2(
        pack_id=pack_id or f"ep-{id(legacy):x}",
        source=getattr(legacy, "source", ""),
        summary=getattr(legacy, "summary", ""),
        key_signals=getattr(legacy, "key_signals", []),
        statistics=getattr(legacy, "statistics", {}),
        full_records_ref=getattr(legacy, "full_records_ref", ""),
    )


# ---------------------------------------------------------------------------
# Multi-factor ConfidenceGate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfidenceFactor:
    """Result from a single confidence factor evaluation."""
    name: str
    level: ConfidenceLevel
    reason: str
    passed: bool = True  # True = no gate triggered, False = gate lowered confidence


@dataclass(frozen=True)
class ConfidenceGateResult:
    """Aggregated result from all confidence factors."""
    original_confidence: ConfidenceLevel
    final_confidence: ConfidenceLevel
    factors: List[ConfidenceFactor]
    gate_applied: bool  # True if any factor lowered confidence
    reason: str


class ConfidenceGate:
    """Multi-factor confidence gate (R3/R4/R5/R6).

    Factors:
    1. Provider availability — missing provider → cap to medium
    2. Ensemble stability — low kappa/stability → lower confidence
    3. Historical deviation — exceeded threshold → lower confidence
    4. Evidence balance — positive/negative imbalance → lower confidence

    Gate logic: final = min(all factors). Original confidence is preserved
    if no gate fires.
    """

    def evaluate(
        self,
        raw_confidence: Any,
        *,
        provider_available: bool = True,
        ensemble_kappa: Optional[float] = None,
        ensemble_stability: Optional[str] = None,
        ensemble_agreement_rate: Optional[float] = None,
        historical_max_deviation_pct: Optional[float] = None,
        historical_threshold_pct: float = 50.0,
        evidence_positive_count: int = 0,
        evidence_negative_count: int = 0,
        evidence_silent_count: int = 0,
        tribunal_confidence_cap: Optional[str] = None,
        topology_scale_acceptable: Optional[bool] = None,
        topology_type_acceptable: Optional[bool] = None,
    ) -> ConfidenceGateResult:
        original = normalize_confidence(raw_confidence)
        factors: List[ConfidenceFactor] = []

        # Factor 1: Provider availability
        factors.append(self._check_provider(provider_available))

        # Factor 2: Ensemble stability
        factors.append(self._check_ensemble(
            kappa=ensemble_kappa,
            stability=ensemble_stability,
            agreement_rate=ensemble_agreement_rate,
        ))

        # Factor 3: Historical deviation
        factors.append(self._check_historical(
            max_deviation_pct=historical_max_deviation_pct,
            threshold_pct=historical_threshold_pct,
        ))

        # Factor 4: Evidence balance
        factors.append(self._check_evidence_balance(
            positive=evidence_positive_count,
            negative=evidence_negative_count,
            silent=evidence_silent_count,
        ))

        # Factor 5: Tribunal recommended confidence cap (R6)
        factors.append(self._check_tribunal_cap(tribunal_confidence_cap))

        # Factor 6: Topology calibration (R3)
        factors.append(self._check_topology(
            scale_acceptable=topology_scale_acceptable,
            type_acceptable=topology_type_acceptable,
        ))

        # Gate logic: take minimum of all factor levels
        factor_levels = [f.level for f in factors]
        gated_level = ConfidenceLevel.min_of(original, *factor_levels)
        gate_applied = gated_level != original

        reason = ""
        if gate_applied:
            triggered = [f for f in factors if not f.passed]
            reason = "; ".join(f"{f.name}: {f.reason}" for f in triggered)

        return ConfidenceGateResult(
            original_confidence=original,
            final_confidence=gated_level,
            factors=factors,
            gate_applied=gate_applied,
            reason=reason,
        )

    # --- Factor implementations ---

    @staticmethod
    def _check_provider(available: bool) -> ConfidenceFactor:
        if available:
            return ConfidenceFactor(
                name="provider_availability",
                level=ConfidenceLevel.HIGH,
                reason="Provider data available",
                passed=True,
            )
        return ConfidenceFactor(
            name="provider_availability",
            level=ConfidenceLevel.MEDIUM,
            reason="No provider data — confidence capped to medium",
            passed=False,
        )

    @staticmethod
    def _check_ensemble(
        kappa: Optional[float],
        stability: Optional[str],
        agreement_rate: Optional[float],
    ) -> ConfidenceFactor:
        # No ensemble data → factor is neutral (doesn't gate)
        if kappa is None and stability is None and agreement_rate is None:
            return ConfidenceFactor(
                name="ensemble_stability",
                level=ConfidenceLevel.HIGH,
                reason="No ensemble data (single run)",
                passed=True,
            )

        # Low stability triggers gate
        if stability in ("low",):
            return ConfidenceFactor(
                name="ensemble_stability",
                level=ConfidenceLevel.LOW,
                reason=f"Ensemble stability={stability}, kappa={kappa}",
                passed=False,
            )

        # Low kappa triggers gate
        if kappa is not None and kappa < 0.4:
            return ConfidenceFactor(
                name="ensemble_stability",
                level=ConfidenceLevel.LOW,
                reason=f"Low kappa={kappa:.3f}",
                passed=False,
            )

        # Low agreement rate
        if agreement_rate is not None and agreement_rate < 0.5:
            return ConfidenceFactor(
                name="ensemble_stability",
                level=ConfidenceLevel.MEDIUM,
                reason=f"Low agreement rate={agreement_rate:.1%}",
                passed=False,
            )

        return ConfidenceFactor(
            name="ensemble_stability",
            level=ConfidenceLevel.HIGH,
            reason=f"Stable ensemble (kappa={kappa}, stability={stability})",
            passed=True,
        )

    @staticmethod
    def _check_historical(
        max_deviation_pct: Optional[float],
        threshold_pct: float,
    ) -> ConfidenceFactor:
        if max_deviation_pct is None:
            return ConfidenceFactor(
                name="historical_deviation",
                level=ConfidenceLevel.HIGH,
                reason="No historical data for comparison",
                passed=True,
            )

        if max_deviation_pct > threshold_pct * 2:
            return ConfidenceFactor(
                name="historical_deviation",
                level=ConfidenceLevel.LOW,
                reason=f"Deviation {max_deviation_pct:.1f}% >> threshold {threshold_pct:.1f}%",
                passed=False,
            )

        if max_deviation_pct > threshold_pct:
            return ConfidenceFactor(
                name="historical_deviation",
                level=ConfidenceLevel.MEDIUM,
                reason=f"Deviation {max_deviation_pct:.1f}% > threshold {threshold_pct:.1f}%",
                passed=False,
            )

        return ConfidenceFactor(
            name="historical_deviation",
            level=ConfidenceLevel.HIGH,
            reason=f"Within historical threshold (deviation={max_deviation_pct:.1f}%)",
            passed=True,
        )

    @staticmethod
    def _check_evidence_balance(
        positive: int,
        negative: int,
        silent: int,
    ) -> ConfidenceFactor:
        total = positive + negative + silent
        if total == 0:
            return ConfidenceFactor(
                name="evidence_balance",
                level=ConfidenceLevel.HIGH,
                reason="No evidence signals",
                passed=True,
            )

        # If positive signals dominate heavily (>80% of non-silent) with few negative
        non_silent = positive + negative
        if non_silent > 0:
            positive_ratio = positive / non_silent
            if positive_ratio > 0.9 and negative < 2:
                return ConfidenceFactor(
                    name="evidence_balance",
                    level=ConfidenceLevel.MEDIUM,
                    reason=f"Evidence imbalanced: {positive} positive vs {negative} negative",
                    passed=False,
                )

        # If silent signals dominate (>70% of total)
        if total > 5 and silent / total > 0.7:
            return ConfidenceFactor(
                name="evidence_balance",
                level=ConfidenceLevel.MEDIUM,
                reason=f"High silent ratio: {silent}/{total} signals are silent/low-energy",
                passed=False,
            )

        return ConfidenceFactor(
            name="evidence_balance",
            level=ConfidenceLevel.HIGH,
            reason=f"Balanced evidence: +{positive} -{negative} ~{silent}",
            passed=True,
        )

    @staticmethod
    def _check_tribunal_cap(
        cap: Optional[str],
    ) -> ConfidenceFactor:
        """R6: Tribunal recommended confidence cap."""
        if cap is None:
            return ConfidenceFactor(
                name="tribunal_audit",
                level=ConfidenceLevel.HIGH,
                reason="No tribunal confidence cap recommended",
                passed=True,
            )
        cap_level = normalize_confidence(cap)
        if cap_level < ConfidenceLevel.HIGH:
            return ConfidenceFactor(
                name="tribunal_audit",
                level=cap_level,
                reason=f"Tribunal recommended confidence cap: {cap}",
                passed=False,
            )
        return ConfidenceFactor(
            name="tribunal_audit",
            level=ConfidenceLevel.HIGH,
            reason="Tribunal audit passed",
            passed=True,
        )

    @staticmethod
    def _check_topology(
        scale_acceptable: Optional[bool],
        type_acceptable: Optional[bool],
    ) -> ConfidenceFactor:
        """R3: Topology calibration — validate LLM topology against provider data."""
        if scale_acceptable is None and type_acceptable is None:
            return ConfidenceFactor(
                name="topology_calibration",
                level=ConfidenceLevel.HIGH,
                reason="No topology provider data for comparison",
                passed=True,
            )
        issues: List[str] = []
        level = ConfidenceLevel.HIGH
        if scale_acceptable is False:
            issues.append("scale deviation exceeds threshold")
            level = ConfidenceLevel.MEDIUM
        if type_acceptable is False:
            issues.append("type distribution deviation exceeds threshold")
            level = ConfidenceLevel.MEDIUM
        if scale_acceptable is False and type_acceptable is False:
            level = ConfidenceLevel.LOW
        if issues:
            return ConfidenceFactor(
                name="topology_calibration",
                level=level,
                reason=f"Topology issues: {'; '.join(issues)}",
                passed=False,
            )
        return ConfidenceFactor(
            name="topology_calibration",
            level=ConfidenceLevel.HIGH,
            reason="Topology within provider bounds",
            passed=True,
        )
