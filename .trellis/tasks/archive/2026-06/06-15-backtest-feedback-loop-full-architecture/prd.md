# Backtest Feedback Loop — Full Architecture (Path C)

## Goal

Close the backtest loop so that systematic prediction bias is automatically detected and corrected: backtest results feed into a deviation analyzer, which drives a parameter optimizer, which produces calibrated parameters for the next simulation run.

## What I already know

* `HistoricalCalibrator` has two tunable params: `threshold` (default 100.0) and `p95_hard_cap` (default 200.0)
* `ConfidenceGate.evaluate` has one tunable param: `historical_threshold_pct` (default 50.0)
* Backtest currently prints results to stdout / JSON but **never persists them** — next run starts from scratch
* `signed_mape` (added in previous task) gives direction: positive = over-predict, negative = under-predict
* `median_adjustment` action type (added in previous task) provides the execution path for median-based correction
* Current backtest uses synthetic predictions with fixed bias multipliers — no real LLM calls

## Assumptions (temporary)

* The feedback loop operates on real backtest history (persisted to disk), not just in-memory
* Parameter optimization is gradient-free (no autodiff) — simple grid search or Bayesian optimization
* Rollback is handled by versioning parameter snapshots, not by branching code
* A/B validation runs a second backtest with new params against the same cases

## Open Questions

* Persistence format: SQLite — structured queries, atomic writes, indexed by run_id/timestamp
* Optimizer scope: only 3 threshold params (`threshold`, `p95_hard_cap`, `historical_threshold_pct`) — grid search, no gate factor weights
* Validation strategy: full re-run (no sampling) — mock predictions have zero LLM cost
* Trigger: manual `ripple backtest optimize` command + `ripple backtest run --auto-optimize` flag for CI

## Requirements (evolving)

* R1: Persist backtest results (BacktestReport + per-case results) to a versioned store on disk
* R2: Implement a DeviationAnalyzer that reads persisted history and computes systematic bias per metric
* R3: Implement a ParameterOptimizer that takes bias signals and proposes new parameter values
* R4: Implement A/B validation — run backtest with old params vs new params on same case set
* R5: Implement rollback — restore previous parameter snapshot if new params degrade metrics
* R6: CLI command `ripple backtest optimize` to trigger the full loop
* R7: CLI command `ripple backtest history` to list persisted runs

## Acceptance Criteria

* [ ] `ripple backtest run` persists a BacktestReport to disk with a unique run ID
* [ ] `ripple backtest history` lists all persisted runs with timestamps and MAPE summaries
* [ ] `ripple backtest optimize` reads recent history, computes bias, proposes new params
* [ ] Proposed params are A/B validated against the same case set as the baseline
* [ ] If new params degrade any metric > 10%, rollback is triggered automatically
* [ ] Parameter snapshots are versioned and restorable
* [ ] Existing backtest tests continue to pass
* [ ] New unit tests for DeviationAnalyzer, ParameterOptimizer, rollback logic

## Definition of Done

* All new/modified paths have unit tests
* Lint / typecheck green
* No regression in existing tests
* CLI commands documented in help output

## Out of Scope

* Real-chain backtest with actual LLM calls (separate task)
* Automatic optimization on every `backtest run` (manual trigger only for MVP)
* Optimizing ConfidenceGate factor weights (only threshold params for MVP)
* Multi-skill parameter optimization (single skill scope for MVP)
* Asynchronous/long-running optimize jobs (blocking CLI for MVP)
* Parameter optimization across skill versions (single version scope)

## Expansion Considerations (not in MVP, but reserve extension points)

* **Future evolution**: Real LLM backtest will make full re-run expensive — reserve `sample_ratio` parameter in optimizer for future partial-run mode
* **Related scenarios**: `ripple doctor` should be able to read persisted backtest history and surface bias trends; reserve a `store.query_recent(n=5)` method
* **Failure/edge cases**: Concurrent `optimize` runs on same store could conflict — reserve a file lock or `is_running` flag in store; rollback when new params degrade > 10% on any metric

## Technical Notes

### Key tunable parameters

| Parameter | Owner | Default | Effect |
|-----------|-------|---------|--------|
| `threshold` | `HistoricalCalibrator` | 100.0 | Deviation % that triggers `lower_confidence` |
| `p95_hard_cap` | `HistoricalCalibrator` | 200.0 | Deviation % above P95 → confidence "low" |
| `historical_threshold_pct` | `ConfidenceGate` | 50.0 | Gate triggers when deviation > this % |

### Files to create/modify

| File | Role |
|------|------|
| `ripple/backtest/store.py` | Persistence layer: save/load BacktestReport via SQLite |
| `ripple/backtest/analyzer.py` | DeviationAnalyzer: systematic bias detection |
| `ripple/backtest/optimizer.py` | ParameterOptimizer: propose new param values |
| `ripple/backtest/validator.py` | A/B validation + rollback logic |
| `ripple/backtest/schema.py` | Add `run_id`, `timestamp`, `params_snapshot` to BacktestReport |
| `ripple/cli/app.py` | Add `backtest history` and `backtest optimize` commands, `--auto-optimize` flag |
| `tests/backtest/test_store.py` | Persistence tests |
| `tests/backtest/test_analyzer.py` | Analyzer tests |
| `tests/backtest/test_optimizer.py` | Optimizer tests |
| `tests/backtest/test_validator.py` | Validation/rollback tests |

## Decision (ADR-lite)

**Context**: Four preference decisions needed for implementation.

**Decisions**:
1. SQLite for persistence — structured queries, atomic writes, indexed by run_id/timestamp. Zero external dependency (stdlib `sqlite3`).
2. Only 3 threshold params in optimizer scope — `threshold`, `p95_hard_cap`, `historical_threshold_pct`. Grid search (3-5 values per dim = 27-125 combos). No gate factor weights to avoid overfitting.
3. Manual `ripple backtest optimize` + `ripple backtest run --auto-optimize` flag — default safe, CI-friendly.
4. Full re-run for A/B validation — mock predictions have zero LLM cost, so no sampling bias trade-off needed.

**Consequences**: SQLite adds a binary file to `~/.ripple/data/backtest/`; optimizer converges quickly on small param space but may need extension when real LLM backtest is added (re-run cost will increase).
