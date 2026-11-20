"""Validate records against a schema and detect duplicates.

Each field is described by a :class:`FieldSpec`: whether it is required, a
parser that converts the raw value to the right Python type, and an optional
check on the converted value. CSV gives every value as text and JSON gives
native types, so the parsers accept both (``"42"`` and ``42`` are both a valid
integer) but reject values that would lose information (``42.5`` is not an
integer, ``true`` is not a number).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
DATE_FORMAT = "%Y-%m-%d"
_TRUE_VALUES = frozenset({"true", "t", "yes", "y", "1"})
_FALSE_VALUES = frozenset({"false", "f", "no", "n", "0"})

# Field(s) that identify a customer. Two records with the same key are duplicates.
DEDUP_KEY_FIELDS: tuple[str, ...] = ("customer_id",)


class CoercionError(ValueError):
    """Raised when a raw value cannot be converted to the expected type."""


# --------------------------------------------------------------------------- #
# Parsers: raw value -> typed value
# --------------------------------------------------------------------------- #


def parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise CoercionError("expected an integer, found a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise CoercionError(f"expected an integer, found {value!r}")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise CoercionError(f"expected an integer, found {value!r}") from None
    raise CoercionError(f"expected an integer, found {type(value).__name__}")


def parse_float(value: Any) -> float:
    if isinstance(value, bool):
        raise CoercionError("expected a number, found a boolean")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise CoercionError(f"expected a number, found {value!r}") from None
    else:
        raise CoercionError(f"expected a number, found {type(value).__name__}")
    if not math.isfinite(number):
        raise CoercionError(f"expected a finite number, found {value!r}")
    return number


def parse_str(value: Any) -> str:
    if not isinstance(value, str):
        raise CoercionError(f"expected text, found {type(value).__name__}")
    return value.strip()


def parse_email(value: Any) -> str:
    return parse_str(value).lower()


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise CoercionError(f"expected a date (YYYY-MM-DD), found {type(value).__name__}")
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError:
        raise CoercionError(f"expected a date (YYYY-MM-DD), found {value!r}") from None


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
    raise CoercionError(f"expected true/false, found {value!r}")


# --------------------------------------------------------------------------- #
# Checks: typed value -> error message, or None if it is fine
# --------------------------------------------------------------------------- #

Check = Callable[[Any], "str | None"]


def in_range(minimum: float | None = None, maximum: float | None = None) -> Check:
    def check(value: float) -> str | None:
        if minimum is not None and value < minimum:
            return f"must be at least {minimum}, found {value}"
        if maximum is not None and value > maximum:
            return f"must be at most {maximum}, found {value}"
        return None

    return check


def max_length(limit: int) -> Check:
    def check(value: str) -> str | None:
        if len(value) > limit:
            return f"must be at most {limit} characters, found {len(value)}"
        return None

    return check


def valid_email(value: str) -> str | None:
    if not EMAIL_PATTERN.match(value):
        return f"not a valid email address: {value!r}"
    return None


def not_in_future(value: date) -> str | None:
    if value > date.today():
        return f"must not be in the future, found {value.isoformat()}"
    return None


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    name: str
    parser: Callable[[Any], Any]
    required: bool = True
    checks: tuple[Check, ...] = ()


CUSTOMER_SCHEMA: tuple[FieldSpec, ...] = (
    FieldSpec("customer_id", parse_int, checks=(in_range(minimum=1),)),
    FieldSpec("first_name", parse_str, checks=(max_length(50),)),
    FieldSpec("last_name", parse_str, checks=(max_length(50),)),
    FieldSpec("email", parse_email, checks=(max_length(254), valid_email)),
    FieldSpec("age", parse_int, required=False, checks=(in_range(0, 120),)),
    FieldSpec("country", parse_str, checks=(max_length(56),)),
    FieldSpec("signup_date", parse_date, checks=(not_in_future,)),
    FieldSpec("total_spend", parse_float, required=False, checks=(in_range(minimum=0),)),
    FieldSpec("is_active", parse_bool, required=False),
)


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    record: dict[str, Any] | None
    errors: list[FieldError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_record(
    data: Mapping[str, Any], schema: Sequence[FieldSpec] = CUSTOMER_SCHEMA
) -> ValidationResult:
    """Validate one record and return the cleaned record or all of its errors.

    Every field is checked, so a record with several problems reports all of
    them. Fields that are not in the schema are ignored.
    """
    if not isinstance(data, Mapping):
        return ValidationResult(None, [FieldError("_record", "record is not an object")])

    clean: dict[str, Any] = {}
    errors: list[FieldError] = []

    for spec in schema:
        raw = data.get(spec.name)
        if is_missing(raw):
            if spec.required:
                errors.append(FieldError(spec.name, "required field is missing"))
            else:
                clean[spec.name] = None
            continue

        try:
            value = spec.parser(raw)
        except CoercionError as exc:
            errors.append(FieldError(spec.name, str(exc)))
            continue

        problems = [msg for check in spec.checks if (msg := check(value)) is not None]
        if problems:
            errors.extend(FieldError(spec.name, msg) for msg in problems)
        else:
            clean[spec.name] = value

    if errors:
        return ValidationResult(None, errors)
    return ValidationResult(clean)


# --------------------------------------------------------------------------- #
# De-duplication
# --------------------------------------------------------------------------- #


class Deduplicator:
    """Remember the keys seen so far and flag records whose key repeats.

    Pass ``existing_keys`` (for example, keys already in the database) so that
    records loaded by an earlier run are also treated as duplicates.
    """

    def __init__(
        self,
        key_fields: Sequence[str] = DEDUP_KEY_FIELDS,
        existing_keys: Iterable[tuple[Any, ...]] = (),
    ) -> None:
        if not key_fields:
            raise ValueError("key_fields must not be empty")
        self.key_fields = tuple(key_fields)
        self._seen: set[tuple[Any, ...]] = set(existing_keys)

    def key_for(self, record: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(record[name] for name in self.key_fields)

    def is_duplicate(self, record: Mapping[str, Any]) -> bool:
        """Return True if the key was seen before; otherwise remember it."""
        key = self.key_for(record)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def __len__(self) -> int:
        return len(self._seen)
