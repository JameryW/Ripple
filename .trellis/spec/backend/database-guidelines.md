# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

This project uses **stdlib `sqlite3`** for local persistence. No ORM, no external database dependency. SQLite is used for structured queries, atomic writes, and indexed retrieval by run_id/timestamp.

---

## Backtest Store (SQLite)

### Location

Default path: `~/.ripple/data/backtest/backtest.db`

Override via `BacktestStore(db_path=...)` constructor.

### Schema

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    timestamp  TEXT NOT NULL,
    report     TEXT NOT NULL  -- JSON-serialized BacktestReport
);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp);
```

### Key Operations

| Method | Signature | Notes |
|--------|-----------|-------|
| `save` | `(report: BacktestReport) -> None` | Upsert by run_id |
| `load` | `(run_id: str) -> BacktestReport or None` | Returns None if not found |
| `list_runs` | `(limit: int = 20) -> list[RunSummary]` | Newest first, limited |
| `query_recent` | `(n: int = 5) -> list[BacktestReport]` | Full reports, newest first |
| `delete` | `(run_id: str) -> bool` | Returns True if deleted |
| `close` | `() -> None` | Close connection |

### Contracts

- `BacktestStore.__init__` coerces `db_path` to `Path` internally — string or Path accepted
- `save` creates parent directories automatically
- `list_runs` returns `RunSummary` dataclasses: `run_id`, `timestamp`, `overall_mape`, `case_count`
- `query_recent` is reserved for `ripple doctor` future use (PRD expansion point)

---

## Query Patterns

- Always use parameterized queries (`?` placeholders) — no string interpolation
- Write operations are auto-committed (CLI tool, no concurrent writers in MVP)
- JSON serialization for `report` column: `dataclasses.asdict()` → `json.dumps()`

---

## Migrations

- Schema created via `CREATE TABLE IF NOT EXISTS` on first connection
- No migration framework — single-table schema is simple enough
- If schema evolves, add columns with `ALTER TABLE` and `IF NOT EXISTS` guards

---

## Naming Conventions

- Table names: lowercase, snake_case (`runs`)
- Column names: lowercase, snake_case (`run_id`, `timestamp`)
- Index names: `idx_<table>_<column>` (`idx_runs_timestamp`)

---

## Common Mistakes

### Passing string path without Path coercion

**Symptom**: `AttributeError: 'str' object has no attribute 'parent'` when `BacktestStore` tries `self._db_path.parent.mkdir(...)`

**Fix**: `BacktestStore.__init__` now does `self._db_path = Path(db_path)` — always pass through Path()

### Not closing the store in long-running contexts

**Symptom**: SQLite `ProgrammingError: closed` if store is used after close

**Fix**: For CLI tools, process exit handles cleanup. For library use, call `store.close()` explicitly or use context manager pattern.
