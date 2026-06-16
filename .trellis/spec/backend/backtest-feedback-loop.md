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
  .validate(cases, simulate_fn, old_params, new_params) -> ValidationResult
  .should_rollback(result: ValidationResult) -> bool
  .rollback(old_params: dict) -> dict

# Persistence (Path C → CalibrationDataStore)
CalibrationDataStore(data_dir: Path | None = None)
  .set_effective_threshold(bucket_key, threshold_pct) -> None
  .get_effective_threshold(bucket_key, default) -> float | None
  .set_calibrator_params(bucket_key, threshold, p95_hard_cap) -> None
  .get_calibrator_params(bucket_key) -> dict | None

apply_optimization_result(opt_result: OptimizationResult, store: CalibrationDataStore, bucket_key: str, validation_passed: bool) -> dict
  # Returns {"written": bool, "reason": str, "params_written": dict}

# Runtime bridges
get_calibrated_threshold(bucket_context, default, store) -> float
get_calibrated_calibrator_params(bucket_context, store) -> dict  # {"threshold": X, "p95_hard_cap": Y}
apply_calibrator_feedback(bucket_context, store) -> dict          # {"threshold": X, "p95_hard_cap": Y}

# CLI
ripple-cli backtest optimize [--apply] [--recent N] [--json]
```

### 3. Contracts

#### BacktestReport (extended fields)

| Field | Type | Added by |
|-------|------|----------|
| `run_id` | `str` | UUID4, set by runner |
| `timestamp` | `str` | ISO format, set by runner |
| `params_snapshot` | `dict` | Tunable params at run time |
| `ensemble_stability` | `str or None` | Worst stability across cases ("high"/"medium"/"low") |
| `tribunal_divergence` | `str or None` | Worst divergence across cases ("high"/"medium"/"low") |
| `evidence_balance` | `dict[str, int]` | Aggregated {"positive": N, "negative": N, "silent": N} |
| `input_completeness` | `float or None` | Mean completeness across cases (0.0-1.0) |
| `historical_deviation` | `float or None` | Max historical deviation pct |
| `residual_risks` | `list[str]` | Deduplicated risk warnings |
| `quality_report_dict` | `dict or None` | Raw quality_report dump for future fields |

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
| `--apply` not set (default) | Dry-run: no CalibrationDataStore writes, print yellow status |
| `--apply` set, A/B passed | Write all 3 params to CalibrationDataStore, print green status |
| `--apply` set, A/B failed | No writes, print red status (rollback) |
| Partial proposed_params (missing threshold or p95_hard_cap) | `apply_optimization_result` logs warning, skips calibrator_params write, still writes historical_threshold_pct |
| `apply_optimization_result` exception | Returns `{"written": False, "reason": str(exc)}` (non-fatal) |

### 5. Good/Base/Bad Cases

- **Good**: 3+ persisted runs with consistent over-predict bias → optimizer lowers thresholds → A/B validates improvement → `--apply` writes to CalibrationDataStore → subsequent simulate uses calibrated params
- **Base**: 2 runs with mixed bias → neutral result → DEFAULT_PARAMS returned → dry-run, no writes
- **Bad**: Optimizer proposes aggressive param shift → A/B catches > 10% degradation → rollback fires → even with `--apply`, no writes

### 6. Tests Required

| Module | Key Assertions |
|--------|---------------|
| `test_store.py` | Save/load roundtrip, list ordering (newest first), query_recent, delete, string-path coercion |
| `test_analyzer.py` | Over/under/neutral bias detection, min_runs enforcement, per-metric breakdown |
| `test_optimizer.py` | Over-predict → lower thresholds, under-predict → higher thresholds, neutral → defaults, custom grid |
| `test_validator.py` | No-degradation passes, degradation triggers rollback, rollback returns old params, custom threshold |
| `test_backtest.py` | Quality signal extraction, aggregation, backward compatibility, graceful defaults |
| `test_calibration_feedback.py` | `apply_optimization_result` writes all 3 params; validation_failed → no write; partial params → warning + skip calibrator_params; `get_calibrated_calibrator_params` bucket matching; `set/get_calibrator_params` roundtrip |

### 7. Wrong vs Correct

#### Wrong: Skipping A/B validation

```python
# Apply proposed params directly without validation
params = optimizer.optimize(deviation).proposed_params
apply_optimization_result(opt_result, store, validation_passed=True)  # Always writes!
```

#### Correct: A/B validate before applying

```python
result = optimizer.optimize(deviation)
val_result = await validator.validate(cases, simulate_fn, current_params, result.proposed_params)
if val_result.passed and not val_result.rolled_back:
    apply_optimization_result(opt_result, store, validation_passed=True)
