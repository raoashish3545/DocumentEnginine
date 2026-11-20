"""Command-line interface.

    python -m document_engine init-db
    python -m document_engine ingest data/sample/customers.csv data/sample/customers.json
    python -m document_engine stats
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from document_engine import database
from document_engine.pipeline import process_files
from document_engine.reporting import format_report, format_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="document-engine",
        description="Validate CSV/JSON customer records and store clean ones in SQLite.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=database.DEFAULT_DB_PATH,
        help=f"SQLite database file (default: {database.DEFAULT_DB_PATH})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show log messages")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="create the database and its tables")

    ingest = commands.add_parser("ingest", help="validate files and load clean records")
    ingest.add_argument("files", nargs="+", type=Path, help="CSV, JSON or JSONL files")
    ingest.add_argument("--json", action="store_true", help="print reports as JSON")

    stats = commands.add_parser("stats", help="show table counts and recent runs")
    stats.add_argument("--limit", type=int, default=10, help="number of runs to show")
    return parser


def _init_db(db_path: Path) -> int:
    conn = database.connect(db_path)
    try:
        database.init_db(conn)
        tables = ", ".join(database.table_counts(conn))
    finally:
        conn.close()
    print(f"Database ready: {db_path.resolve()}\nTables: {tables}")
    return 0


def _ingest(db_path: Path, files: Sequence[Path], as_json: bool) -> int:
    reports = process_files(files, db_path)
    if as_json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            print(format_report(report))
            print("-" * 40)
        if len(reports) > 1:
            print(format_summary(reports))
    return 0 if all(r.succeeded for r in reports) else 1


def _stats(db_path: Path, limit: int) -> int:
    if not db_path.exists():
        print(f"No database at {db_path}. Run 'init-db' first.", file=sys.stderr)
        return 1
    conn = database.connect(db_path)
    try:
        database.init_db(conn)
        counts = database.table_counts(conn)
        runs = database.recent_runs(conn, limit)
    finally:
        conn.close()

    print(f"Database: {db_path.resolve()}")
    for table, count in counts.items():
        print(f"  {table:<18} {count:>8,} rows")
    if runs:
        print(f"\nLast {len(runs)} runs:")
        print(f"  {'id':>4}  {'status':<9} {'total':>7} {'loaded':>7} {'rejected':>8} {'dupes':>6}  file")
        for run in runs:
            print(
                f"  {run['run_id']:>4}  {run['status']:<9} {run['total_records']:>7} "
                f"{run['loaded_records']:>7} {run['rejected_records']:>8} "
                f"{run['duplicate_records']:>6}  {run['source_file']}"
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "init-db":
        return _init_db(args.db)
    if args.command == "ingest":
        return _ingest(args.db, args.files, args.json)
    return _stats(args.db, args.limit)


if __name__ == "__main__":
    sys.exit(main())
