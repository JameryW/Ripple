# ripple/backtest/store.py
"""SQLite-backed persistence for backtest run history.

Each ``BacktestReport`` is saved as a single row with the full report
JSON in a ``report_json`` column.  Lightweight columns (run_id, timestamp,
mape, signed_mape) are duplicated for fast querying without parsing JSON.

Store location: ``~/.ripple/data/backtest/history.db``
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ripple.backtest.schema import BacktestReport

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path.home() / ".ripple" / "data" / "backtest"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    timestamp     TEXT NOT NULL,
    mape          REAL,
    signed_mape   REAL,
    mae           REAL,
    rmse          REAL,
    macro_f1      REAL,
    total_cases   INTEGER NOT NULL DEFAULT 0,
    completed_cases INTEGER NOT NULL DEFAULT 0,
    failed_cases  INTEGER NOT NULL DEFAULT 0,
    params_snapshot TEXT NOT NULL DEFAULT '{}',
    report_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
"""


def _row_to_summary(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "timestamp": row["timestamp"],
        "mape": row["mape"],
        "signed_mape": row["signed_mape"],
        "mae": row["mae"],
        "rmse": row["rmse"],
        "macro_f1": row["macro_f1"],
        "total_cases": row["total_cases"],
        "completed_cases": row["completed_cases"],
        "failed_cases": row["failed_cases"],
        "params_snapshot": json.loads(row["params_snapshot"]),
    }


class BacktestStore:
    """Persist and query backtest run history via SQLite."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = _DEFAULT_DIR / "history.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA_SQL)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Write ────────────────────────────────────────────────────────────

    def save(self, report: BacktestReport) -> None:
        """Persist a BacktestReport to the store."""
        report_dict = asdict(report)
        # BacktestResult / PredictionError / GradeError are frozen dataclasses — asdict works.
        report_json = json.dumps(report_dict, ensure_ascii=False, default=str)

        conn = self._connect()
        conn.execute(
            """\
INSERT OR REPLACE INTO runs
    (run_id, timestamp, mape, signed_mape, mae, rmse, macro_f1,
     total_cases, completed_cases, failed_cases, params_snapshot, report_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                report.run_id,
                report.timestamp,
                report.mape,
                report.signed_mape,
                report.mae,
                report.rmse,
                report.macro_f1,
                report.total_cases,
                report.completed_cases,
                report.failed_cases,
                json.dumps(report.params_snapshot, ensure_ascii=False),
                report_json,
            ),
        )
        conn.commit()
        logger.info("Saved backtest run %s", report.run_id)

    # ── Read ─────────────────────────────────────────────────────────────

    def load(self, run_id: str) -> Optional[BacktestReport]:
        """Load a full BacktestReport by run_id."""
        conn = self._connect()
        row = conn.execute(
            "SELECT report_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return self._report_from_json(row["report_json"])

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent runs (newest first) with summary fields only."""
        conn = self._connect()
        rows = conn.execute(
            """\
SELECT run_id, timestamp, mape, signed_mape, mae, rmse, macro_f1,
       total_cases, completed_cases, failed_cases, params_snapshot
FROM runs ORDER BY timestamp DESC LIMIT ?
""",
            (limit,),
        ).fetchall()
        return [_row_to_summary(r) for r in rows]

    def query_recent(self, n: int = 5) -> List[BacktestReport]:
        """Load the N most recent full reports (reserved for ``ripple doctor``)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT report_json FROM runs ORDER BY timestamp DESC LIMIT ?", (n,)
        ).fetchall()
        reports: List[BacktestReport] = []
        for row in rows:
            report = self._report_from_json(row["report_json"])
            if report is not None:
                reports.append(report)
        return reports

    def delete(self, run_id: str) -> bool:
        """Delete a run by run_id. Returns True if a row was deleted."""
        conn = self._connect()
        cursor = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        conn.commit()
        return cursor.rowcount > 0

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _report_from_json(raw: str) -> Optional[BacktestReport]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupt report JSON, skipping")
            return None
        # Reconstruct nested dataclass objects
        from ripple.backtest.schema import BacktestResult, PredictionError, GradeError

        results = []
        for r in data.pop("results", []):
            errors = [PredictionError(**e) for e in r.pop("errors", [])]
            grade_errors = [GradeError(**g) for g in r.pop("grade_errors", [])]
            results.append(
                BacktestResult(
                    **r, errors=errors, grade_errors=grade_errors
                )
            )
        return BacktestReport(**data, results=results)