else:
    restored = validator.rollback(current_params)
```

#### Wrong: Always write on `--apply` regardless of validation

```python
if apply_flag:
    apply_optimization_result(opt_result, store, validation_passed=True)  # Ignores A/B result!
```

#### Correct: Gate write on A/B validation result

```python
if apply_flag and val_result.passed and not val_result.rolled_back:
    apply_optimization_result(opt_result, store, validation_passed=True)
elif apply_flag:
    # A/B failed — do NOT write
    console.print("[red]A/B validation failed — optimization result NOT written[/red]")
```

---

## Scenario: Calibration Feedback Loop (Path B — Auto-Threshold)

### 1. Scope / Trigger

- Trigger: `run_backtest()` called with `calibration_feedback_config` parameter
- Automatic: BacktestReport.signed_mape → BiasPattern → CalibrationDataStore → HistoricalCalibrator → ConfidenceGate
- No CLI command needed; fires automatically when backtest runs with config enabled

### 2. Signatures

```python
# Config
@dataclass(frozen=True)
class CalibrationFeedbackConfig:
    enabled: bool = True
    feedback_strength: float = 0.5       # [0.0, 1.0] — 0.0 = no adjustment, 1.0 = full adjustment
    cooldown_period: int = 3              # minimum new backtest runs between adjustments
    min_cases_for_adjustment: int = 5     # minimum backtest cases to trigger adjustment
    bias_threshold_pct: float = 10.0      # |signed_mape| must exceed this to trigger

# Data models
@dataclass(frozen=True)
class BiasPattern:
    bucket_key: str       # "" for global, "platform=xiaohongshu" for per-bucket
    signed_mape: float    # positive = over-predict, negative = under-predict
    sample_size: int
    timestamp: str        # ISO 8601
    run_id: str

@dataclass(frozen=True)
class CalibrationAdjustment:
    bucket_key: str
    old_threshold_pct: float
    new_threshold_pct: float
    reason: str
    timestamp: str
    run_id: str
    feedback_strength: float

# Store
CalibrationDataStore(data_dir: Path | None = None)
  .save_bias_pattern(pattern: BiasPattern) -> None
  .get_bias_patterns(bucket_key: str | None = None) -> List[BiasPattern]
  .save_adjustment(adjustment: CalibrationAdjustment) -> None
  .get_adjustments(bucket_key: str | None = None) -> List[CalibrationAdjustment]
  .set_effective_threshold(bucket_key: str, threshold_pct: float) -> None
  .get_effective_threshold(bucket_key: str, default: float | None = 50.0) -> float | None
  .get_all_thresholds() -> Dict[str, float]

# Core functions
extract_bias_patterns(report: BacktestReport, config: CalibrationFeedbackConfig) -> List[BiasPattern]
compute_threshold_adjustment(patterns: List[BiasPattern], store: CalibrationDataStore, config: CalibrationFeedbackConfig) -> List[CalibrationAdjustment]
apply_feedback(report: BacktestReport, store: CalibrationDataStore, config: CalibrationFeedbackConfig | None = None) -> List[CalibrationAdjustment]
get_calibrated_threshold(bucket_context: Dict[str, Any] | None = None, default: float = 50.0, store: CalibrationDataStore | None = None) -> float

# Bridge function (in historical_calibrator.py)
apply_calibration_feedback(bucket_context: Dict[str, Any] | None = None, default: float = 50.0, store: CalibrationDataStore | None = None) -> float
```

### 3. Contracts

#### Storage Layout

```
~/.ripple/data/calibration/
  bias_patterns.json     — append-only list of BiasPattern dicts
  adjustments.json       — append-only list of CalibrationAdjustment dicts
  thresholds.json        — current effective thresholds {bucket_key: float}
