"""Rules for one row of an ingested CSV file.

The result is always recomputed by the domain and never read from the file. The calculation
id is a UUIDv5 of the normalized row content, so ingesting the same row (or file) again
produces the same id and the idempotent repository save creates no duplicate.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from calculator_core.domain.calculation import Calculation
from calculator_core.domain.errors import DomainError
from calculator_core.domain.operation import Operation, validate_operand

HEADER = ("operation", "operand_a", "operand_b", "occurred_at")
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_CELL_CHARS = 64
MAX_SNIPPET_CHARS = 20
# Fixed namespace of the row ids: changing it would make old files create duplicates.
INGEST_NAMESPACE = UUID("6f1d3c52-8a0e-4b6e-9a53-2d7b0c9e41af")

_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)


class InvalidRowError(DomainError):
    """A row of the file broke a rule; the message is safe to put in the report."""

    code = "INVALID_ROW"


def sanitize_snippet(value: str) -> str:
    """Short, printable excerpt of a cell for the report (never the whole value)."""
    printable = "".join(char if char.isprintable() else "?" for char in value)
    if len(printable) > MAX_SNIPPET_CHARS:
        return printable[:MAX_SNIPPET_CHARS] + "..."
    return printable


def parse_row(cells: Sequence[str]) -> Calculation:
    """Validate one data row and build the calculation (result recomputed by the domain)."""
    if len(cells) != len(HEADER):
        raise InvalidRowError(f"Expected {len(HEADER)} columns but found {len(cells)}.")
    operation_cell, a_cell, b_cell, time_cell = (cell.strip() for cell in cells)
    operation = _parse_operation(operation_cell)
    operand_a = _parse_operand("operand_a", a_cell)
    operand_b = _parse_operand("operand_b", b_cell)
    occurred_at = _parse_time(time_cell)
    return Calculation(
        id=row_id(operation, operand_a, operand_b, occurred_at),
        operation=operation,
        operand_a=operand_a,
        operand_b=operand_b,
        result=operation.calculate(operand_a, operand_b),
        occurred_at=occurred_at,
    )


def row_id(operation: Operation, a: Decimal, b: Decimal, occurred_at: datetime) -> UUID:
    content = "|".join(
        (operation.value, _canonical(a), _canonical(b), occurred_at.astimezone(UTC).isoformat())
    )
    return uuid5(INGEST_NAMESPACE, content)


def _canonical(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _parse_operation(cell: str) -> Operation:
    try:
        return Operation(cell.lower())
    except ValueError:
        raise InvalidRowError(f"Unknown operation '{sanitize_snippet(cell)}'.") from None


def _parse_operand(name: str, cell: str) -> Decimal:
    message = f"{name} is not a valid number ('{sanitize_snippet(cell)}')."
    if len(cell) > MAX_CELL_CHARS or not _NUMBER.fullmatch(cell):
        raise InvalidRowError(message)
    try:
        value = Decimal(cell)
    except InvalidOperation:
        raise InvalidRowError(message) from None
    # Validated here, before the id is derived: normalizing a huge exponent would overflow.
    return validate_operand(value)


def _parse_time(cell: str) -> datetime:
    message = f"occurred_at is not an ISO-8601 time ('{sanitize_snippet(cell)}')."
    if len(cell) > MAX_CELL_CHARS:
        raise InvalidRowError(message)
    try:
        moment = datetime.fromisoformat(cell)
    except ValueError:
        raise InvalidRowError(message) from None
    if moment.utcoffset() != timedelta(0):
        raise InvalidRowError("occurred_at must be in UTC (suffix Z or +00:00).")
    return moment.astimezone(UTC)
