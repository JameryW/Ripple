# tests/primitives/test_prediction_quality.py
"""Tests for prediction quality data models — R1/R2/R3-R6 confidence gate."""

import pytest

from ripple.primitives.prediction_quality import (
    ConfidenceFactor,
    ConfidenceGate,
    ConfidenceGateResult,
    ConfidenceLevel,
    EnergyDecaySummary,
    EvidencePackV2,
    GradePrediction,
    NumericPrediction,
    PredictionContract,
    SignalSummary,
    StratifiedStats,
    normalize_confidence,
    parse_prediction_contract,
    upgrade_evidence_pack,
)
from ripple.primitives.pmf_models import EvidencePack


# ---------------------------------------------------------------------------
# ConfidenceLevel & normalize_confidence
# ---------------------------------------------------------------------------


class TestConfidenceLevel:
    def test_ordering(self):
        assert ConfidenceLevel.LOW < ConfidenceLevel.MEDIUM
        assert ConfidenceLevel.MEDIUM < ConfidenceLevel.HIGH

    def test_min_of(self):
        assert ConfidenceLevel.min_of(ConfidenceLevel.HIGH, ConfidenceLevel.LOW) == ConfidenceLevel.LOW
        assert ConfidenceLevel.min_of(ConfidenceLevel.MEDIUM, ConfidenceLevel.MEDIUM) == ConfidenceLevel.MEDIUM

    def test_rank(self):
        assert ConfidenceLevel.LOW.rank == 0
        assert ConfidenceLevel.MEDIUM.rank == 1
        assert ConfidenceLevel.HIGH.rank == 2


class TestNormalizeConfidence:
    @pytest.mark.parametrize("raw", ["High", "high", "HIGH", "H"])
    def test_high_variants(self, raw):
        assert normalize_confidence(raw) == ConfidenceLevel.HIGH

    @pytest.mark.parametrize("raw", ["Medium", "medium", "Med", "M", "moderate"])
    def test_medium_variants(self, raw):
        assert normalize_confidence(raw) == ConfidenceLevel.MEDIUM

    @pytest.mark.parametrize("raw", ["Low", "low", "L"])
    def test_low_variants(self, raw):
        assert normalize_confidence(raw) == ConfidenceLevel.LOW

    def test_numeric_float(self):
        assert normalize_confidence(0.8) == ConfidenceLevel.HIGH
        assert normalize_confidence(0.5) == ConfidenceLevel.MEDIUM
        assert normalize_confidence(0.2) == ConfidenceLevel.LOW

    def test_numeric_int_percentage(self):
        assert normalize_confidence(80) == ConfidenceLevel.HIGH
        assert normalize_confidence(50) == ConfidenceLevel.MEDIUM

    def test_string_percentage(self):
        assert normalize_confidence("80%") == ConfidenceLevel.HIGH
        assert normalize_confidence("30%") == ConfidenceLevel.LOW

    def test_unknown_defaults_medium(self):
        assert normalize_confidence("gibberish") == ConfidenceLevel.MEDIUM
        assert normalize_confidence(None) == ConfidenceLevel.MEDIUM

    def test_passthrough_enum(self):
        assert normalize_confidence(ConfidenceLevel.HIGH) == ConfidenceLevel.HIGH


# ---------------------------------------------------------------------------
# PredictionContract (R1)
# ---------------------------------------------------------------------------


class TestNumericPrediction:
    def test_creation(self):
        p = NumericPrediction(
            target="impressions",
            unit="count",
            time_window="48h",
            p50=10000,
            p80=15000,
            p95=25000,
            confidence=ConfidenceLevel.HIGH,
            evidence_ids=["ev-1", "ev-3"],
        )
        assert p.target == "impressions"
        assert p.p50 == 10000
        assert p.confidence == ConfidenceLevel.HIGH
        assert len(p.evidence_ids) == 2

    def test_point_only(self):
        p = NumericPrediction(target="engagement", unit="count", time_window="7d", point=5000)
        assert p.p50 is None
        assert p.point == 5000


class TestGradePrediction:
    def test_creation(self):
        g = GradePrediction(
            target="overall",
            grade="B+",
            grade_distribution={"A": 0.1, "B": 0.6, "C": 0.3},
            dimension_scores={"retention": 4, "utility": 3},
            confidence=ConfidenceLevel.MEDIUM,
        )
        assert g.grade == "B+"
        assert g.grade_distribution["B"] == 0.6


class TestPredictionContract:
    def test_creation(self):
        c = PredictionContract(
            skill_id="social-media",
            predictions=[
                NumericPrediction(target="impressions", unit="count", time_window="48h", p50=10000)
            ],
            grades=[
                GradePrediction(target="PMF", grade="B", dimension_scores={"retention": 4})
            ],
            overall_confidence=ConfidenceLevel.HIGH,
        )
        assert c.skill_id == "social-media"
        assert len(c.predictions) == 1
        assert len(c.grades) == 1


