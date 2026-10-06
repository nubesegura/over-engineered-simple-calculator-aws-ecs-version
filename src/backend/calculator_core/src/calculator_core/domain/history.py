"""History ordering and the opaque pagination cursor.

Order: `occurred_at` descending, ties broken by `calculation_id` descending. The cursor
holds the key of the last row of a page, so the next page is "every row strictly after it
in that order" (keyset pagination: no repeats, no skips, stable while rows are added).
"""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from calculator_core.domain.calculation import Calculation
from calculator_core.domain.errors import InvalidCursorError


@dataclass(frozen=True)
class HistoryCursor:
    occurred_at: datetime
    calculation_id: UUID


def sort_key(calculation: Calculation) -> tuple[datetime, UUID]:
    """Key of the history order; sort with `reverse=True` (most recent first)."""
    return (calculation.occurred_at, calculation.id)


def cursor_for(calculation: Calculation) -> HistoryCursor:
    return HistoryCursor(calculation.occurred_at, calculation.id)


def is_after_cursor(calculation: Calculation, cursor: HistoryCursor) -> bool:
    """True when the row comes strictly later than the cursor in the history order."""
    return sort_key(calculation) < (cursor.occurred_at, cursor.calculation_id)


def encode_cursor(cursor: HistoryCursor) -> str:
    payload = {
        "t": cursor.occurred_at.astimezone(UTC).isoformat(),
        "i": str(cursor.calculation_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> HistoryCursor:
    """Decode a cursor produced by `encode_cursor`; anything else is an input error."""
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        occurred_at = datetime.fromisoformat(payload["t"])
        calculation_id = UUID(payload["i"])
    except (
        binascii.Error,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
    ) as error:
        raise InvalidCursorError() from error
    if occurred_at.tzinfo is None:
        raise InvalidCursorError()
    return HistoryCursor(occurred_at, calculation_id)
