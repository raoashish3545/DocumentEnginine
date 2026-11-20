"""Processing reports: how many records were loaded, rejected or duplicated."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class IngestionReport:
    """Counts for one processed file.

    ``processed`` is every record read from the file, and always equals
    ``loaded + rejected + duplicates``.
    """

    source_file: str
    run_id: int | None = None
    status: str = STATUS_COMPLETED
    error: str | None = None
    loaded: int = 0
    rejected: int = 0
    duplicates: int = 0
    duration_seconds: float = 0.0
    rejection_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def processed(self) -> int:
        return self.loaded + self.rejected + self.duplicates

    @property
    def succeeded(self) -> bool:
        return self.status == STATUS_COMPLETED

    def add_rejection(self, fields: Iterable[str]) -> None:
        """Count one rejected record, noting each field that failed."""
        self.rejected += 1
        for name in set(fields):
            self.rejection_reasons[name] += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "processed": self.processed,
            "loaded": self.loaded,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "duration_seconds": round(self.duration_seconds, 3),
            "rejection_reasons": dict(self.rejection_reasons.most_common()),
        }


def format_report(report: IngestionReport) -> str:
    """Human-readable summary of one file."""
    lines = [
        f"File:        {report.source_file}",
        f"Run id:      {report.run_id}",
        f"Status:      {report.status.upper()}",
    ]
    if report.error:
        lines.append(f"Error:       {report.error}")
    lines += [
        f"Processed:   {report.processed:>8,}",
        f"  Loaded:    {report.loaded:>8,}",
        f"  Rejected:  {report.rejected:>8,}",
        f"  Duplicate: {report.duplicates:>8,}",
        f"Duration:    {report.duration_seconds:.2f}s",
    ]
    if report.rejection_reasons:
        lines.append("Rejections by field:")
        for name, count in report.rejection_reasons.most_common():
            lines.append(f"  {name:<14} {count:>6,}")
    return "\n".join(lines)


def format_summary(reports: Sequence[IngestionReport]) -> str:
    """Totals across several files."""
    failed = [r for r in reports if not r.succeeded]
    return "\n".join(
        [
            f"Files:       {len(reports)} ({len(failed)} failed)",
            f"Processed:   {sum(r.processed for r in reports):>8,}",
            f"  Loaded:    {sum(r.loaded for r in reports):>8,}",
            f"  Rejected:  {sum(r.rejected for r in reports):>8,}",
            f"  Duplicate: {sum(r.duplicates for r in reports):>8,}",
        ]
    )
