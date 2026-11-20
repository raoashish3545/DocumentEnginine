"""document_engine: validate CSV and JSON records and store clean data in SQLite."""

from document_engine.ingestion import IngestionError, RawRecord, read_records
from document_engine.pipeline import process_file, process_files
from document_engine.reporting import IngestionReport, format_report, format_summary
from document_engine.validation import Deduplicator, ValidationResult, validate_record

__version__ = "0.1.0"

__all__ = [
    "Deduplicator",
    "IngestionError",
    "IngestionReport",
    "RawRecord",
    "ValidationResult",
    "format_report",
    "format_summary",
    "process_file",
    "process_files",
    "read_records",
    "validate_record",
]