# ---------------------------------------------------------------------------
# EvidencePackV2 (R2)
# ---------------------------------------------------------------------------


class TestEvidencePackV2:
    def test_defaults(self):
        ep = EvidencePackV2(pack_id="ep-test", source="test", summary="test summary")
        assert ep.positive_signals.count == 0
        assert ep.stratified.star_count == 0
        assert ep.energy_decay.peak_wave == 0
        assert ep.cross_layer_depth == 0

    def test_with_signals(self):
        ep = EvidencePackV2(
            pack_id="ep-test",
            source="RIPPLE",
            summary="test",
            positive_signals=SignalSummary(count=5, top_signals=[], energy_total=10.0),
            negative_signals=SignalSummary(count=2, top_signals=[], energy_total=3.0),
            silent_signals=SignalSummary(count=3, top_signals=[], energy_total=0.0),
            stratified=StratifiedStats(star_count=2, sea_count=8),
        )
        assert ep.positive_signals.count == 5
        assert ep.negative_signals.count == 2
        assert ep.silent_signals.count == 3

    def test_upgrade_from_legacy(self):
        legacy = EvidencePack(
            source="RIPPLE Phase, Wave 0-3",
            summary="4 waves",
            key_signals=[{"wave_id": "w0", "response_type": "amplify"}],
            statistics={"total_waves": 4},
            full_records_ref="#/process/waves",
        )
        v2 = upgrade_evidence_pack(legacy)
        assert v2.source == "RIPPLE Phase, Wave 0-3"
        assert v2.summary == "4 waves"
        assert len(v2.key_signals) == 1
        assert v2.positive_signals.count == 0  # not classified in legacy
        assert v2.statistics["total_waves"] == 4

    def test_upgrade_with_custom_pack_id(self):
        legacy = EvidencePack(source="s", summary="s", key_signals=[], statistics={}, full_records_ref="#")
        v2 = upgrade_evidence_pack(legacy, pack_id="ep-custom")
        assert v2.pack_id == "ep-custom"


# ---------------------------------------------------------------------------
# ConfidenceGate (R3/R4/R5/R6)
# ---------------------------------------------------------------------------