```

#### BiasPattern Extraction Rules

| Source | Condition | Pattern Generated |
|--------|-----------|-------------------|
| `report.signed_mape` | `completed_cases >= min_cases_for_adjustment` AND `abs(signed_mape) >= bias_threshold_pct` | Global pattern (`bucket_key=""`) |
| `report.buckets[key]` | `count >= min_cases_for_adjustment` AND `abs(signed_mape) >= bias_threshold_pct` | Per-bucket pattern (`bucket_key="platform=xiaohongshu"`) |

#### Threshold Adjustment Formula

```
raw_adjustment = signed_mape * feedback_strength
clamped_adjustment = clamp(raw_adjustment, -15, +15)  # max ±15% per adjustment
new_threshold = clamp(current_threshold + clamped_adjustment, 10, 200)
```

Direction:
- `signed_mape > 0` (over-prediction) → raise threshold → stricter gating
- `signed_mape < 0` (under-prediction) → lower threshold → more permissive gating

#### Cooldown Logic

Check last `cooldown_period` adjustments for the bucket. If the current pattern's `run_id` appears in any of those adjustments, skip (prevents duplicate adjustments from the same backtest run). This does NOT permanently freeze buckets — old adjustments with different run_ids are allowed.

> **Critical gotcha**: Previous implementation counted total adjustments and compared against `cooldown_period`, which permanently froze buckets once they accumulated enough adjustments. The correct logic only checks the most recent `cooldown_period` entries and only blocks same-run duplicates.

#### Bucket Matching Priority (get_calibrated_threshold)

1. Exact multi-field match: `"platform=xiaohongshu,channel=generic"`
2. Single-field match: `"platform=xiaohongshu"` (first matching field)
3. Global threshold: `bucket_key=""`
4. Default: `50.0`

#### Runtime Integration

- `SimulationRuntime._calibrate_historical()`: calls `apply_calibration_feedback()` → stores result in `self._calibrated_historical_threshold`
- `SimulationRuntime._evaluate_confidence_gate()`: passes `self._calibrated_historical_threshold` as `historical_threshold_pct` to `ConfidenceGate.evaluate()`
- `SimulationRuntime.run()`: resets `_calibrated_historical_threshold = 50.0` at start (prevents threshold leaking across runs when runtime is reused)

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| `config.enabled = False` | `apply_feedback()` returns `[]`, no I/O |
| `feedback_strength < 0` or `> 1` | `ValueError` in `__post_init__` |
| `cooldown_period < 0` | `ValueError` in `__post_init__` |
| `min_cases_for_adjustment < 0` | `ValueError` in `__post_init__` |
| `bias_threshold_pct < 0` | `ValueError` in `__post_init__` |
| No significant bias detected | Returns `[]`, no I/O |
| Cooldown active (same run_id) | Skip adjustment, log info |
| Store file corrupted | `logger.warning`, return empty list |
| Store write fails | `logger.warning`, exception propagated to caller |
| `apply_feedback()` exception | `logger.warning`, return `[]` (non-fatal) |
| `get_calibrated_threshold()` store creation fails | Return `default` (50.0) |
| Runtime reused across simulations | `_calibrated_historical_threshold` reset to 50.0 at `run()` start |

### 5. Good/Base/Bad Cases

- **Good**: Backtest with 20 cases, `signed_mape = +35%` → threshold raised from 50% to 67.5% (50 + 35×0.5 = 67.5, capped at 65) → subsequent simulations gate more strictly → predictions become more calibrated
- **Base**: Backtest with `signed_mape = +5%` (< `bias_threshold_pct=10%`) → no adjustment
- **Bad**: Backtest with `signed_mape = +200%`, `feedback_strength = 1.0` → raw adjustment = +200%, clamped to +15% → threshold goes from 50% to 65% (not extreme)

### 6. Tests Required

| Module | Key Assertions |
|--------|---------------|
| `test_calibration_feedback.py` | Bias extraction from global signed_mape; per-bucket extraction; min_cases filter; bias_threshold filter; threshold adjustment direction; feedback_strength dampening; max single adjustment cap (±15%); threshold bounds [10, 200]; cooldown skip same run_id; cooldown NOT freeze old adjustments; config validation (negative values, over-range); disabled config; CalibrationDataStore save/load roundtrip; atomic write; corrupted file tolerance |
| `test_historical_calibrator.py` | `apply_calibration_feedback()` returns float; passes store param; returns calibrated value from store |
| `test_backtest_integration.py` | `run_backtest(calibration_feedback_config=...)` triggers feedback; adjustments persisted |

### 7. Wrong vs Correct

#### Wrong: Cumulative cooldown permanently freezes buckets

```python
# Count ALL historical adjustments for a bucket
recent_adjustments = store.get_adjustments(bucket_key)
if len(recent_adjustments) >= config.cooldown_period:
    continue  # Once bucket has N adjustments, it's permanently frozen!
```

#### Correct: Only check recent adjustments for same-run duplicates

```python
# Only look at recent N adjustments
recent_subset = recent_adjustments[:config.cooldown_period]
latest_run_ids = {a.run_id for a in recent_subset}
if pattern.run_id in latest_run_ids:
    continue  # Skip duplicate from same backtest run, allow new runs
```

#### Wrong: Threshold leaks across simulation runs

```python
class SimulationRuntime:
    def _calibrate_historical(self, ...):
        self._calibrated_historical_threshold = get_calibrated_threshold(...)
        # NOT reset at run() start → previous run's threshold leaks into next run
