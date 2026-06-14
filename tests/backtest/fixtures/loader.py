# tests/backtest/fixtures/loader.py
"""Backward-compatible re-export from the production fixture loader.

The canonical loader now lives in ``ripple.backtest.fixtures.loader`` so that
the CLI command can import it in production builds.  This module re-exports
the same symbols so that existing test imports continue to work.
"""

from ripple.backtest.fixtures.loader import (  # noqa: F401
    load_seed_cases,
    load_seed_cases_with_predictions,
)
