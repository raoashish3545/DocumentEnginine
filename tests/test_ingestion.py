"""Tests for reading CSV, JSON and JSONL files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from document_engine.ingestion import IngestionError, read_records

HEADER = "customer_id,first_name,last_name,email\n"


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# File-level errors
# --------------------------------------------------------------------------- #


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="File not found"):
        read_records(tmp_path / "nope.csv")


def test_directory_raises(tmp_path: Path) -> None:
    folder = tmp_path / "folder.csv"
    folder.mkdir()
    with pytest.raises(IngestionError, match="Not a file"):
        read_records(folder)


@pytest.mark.parametrize("name", ["data.txt", "data.xlsx", "data"])
def test_unsupported_extension_raises(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, name, "anything")
    with pytest.raises(IngestionError, match="Unsupported file type"):
        read_records(path)


def test_non_utf8_file_raises(tmp_path: Path) -> None:
    path = write(tmp_path, "latin1.csv", HEADER.encode() + "1,Jos\xe9,Ruiz,j@x.com\n".encode("latin-1"))
    with pytest.raises(IngestionError, match="not valid UTF-8"):
        read_records(path)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def test_csv_reads_rows_as_dicts(tmp_path: Path) -> None:
    path = write(tmp_path, "c.csv", HEADER + "1,Alice,Johnson,a@example.com\n2,Bob,Smith,b@example.com\n")
    records = read_records(path)
    assert [r.data for r in records] == [
        {"customer_id": "1", "first_name": "Alice", "last_name": "Johnson", "email": "a@example.com"},
        {"customer_id": "2", "first_name": "Bob", "last_name": "Smith", "email": "b@example.com"},
    ]
    assert [r.position for r in records] == [2, 3]  # line numbers, header is line 1


def test_csv_handles_excel_bom_and_header_spaces(tmp_path: Path) -> None:
    path = write(tmp_path, "c.csv", b"\xef\xbb\xbfcustomer_id , first_name\n1,Alice\n")
    [record] = read_records(path)
    assert record.data == {"customer_id": "1", "first_name": "Alice"}


def test_csv_quoted_comma_is_one_field(tmp_path: Path) -> None:
    path = write(tmp_path, "c.csv", HEADER + '1,"Mary, Jr.",Smith,m@example.com\n')
    [record] = read_records(path)
    assert record.data is not None and record.data["first_name"] == "Mary, Jr."


def test_csv_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = write(tmp_path, "c.csv", HEADER + "\n1,A,B,a@x.com\n,,,\n\n2,C,D,c@x.com\n")
    records = read_records(path)
    assert len(records) == 2
    assert [r.position for r in records] == [3, 6]


@pytest.mark.parametrize("row", ["1,Alice,Johnson\n", "1,Alice,Johnson,a@x.com,EXTRA\n"])
def test_csv_wrong_column_count_is_malformed_record(tmp_path: Path, row: str) -> None:
    path = write(tmp_path, "c.csv", HEADER + row + "2,Bob,Smith,b@x.com\n")
    bad, good = read_records(path)
    assert bad.is_malformed and bad.data is None
    assert "expected 4 columns" in (bad.parse_error or "")
    assert bad.raw is not None
    assert not good.is_malformed


def test_csv_empty_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="empty"):
        read_records(write(tmp_path, "c.csv", ""))


def test_csv_header_only_gives_no_records(tmp_path: Path) -> None:
    assert read_records(write(tmp_path, "c.csv", HEADER)) == []


def test_csv_duplicate_header_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="duplicate column"):
        read_records(write(tmp_path, "c.csv", "id,name,name\n1,a,b\n"))


def test_csv_empty_header_name_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="empty column name"):
        read_records(write(tmp_path, "c.csv", "id,,email\n1,a,b\n"))


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


def test_json_array_of_objects(tmp_path: Path) -> None:
    items = [{"customer_id": 1}, {"customer_id": 2}]
    records = read_records(write(tmp_path, "c.json", json.dumps(items)))
    assert [r.data for r in records] == items
    assert [r.position for r in records] == [1, 2]


def test_json_records_wrapper_object(tmp_path: Path) -> None:
    payload = {"source": "crm", "records": [{"customer_id": 1}]}
    [record] = read_records(write(tmp_path, "c.json", json.dumps(payload)))
    assert record.data == {"customer_id": 1}


def test_json_invalid_syntax_raises_with_location(tmp_path: Path) -> None:
    path = write(tmp_path, "c.json", '[{"customer_id": 1},\n]')
    with pytest.raises(IngestionError, match=r"not valid JSON \(line 2"):
        read_records(path)


@pytest.mark.parametrize("payload", ['{"customer_id": 1}', '"text"', "42", "null"])
def test_json_wrong_top_level_shape_raises(tmp_path: Path, payload: str) -> None:
    with pytest.raises(IngestionError, match="expected a JSON array"):
        read_records(write(tmp_path, "c.json", payload))


def test_json_empty_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="empty"):
        read_records(write(tmp_path, "c.json", "   \n"))


def test_json_non_object_items_are_malformed_records(tmp_path: Path) -> None:
    path = write(tmp_path, "c.json", json.dumps([{"customer_id": 1}, "oops", [1, 2], None]))
    good, text, array, null = read_records(path)
    assert not good.is_malformed
    assert text.parse_error == "expected a JSON object, found string"
    assert array.parse_error == "expected a JSON object, found array"
    assert null.parse_error == "expected a JSON object, found null"
    assert text.raw == "oops"


# --------------------------------------------------------------------------- #
# JSON Lines
# --------------------------------------------------------------------------- #


def test_jsonl_one_object_per_line(tmp_path: Path) -> None:
    path = write(tmp_path, "c.jsonl", '{"customer_id": 1}\n\n{"customer_id": 2}\n')
    records = read_records(path)
    assert [r.data for r in records] == [{"customer_id": 1}, {"customer_id": 2}]
    assert [r.position for r in records] == [1, 3]


def test_jsonl_broken_line_does_not_stop_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "c.jsonl", '{"customer_id": 1}\n{"customer_id": \n[3]\n{"customer_id": 4}\n')
    records = read_records(path)
    assert len(records) == 4
    assert records[1].is_malformed and "invalid JSON" in (records[1].parse_error or "")
    assert records[2].parse_error == "expected a JSON object, found array"
    assert records[3].data == {"customer_id": 4}
