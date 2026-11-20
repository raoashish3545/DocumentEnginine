"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from document_engine import database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"


@pytest.fixture
def valid_record() -> dict[str, Any]:
    """A record that passes validation, written the way a CSV file gives it (all text)."""
    return {
        "customer_id": "1",
        "first_name": "Alice",
        "last_name": "Johnson",
        "email": "alice.johnson@example.com",
        "age": "34",
        "country": "USA",
        "signup_date": "2023-01-15",
        "total_spend": "1250.50",
        "is_active": "true",
    }


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory database with the schema created."""
    connection = database.connect(":memory:")
    database.init_db(connection)
    yield connection
    connection.close()
