# Sample data

Small handmade files where every bad record has a known problem, so you can
check the pipeline's output by eye. Ingest them into an **empty** database:
records already in the database count as duplicates on later runs.

## customers.csv: 12 records → 5 loaded, 6 rejected, 1 duplicate

| Line | customer_id | Expected | Why |
|-----:|------------:|----------|-----|
| 2 | 1 | loaded | |
| 3 | 2 | loaded | |
| 4 | 3 | loaded | empty optional `age` / `total_spend` become NULL |
| 5 | 4 | loaded | email is lower-cased; `yes` → true |
| 6 | 5 | rejected | email has no `@` |
| 7 | 6 | rejected | age `abc` is not a number |
| 8 | 7 | rejected | `first_name` is missing |
| 9 | 8 | rejected | month 13 is not a valid date |
| 10 | 9 | rejected | negative `total_spend` |
| 11 | 2 | duplicate | same `customer_id` as line 3 |
| 12 | 10 | rejected | 6 columns instead of 9 |
| 14 | 11 | loaded | the blank line 13 is skipped |

## customers.json: 10 records → 4 loaded, 5 rejected, 1 duplicate

| Index | customer_id | Expected | Why |
|------:|------------:|----------|-----|
| 1 | 101 | loaded | |
| 2 | 102 | loaded | `age: null` is allowed (optional) |
| 3 | "103" | loaded | numbers given as text are converted |
| 4 | 104 | rejected | `email` key is absent |
| 5 | 105 | rejected | age `30.5` is not a whole number |
| 6 | 106 | rejected | `is_active: "maybe"` is not true/false |
| 7 | — | rejected | item is a string, not an object |
| 8 | 101 | duplicate | same `customer_id` as index 1 |
| 9 | 107 | rejected | `signup_date` is in the future |
| 10 | 108 | loaded | the extra `notes` field is ignored |

## customers.jsonl: 6 records → 3 loaded, 2 rejected, 1 duplicate

| Line | customer_id | Expected | Why |
|-----:|------------:|----------|-----|
| 1 | 201 | loaded | |
| 2 | 202 | loaded | |
| 3 | 203 | rejected | line is cut off: not valid JSON |
| 4 | — | skipped | blank line |
| 5 | 0 | rejected | `customer_id` must be at least 1 |
| 6 | 201 | duplicate | same `customer_id` as line 1 |
| 7 | 205 | loaded | |

## malformed/: whole-file failures

Each of these is recorded as a **failed** run and nothing from it is loaded.

| File | Problem |
|------|---------|
| `broken_syntax.json` | trailing comma: invalid JSON |
| `wrong_shape.json` | a single object, not an array of records |
| `empty.csv` | no header row |
| `notes.txt` | unsupported file type |

## Larger data sets

`data/generated/` is created by `scripts/generate_sample_data.py` (see the main
README) and is not committed.
