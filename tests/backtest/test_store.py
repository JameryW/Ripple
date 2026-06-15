# tests/backtest/test_store.py
"""Tests for BacktestStore — SQLite persistence layer."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from ripple.backtest.schema import BacktestReport, BacktestResult, PredictionError
from ripple.backtest.store import BacktestStore


@pytest.fixture
def store(tmp_path: Path) -> BacktestStore:
    return BacktestStore(db_path=tmp_path / "test_history.db")


def _make_report(
    run_id: str = "testrun01",
    mape: float = 45.0,
    signed_mape: float = 30.0,
    params_snapshot: Dict[str, float] | None = None,
) -> BacktestReport:
    return BacktestReport(
        run_id=run_id,
        total_cases=2,
        completed_cases=2,
        mae=100.0,
        mape=mape,
        signed_mape=signed_mape,
        rmse=120.0,
        macro_f1=0.8,
        params_snapshot=params_snapshot or {"threshold": 100.0, "p95_hard_cap": 200.0},
        results=[
            BacktestResult(
                case_id="case-1",
                prediction={"impressions": 500},
                errors=[PredictionError("impressions", 500.0, 400.0, 100.0, 25.0, 22.2)],
                predicted_confidence="high",
                actual_accuracy=True,
                elapsed_seconds=0.1,
            ),
            BacktestResult(
                case_id="case-2",
                prediction={"impressions": 300},
                errors=[PredictionError("impressions", 300.0, 350.0, 50.0, 14.3, -15.4)],
                predicted_confidence="medium",
                actual_accuracy=True,
                elapsed_seconds=0.1,
            ),
        ],
    )


class TestSaveAndLoad:
    def test_save_and_load_roundtrip(self, store: BacktestStore) -> None:
        report = _make_report()
        store.save(report)

        loaded = store.load("testrun01")
        assert loaded is not None
        assert loaded.run_id == "testrun01"
        assert loaded.mape == 45.0
        assert loaded.signed_mape == 30.0
        assert loaded.total_cases == 2
        assert loaded.completed_cases == 2
        assert len(loaded.results) == 2
        assert loaded.results[0].case_id == "case-1"
        assert loaded.results[0].errors[0].signed_percentage_error == 22.2
        assert loaded.params_snapshot == {"threshold": 100.0, "p95_hard_cap": 200.0}

    def test_load_nonexistent_returns_none(self, store: BacktestStore) -> None:
        assert store.load("no_such_run") is None

    def test_save_overwrites_on_duplicate_run_id(self, store: BacktestStore) -> None:
        store.save(_make_report(run_id="dup1", mape=50.0))
        store.save(_make_report(run_id="dup1", mape=30.0))

        loaded = store.load("dup1")
        assert loaded is not None
        assert loaded.mape == 30.0

    def test_save_creates_db_directory(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "deep" / "test.db"
        store = BacktestStore(db_path=db_path)
        store.save(_make_report())
        assert db_path.exists()


class TestListRuns:
    def test_list_empty(self, store: BacktestStore) -> None:
        assert store.list_runs() == []

    def test_list_returns_newest_first(self, store: BacktestStore) -> None:
        store.save(_make_report(run_id="old", mape=60.0))
        store.save(_make_report(run_id="new", mape=40.0))

        runs = store.list_runs()
        assert len(runs) == 2
        assert runs[0]["run_id"] == "new"
        assert runs[1]["run_id"] == "old"

    def test_list_respects_limit(self, store: BacktestStore) -> None:
        for i in range(5):
            store.save(_make_report(run_id=f"run{i:02d}"))

        runs = store.list_runs(limit=3)
        assert len(runs) == 3

    def test_list_summary_fields(self, store: BacktestStore) -> None:
        store.save(_make_report())
        runs = store.list_runs()
        assert len(runs) == 1
        r = runs[0]
        assert "run_id" in r
        assert "timestamp" in r
        assert "mape" in r
        assert "signed_mape" in r
        assert "params_snapshot" in r
        # Full report_json should NOT be in summary
        assert "report_json" not in r
        # Quality dimension fields are extracted from report_json
        assert "ensemble_stability" in r
        assert "tribunal_divergence" in r
        assert "input_completeness" in r
        assert "historical_deviation" in r


class TestQueryRecent:
    def test_query_recent_returns_full_reports(self, store: BacktestStore) -> None:
        store.save(_make_report(run_id="r1", mape=50.0))
        store.save(_make_report(run_id="r2", mape=40.0))

        reports = store.query_recent(n=2)
        assert len(reports) == 2
        assert isinstance(reports[0], BacktestReport)
        assert reports[0].run_id == "r2"  # newest first
        assert len(reports[0].results) == 2

    def test_query_recent_n_less_than_total(self, store: BacktestStore) -> None:
        for i in range(5):
            store.save(_make_report(run_id=f"q{i:02d}"))

        reports = store.query_recent(n=2)
        assert len(reports) == 2
        assert reports[0].run_id == "q04"


class TestDelete:
    def test_delete_existing(self, store: BacktestStore) -> None:
        store.save(_make_report(run_id="del1"))
        assert store.delete("del1") is True
        assert store.load("del1") is None

    def test_delete_nonexistent(self, store: BacktestStore) -> None:
        assert store.delete("nope") is False


class TestClose:
    def test_close_and_reopen(self, store: BacktestStore) -> None:
        store.save(_make_report())
        store.close()

        # Reopen same path
        store2 = BacktestStore(db_path=store._db_path)
        loaded = store2.load("testrun01")
        assert loaded is not None
        assert loaded.mape == 45.0
