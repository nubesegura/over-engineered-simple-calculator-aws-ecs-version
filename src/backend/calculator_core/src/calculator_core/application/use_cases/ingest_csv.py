"""Ingestion of one CSV file: validate every row, then save the valid ones.

The whole file is parsed before anything is saved, so a file that is rejected (wrong header,
invalid CSV, too many rows, too large) saves nothing. Individual invalid rows are skipped
and reported. Counts: `read` data rows (blank lines excluded), `saved` valid rows sent to the
idempotent repository (a repeated row counts again but creates no duplicate), `skipped`
invalid rows.
"""

import csv
import io
import json
from dataclasses import dataclass

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.calculation_repository import CalculationRepository
from calculator_core.application.ports.object_store import ObjectStore, ObjectTooLargeError
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.errors import CalculatorError, DomainError
from calculator_core.domain.ingestion import HEADER, MAX_FILE_BYTES, MAX_ROWS, parse_row


@dataclass(frozen=True)
class SkippedRow:
    line: int
    reason: str


@dataclass(frozen=True)
class IngestResult:
    read: int = 0
    saved: int = 0
    skipped: tuple[SkippedRow, ...] = ()
    rejection: str | None = None

    @property
    def rejected(self) -> bool:
        return self.rejection is not None

    def to_report(self, source_key: str) -> str:
        """JSON report; every value is a JSON string or number, so a formula stays text."""
        return json.dumps(
            {
                "source": source_key,
                "status": "rejected" if self.rejected else "processed",
                "rejection": self.rejection,
                "read": self.read,
                "saved": self.saved,
                "skipped": len(self.skipped),
                "skipped_rows": [{"line": row.line, "reason": row.reason} for row in self.skipped],
            }
        )


@dataclass
class _ParsedFile:
    calculations: list[Calculation]
    skipped: list[SkippedRow]
    read: int


def _rejected(reason: str) -> IngestResult:
    return IngestResult(rejection=reason)


class IngestCsv:
    def __init__(self, store: ObjectStore, repository: CalculationRepository) -> None:
        self.store = store
        self.repository = repository

    def execute(self, key: str) -> IngestResult:
        """Read, validate and save one file; raises `InfrastructureError` on a failure."""
        try:
            data = self.store.get(key, MAX_FILE_BYTES)
        except ObjectTooLargeError:
            return _rejected(f"The file is larger than {MAX_FILE_BYTES} bytes.")
        parsed = _parse_file(data)
        if isinstance(parsed, IngestResult):
            return parsed
        for calculation in parsed.calculations:
            self._save(calculation)
        return IngestResult(
            read=parsed.read, saved=len(parsed.calculations), skipped=tuple(parsed.skipped)
        )

    def _save(self, calculation: Calculation) -> None:
        try:
            self.repository.save(calculation)
        except CalculatorError:
            raise
        except Exception as error:
            raise InfrastructureError("A calculation could not be saved.", error) from error


def _parse_file(data: bytes) -> IngestResult | _ParsedFile:
    if len(data) > MAX_FILE_BYTES:
        return _rejected(f"The file is larger than {MAX_FILE_BYTES} bytes.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _rejected("The file is not valid UTF-8.")
    if "\x00" in text:
        return _rejected("The file contains NUL characters.")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    parsed = _ParsedFile([], [], 0)
    try:
        header = next(reader, None)
        if header is None or tuple(cell.strip() for cell in header) != HEADER:
            return _rejected(f"The header must be exactly: {','.join(HEADER)}.")
        while True:
            start_line = reader.line_num + 1
            cells = next(reader, None)
            if cells is None:
                return parsed
            if not cells:
                continue
            parsed.read += 1
            if parsed.read > MAX_ROWS:
                return _rejected(f"The file has more than {MAX_ROWS} data rows.")
            _add_row(parsed, start_line, cells)
    except csv.Error:
        return _rejected("The file is not valid CSV.")


def _add_row(parsed: _ParsedFile, line: int, cells: list[str]) -> None:
    try:
        parsed.calculations.append(parse_row(cells))
    except DomainError as error:
        parsed.skipped.append(SkippedRow(line, str(error)))
