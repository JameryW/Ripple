# ripple/backtest/__init__.py
"""Offline backtesting framework — R7.

Evaluate prediction accuracy against historical cases with
versioned case schema, error metrics, and bucketed reporting.
"""

from ripple.backtest.schema import BacktestCase as BacktestCase, BacktestResult as BacktestResult, BacktestReport as BacktestReport
from ripple.backtest.metrics import compute_numeric_metrics as compute_numeric_metrics, compute_grade_metrics as compute_grade_metrics, compute_confidence_calibration as compute_confidence_calibration
from ripple.backtest.runner import run_backtest as run_backtest
