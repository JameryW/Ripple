# Fix ensemble merging, stability direction, median adjustment reachability, backtest validity

## Goal

Fix four interrelated bugs in the quality-control pipeline so that ensemble aggregation actually improves predictions, stability flags the worst dimension, historical median adjustment is reachable for the common case, and backtest metrics can prove directional accuracy.

## What I already know

**Bug 1 — Ensemble merge is decorative only:**
- `simulate.py:920` does `merged = dict(last)` — takes the last run's result verbatim
- `ensemble_stats` are attached but never used to replace prediction values
- `_evaluate_confidence_gate` runs inside each `runtime.run()` (per-run), so it cannot see post-aggregation `ensemble_stats`
- `ConfidenceGate.evaluate()` accepts `ensemble_stability` and `ensemble_kappa` params, but these are always `None` during per-run evaluation because ensemble stats don't exist yet

**Bug 2 — Stability direction inverted:**
- `quality_report.py:118`: `min(levels, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x, 1))` — this picks the "minimum" key value, which is `high` (0), not the worst `low` (2)
- Same pattern in `runtime.py:1756` inside `_evaluate_confidence_gate`
- When dimensions have mixed high/low stability, we should report "low" (worst), not "high" (best)

**Bug 3 — Median adjustment unreachable:**
- `historical_calibrator.py:294`: only produces `calibrated_prediction` action when `predicted > P95`
- `runtime.py:1841`: `_apply_calibrated_predictions` only processes `calibrated_prediction` actions
- So `median < predicted <= P95` never triggers median adjustment — the path in `_apply_calibrated_predictions` at line 1871-1873 is dead code
- Test at `test_runtime.py:1146` manually constructs this case with a false reason ("exceeds P95" when predicted=550 < P95=650), covering a non-real path

**Bug 4 — Backtest proves framework, not accuracy:**
- `loader.py:88`: `_generate_prediction` applies fixed bias multipliers to ground truth
- Integration tests mock predictions directly, not running real runtime/LLM/calibration
- `metrics.py:127`: `percentage_error = abs(error) / abs(actual) * 100` is always positive — can't distinguish over- vs under-prediction

## Assumptions (temporary)

* The per-run confidence gate logic is sound; we just need a second pass after ensemble aggregation
* For stability, "worst" means the lowest stability level (low < medium < high)
* For median adjustment, the calibrator should produce a new action type for the median < predicted <= P95 case
* Backtest fix should add directional MAPE but keep existing absolute MAPE for backward compat

## Open Questions

(All resolved — see Decision below)

## Requirements (evolving)

* R1: After ensemble runs complete, replace numeric prediction fields with ensemble medians from `numeric_distributions`
* R2: Re-run confidence gate with ensemble-level stats (kappa, stability, agreement rate) on the merged result
* R3: Fix stability direction — use `max` instead of `min` (or flip the key mapping) so worst level is selected
* R4: Add `median_adjustment` action type in HistoricalCalibrator for `median < predicted <= P95` case
* R5: Process `median_adjustment` actions in `_apply_calibrated_predictions` to apply median values
* R6: Add `signed_mape` (symmetric signed MAPE) to `compute_numeric_metrics` — `(predicted - actual) / ((predicted + actual) / 2) * 100`, keeps existing absolute `mape`
* R7: Fix or replace the synthetic-only backtest test to cover real calibration chain

## Acceptance Criteria

* [ ] Ensemble merge uses median values from `numeric_distributions`, not last-run values
* [ ] Post-ensemble confidence gate runs with ensemble kappa/stability/agreement data
* [ ] `ensemble_stability` picks "low" when any dimension has "low" stability (not "high")
* [ ] Same fix in `_evaluate_confidence_gate` and `build_quality_report`
* [ ] HistoricalCalibrator produces `median_adjustment` action when `median < predicted <= P95`
* [ ] `_apply_calibrated_predictions` handles `median_adjustment` actions (replaces predicted with median)
* [ ] `compute_numeric_metrics` returns both `mape` (absolute) and `signed_mape` (symmetric signed: `(p-a)/((p+a)/2)*100`, positive=over-predict, negative=under-predict)
* [ ] New test: `median_adjustment` action flows from calibrator → runtime without manual construction
* [ ] Existing tests still pass (116 passed baseline)

## Definition of Done

* All new/modified paths have unit tests
* Lint / typecheck green
* No regression in existing 116 tests
* Backward compat: absolute MAPE key unchanged, `signed_mape` is additive (symmetric signed formula)

## Out of Scope

* Real-chain backtest with actual LLM calls (operational complexity, separate task)
* CLI `--real-chain` flag (separate task if needed)
* Ensemble parallelism (current serial execution is correct for shared budget)
* Re-architecting confidence gate out of runtime (out of scope for this fix)

## Technical Notes

### Key files and line numbers

| File | Lines | Role |
|------|-------|------|
| `ripple/api/simulate.py` | 671-929 | `_run_ensemble`: merge logic, ensemble_stats assembly |
| `ripple/api/simulate.py` | 745-804 | `_aggregate_numeric_predictions`: already computes medians |
| `ripple/engine/runtime.py` | 1702-1812 | `_evaluate_confidence_gate`: per-run gate evaluation |
| `ripple/engine/runtime.py` | 1814-1900 | `_apply_calibrated_predictions`: action processing |
| `ripple/engine/quality_report.py` | 105-118 | Ensemble stability derivation (bug 2) |
| `ripple/primitives/prediction_quality.py` | 447-528 | `ConfidenceGate.evaluate` |
| `ripple/providers/historical_calibrator.py` | 215-333 | `HistoricalCalibrator.calibrate` |
| `ripple/backtest/metrics.py` | 17-42 | `compute_numeric_metrics` |
| `ripple/backtest/fixtures/loader.py` | 80-110 | `_generate_prediction` (synthetic bias) |
| `tests/engine/test_runtime.py` | 1062-1200 | Calibration + median adjustment tests |
| `tests/engine/test_quality_report.py` | — | Quality report tests |
| `tests/primitives/test_prediction_quality.py` | 237+ | Confidence gate tests |

### Dependencies between fixes

* R1+R2 (ensemble merge + re-gate) must be done together — they're the same codepath
* R3 (stability direction) is independent but should be consistent across both call sites
* R4+R5 (median adjustment) are a single calibrator change + runtime handler
* R6+R7 (backtest) are independent of R1-R5

## Decision (ADR-lite)

**Context**: Three preference decisions needed for implementation approach.

**Decisions**:
1. Post-ensemble confidence gate runs in `_run_ensemble` (not inside `_run_phases`) — it has access to `ensemble_stats` and doesn't require runtime restructuring
2. New `median_adjustment` action type in HistoricalCalibrator (not lowering `calibrated_prediction` threshold) — preserves existing P95 semantics, clearer audit trail
3. Real-chain backtest is out of scope for this task — only add `signed_mape` metric and fix synthetic test coverage
4. Directional MAPE uses symmetric signed formula: `(predicted - actual) / ((predicted + actual) / 2) * 100` — works when actual=0 as long as predicted≠0

**Consequences**: `_run_ensemble` gains a dependency on `ConfidenceGate`; calibrator output gains a new action type (backward compatible); metrics output gains a key (backward compatible).
