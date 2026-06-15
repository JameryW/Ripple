# Directory Structure

> How backend code is organized in this project.

---

## Overview

Backend code lives under `ripple/` with subpackages organized by domain. Tests mirror the structure under `tests/`.

---

## Directory Layout

```
ripple/
├── backtest/           # Offline backtesting framework
│   ├── __init__.py     # Re-exports: BacktestCase, BacktestResult, BacktestReport, metrics, run_backtest
│   ├── schema.py       # Data classes: BacktestCase, BacktestResult, BacktestReport, PredictionError, GradeError
│   ├── metrics.py      # compute_numeric_metrics, compute_grade_metrics, compute_confidence_calibration, compute_brier_score
│   ├── runner.py       # run_backtest — main entry point
│   ├── store.py        # BacktestStore — SQLite persistence
│   ├── analyzer.py     # DeviationAnalyzer — systematic bias detection
│   ├── optimizer.py    # ParameterOptimizer — grid search param proposal
│   └── validator.py    # ABValidator — A/B validation + rollback
├── cli/
│   └── app.py          # Typer CLI — includes backtest/history/optimize commands
├── primitives/         # Core types (StabilityLevel, PredictionQuality, etc.)
├── api/                # Runtime API (simulate, ensemble, etc.)
├── engine/             # Engine internals
└── llm/                # LLM provider abstractions

tests/
├── backtest/
│   ├── fixtures/       # Seed fixture JSON files
│   ├── test_backtest.py          # Unit tests for metrics
│   ├── test_backtest_integration.py  # Integration tests for runner
│   ├── test_store.py             # Store persistence tests
│   ├── test_analyzer.py          # Analyzer tests
│   ├── test_optimizer.py         # Optimizer tests
│   └── test_validator.py         # Validator/rollback tests
└── ...
```

---

## Module Organization

- Each subpackage has a `schema.py` for dataclasses and a main module (e.g., `runner.py`)
- `__init__.py` re-exports public API with redundant `as` aliases for ruff compliance
- New modules follow the pattern: `schema.py` → `core_logic.py` → `tests/`

---

## Naming Conventions

- **Files**: `snake_case.py`
- **Classes**: `PascalCase` (e.g., `BacktestStore`, `DeviationAnalyzer`)
- **Functions**: `snake_case` (e.g., `run_backtest`, `compute_numeric_metrics`)
- **Dataclasses**: `PascalCase`, fields are `snake_case`
- **CLI commands**: `snake_case` subcommand names (e.g., `backtest optimize`, `backtest history`)

---

## Examples

- Well-organized module: `ripple/backtest/` — clear separation of schema, metrics, runner, store, analyzer, optimizer, validator
- Each module has a corresponding test file under `tests/backtest/`