```

#### Correct: Reset threshold at start of each simulation run

```python
class SimulationRuntime:
    def run(self, ...):
        self._calibrated_historical_threshold = 50.0  # Reset for each run
        # ... rest of run logic
```

#### Wrong: Missing config validation on frozen dataclass

```python
@dataclass(frozen=True)
class CalibrationFeedbackConfig:
    feedback_strength: float = 0.5
    # No __post_init__ → negative values accepted → adjustment direction inverted!
```

#### Correct: Validate in __post_init__

```python
@dataclass(frozen=True)
class CalibrationFeedbackConfig:
    feedback_strength: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.feedback_strength <= 1.0:
            raise ValueError(f"feedback_strength must be in [0.0, 1.0], got {self.feedback_strength}")
```

---

## Tunable Parameters

| Parameter | Owner | Default | Effect |
|-----------|-------|---------|--------|
| `threshold` | `HistoricalCalibrator` | 100.0 | Deviation % triggering `lower_confidence` |
| `p95_hard_cap` | `HistoricalCalibrator` | 200.0 | Deviation % above P95 → confidence "low" |
| `historical_threshold_pct` | `ConfidenceGate` | 50.0 | Gate triggers when deviation > this % |
| `feedback_strength` | `CalibrationFeedbackConfig` | 0.5 | How aggressively to adjust thresholds (0-1) |
| `cooldown_period` | `CalibrationFeedbackConfig` | 3 | Min new backtest runs between adjustments |
| `min_cases_for_adjustment` | `CalibrationFeedbackConfig` | 5 | Min backtest cases needed for adjustment |
| `bias_threshold_pct` | `CalibrationFeedbackConfig` | 10.0 | Min |signed_mape| to trigger adjustment |

Grid search: 4 values per param × 3 params = 64 candidates (original tunables only).

---

## Data Flow

### Path B — Auto-Threshold (run_backtest → CalibrationDataStore)

```
run_backtest(calibration_feedback_config=...)
  → simulate_fn per case → extract quality signals
  → BacktestReport (with signed_mape, buckets)
  → apply_feedback()
    → extract_bias_patterns() → List[BiasPattern]
    → compute_threshold_adjustment() → List[CalibrationAdjustment]
    → CalibrationDataStore.save_bias_pattern()
    → CalibrationDataStore.save_adjustment()
    → CalibrationDataStore.set_effective_threshold()
  → BacktestStore.save() (if persist=True)
```

### Path C — Full Optimize Loop (CLI → CalibrationDataStore)

```
ripple-cli backtest optimize --apply
  → BacktestStore.query_recent(n=5)
  → DeviationAnalyzer.analyze() → DeviationReport
  → ParameterOptimizer.optimize() → OptimizationResult (3 params)
  → ABValidator.validate(old_params, new_params) → ValidationResult
  → if --apply and val_result.passed and not rolled_back:
      → apply_optimization_result(opt_result, store, validation_passed=True)
        → CalibrationDataStore.set_effective_threshold("historical_threshold_pct")
        → CalibrationDataStore.set_calibrator_params(threshold, p95_hard_cap)
  → else if --apply and failed:
      → no writes, print rollback
  → else (no --apply):
      → dry-run, no writes
```

### Runtime reads calibrated params (both Path B and Path C)

```
simulate()
  → SimulationRuntime.run()
    → _calibrated_historical_threshold = 50.0  # reset
    → _calibrated_calibrator_params = None     # reset
    → _calibrate_historical()
      → apply_calibration_feedback() → get_calibrated_threshold()
        → ConfidenceGate: historical_threshold_pct
      → apply_calibrator_feedback() → get_calibrated_calibrator_params()
        → HistoricalCalibrator(threshold=X, p95_hard_cap=Y)
    → _evaluate_confidence_gate()
      → ConfidenceGate.evaluate(historical_threshold_pct=self._calibrated_historical_threshold)
```

### CalibrationDataStore File Layout

```
~/.ripple/data/calibration/
  bias_patterns.json       — append-only list of BiasPattern dicts
  adjustments.json         — append-only list of CalibrationAdjustment dicts
  thresholds.json          — {bucket_key: historical_threshold_pct}
  calibrator_params.json   — {bucket_key: {"threshold": float, "p95_hard_cap": float}}
```

---

## Future Extension Points

- `query_recent(n)` reserved for `ripple doctor` bias trend surfacing
- `sample_ratio` parameter in optimizer reserved for when real LLM backtest makes full re-run expensive
- File lock / `is_running` flag in store for concurrent optimize protection
- Cross-skill calibration sharing (currently out of scope)
- Adaptive feedback_strength: auto-tune based on adjustment success rate
- Real LLM A/B validation (currently uses mock simulate_fn from seed fixtures)
