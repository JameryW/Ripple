# ripple/engine/quality_report.py
"""Prediction Quality Report — R8.

Generates a structured quality report after each simulation,
covering 9 dimensions of prediction quality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityReport:
    """Structured quality report for a simulation run."""
    input_completeness: float  # 0.0-1.0: how complete was the input
    provider_coverage: Dict[str, str]  # category → "available"|"stub"|"missing"
    evidence_balance: Dict[str, int]  # {"positive": N, "negative": N, "silent": N}
    ensemble_stability: Optional[str] = None  # "high"|"medium"|"low"|None
    tribunal_divergence: Optional[str] = None  # "high"|"medium"|"low"|None
    historical_deviation: Optional[float] = None  # max deviation pct
    confidence_gate: Optional[Dict[str, Any]] = None
    residual_risks: List[str] = field(default_factory=list)
    recommended_verification_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_completeness": round(self.input_completeness, 3),
            "provider_coverage": self.provider_coverage,
            "evidence_balance": self.evidence_balance,
            "ensemble_stability": self.ensemble_stability,
            "tribunal_divergence": self.tribunal_divergence,
            "historical_deviation": self.historical_deviation,
            "confidence_gate": self.confidence_gate,
            "residual_risks": self.residual_risks,
            "recommended_verification_actions": self.recommended_verification_actions,
        }


def build_quality_report(
    simulation_input: Dict[str, Any],
    result: Dict[str, Any],
    providers: Any = None,
    evidence_pack_v2: Any = None,
    calibration_report: Any = None,
    deliberation_summary: Any = None,
) -> QualityReport:
    """Build a quality report from simulation artifacts.

    Non-fatal: any exception in sub-computations results in safe defaults.
    """
    try:
        return _build_report_inner(
            simulation_input, result, providers, evidence_pack_v2,
            calibration_report, deliberation_summary,
        )
    except Exception as exc:
        logger.warning("Quality report generation failed (non-fatal): %s", exc)
        return QualityReport(
            input_completeness=0.0,
            provider_coverage={},
            evidence_balance={},
            residual_risks=[f"Quality report generation failed: {exc}"],
        )


def _build_report_inner(
    simulation_input: Dict[str, Any],
    result: Dict[str, Any],
    providers: Any,
    evidence_pack_v2: Any,
    calibration_report: Any,
    deliberation_summary: Any,
) -> QualityReport:
    # 1. Input completeness: check required fields
    required = {"event"}
    present = sum(1 for k in required if simulation_input.get(k))
    optional = {"platform", "channel", "vertical", "historical", "source", "environment"}
    optional_present = sum(1 for k in optional if simulation_input.get(k))
    input_completeness = (present / len(required)) * 0.6 + (optional_present / len(optional)) * 0.4

    # 2. Provider coverage
    provider_coverage: Dict[str, str] = {}
    if providers is not None:
        from ripple.providers.registry import ProviderRegistry
        if isinstance(providers, ProviderRegistry):
            for cat in ("historical", "topology", "embedding", "ambient"):
                try:
                    p = providers.get(cat)
                    provider_coverage[cat] = "available" if p.is_available() else "stub"
                except Exception:
                    provider_coverage[cat] = "error"

    # 3. Evidence balance
    evidence_balance: Dict[str, int] = {"positive": 0, "negative": 0, "silent": 0}
    if evidence_pack_v2 is not None:
        evidence_balance["positive"] = evidence_pack_v2.positive_signals.count
        evidence_balance["negative"] = evidence_pack_v2.negative_signals.count
        evidence_balance["silent"] = evidence_pack_v2.silent_signals.count

    # 4. Ensemble stability
    ensemble_stability = None
    ensemble_stats = result.get("ensemble_stats", {})
    if isinstance(ensemble_stats, dict):
        dim_agg = ensemble_stats.get("dimension_aggregates", {})
        if isinstance(dim_agg, dict):
            levels = set()
            for dim_vals in dim_agg.values():
                if isinstance(dim_vals, dict):
                    sl = dim_vals.get("stability_level")
                    if sl:
                        levels.add(sl)
            if levels:
                ensemble_stability = max(levels, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x, 1))

    # 5. Tribunal divergence
    tribunal_divergence = None
    # Prefer explicit deliberation_summary parameter (from _extra_phase_outputs),
    # fall back to result dict for backward compat
    deliberate_output = deliberation_summary if deliberation_summary is not None else result.get("deliberation_summary", {})
    if isinstance(deliberate_output, dict):
        dissent = deliberate_output.get("dissent_points", [])
        consensus = deliberate_output.get("consensus_points", [])
        if isinstance(dissent, list) and isinstance(consensus, list):
            total = len(dissent) + len(consensus)
            if total > 0:
                dissent_ratio = len(dissent) / total
                if dissent_ratio > 0.5:
                    tribunal_divergence = "high"
                elif dissent_ratio > 0.3:
                    tribunal_divergence = "medium"
                else:
                    tribunal_divergence = "low"

    # 6. Historical deviation
    historical_deviation = None
    if calibration_report is not None:
        for cm in getattr(calibration_report, "calibrated_metrics", []):
            dev = abs(getattr(cm, "deviation_from_avg_pct", 0.0))
            if historical_deviation is None or dev > historical_deviation:
                historical_deviation = round(dev, 2)

    # 7. Confidence gate
    confidence_gate = result.get("confidence_gate")

    # 8. Residual risks
    residual_risks: List[str] = []
    if input_completeness < 0.5:
        residual_risks.append("Low input completeness — prediction may be unreliable")
    if evidence_balance.get("positive", 0) > 0 and evidence_balance.get("negative", 0) == 0:
        residual_risks.append("No negative evidence signals — potential optimism bias")
    if provider_coverage and all(v == "stub" for v in provider_coverage.values()):
        residual_risks.append("All providers are stubs — no external data validation")
    if ensemble_stability == "low":
        residual_risks.append("Low ensemble stability — predictions vary significantly across runs")
    if tribunal_divergence == "high":
        residual_risks.append("High tribunal divergence — expert panel disagrees on key dimensions")

    # 9. Recommended verification actions
    verification_actions: List[str] = []
    if confidence_gate and confidence_gate.get("gate_applied"):
        verification_actions.append(f"Confidence was gated: {confidence_gate.get('reason')}")
    if historical_deviation and historical_deviation > 100:
        verification_actions.append("Prediction significantly exceeds historical baseline — verify with real data")
    if evidence_balance.get("silent", 0) > evidence_balance.get("positive", 0) + evidence_balance.get("negative", 0):
        verification_actions.append("High silent/low-energy signal ratio — verify market interest")
    if not provider_coverage or all(v != "available" for v in provider_coverage.values()):
        verification_actions.append("No provider data available — validate prediction with independent sources")

    return QualityReport(
        input_completeness=round(input_completeness, 3),
        provider_coverage=provider_coverage,
        evidence_balance=evidence_balance,
        ensemble_stability=ensemble_stability,
        tribunal_divergence=tribunal_divergence,
        historical_deviation=historical_deviation,
        confidence_gate=confidence_gate,
        residual_risks=residual_risks,
        recommended_verification_actions=verification_actions,
    )
