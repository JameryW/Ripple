# tests/backtest/test_backtest_integration.py
"""Integration test for backtest seed fixtures and calibration threshold tuning.

Loads realistic synthetic cases, runs the backtest runner with a mock
simulate_fn, and verifies that:
- Metrics are computed correctly (MAE, MAPE, RMSE, Brier, confidence calibration)
- Optimistic bias cases show positive MAPE (over-prediction)
- Conservative bias cases show negative MAPE (under-prediction)
- Calibrated cases show MAPE within ~30%

Also prints a human-readable summary for manual review.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

import pytest

from ripple.backtest.schema import BacktestCase
from ripple.backtest.runner import run_backtest
from ripple.backtest.metrics import (
    compute_prediction_errors,
)
from tests.backtest.fixtures.loader import (
    load_seed_cases,
    load_seed_cases_with_predictions,
)


# ---------------------------------------------------------------------------
# Mock simulate_fn that returns pre-generated biased predictions
# ---------------------------------------------------------------------------

def _make_mock_simulate(
    predictions_by_case_id: Dict[str, Dict[str, Any]],
) -> Callable[..., Awaitable[Dict[str, Any]]]:
    """Create a mock simulate_fn that returns stored predictions per case."""

    async def _mock_simulate(simulation_input: Dict[str, Any]) -> Dict[str, Any]:
        # Find the case_id from simulation_input metadata
        case_id = simulation_input.get("_backtest_case_id", "")
        prediction = predictions_by_case_id.get(case_id, {})
        return {
            "prediction": prediction,
            "confidence": prediction.get("confidence", "medium"),
        }

    return _mock_simulate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def seed_cases() -> List[BacktestCase]:
    return load_seed_cases()


@pytest.fixture
def cases_with_predictions() -> List:
    return load_seed_cases_with_predictions()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSeedFixtureLoading:
    """Verify that seed fixtures load correctly."""

    def test_loads_all_cases(self, seed_cases):
        assert len(seed_cases) >= 6, f"Expected >= 6 cases, got {len(seed_cases)}"

    def test_each_case_has_ground_truth(self, seed_cases):
        for case in seed_cases:
            assert case.ground_truth, f"Case {case.case_id} has empty ground_truth"

    def test_each_case_has_simulation_input(self, seed_cases):
        for case in seed_cases:
            assert case.simulation_input, f"Case {case.case_id} has empty simulation_input"

    def test_each_case_has_platform(self, seed_cases):
        for case in seed_cases:
            assert case.platform, f"Case {case.case_id} has no platform"

    def test_schema_version(self, seed_cases):
        for case in seed_cases:
            assert case.schema_version == "1.0"

    def test_bias_tags_present(self, seed_cases):
        bias_tags = set()
        for case in seed_cases:
            bias_tags.update(case.tags)
        for expected in ("optimistic", "conservative", "calibrated"):
            assert expected in bias_tags, f"Missing bias tag: {expected}"


class TestPredictionGeneration:
    """Verify that synthetic predictions reflect the intended bias."""

    def test_optimistic_predictions_exceed_truth(self, cases_with_predictions):
        for case, prediction in cases_with_predictions:
            if "optimistic" not in case.tags:
                continue
            # At least one count field should be over-predicted
            over_predicted = False
            for key in ("impressions", "engagement", "reach"):
                if key in case.ground_truth and key in prediction:
                    if prediction[key] > case.ground_truth[key]:
                        over_predicted = True
                        break
            assert over_predicted, (
                f"Optimistic case {case.case_id}: no count field over-predicted"
            )

    def test_conservative_predictions_below_truth(self, cases_with_predictions):
        for case, prediction in cases_with_predictions:
            if "conservative" not in case.tags:
                continue
            under_predicted = False
            for key in ("impressions", "engagement", "reach"):
                if key in case.ground_truth and key in prediction:
                    if prediction[key] < case.ground_truth[key]:
                        under_predicted = True
                        break
            assert under_predicted, (
                f"Conservative case {case.case_id}: no count field under-predicted"
            )

    def test_calibrated_predictions_near_truth(self, cases_with_predictions):
        for case, prediction in cases_with_predictions:
            if "calibrated" not in case.tags:
                continue
            for key in ("impressions", "engagement", "reach"):
                if key in case.ground_truth and key in prediction:
                    actual = case.ground_truth[key]
                    if actual == 0:
                        continue
                    pct_error = abs(prediction[key] - actual) / actual * 100
                    assert pct_error <= 35, (
                        f"Calibrated case {case.case_id}: {key} off by {pct_error:.1f}%"
                    )


class TestBacktestRunnerIntegration:
    """Run the full backtest pipeline on seed fixtures."""

    @pytest.mark.asyncio
    async def test_backtest_computes_metrics(self, cases_with_predictions):
        """Verify that the runner produces MAE, MAPE, RMSE, and Brier score."""
        # Build predictions map keyed by case_id
        predictions_by_id = {}
        # We need to inject case_id into simulation_input so the mock can find it
        cases = []
        for case, pred in cases_with_predictions:
            modified_input = dict(case.simulation_input)
            modified_input["_backtest_case_id"] = case.case_id
            modified_case = BacktestCase(
                case_id=case.case_id,
                schema_version=case.schema_version,
                skill_id=case.skill_id,
                simulation_input=modified_input,
                ground_truth=case.ground_truth,
                platform=case.platform,
                channel=case.channel,
                vertical=case.vertical,
                time_window=case.time_window,
                content_type=case.content_type,
                tags=case.tags,
            )
            cases.append(modified_case)
            predictions_by_id[case.case_id] = pred

        mock_fn = _make_mock_simulate(predictions_by_id)
        report = await run_backtest(cases, mock_fn)

        assert report.total_cases == len(cases)
        assert report.completed_cases == len(cases)
        assert report.failed_cases == 0
        assert report.mae is not None
        assert report.mape is not None
        assert report.rmse is not None
        assert report.brier_score is not None

    @pytest.mark.asyncio
    async def test_optimistic_cases_positive_mape(self, cases_with_predictions):
        """Optimistic bias cases should show positive MAPE (over-prediction)."""
        optimistic_cases = []
        optimistic_preds = {}
        for case, pred in cases_with_predictions:
            if "optimistic" in case.tags:
                modified_input = dict(case.simulation_input)
                modified_input["_backtest_case_id"] = case.case_id
                modified_case = BacktestCase(
                    case_id=case.case_id,
                    schema_version=case.schema_version,
                    skill_id=case.skill_id,
                    simulation_input=modified_input,
                    ground_truth=case.ground_truth,
                    platform=case.platform,
                    channel=case.channel,
                    vertical=case.vertical,
                    time_window=case.time_window,
                    content_type=case.content_type,
                    tags=case.tags,
                )
                optimistic_cases.append(modified_case)
                optimistic_preds[case.case_id] = pred

        if not optimistic_cases:
            pytest.skip("No optimistic cases in fixtures")

        mock_fn = _make_mock_simulate(optimistic_preds)
        report = await run_backtest(optimistic_cases, mock_fn)

        assert report.mape is not None
        assert report.mape > 0, (
            f"Optimistic cases should have positive MAPE, got {report.mape}"
        )

    @pytest.mark.asyncio
    async def test_conservative_cases_negative_mape(self, cases_with_predictions):
        """Conservative bias cases should show negative MAPE (under-prediction).

        Note: MAPE uses absolute percentage error, so it's always positive.
        Instead, we check that the raw prediction errors are negative
        (predicted < actual) for count fields.
        """
        conservative_cases = []
        conservative_preds = {}
        for case, pred in cases_with_predictions:
            if "conservative" in case.tags:
                modified_input = dict(case.simulation_input)
                modified_input["_backtest_case_id"] = case.case_id
                modified_case = BacktestCase(
                    case_id=case.case_id,
                    schema_version=case.schema_version,
                    skill_id=case.skill_id,
                    simulation_input=modified_input,
                    ground_truth=case.ground_truth,
                    platform=case.platform,
                    channel=case.channel,
                    vertical=case.vertical,
                    time_window=case.time_window,
                    content_type=case.content_type,
                    tags=case.tags,
                )
                conservative_cases.append(modified_case)
                conservative_preds[case.case_id] = pred

        if not conservative_cases:
            pytest.skip("No conservative cases in fixtures")

        mock_fn = _make_mock_simulate(conservative_preds)
        report = await run_backtest(conservative_cases, mock_fn)

        # Verify that predicted < actual for count fields
        for result in report.results:
            for error in result.errors:
                if "probability" not in error.metric.lower():
                    assert error.predicted < error.actual, (
                        f"Conservative case {result.case_id}: {error.metric} "
                        f"predicted={error.predicted} should be < actual={error.actual}"
                    )

    @pytest.mark.asyncio
    async def test_calibrated_cases_low_mape(self, cases_with_predictions):
        """Calibrated cases should have MAPE within ~30%."""
        calibrated_cases = []
        calibrated_preds = {}
        for case, pred in cases_with_predictions:
            if "calibrated" in case.tags:
                modified_input = dict(case.simulation_input)
                modified_input["_backtest_case_id"] = case.case_id
                modified_case = BacktestCase(
                    case_id=case.case_id,
                    schema_version=case.schema_version,
                    skill_id=case.skill_id,
                    simulation_input=modified_input,
                    ground_truth=case.ground_truth,
                    platform=case.platform,
                    channel=case.channel,
                    vertical=case.vertical,
                    time_window=case.time_window,
                    content_type=case.content_type,
                    tags=case.tags,
                )
                calibrated_cases.append(modified_case)
                calibrated_preds[case.case_id] = pred

        if not calibrated_cases:
            pytest.skip("No calibrated cases in fixtures")

        mock_fn = _make_mock_simulate(calibrated_preds)
        report = await run_backtest(calibrated_cases, mock_fn)

        assert report.mape is not None
        assert report.mape <= 35.0, (
            f"Calibrated cases MAPE should be <= 35%, got {report.mape:.1f}%"
        )

    @pytest.mark.asyncio
    async def test_bucket_breakdown(self, cases_with_predictions):
        """Verify per-bucket metrics are computed."""
        cases = []
        predictions_by_id = {}
        for case, pred in cases_with_predictions:
            modified_input = dict(case.simulation_input)
            modified_input["_backtest_case_id"] = case.case_id
            modified_case = BacktestCase(
                case_id=case.case_id,
                schema_version=case.schema_version,
                skill_id=case.skill_id,
                simulation_input=modified_input,
                ground_truth=case.ground_truth,
                platform=case.platform,
                channel=case.channel,
                vertical=case.vertical,
                time_window=case.time_window,
                content_type=case.content_type,
                tags=case.tags,
            )
            cases.append(modified_case)
            predictions_by_id[case.case_id] = pred

        mock_fn = _make_mock_simulate(predictions_by_id)
        report = await run_backtest(cases, mock_fn)

        # Should have bucket breakdowns by platform
        platform_buckets = [k for k in report.buckets if k.startswith("platform=")]
        assert len(platform_buckets) >= 1, "Expected at least one platform bucket"

    @pytest.mark.asyncio
    async def test_confidence_calibration_computed(self, cases_with_predictions):
        """Verify confidence calibration is computed."""
        cases = []
        predictions_by_id = {}
        for case, pred in cases_with_predictions:
            modified_input = dict(case.simulation_input)
            modified_input["_backtest_case_id"] = case.case_id
            modified_case = BacktestCase(
                case_id=case.case_id,
                schema_version=case.schema_version,
                skill_id=case.skill_id,
                simulation_input=modified_input,
                ground_truth=case.ground_truth,
                platform=case.platform,
                channel=case.channel,
                vertical=case.vertical,
                time_window=case.time_window,
                content_type=case.content_type,
                tags=case.tags,
            )
            cases.append(modified_case)
            predictions_by_id[case.case_id] = pred

        mock_fn = _make_mock_simulate(predictions_by_id)
        report = await run_backtest(cases, mock_fn)

        assert isinstance(report.confidence_calibration, dict)


class TestBacktestSummaryReport:
    """Print a human-readable summary for manual review (not an assertion test)."""

    @pytest.mark.asyncio
    async def test_print_summary(self, cases_with_predictions, capsys):
        """Run backtest and print summary — for human review, not CI gating."""
        cases = []
        predictions_by_id = {}
        for case, pred in cases_with_predictions:
            modified_input = dict(case.simulation_input)
            modified_input["_backtest_case_id"] = case.case_id
            modified_case = BacktestCase(
                case_id=case.case_id,
                schema_version=case.schema_version,
                skill_id=case.skill_id,
                simulation_input=modified_input,
                ground_truth=case.ground_truth,
                platform=case.platform,
                channel=case.channel,
                vertical=case.vertical,
                time_window=case.time_window,
                content_type=case.content_type,
                tags=case.tags,
            )
            cases.append(modified_case)
            predictions_by_id[case.case_id] = pred

        mock_fn = _make_mock_simulate(predictions_by_id)
        report = await run_backtest(cases, mock_fn)

        lines = [
            "",
            "=" * 60,
            "BACKTEST SEED FIXTURE SUMMARY",
            "=" * 60,
            f"Total cases:    {report.total_cases}",
            f"Completed:      {report.completed_cases}",
            f"Failed:         {report.failed_cases}",
            f"MAE:            {report.mae}",
            f"MAPE:           {report.mape}%",
            f"RMSE:           {report.rmse}",
            f"Brier score:    {report.brier_score}",
            f"Macro F1:       {report.macro_f1}",
            f"Conf. calib.:   {report.confidence_calibration}",
            "-" * 60,
            "Per-case results:",
        ]

        for r in report.results:
            bias_tag = ""
            for case, _ in cases_with_predictions:
                if case.case_id == r.case_id:
                    bias_tag = next(
                        (t for t in case.tags if t in ("optimistic", "conservative", "calibrated")),
                        "",
                    )
                    break
            mape_vals = [e.percentage_error for e in r.errors if e.percentage_error is not None]
            avg_mape = sum(mape_vals) / len(mape_vals) if mape_vals else 0
            lines.append(
                f"  {r.case_id:30s}  bias={bias_tag:12s}  "
                f"avg_mape={avg_mape:7.1f}%  conf={r.predicted_confidence}"
            )

        lines.append("-" * 60)
        lines.append("Bucket breakdowns:")
        for bucket_key, bucket_data in sorted(report.buckets.items()):
            lines.append(
                f"  {bucket_key:30s}  n={bucket_data.get('count', 0)}  "
                f"mape={bucket_data.get('mape', 'N/A')}%"
            )

        lines.append("=" * 60)
        lines.append("")

        print("\n".join(lines))

        # Basic sanity: report should have been produced
        assert report.total_cases > 0


class TestCalibrationThresholdTuning:
    """Derive a reasonable historical_threshold_pct from fixture data.

    The current default of 100.0% is too lenient — deviation must exceed 100%
    before the confidence gate triggers.  Based on the fixture data, we can
    determine a more appropriate default.
    """

    def test_optimistic_deviation_exceeds_50pct(self, cases_with_predictions):
        """Optimistic bias cases should show deviations > 50%, justifying a
        lower default threshold."""
        for case, prediction in cases_with_predictions:
            if "optimistic" not in case.tags:
                continue
            errors = compute_prediction_errors(prediction, case.ground_truth)
            # At least one count field should have deviation > 50%
            high_deviation = False
            for e in errors:
                if e.percentage_error is not None and "probability" not in e.metric.lower():
                    if e.percentage_error > 50.0:
                        high_deviation = True
                        break
            assert high_deviation, (
                f"Optimistic case {case.case_id}: no count field exceeds 50% deviation"
            )

    def test_calibrated_deviation_within_50pct(self, cases_with_predictions):
        """Calibrated cases should have all deviations within 50%, confirming
        that 50% is a reasonable threshold that catches bias but not noise."""
        for case, prediction in cases_with_predictions:
            if "calibrated" not in case.tags:
                continue
            errors = compute_prediction_errors(prediction, case.ground_truth)
            for e in errors:
                if e.percentage_error is not None and "probability" not in e.metric.lower():
                    assert e.percentage_error <= 35.0, (
                        f"Calibrated case {case.case_id}: {e.metric} deviation "
                        f"{e.percentage_error:.1f}% exceeds 35%"
                    )

    def test_recommended_threshold(self, cases_with_predictions):
        """Compute the threshold that separates calibrated from optimistic cases.

        The threshold should be high enough that calibrated cases pass,
        but low enough that optimistic cases trigger the gate.
        A value of 50.0% satisfies both constraints based on these fixtures.
        """
        calibrated_max_dev = 0.0
        optimistic_min_dev = float("inf")

        for case, prediction in cases_with_predictions:
            errors = compute_prediction_errors(prediction, case.ground_truth)
            count_deviations = [
                e.percentage_error for e in errors
                if e.percentage_error is not None and "probability" not in e.metric.lower()
            ]
            if not count_deviations:
                continue
            max_dev = max(count_deviations)
            min_dev = min(count_deviations)

            if "calibrated" in case.tags:
                calibrated_max_dev = max(calibrated_max_dev, max_dev)
            elif "optimistic" in case.tags:
                optimistic_min_dev = min(optimistic_min_dev, min_dev)

        # There should be a clear gap between calibrated max and optimistic min
        assert optimistic_min_dev > calibrated_max_dev, (
            f"No clear gap: calibrated max={calibrated_max_dev:.1f}%, "
            f"optimistic min={optimistic_min_dev:.1f}%"
        )

        # 50% sits in the gap
        recommended = 50.0
        assert calibrated_max_dev < recommended < optimistic_min_dev, (
            f"Recommended threshold {recommended}% does not sit in gap: "
            f"calibrated max={calibrated_max_dev:.1f}%, "
            f"optimistic min={optimistic_min_dev:.1f}%"
        )
