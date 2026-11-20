"""Tie the steps together: read -> validate -> de-duplicate -> store -> report."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from document_engine import database
from document_engine.database import RejectedRow
from document_engine.ingestion import IngestionError, read_records
from document_engine.reporting import STATUS_FAILED, IngestionReport
from document_engine.validation import (
    CUSTOMER_SCHEMA,
    DEDUP_KEY_FIELDS,
    Deduplicator,
    FieldSpec,
    validate_record,
)

logger = logging.getLogger(__name__)


def process_file(
    path: str | Path,
    conn: sqlite3.Connection,
    *,
    schema: Sequence[FieldSpec] = CUSTOMER_SCHEMA,
    key_fields: Sequence[str] = DEDUP_KEY_FIELDS,
) -> IngestionReport:
    """Load one file into the database and return its report.

    This never raises for bad input: a file that cannot be read, or a database
    error while saving, is recorded as a failed run instead.
    """
    file_path = Path(path)
    started = time.perf_counter()
    report = IngestionReport(source_file=str(file_path))
    report.run_id = database.start_run(
        conn, str(file_path), file_path.suffix.lstrip(".").lower() or "unknown"
    )

    try:
        raw_records = read_records(file_path)
    except IngestionError as exc:
        logger.warning("Skipping file: %s", exc)
        report.status, report.error = STATUS_FAILED, str(exc)
        return _finish(conn, report, started)

    dedup = Deduplicator(key_fields, database.fetch_existing_keys(conn, key_fields))
    clean_records = []
    rejected_rows: list[RejectedRow] = []

    for raw in raw_records:
        if raw.data is None:
            message = f"_record: {raw.parse_error}"
            rejected_rows.append(RejectedRow(raw.position, "invalid", [message], raw.raw))
            report.add_rejection(["_record"])
            continue

        result = validate_record(raw.data, schema)
        if not result.is_valid or result.record is None:
            rejected_rows.append(
                RejectedRow(raw.position, "invalid", [str(e) for e in result.errors], raw.data)
            )
            report.add_rejection(e.field for e in result.errors)
        elif dedup.is_duplicate(result.record):
            key = dict(zip(key_fields, dedup.key_for(result.record)))
            rejected_rows.append(
                RejectedRow(raw.position, "duplicate", [f"duplicate key {key}"], raw.data)
            )
            report.duplicates += 1
        else:
            clean_records.append(result.record)

    try:
        with conn:  # one transaction: all of this file's rows, or none of them
            report.loaded = database.insert_customers(
                conn, clean_records, run_id=report.run_id, source_file=str(file_path)
            )
            database.insert_rejected(conn, report.run_id, rejected_rows)
    except sqlite3.Error as exc:
        logger.error("Database error while saving %s: %s", file_path, exc)
        report.status, report.error = STATUS_FAILED, f"database error: {exc}"
        report.loaded = 0

    return _finish(conn, report, started)


def _finish(conn: sqlite3.Connection, report: IngestionReport, started: float) -> IngestionReport:
    report.duration_seconds = time.perf_counter() - started
    assert report.run_id is not None
    database.finish_run(
        conn,
        report.run_id,
        status=report.status,
        total=report.processed,
        loaded=report.loaded,
        rejected=report.rejected,
        duplicates=report.duplicates,
        error=report.error,
    )
    return report


def process_files(
    paths: Iterable[str | Path], db_path: str | Path = database.DEFAULT_DB_PATH
) -> list[IngestionReport]:
    """Process several files into one database, in order."""
    conn = database.connect(db_path)
    try:
        database.init_db(conn)
        return [process_file(path, conn) for path in paths]
    finally:
        conn.close()
