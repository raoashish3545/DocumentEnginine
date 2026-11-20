"""Tests for record validation and de-duplication."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from document_engine.validation import (
    CoercionError,
    Deduplicator,
    parse_bool,
    parse_float,
    parse_int,
    validate_record,
)


def error_fields(record: dict[str, Any]) -> set[str]:
    return {e.field for e in validate_record(record).errors}


# --------------------------------------------------------------------------- #
# Valid records
# --------------------------------------------------------------------------- #


def test_valid_csv_style_record_is_converted_to_python_types(valid_record: dict[str, Any]) -> None:
    result = validate_record(valid_record)
    assert result.is_valid
    assert result.record == {
        "customer_id": 1,
        "first_name": "Alice",
        "last_name": "Johnson",
        "email": "alice.johnson@example.com",
        "age": 34,
        "country": "USA",
        "signup_date": date(2023, 1, 15),
        "total_spend": 1250.5,
        "is_active": True,
    }


def test_valid_json_style_record_with_native_types() -> None:
    record = {
        "customer_id": 7,
        "first_name": "Bo",
        "last_name": "Li",
        "email": "bo.li@example.com",
        "age": 40.0,  # whole-number float is accepted
        "country": "China",
        "signup_date": "2020-02-29",
        "total_spend": 0,
        "is_active": False,
    }
    result = validate_record(record)
    assert result.is_valid
    assert result.record is not None
    assert result.record["age"] == 40 and isinstance(result.record["age"], int)
    assert result.record["total_spend"] == 0.0
    assert result.record["is_active"] is False


def test_optional_fields_may_be_missing(valid_record: dict[str, Any]) -> None:
    for name in ("age", "total_spend", "is_active"):
        del valid_record[name]
    result = validate_record(valid_record)
    assert result.is_valid
    assert result.record is not None
    assert result.record["age"] is None
    assert result.record["total_spend"] is None
    assert result.record["is_active"] is None


def test_text_is_trimmed_and_email_lower_cased(valid_record: dict[str, Any]) -> None:
    valid_record.update(first_name="  Alice ", email="  Alice.Johnson@Example.COM ")
    result = validate_record(valid_record)
    assert result.record is not None
    assert result.record["first_name"] == "Alice"
    assert result.record["email"] == "alice.johnson@example.com"


def test_unknown_fields_are_ignored(valid_record: dict[str, Any]) -> None:
    valid_record["notes"] = "VIP"
    result = validate_record(valid_record)
    assert result.is_valid and result.record is not None
    assert "notes" not in result.record


# --------------------------------------------------------------------------- #
# Required fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name", ["customer_id", "first_name", "last_name", "email", "country", "signup_date"]
)
@pytest.mark.parametrize("missing", [None, "", "   ", "<absent>"])
def test_required_field_missing(valid_record: dict[str, Any], name: str, missing: Any) -> None:
    if missing == "<absent>":
        del valid_record[name]
    else:
        valid_record[name] = missing
    result = validate_record(valid_record)
    assert not result.is_valid
    assert result.record is None
    assert [str(e) for e in result.errors] == [f"{name}: required field is missing"]


def test_all_errors_are_reported_together(valid_record: dict[str, Any]) -> None:
    valid_record.update(email="nope", age="abc", total_spend="-1", first_name="")
    assert error_fields(valid_record) == {"email", "age", "total_spend", "first_name"}


def test_non_mapping_record_is_rejected() -> None:
    result = validate_record(["not", "a", "dict"])  # type: ignore[arg-type]
    assert not result.is_valid
    assert result.errors[0].field == "_record"


# --------------------------------------------------------------------------- #
# Data types and value rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("customer_id", "abc"),
        ("customer_id", "12.5"),
        ("customer_id", 0),
        ("customer_id", -4),
        ("customer_id", True),
        ("age", "thirty"),
        ("age", 30.5),
        ("age", -1),
        ("age", 121),
        ("age", False),
        ("total_spend", "ten"),
        ("total_spend", -0.01),
        ("total_spend", "nan"),
        ("total_spend", "inf"),
        ("is_active", "maybe"),
        ("is_active", 2),
        ("first_name", 123),
        ("first_name", "x" * 51),
        ("email", "no-at-sign.example.com"),
        ("email", "two@@example.com"),
        ("email", "user@nodot"),
        ("email", "has space@example.com"),
        ("signup_date", "2023-13-01"),
        ("signup_date", "2023-02-30"),
        ("signup_date", "15/01/2023"),
        ("signup_date", "yesterday"),
        ("signup_date", 20230115),
    ],
)
def test_invalid_value_is_rejected(valid_record: dict[str, Any], name: str, value: Any) -> None:
    valid_record[name] = value
    assert error_fields(valid_record) == {name}


def test_future_signup_date_is_rejected(valid_record: dict[str, Any]) -> None:
    valid_record["signup_date"] = (date.today() + timedelta(days=1)).isoformat()
    result = validate_record(valid_record)
    assert "must not be in the future" in str(result.errors[0])


def test_today_is_a_valid_signup_date(valid_record: dict[str, Any]) -> None:
    valid_record["signup_date"] = date.today().isoformat()
    assert validate_record(valid_record).is_valid


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("YES", True), ("1", True), (1, True), ("False", False), ("n", False), (0, False)],
)
def test_parse_bool(value: Any, expected: bool) -> None:
    assert parse_bool(value) is expected


@pytest.mark.parametrize(("value", "expected"), [("42", 42), (" 7 ", 7), (3.0, 3), (5, 5)])
def test_parse_int(value: Any, expected: int) -> None:
    assert parse_int(value) == expected


def test_parse_float_rejects_lists() -> None:
    with pytest.raises(CoercionError):
        parse_float([1.0])


# --------------------------------------------------------------------------- #
# De-duplication
# --------------------------------------------------------------------------- #


def test_deduplicator_flags_repeated_key() -> None:
    dedup = Deduplicator(("customer_id",))
    assert dedup.is_duplicate({"customer_id": 1}) is False
    assert dedup.is_duplicate({"customer_id": 2}) is False
    assert dedup.is_duplicate({"customer_id": 1, "email": "other@x.com"}) is True
    assert len(dedup) == 2


def test_deduplicator_uses_existing_keys() -> None:
    dedup = Deduplicator(("customer_id",), existing_keys={(5,)})
    assert dedup.is_duplicate({"customer_id": 5}) is True
    assert dedup.is_duplicate({"customer_id": 6}) is False


def test_deduplicator_composite_key() -> None:
    dedup = Deduplicator(("email", "country"))
    assert not dedup.is_duplicate({"email": "a@x.com", "country": "UK"})
    assert not dedup.is_duplicate({"email": "a@x.com", "country": "US"})
    assert dedup.is_duplicate({"email": "a@x.com", "country": "UK"})


def test_text_and_number_ids_are_the_same_key(valid_record: dict[str, Any]) -> None:
    """"1" from a CSV and 1 from JSON must count as the same customer."""
    from_csv = validate_record(valid_record).record
    from_json = validate_record({**valid_record, "customer_id": 1}).record
    assert from_csv is not None and from_json is not None
    dedup = Deduplicator()
    assert not dedup.is_duplicate(from_csv)
    assert dedup.is_duplicate(from_json)


def test_deduplicator_requires_key_fields() -> None:
    with pytest.raises(ValueError):
        Deduplicator(())
