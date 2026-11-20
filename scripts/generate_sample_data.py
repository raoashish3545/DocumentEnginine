"""Generate synthetic customer files with known numbers of good and bad records.

Examples:
    python scripts/generate_sample_data.py                      # 10,000 records, all formats
    python scripts/generate_sample_data.py --count 500 --format csv
    python scripts/generate_sample_data.py --invalid-rate 0.10 --duplicate-rate 0.05

The script prints the counts the pipeline should report for each file, so you
can compare them with the ingestion report. With ``--format all`` each format
uses its own customer_id range (1..., 1000001..., 2000001...) so the three files
do not count as duplicates of each other.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

FIELDNAMES: tuple[str, ...] = (
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

FIRST_NAMES = (
    "Aarav", "Amelia", "Ana", "Ben", "Chloe", "Daniel", "Diya", "Elena", "Ethan", "Fatima",
    "George", "Hana", "Isaac", "Jade", "Kenji", "Laura", "Leo", "Maya", "Mohammed", "Nina",
    "Omar", "Priya", "Rohan", "Sofia", "Wei", "Zara",
)
LAST_NAMES = (
    "Ali", "Brown", "Chen", "Costa", "Das", "Evans", "Garcia", "Gupta", "Hansen", "Ito",
    "Jones", "Khan", "Kowalski", "Martin", "Mehta", "Novak", "O'Brien", "Rossi", "Sato",
    "Schmidt", "Singh", "Smith", "Wang", "Yilmaz",
)
COUNTRIES = (
    "Australia", "Brazil", "Canada", "France", "Germany", "India", "Italy", "Japan",
    "Mexico", "Poland", "Spain", "UK", "USA",
)
DOMAINS = ("example.com", "mail.test", "corp.example.org")


class Malformed:
    """Marker for a record that cannot be parsed at all (bad CSV row / bad JSON)."""


MALFORMED = Malformed()
Row = dict[str, Any] | Malformed


@dataclass(frozen=True)
class Expected:
    total: int
    loaded: int
    rejected: int
    duplicates: int


def make_valid_record(rng: random.Random, customer_id: int) -> dict[str, Any]:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    local = f"{first}.{last}".lower().replace("'", "")
    days_ago = rng.randint(1, 365 * 8)
    return {
        "customer_id": customer_id,
        "first_name": first,
        "last_name": last,
        "email": f"{local}{customer_id}@{rng.choice(DOMAINS)}",
        "age": rng.randint(18, 90) if rng.random() > 0.05 else None,
        "country": rng.choice(COUNTRIES),
        "signup_date": (date.today() - timedelta(days=days_ago)).isoformat(),
        "total_spend": round(rng.uniform(0, 10_000), 2) if rng.random() > 0.05 else None,
        "is_active": rng.random() > 0.2,
    }


# Each corruption makes a valid record fail validation in exactly one way.
CORRUPTIONS: dict[str, Callable[[dict[str, Any], random.Random], None]] = {
    "missing_customer_id": lambda r, g: r.update(customer_id=""),
    "non_numeric_customer_id": lambda r, g: r.update(customer_id=f"CUST-{r['customer_id']}"),
    "missing_first_name": lambda r, g: r.update(first_name=""),
    "missing_email": lambda r, g: r.update(email=None),
    "invalid_email": lambda r, g: r.update(email=r["email"].replace("@", "_at_")),
    "non_numeric_age": lambda r, g: r.update(age="thirty"),
    "age_out_of_range": lambda r, g: r.update(age=g.choice([-3, 150])),
    "invalid_date": lambda r, g: r.update(signup_date=g.choice(["2023-02-30", "31/12/2023", "n/a"])),
    "future_date": lambda r, g: r.update(signup_date=(date.today() + timedelta(days=400)).isoformat()),
    "negative_spend": lambda r, g: r.update(total_spend=-round(g.uniform(1, 500), 2)),
    "invalid_is_active": lambda r, g: r.update(is_active="maybe"),
}


def generate_dataset(
    count: int,
    *,
    invalid_rate: float = 0.05,
    duplicate_rate: float = 0.03,
    malformed_rate: float = 0.01,
    seed: int = 42,
    start_id: int = 1,
) -> tuple[list[Row], Expected]:
    """Build ``count`` rows and the counts the pipeline should report for them."""
    if invalid_rate + duplicate_rate + malformed_rate >= 1:
        raise ValueError("invalid + duplicate + malformed rates must be below 1")

    rng = random.Random(seed)
    rows: list[Row] = []
    valid_pool: list[dict[str, Any]] = []
    loaded = rejected = duplicates = 0
    next_id = start_id

    for _ in range(count):
        roll = rng.random()
        if roll < malformed_rate:
            rows.append(MALFORMED)
            rejected += 1
        elif roll < malformed_rate + invalid_rate:
            record = make_valid_record(rng, next_id)
            next_id += 1
            CORRUPTIONS[rng.choice(sorted(CORRUPTIONS))](record, rng)
            rows.append(record)
            rejected += 1
        elif roll < malformed_rate + invalid_rate + duplicate_rate and valid_pool:
            duplicate = dict(rng.choice(valid_pool))
            if rng.random() < 0.5:  # same id, different details
                duplicate["total_spend"] = round(rng.uniform(0, 10_000), 2)
            rows.append(duplicate)
            duplicates += 1
        else:
            record = make_valid_record(rng, next_id)
            next_id += 1
            rows.append(record)
            valid_pool.append(record)
            loaded += 1

    return rows, Expected(count, loaded, rejected, duplicates)


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csv(rows: Sequence[Row], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDNAMES)
        for row in rows:
            if isinstance(row, Malformed):
                writer.writerow(["999999999", "Broken", "Row"])  # too few columns
            else:
                writer.writerow([_csv_cell(row.get(name)) for name in FIELDNAMES])


def write_json(rows: Sequence[Row], path: Path) -> None:
    items = ["corrupted record" if isinstance(r, Malformed) else r for r in rows]
    path.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")


def write_jsonl(rows: Sequence[Row], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, Malformed):
                handle.write('{"customer_id": 1, "first_name": \n')  # truncated JSON
            else:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")


WRITERS: dict[str, Callable[[Sequence[Row], Path], None]] = {
    "csv": write_csv,
    "json": write_json,
    "jsonl": write_jsonl,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=10_000, help="records per file")
    parser.add_argument("--format", choices=[*WRITERS, "all"], default="all")
    parser.add_argument("--out", type=Path, default=Path("data") / "generated")
    parser.add_argument("--invalid-rate", type=float, default=0.05)
    parser.add_argument("--duplicate-rate", type=float, default=0.03)
    parser.add_argument("--malformed-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    formats = list(WRITERS) if args.format == "all" else [args.format]
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"{'file':<36} {'total':>7} {'loaded':>7} {'rejected':>8} {'dupes':>6}")
    for index, fmt in enumerate(formats):
        rows, expected = generate_dataset(
            args.count,
            invalid_rate=args.invalid_rate,
            duplicate_rate=args.duplicate_rate,
            malformed_rate=args.malformed_rate,
            seed=args.seed + index,
            start_id=1 + index * 1_000_000,
        )
        path = args.out / f"customers_{args.count}.{fmt}"
        WRITERS[fmt](rows, path)
        print(
            f"{str(path):<36} {expected.total:>7} {expected.loaded:>7} "
            f"{expected.rejected:>8} {expected.duplicates:>6}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
