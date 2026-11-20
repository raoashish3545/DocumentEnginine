"""SQLite storage for clean customers, rejected records and ingestion runs.

Tables
------
ingestion_runs    One row per file processed, with its final counts.
customers         Clean, validated, de-duplicated records.
rejected_records  Invalid and duplicate records, with the reasons, for review.

Insert helpers do not commit. Wrap them in ``with conn:`` so that one file's
records are written in a single transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data") / "document_engine.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file       TEXT    NOT NULL,
    file_type         TEXT    NOT NULL,
    started_at        TEXT    NOT NULL,
    finished_at       TEXT,
    status            TEXT    NOT NULL
                      CHECK (status IN ('running', 'completed', 'failed')),
    total_records     INTEGER NOT NULL DEFAULT 0,
    loaded_records    INTEGER NOT NULL DEFAULT 0,
    rejected_records  INTEGER NOT NULL DEFAULT 0,
    duplicate_records INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id  INTEGER PRIMARY KEY,
    first_name   TEXT    NOT NULL,
    last_name    TEXT    NOT NULL,
    email        TEXT    NOT NULL,
    age          INTEGER CHECK (age IS NULL OR age BETWEEN 0 AND 120),
    country      TEXT    NOT NULL,
    signup_date  TEXT    NOT NULL,          -- ISO 8601: YYYY-MM-DD
    total_spend  REAL    CHECK (total_spend IS NULL OR total_spend >= 0),
    is_active    INTEGER CHECK (is_active IS NULL OR is_active IN (0, 1)),
    source_file  TEXT    NOT NULL,
    run_id       INTEGER NOT NULL REFERENCES ingestion_runs (run_id),
    loaded_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES ingestion_runs (run_id),
    position     INTEGER,                   -- line number (CSV/JSONL) or index (JSON)
    reason_type  TEXT    NOT NULL CHECK (reason_type IN ('invalid', 'duplicate')),
    errors       TEXT    NOT NULL,          -- JSON array of messages
    raw_data     TEXT,                      -- original record as JSON
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_email   ON customers (email);
CREATE INDEX IF NOT EXISTS idx_customers_run     ON customers (run_id);
CREATE INDEX IF NOT EXISTS idx_rejected_run      ON rejected_records (run_id);
"""

CUSTOMER_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "first_name",
    "last_name",
    "email",
    "age",
    "country",
    "signup_date",
    "total_spend",
    "is_active",
)

_INSERT_CUSTOMER_SQL = f"""
INSERT INTO customers ({", ".join(CUSTOMER_COLUMNS)}, source_file, run_id, loaded_at)
VALUES ({", ".join("?" for _ in CUSTOMER_COLUMNS)}, ?, ?, ?)
"""

_INSERT_REJECTED_SQL = """
INSERT INTO rejected_records (run_id, position, reason_type, errors, raw_data, created_at)
VALUES (?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class RejectedRow:
    """A record that was not loaded into ``customers``."""

    position: int
    reason_type: str  # "invalid" or "duplicate"
    errors: Sequence[str]
    raw_data: Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (and create if needed) the SQLite database file."""
    target = str(db_path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes. Safe to run more than once."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def start_run(conn: sqlite3.Connection, source_file: str, file_type: str) -> int:
    """Record that a file has started processing and return its run id."""
    cursor = conn.execute(
        "INSERT INTO ingestion_runs (source_file, file_type, started_at, status) "
        "VALUES (?, ?, ?, 'running')",
        (source_file, file_type, _utc_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    total: int,
    loaded: int,
    rejected: int,
    duplicates: int,
    error: str | None = None,
) -> None:
    """Store the final counts and status for a run."""
    conn.execute(
        """
        UPDATE ingestion_runs
           SET finished_at = ?, status = ?, total_records = ?, loaded_records = ?,
               rejected_records = ?, duplicate_records = ?, error_message = ?
         WHERE run_id = ?
        """,
        (_utc_now(), status, total, loaded, rejected, duplicates, error, run_id),
    )
    conn.commit()


def fetch_existing_keys(
    conn: sqlite3.Connection, key_fields: Sequence[str]
) -> set[tuple[Any, ...]]:
    """Return the de-duplication keys of every customer already stored."""
    unknown = [name for name in key_fields if name not in CUSTOMER_COLUMNS]
    if not key_fields or unknown:
        raise ValueError(f"Invalid key fields: {list(key_fields)}")
    columns = ", ".join(key_fields)  # safe: checked against CUSTOMER_COLUMNS
    return {tuple(row) for row in conn.execute(f"SELECT {columns} FROM customers")}


def _customer_row(record: Mapping[str, Any], run_id: int, source_file: str) -> tuple[Any, ...]:
    values: list[Any] = []
    for column in CUSTOMER_COLUMNS:
        value = record.get(column)
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, bool):
            value = int(value)
        values.append(value)
    return (*values, source_file, run_id, _utc_now())


def insert_customers(
    conn: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    run_id: int,
    source_file: str,
) -> int:
    """Insert clean records. Returns the number of rows written."""
    rows = [_customer_row(record, run_id, source_file) for record in records]
    conn.executemany(_INSERT_CUSTOMER_SQL, rows)
    return len(rows)


def insert_rejected(
    conn: sqlite3.Connection, run_id: int, rejected: Iterable[RejectedRow]
) -> int:
    """Insert invalid and duplicate records. Returns the number of rows written."""
    now = _utc_now()
    rows = [
        (
            run_id,
            item.position,
            item.reason_type,
            json.dumps(list(item.errors), ensure_ascii=False),
            json.dumps(item.raw_data, ensure_ascii=False, default=str),
            now,
        )
        for item in rejected
    ]
    conn.executemany(_INSERT_REJECTED_SQL, rows)
    return len(rows)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for each table."""
    tables = ("ingestion_runs", "customers", "rejected_records")
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }


def recent_runs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """The most recent ingestion runs, newest first."""
    return conn.execute(
        "SELECT * FROM ingestion_runs ORDER BY run_id DESC LIMIT ?", (limit,)
    ).fetchall()
