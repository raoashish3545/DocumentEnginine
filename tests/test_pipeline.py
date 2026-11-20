"""End-to-end tests: files in, SQLite rows and reports out."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from conftest import PROJECT_ROOT, SAMPLE_DIR
from document_engine import database
from document_engine.__main__ import main
from document_engine.pipeline import process_file


def load_generator() -> ModuleType:
    path = PROJECT_ROOT / "scripts" / "generate_sample_data.py"
    spec = importlib.util.spec_from_file_location("generate_sample_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # needed so dataclasses can resolve the module
    spec.loader.exec_module(module)
    return module


def count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


# --------------------------------------------------------------------------- #
# Sample files (expected results are documented in data/sample/README.md)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "loaded", "rejected", "duplicates"),
    [("customers.csv", 5, 6, 1), ("customers.json", 4, 5, 1), ("customers.jsonl", 3, 2, 1)],
)
def test_sample_files(
    conn: sqlite3.Connection, name: str, loaded: int, rejected: int, duplicates: int
) -> None:
    report = process_file(SAMPLE_DIR / name, conn)
    assert report.succeeded
    assert (report.loaded, report.rejected, report.duplicates) == (loaded, rejected, duplicates)
    assert count(conn, "SELECT COUNT(*) FROM customers") == loaded
    assert count(conn, "SELECT COUNT(*) FROM rejected_records WHERE reason_type='invalid'") == rejected
    assert count(conn, "SELECT COUNT(*) FROM rejected_records WHERE reason_type='duplicate'") == duplicates


@pytest.mark.parametrize("name", ["broken_syntax.json", "wrong_shape.json", "empty.csv", "notes.txt"])
def test_malformed_files_fail_without_raising(conn: sqlite3.Connection, name: str) -> None:
    report = process_file(SAMPLE_DIR / "malformed" / name, conn)
    assert not report.succeeded
    assert report.error
    assert report.processed == 0
    run = conn.execute("SELECT status, error_message FROM ingestion_runs").fetchone()
    assert run["status"] == "failed" and run["error_message"] == report.error


def test_stored_values_have_clean_types(conn: sqlite3.Connection) -> None:
    process_file(SAMPLE_DIR / "customers.csv", conn)
    row = conn.execute("SELECT * FROM customers WHERE customer_id = 4").fetchone()
    assert row["email"] == "david.lee@example.com"
    assert row["is_active"] == 1
    assert row["signup_date"] == "2023-07-09"
    empty = conn.execute("SELECT age, total_spend FROM customers WHERE customer_id = 3").fetchone()
    assert tuple(empty) == (None, None)


def test_rejected_records_keep_reasons_and_raw_data(conn: sqlite3.Connection) -> None:
    process_file(SAMPLE_DIR / "customers.csv", conn)
    row = conn.execute("SELECT * FROM rejected_records WHERE position = 7").fetchone()
    assert row["reason_type"] == "invalid"
    assert json.loads(row["errors"]) == ["age: expected an integer, found 'abc'"]
    assert json.loads(row["raw_data"])["first_name"] == "Farhan"


# --------------------------------------------------------------------------- #
# Duplicates across runs and files
# --------------------------------------------------------------------------- #


def test_rerunning_a_file_loads_nothing_new(conn: sqlite3.Connection) -> None:
    first = process_file(SAMPLE_DIR / "customers.csv", conn)
    second = process_file(SAMPLE_DIR / "customers.csv", conn)
    assert second.loaded == 0
    assert second.duplicates == first.loaded + first.duplicates
    assert second.rejected == first.rejected
    assert count(conn, "SELECT COUNT(*) FROM customers") == first.loaded


def test_duplicates_across_csv_and_json(conn: sqlite3.Connection, tmp_path: Path) -> None:
    csv_file = tmp_path / "a.csv"
    csv_file.write_text(
        "customer_id,first_name,last_name,email,country,signup_date\n"
        "1,Ann,Lee,ann@x.com,UK,2022-01-01\n",
        encoding="utf-8",
    )
    json_file = tmp_path / "b.json"
    json_file.write_text(
        json.dumps([{"customer_id": 1, "first_name": "Ann", "last_name": "Lee",
                     "email": "ann@x.com", "country": "UK", "signup_date": "2022-01-01"}]),
        encoding="utf-8",
    )
    assert process_file(csv_file, conn).loaded == 1
    assert process_file(json_file, conn).duplicates == 1


# --------------------------------------------------------------------------- #
# Robustness and volume
# --------------------------------------------------------------------------- #


def test_database_error_marks_run_failed_and_rolls_back(conn: sqlite3.Connection) -> None:
    # A trigger makes every rejected-record insert fail after customers were inserted.
    conn.execute(
        "CREATE TRIGGER block BEFORE INSERT ON rejected_records "
        "BEGIN SELECT RAISE(ABORT, 'disk full (simulated)'); END"
    )
    report = process_file(SAMPLE_DIR / "customers.csv", conn)
    assert not report.succeeded
    assert "disk full" in (report.error or "")
    assert count(conn, "SELECT COUNT(*) FROM customers") == 0  # rolled back
    assert conn.execute("SELECT status FROM ingestion_runs").fetchone()[0] == "failed"


@pytest.mark.parametrize("fmt", ["csv", "json", "jsonl"])
def test_10000_synthetic_records(conn: sqlite3.Connection, tmp_path: Path, fmt: str) -> None:
    generator = load_generator()
    rows, expected = generator.generate_dataset(10_000, seed=7)
    path = tmp_path / f"customers.{fmt}"
    generator.WRITERS[fmt](rows, path)

    report = process_file(path, conn)

    assert report.succeeded
    assert report.processed == 10_000
    assert (report.loaded, report.rejected, report.duplicates) == (
        expected.loaded,
        expected.rejected,
        expected.duplicates,
    )
    assert count(conn, "SELECT COUNT(*) FROM customers") == expected.loaded


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def test_cli_init_ingest_and_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "cli.db"
    assert main(["--db", str(db_path), "init-db"]) == 0
    assert "Database ready" in capsys.readouterr().out
    assert main(["--db", str(db_path), "ingest", "--json", str(SAMPLE_DIR / "customers.json")]) == 0
    [report] = json.loads(capsys.readouterr().out)
    assert report["processed"] == 10 and report["loaded"] == 4

    assert main(["--db", str(db_path), "stats"]) == 0
    assert "customers" in capsys.readouterr().out

    conn = database.connect(db_path)
    try:
        assert database.table_counts(conn)["customers"] == 4
    finally:
        conn.close()


def test_cli_returns_1_when_a_file_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.db"
    assert main(["--db", str(db_path), "ingest", str(tmp_path / "missing.csv")]) == 1
