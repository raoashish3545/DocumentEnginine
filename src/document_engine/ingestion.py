"""Read CSV, JSON and JSON Lines files into raw records.

Two kinds of problems are handled differently:

* File-level problems (missing file, unsupported type, empty file, broken JSON
  syntax, bad encoding) raise :class:`IngestionError`. Nothing in the file can
  be trusted, so the whole file is skipped.
* Record-level problems (a CSV row with the wrong number of columns, a JSON
  item that is not an object, a JSONL line that is not valid JSON) produce a
  :class:`RawRecord` with ``parse_error`` set. The rest of the file is still
  processed.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".csv", ".json", ".jsonl"})

# "utf-8-sig" also reads files that Excel saved with a byte-order mark.
DEFAULT_ENCODING = "utf-8-sig"


class IngestionError(Exception):
    """Raised when a whole file cannot be read."""


@dataclass(frozen=True)
class RawRecord:
    """One record as read from a file, before validation.

    Attributes:
        position: 1-based line number (CSV, JSONL) or array index (JSON).
        data: The record's fields, or ``None`` if it could not be parsed.
        parse_error: Why the record could not be parsed, if it could not.
        raw: The original content of an unparseable record, kept for review.
    """

    position: int
    data: dict[str, Any] | None
    parse_error: str | None = None
    raw: Any = None

    @property
    def is_malformed(self) -> bool:
        return self.parse_error is not None


def read_records(path: str | Path) -> list[RawRecord]:
    """Read every record from a CSV, JSON or JSONL file.

    Raises:
        IngestionError: If the file cannot be read as a whole.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise IngestionError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise IngestionError(f"Not a file: {file_path}")

    suffix = file_path.suffix.lower()
    readers: dict[str, Callable[[Path], list[RawRecord]]] = {
        ".csv": read_csv,
        ".json": read_json,
        ".jsonl": read_jsonl,
    }
    reader = readers.get(suffix)
    if reader is None:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise IngestionError(
            f"Unsupported file type '{suffix or '(none)'}' for {file_path}. "
            f"Supported: {supported}"
        )

    try:
        return reader(file_path)
    except UnicodeDecodeError as exc:
        raise IngestionError(f"{file_path} is not valid UTF-8 text: {exc.reason}") from exc
    except OSError as exc:
        raise IngestionError(f"Could not read {file_path}: {exc}") from exc


def read_csv(path: Path) -> list[RawRecord]:
    """Read a CSV file whose first row is the header."""
    with path.open(newline="", encoding=DEFAULT_ENCODING) as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise IngestionError(f"{path} is empty") from None
        except csv.Error as exc:
            raise IngestionError(f"{path}: malformed CSV header: {exc}") from exc

        columns = [name.strip() for name in header]
        _check_header(columns, path)

        records: list[RawRecord] = []
        try:
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue  # skip blank lines
                line = reader.line_num
                if len(row) != len(columns):
                    records.append(
                        RawRecord(
                            position=line,
                            data=None,
                            parse_error=f"expected {len(columns)} columns, found {len(row)}",
                            raw=row,
                        )
                    )
                else:
                    records.append(RawRecord(position=line, data=dict(zip(columns, row))))
        except csv.Error as exc:
            raise IngestionError(
                f"{path}: malformed CSV near line {reader.line_num}: {exc}"
            ) from exc
    return records


def _check_header(columns: list[str], path: Path) -> None:
    if not any(columns):
        raise IngestionError(f"{path}: header row is empty")
    if any(not name for name in columns):
        raise IngestionError(f"{path}: header contains an empty column name")
    duplicates = sorted({name for name in columns if columns.count(name) > 1})
    if duplicates:
        raise IngestionError(f"{path}: duplicate column names in header: {duplicates}")


def read_json(path: Path) -> list[RawRecord]:
    """Read a JSON file holding an array of objects, or ``{"records": [...]}``."""
    text = path.read_text(encoding=DEFAULT_ENCODING)
    if not text.strip():
        raise IngestionError(f"{path} is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestionError(
            f"{path} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        items = payload["records"]
    else:
        raise IngestionError(
            f"{path}: expected a JSON array of records or an object with a 'records' array, "
            f"found {_json_type_name(payload)}"
        )
    return [_item_to_record(index, item) for index, item in enumerate(items, start=1)]


def read_jsonl(path: Path) -> list[RawRecord]:
    """Read a JSON Lines file: one JSON object per line."""
    records: list[RawRecord] = []
    with path.open(encoding=DEFAULT_ENCODING) as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                records.append(
                    RawRecord(
                        position=line_number,
                        data=None,
                        parse_error=f"invalid JSON: {exc.msg} (column {exc.colno})",
                        raw=text,
                    )
                )
                continue
            records.append(_item_to_record(line_number, item))
    return records


def _item_to_record(position: int, item: Any) -> RawRecord:
    if isinstance(item, dict):
        return RawRecord(position=position, data=item)
    return RawRecord(
        position=position,
        data=None,
        parse_error=f"expected a JSON object, found {_json_type_name(item)}",
        raw=item,
    )


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
