# Backtest Feedback Loop

> Automatic detection and correction of systematic prediction bias via backtest → analysis → optimization → validation cycle.

---

## Scenario: Full Feedback Loop (Path C)

### 1. Scope / Trigger

- Trigger: `ripple backtest optimize` CLI command, or `ripple backtest run --auto-optimize`
- Reads persisted backtest history, computes bias, proposes calibrated params, validates via A/B comparison

### 2. Signatures

```
# Store
BacktestStore(db_path: Path | str = DEFAULT_PATH)
  .save(report: BacktestReport) -> None
  .load(run_id: str) -> BacktestReport | None
  .list_runs(limit: int = 20) -> list[RunSummary]
  .query_recent(n: int = 5) -> list[BacktestReport]
  .delete(run_id: str) -> bool
  .close() -> None

# Analyzer
DeviationAnalyzer(min_runs: int = 2)
  .analyze(reports: list[BacktestReport]) -> DeviationReport

# Optimizer
ParameterOptimizer(current_params: dict | None = None, grid: dict | None = None)
  .optimize(deviation: DeviationReport) -> OptimizationResult

# Validator
ABValidator(degradation_threshold: float = 0.10)
  .validate(baseline: BacktestReport, trial: BacktestReport) -> ValidationResult
  .should_rollback(result: ValidationResult) -> bool
  .rollback() -> dict  # returns previous params
```

### 3. Contracts

#### BacktestReport (extended fields)

| Field | Type | Added by |
|-------|------|----------|
| `run_id` | `str` | UUID4, set by runner |
| `timestamp` | `str` | ISO format, set by runner |
| `params_snapshot` | `dict` | Tunable params at run time |

#### DeviationReport

| Field | Type | Description |
|-------|------|-------------|
| `overall_bias` | `BiasDirection` | `over_predict`, `under_predict`, `neutral` |
| `overall_signed_mape` | `float` | Mean signed MAPE across all cases |
| `per_metric` | `dict[str, MetricDeviation]` | Per-metric bias signals |
| `run_count` | `int` | Number of reports analyzed |

#### OptimizationResult

| Field | Type | Description |
|-------|------|-------------|
| `proposed_params` | `dict[str, float]` | New param values |
| `current_params` | `dict[str, float]` | Params at optimization time |
| `score` | `float` | Candidate score (lower = better) |
| `improvement_estimate` | `float` | Expected MAPE reduction |
| `candidates_evaluated` | `int` | Total grid points scored |

#### ValidationResult

| Field | Type | Description |
|-------|------|-------------|
| `passed` | `bool` | True if no metric degraded > threshold |
| `mape_change_pct` | `dict[str, float or None]` | Per-case MAPE change percentage |
| `rolled_back` | `bool` | Whether rollback was executed |
| `baseline_report` | `BacktestReport or None` | Baseline for comparison |
| `trial_report` | `BacktestReport or None` | Trial with proposed params |

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| < 2 reports in store | `DeviationAnalyzer` raises `ValueError` |
| Neutral bias | Optimizer returns DEFAULT_PARAMS unchanged |
| New params degrade any metric > 10% | `should_rollback()` returns True, `rollback()` restores previous params |
| No previous params to rollback to | `rollback()` returns `DEFAULT_PARAMS` |
| Store path does not exist | `BacktestStore.save()` creates parent dirs |
| Single report in store | Optimizer produces neutral result, A/B trivially passes |

### 5. Good/Base/Bad Cases

- **Good**: 3+ persisted runs with consistent over-predict bias → optimizer lowers thresholds → A/B validates improvement
- **Base**: 2 runs with mixed bias → neutral result → DEFAULT_PARAMS returned
- **Bad**: Optimizer proposes aggressive param shift → A/B catches > 10% degradation → rollback fires

### 6. Tests Required

| Module | Key Assertions |
|--------|---------------|
| `test_store.py` | Save/load roundtrip, list ordering (newest first), query_recent, delete, string-path coercion |
| `test_analyzer.py` | Over/under/neutral bias detection, min_runs enforcement, per-metric breakdown |
| `test_optimizer.py` | Over-predict → lower thresholds, under-predict → higher thresholds, neutral → defaults, custom grid |
| `test_validator.py` | No-degradation passes, degradation triggers rollback, rollback returns old params, custom threshold |

### 7. Wrong vs Correct

#### Wrong: Skipping A/B validation

```python
# Apply proposed params directly without validation
params = optimizer.optimize(deviation).proposed_params
apply_params(params)  # Could degrade metrics!
```

#### Correct: A/B validate before applying

```python
result = optimizer.optimize(deviation)
trial_report = run_backtest(cases, params=result.proposed_params)
validation = validator.validate(baseline_report, trial_report)
if not validation.passed:
    params = validator.rollback()
```

---

## Tunable Parameters

| Parameter | Owner | Default | Effect |
|-----------|-------|---------|--------|
| `threshold` | `HistoricalCalibrator` | 100.0 | Deviation % triggering `lower_confidence` |
| `p95_hard_cap` | `HistoricalCalibrator` | 200.0 | Deviation % above P95 → confidence "low" |
| `historical_threshold_pct` | `ConfidenceGate` | 50.0 | Gate triggers when deviation > this % |

Grid search: 4 values per param × 3 params = 64 candidates.

---

## CLI Commands

```bash
# Run backtest and persist results
ripple backtest run --persist

# List persisted runs
ripple backtest history [--limit N] [--json]

# Run full feedback loop
ripple backtest optimize

# Run with auto-optimize after persist
ripple backtest run --persist --auto-optimize
```

---

## Data Flow

```
run --persist
  → BacktestReport (with run_id, timestamp, params_snapshot)
  → BacktestStore.save()

optimize
  → BacktestStore.query_recent()
  → DeviationAnalyzer.analyze() → DeviationReport
  → ParameterOptimizer.optimize() → OptimizationResult
  → run_backtest(proposed_params) → trial BacktestReport
  → ABValidator.validate(baseline, trial) → ValidationResult
  → if not passed: ABValidator.rollback() → previous params
```

---

## Future Extension Points

- `query_recent(n)` reserved for `ripple doctor` bias trend surfacing
- `sample_ratio` parameter in optimizer reserved for when real LLM backtest makes full re-run expensive
- File lock / `is_running` flag in store for concurrent optimize protection
