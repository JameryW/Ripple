# tests/engine/test_quality_report.py
"""Tests for prediction quality report — R8."""

import pytest

from ripple.engine.quality_report import build_quality_report, QualityReport


class TestQualityReport:
    def test_basic_report(self):
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}, "platform": "xiaohongshu"},
            result={
                "prediction": {"views": 500},
                "confidence_gate": {
                    "original_confidence": "high",
                    "final_confidence": "medium",
                    "gate_applied": True,
                    "reason": "No provider data",
                },
            },
        )
        assert report.input_completeness > 0
        assert isinstance(report.evidence_balance, dict)
        assert report.confidence_gate is not None
        assert len(report.residual_risks) >= 0

    def test_minimal_input(self):
        report = build_quality_report(
            simulation_input={},
            result={},
        )
        assert report.input_completeness < 1.0
        assert report.evidence_balance == {"positive": 0, "negative": 0, "silent": 0}

    def test_to_dict(self):
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={"prediction": {}},
        )
        d = report.to_dict()
        assert "input_completeness" in d
        assert "provider_coverage" in d
        assert "evidence_balance" in d
        assert "residual_risks" in d
        assert "recommended_verification_actions" in d

    def test_residual_risks_with_all_stubs(self):
        """When all providers are stubs, a residual risk should be flagged."""
        from ripple.providers.registry import ProviderRegistry
        providers = ProviderRegistry()  # all stubs
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={"prediction": {}},
            providers=providers,
        )
        risk_texts = " ".join(report.residual_risks)
        assert "stub" in risk_texts.lower() or "no provider" in risk_texts.lower() or "No external" in risk_texts

    def test_verification_actions_with_gated_confidence(self):
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={
                "confidence_gate": {
                    "gate_applied": True,
                    "reason": "Provider missing",
                },
            },
        )
        assert len(report.recommended_verification_actions) >= 1

    def test_error_handling(self):
        """Quality report generation must be non-fatal."""
        # Pass broken input — should get a fallback report
        report = build_quality_report(
            simulation_input=None,  # type: ignore
            result=None,  # type: ignore
        )
        assert isinstance(report, QualityReport)

    def test_tribunal_divergence_from_deliberation_summary(self):
        """Tribunal divergence should be computed from deliberation_summary param."""
        deliberation_summary = {
            "dissent_points": ["point1", "point2", "point3"],
            "consensus_points": ["c1"],
        }
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={"prediction": {}},
            deliberation_summary=deliberation_summary,
        )
        assert report.tribunal_divergence == "high"

    def test_tribunal_divergence_low(self):
        """Low tribunal divergence when consensus dominates."""
        deliberation_summary = {
            "dissent_points": ["d1"],
            "consensus_points": ["c1", "c2", "c3", "c4"],
        }
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={"prediction": {}},
            deliberation_summary=deliberation_summary,
        )
        assert report.tribunal_divergence == "low"

    def test_no_deliberation_summary_means_no_divergence(self):
        """Without deliberation_summary, tribunal_divergence should be None."""
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result={"prediction": {}},
        )
        assert report.tribunal_divergence is None

    def test_ensemble_stability_picks_worst_level(self):
        """Ensemble stability should pick the worst (lowest) level across dimensions."""
        result = {
            "prediction": {},
            "ensemble_stats": {
                "dimension_aggregates": {
                    "reach": {"stability_level": "high"},
                    "engagement": {"stability_level": "low"},
                    "virality": {"stability_level": "medium"},
                },
            },
        }
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result=result,
        )
        # "low" is worst — should be selected, not "high"
        assert report.ensemble_stability == "low"

    def test_ensemble_stability_all_high(self):
        """When all dimensions have high stability, report high."""
        result = {
            "prediction": {},
            "ensemble_stats": {
                "dimension_aggregates": {
                    "reach": {"stability_level": "high"},
                    "engagement": {"stability_level": "high"},
                },
            },
        }
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result=result,
        )
        assert report.ensemble_stability == "high"

    def test_ensemble_stability_with_low_flags_risk(self):
        """Low ensemble stability should be flagged as a residual risk."""
        result = {
            "prediction": {},
            "ensemble_stats": {
                "dimension_aggregates": {
                    "reach": {"stability_level": "low"},
                },
            },
        }
        report = build_quality_report(
            simulation_input={"event": {"title": "Test"}},
            result=result,
        )
        assert report.ensemble_stability == "low"
        risk_texts = " ".join(report.residual_risks)
        assert "Low ensemble stability" in risk_texts
