# Document Engine: CSV and JSON validation into SQLite

A Python application that reads customer records from CSV, JSON and JSON Lines
files, validates them, rejects malformed records, removes duplicates and stores
the clean records in SQLite. It reports how many records were processed, loaded,
rejected and duplicated.

It uses only the Python standard library. pytest is the only development
dependency.

## Project structure

```
CSV-JSON_Parsing/
├── src/document_engine/
│   ├── __init__.py        public API
│   ├── __main__.py        command line: init-db, ingest, stats
│   ├── ingestion.py       read CSV / JSON / JSONL into raw records
│   ├── validation.py      schema, type conversion, rules, de-duplication
│   ├── database.py        SQLite schema and read/write helpers
│   ├── reporting.py       counts and printable reports
│   └── pipeline.py        read → validate → de-duplicate → store → report
├── tests/
│   ├── conftest.py        shared fixtures
│   ├── test_ingestion.py
│   ├── test_validation.py
│   └── test_pipeline.py   end-to-end, including 10,000-record runs
├── scripts/
│   └── generate_sample_data.py   synthetic data with known good/bad counts
├── data/
│   ├── sample/            small handmade files (see data/sample/README.md)
│   ├── generated/         output of the generator (not committed)
│   └── document_engine.db the database (not committed)
├── README.md
├── pyproject.toml
└── .gitignore
```

`pipeline.py`, `__main__.py`, `conftest.py`, `test_pipeline.py` and `scripts/` go
beyond the required structure. They hold the glue code, the command line and
the end-to-end tests.

## Setup

Requires Python 3.10 or newer. From the project folder, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

`pip install -e` installs the package in editable mode, so code changes take
effect without reinstalling. It also adds a `document-engine` command, which
does the same as `python -m document_engine`.

## The database

Python includes the `sqlite3` module, so you do not need the SQLite command-line
tool to create or use the database.

### Create it

```powershell
python -m document_engine init-db
```

This creates `data/document_engine.db` with the tables below. It is safe to run
again: existing tables and data are kept. `ingest` also creates the tables if
they are missing. Use `--db path\to\other.db` (before the command name) to work
with another file.

### Tables

**`ingestion_runs`**: one row per file processed.

| Column | Meaning |
|--------|---------|
| `run_id` | primary key |
| `source_file`, `file_type` | which file, and csv / json / jsonl |
| `started_at`, `finished_at` | UTC timestamps |
| `status` | `running`, `completed` or `failed` |
| `total_records`, `loaded_records`, `rejected_records`, `duplicate_records` | the counts from the report |
| `error_message` | why a whole file failed |

**`customers`**: clean records only.

| Column | Type | Rule |
|--------|------|------|
| `customer_id` | INTEGER | primary key, ≥ 1 |
| `first_name`, `last_name` | TEXT | required, ≤ 50 characters |
| `email` | TEXT | required, valid format, stored in lower case |
| `age` | INTEGER | optional, 0–120 |
| `country` | TEXT | required |
| `signup_date` | TEXT | required, `YYYY-MM-DD`, not in the future |
| `total_spend` | REAL | optional, ≥ 0 |
| `is_active` | INTEGER | optional, 0 or 1 |
| `source_file`, `run_id`, `loaded_at` | | where and when the row came from |

**`rejected_records`**: invalid and duplicate records, kept for review.

| Column | Meaning |
|--------|---------|
| `run_id` | the run that rejected it |
| `position` | line number (CSV, JSONL) or 1-based index (JSON) in the file |
| `reason_type` | `invalid` or `duplicate` |
| `errors` | JSON array of messages, e.g. `["age: expected an integer, found 'abc'"]` |
| `raw_data` | the original record as JSON |

### Look inside it

```powershell
python -m document_engine stats
```

shows row counts and the most recent runs. For your own queries:

```powershell
python -c "import sqlite3; c = sqlite3.connect('data/document_engine.db'); print(c.execute('SELECT reason_type, errors FROM rejected_records LIMIT 5').fetchall())"
```

You can also open the `.db` file in [DB Browser for SQLite](https://sqlitebrowser.org/),
or in the SQLite command-line tool you downloaded once its folder is on your
PATH (`sqlite3 data\document_engine.db`, then `.tables` or any SQL query).

### Start over

Delete the file and recreate it:

```powershell
Remove-Item data\document_engine.db; python -m document_engine init-db
```

## Test data

`data/sample/` holds small handmade files in which every bad record has a
documented reason. `data/sample/README.md` lists the expected result for each
line. `data/sample/malformed/` holds files that fail as a whole (broken JSON,
wrong shape, empty, unsupported type).

For volume testing, generate synthetic files:

```powershell
python scripts/generate_sample_data.py
```

This writes `customers_10000.csv`, `.json` and `.jsonl` to `data/generated/` and
prints the counts the pipeline should report for each one. By default about 5%
of records are invalid, 3% are duplicates and 1% cannot be parsed. Options:
`--count`, `--format csv|json|jsonl|all`, `--invalid-rate`, `--duplicate-rate`,
`--malformed-rate`, `--seed`, `--out`.

## Usage

```powershell
python -m document_engine ingest data/sample/customers.csv data/sample/customers.json data/sample/customers.jsonl
```

Example report:

```
File:        data\sample\customers.csv
Run id:      1
Status:      COMPLETED
Processed:         12
  Loaded:           5
  Rejected:         6
  Duplicate:        1
Duration:    0.03s
Rejections by field:
  email               1
  age                 1
  ...
```

Add `--json` for machine-readable reports, and `-v` (before the command name)
for log messages. The exit code is 1 if any file failed, so scripts and CI can
detect it.

From Python:

```python
from document_engine import process_files, format_report

for report in process_files(["data/sample/customers.csv"], "data/document_engine.db"):
    print(format_report(report))
```

## How it works

1. **Ingestion** (`ingestion.py`) reads the file. If the file as a whole is
   unusable (missing, unsupported type, empty, invalid JSON syntax, not UTF-8,
   duplicate CSV headers), it raises `IngestionError` and the run is stored as
   `failed`. A single bad record (a CSV row with the wrong number of columns, a
   JSON item that is not an object, a JSONL line that is not valid JSON) is
   marked as malformed and the rest of the file is still processed.
2. **Validation** (`validation.py`) checks each record against
   `CUSTOMER_SCHEMA`. Values are converted to the right type: `"42"` from a CSV
   and `42` from JSON are both valid integers, but `42.5`, `true` or `"abc"` are
   not. Every field is checked, so a record with three problems reports all
   three.
3. **De-duplication** uses `customer_id` as the key. A record is a duplicate if
   an earlier record in the same file, or a record already in the database, has
   the same key. Running the same file twice therefore loads nothing new. The
   first valid occurrence wins.
4. **Storage** (`database.py`) writes each file's clean and rejected rows in a
   single transaction. If the database fails part-way, nothing from that file is
   kept and the run is marked `failed`.
5. **Reporting** (`reporting.py`): `processed = loaded + rejected + duplicates`.
   Rejections are also counted by field, to show which rules fail most.

To change the rules, edit `CUSTOMER_SCHEMA` in `validation.py`. To add a column,
also add it to `SCHEMA_SQL` and `CUSTOMER_COLUMNS` in `database.py`.

## Tests

```powershell
python -m pytest
```

121 tests cover file-level and record-level errors for each format, every
validation rule, de-duplication within and across files and runs, transaction
rollback on database errors, the command line, and three 10,000-record runs
whose counts are checked against the generator's expected numbers.