class TestConfidenceGate:
    def setup_method(self):
        self.gate = ConfidenceGate()

    def test_no_gates_triggered(self):
        r = self.gate.evaluate("high", provider_available=True, ensemble_kappa=0.85, historical_max_deviation_pct=30.0)
        assert r.final_confidence == ConfidenceLevel.HIGH
        assert not r.gate_applied

    def test_provider_missing_caps_to_medium(self):
        r = self.gate.evaluate("high", provider_available=False)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied
        assert any(f.name == "provider_availability" and not f.passed for f in r.factors)

    def test_low_kappa_lowers_confidence(self):
        r = self.gate.evaluate("high", ensemble_kappa=0.2, ensemble_stability="low")
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_medium_kappa_moderate(self):
        r = self.gate.evaluate("high", ensemble_kappa=0.5, ensemble_stability="medium")
        assert not r.gate_applied  # kappa 0.5 > 0.4, stability medium is ok

    def test_historical_deviation_exceeds_threshold(self):
        r = self.gate.evaluate("high", historical_max_deviation_pct=250.0, historical_threshold_pct=100.0)
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_historical_deviation_slightly_over(self):
        r = self.gate.evaluate("high", historical_max_deviation_pct=150.0, historical_threshold_pct=100.0)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_evidence_strong_positive(self):
        """96% positive ratio → HIGH (strong supporting evidence)."""
        r = self.gate.evaluate("high", evidence_positive_count=24, evidence_negative_count=1, evidence_silent_count=0)
        assert r.final_confidence == ConfidenceLevel.HIGH
        assert not r.gate_applied

    def test_evidence_moderate_positive(self):
        """90% positive ratio → MEDIUM (moderate supporting evidence)."""
        r = self.gate.evaluate("high", evidence_positive_count=18, evidence_negative_count=2, evidence_silent_count=0)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_evidence_weak_positive(self):
        """80% positive ratio → LOW (weak supporting evidence)."""
        r = self.gate.evaluate("high", evidence_positive_count=16, evidence_negative_count=4, evidence_silent_count=0)
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_high_silent_ratio_low(self):
        """86% silent ratio → LOW (very high silent dominance)."""
        r = self.gate.evaluate("high", evidence_positive_count=1, evidence_negative_count=0, evidence_silent_count=7)
        # positive_ratio = 100% → HIGH (evidence_balance), but silent_ratio = 87.5% > 85% → LOW (evidence_silent)
        assert r.final_confidence == ConfidenceLevel.LOW

    def test_moderate_silent_ratio(self):
        """75% silent ratio → MEDIUM (high silent ratio)."""
        r = self.gate.evaluate("high", evidence_positive_count=1, evidence_negative_count=1, evidence_silent_count=6)
        # positive_ratio = 50% → LOW (evidence_balance), but silent_ratio = 75% → MEDIUM (evidence_silent)
        # min(LOW, MEDIUM) = LOW
        assert r.final_confidence == ConfidenceLevel.LOW

    def test_balanced_evidence_mixed_signals(self):
        """70%+ positive ratio with some negative → LOW (below 85% threshold)."""
        r = self.gate.evaluate("high", evidence_positive_count=7, evidence_negative_count=3, evidence_silent_count=0)
        # positive_ratio = 70% < 85% → LOW (evidence_balance)
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_evidence_no_signals(self):
        """No evidence signals → HIGH (neutral, no gate)."""
        r = self.gate.evaluate("high", evidence_positive_count=0, evidence_negative_count=0, evidence_silent_count=0)
        assert not r.gate_applied

    def test_evidence_all_silent(self):
        """Only silent signals → MEDIUM (no positive or negative evidence)."""
        r = self.gate.evaluate("high", evidence_positive_count=0, evidence_negative_count=0, evidence_silent_count=5)
        # non_silent = 0 → all silent path → MEDIUM
        assert r.final_confidence == ConfidenceLevel.MEDIUM

    def test_single_run_no_ensemble_data(self):
        r = self.gate.evaluate("high")
        assert r.final_confidence == ConfidenceLevel.HIGH
        assert not r.gate_applied

    def test_low_agreement_rate(self):
        r = self.gate.evaluate("high", ensemble_agreement_rate=0.3)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_multiple_gates_stack(self):
        # Provider missing + strong positive evidence → medium (provider caps)
        r = self.gate.evaluate(
            "high",
            provider_available=False,
            evidence_positive_count=20,
            evidence_negative_count=1,
        )
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_low_raw_confidence_unchanged(self):
        r = self.gate.evaluate("low", provider_available=True)
        assert r.final_confidence == ConfidenceLevel.LOW
        assert not r.gate_applied

    def test_original_confidence_preserved_in_result(self):
        r = self.gate.evaluate("high", provider_available=False)
        assert r.original_confidence == ConfidenceLevel.HIGH
        assert r.final_confidence == ConfidenceLevel.MEDIUM

    def test_no_historical_data_is_neutral(self):
        r = self.gate.evaluate("high", historical_max_deviation_pct=None)
        assert not r.gate_applied

    def test_tribunal_cap_medium(self):
        r = self.gate.evaluate("high", tribunal_confidence_cap="medium")
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_tribunal_cap_low(self):
        r = self.gate.evaluate("high", tribunal_confidence_cap="low")
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_tribunal_cap_none_is_neutral(self):
        r = self.gate.evaluate("high", tribunal_confidence_cap=None)
        assert not r.gate_applied

    # R3: Topology calibration factor
    def test_topology_no_data_is_neutral(self):
        r = self.gate.evaluate("high", topology_scale_acceptable=None, topology_type_acceptable=None)
        assert not r.gate_applied

    def test_topology_scale_exceeds(self):
        r = self.gate.evaluate("high", topology_scale_acceptable=False, topology_type_acceptable=True)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied
        assert any(f.name == "topology_calibration" for f in r.factors)

    def test_topology_type_exceeds(self):
        r = self.gate.evaluate("high", topology_scale_acceptable=True, topology_type_acceptable=False)
        assert r.final_confidence == ConfidenceLevel.MEDIUM

    def test_topology_both_exceed(self):
        r = self.gate.evaluate("high", topology_scale_acceptable=False, topology_type_acceptable=False)
        assert r.final_confidence == ConfidenceLevel.LOW

    def test_topology_both_acceptable(self):
        r = self.gate.evaluate("high", topology_scale_acceptable=True, topology_type_acceptable=True)
        assert not r.gate_applied

    def test_default_threshold_triggers_at_50pct(self):
        """Calibrated default threshold: 50% deviation triggers MEDIUM gate."""
        r = self.gate.evaluate("high", historical_max_deviation_pct=60.0)
        assert r.final_confidence == ConfidenceLevel.MEDIUM
        assert r.gate_applied

    def test_default_threshold_double_triggers_low(self):
        """100% deviation (>2x the 50% default threshold) triggers LOW gate."""
        r = self.gate.evaluate("high", historical_max_deviation_pct=120.0)
        assert r.final_confidence == ConfidenceLevel.LOW
        assert r.gate_applied

    def test_default_threshold_within_passes(self):
        """40% deviation is within the 50% default threshold, no gate."""
        r = self.gate.evaluate("high", historical_max_deviation_pct=40.0)
        assert not r.gate_applied


# ---------------------------------------------------------------------------
# R1: PredictionContract Parser
# ---------------------------------------------------------------------------

class TestPredictionContractParser:
    def test_numeric_prediction_with_point_estimate(self):
        pred = {"impressions": 50000, "engagement": 3000, "confidence": "high"}
        contract = parse_prediction_contract(pred, skill_id="social-media")
        assert contract.skill_id == "social-media"
        assert len(contract.predictions) == 2
        imp = next(p for p in contract.predictions if p.target == "impressions")
        assert imp.point == 50000.0
        assert imp.unit == "count"
        assert imp.confidence == ConfidenceLevel.HIGH

    def test_numeric_prediction_with_quantiles(self):
        pred = {
            "impressions": 50000,
            "impressions_p50": 48000,
            "impressions_p80": 65000,
            "impressions_p95": 80000,
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred, skill_id="social-media")
        imp = next(p for p in contract.predictions if p.target == "impressions")
        assert imp.p50 == 48000.0
        assert imp.p80 == 65000.0
        assert imp.p95 == 80000.0

    def test_probability_field_detection(self):
        pred = {"virality_probability": 0.3, "breakout_probability": 0.05}
        contract = parse_prediction_contract(pred)
        assert len(contract.predictions) == 2
        vp = next(p for p in contract.predictions if p.target == "virality_probability")
        assert vp.unit == "probability"

    def test_grade_prediction(self):
        pred = {
            "grade": "B+",
            "grade_distribution": {"A": 0.1, "B": 0.6, "C": 0.3},
            "dimension_scores": {"retention": 4, "utility": 3},
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred, skill_id="pmf-validation")
        assert len(contract.grades) == 1
        g = contract.grades[0]
        assert g.grade == "B+"
        assert g.grade_distribution["B"] == 0.6
        assert g.dimension_scores["retention"] == 4

    def test_mixed_numeric_and_grade(self):
        pred = {
            "impressions": 50000,
            "grade": "B+",
            "grade_distribution": {"A": 0.2, "B": 0.5, "C": 0.3},
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred)
        assert len(contract.predictions) == 1
        assert len(contract.grades) == 1

    def test_empty_prediction(self):
        contract = parse_prediction_contract({})
        assert contract.skill_id == "unknown"
        assert contract.predictions == []
        assert contract.grades == []

    def test_non_dict_prediction(self):
        contract = parse_prediction_contract("just a string")
        assert len(contract.validation_warnings) > 0

    def test_evidence_ids_from_evidence_pack(self):
        ep = EvidencePackV2(
            pack_id="ep-test",
            source="test",
            summary="test",
            key_signals=[
                {"evidence_id": "ev-1", "response_type": "amplify"},
                {"evidence_id": "ev-2", "response_type": "create"},
            ],
        )
        pred = {"impressions": 50000, "confidence": "high"}
        contract = parse_prediction_contract(pred, evidence_pack_v2=ep)
        imp = contract.predictions[0]
        assert len(imp.evidence_ids) > 0

    def test_high_confidence_without_evidence_warns(self):
        pred = {"impressions": 50000, "confidence": "high"}
        contract = parse_prediction_contract(pred)
        assert any("evidence_ids" in w for w in contract.validation_warnings)

    def test_quantile_ordering_validation(self):
        pred = {
            "impressions": 50000,
            "impressions_p50": 60000,
            "impressions_p80": 50000,
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred)
        assert any("p50" in w and "p80" in w for w in contract.validation_warnings)

    def test_grade_distribution_sum_warning(self):
        pred = {
            "grade": "B+",
            "grade_distribution": {"A": 0.5, "B": 0.2},
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred)
        assert any("sums to" in w for w in contract.validation_warnings)

    def test_to_dict_round_trip(self):
        pred = {
            "impressions": 50000,
            "grade": "A",
            "grade_distribution": {"A": 0.7, "B": 0.3},
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred, skill_id="test")
        d = contract.to_dict()
        assert d["skill_id"] == "test"
        assert len(d["predictions"]) == 1
        assert len(d["grades"]) == 1
        assert d["overall_confidence"] == "medium"

    def test_skips_non_numeric_fields(self):
        pred = {
            "impressions": 50000,
            "verdict": "positive",
            "reasoning": "because...",
            "confidence": "medium",
        }
        contract = parse_prediction_contract(pred)
        targets = [p.target for p in contract.predictions]
        assert "impressions" in targets
        assert "verdict" not in targets
        assert "reasoning" not in targets
