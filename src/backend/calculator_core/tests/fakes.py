"""In-memory fakes of the outbound ports, shared by the unit tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.object_store import ObjectTooLargeError
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import HistoryCursor

FIXED_ID = UUID("00000000-0000-0000-0000-000000000001")
FIXED_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class FixedIdGenerator:
    def new_id(self) -> UUID:
        return FIXED_ID


class FailingRepository:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def save(self, calculation: Calculation) -> None:
        raise self.error

    def list_after(self, limit: int, after: HistoryCursor | None) -> list[Calculation]:
        raise InfrastructureError("not used")


def minutes_after_fixed(minutes: int) -> datetime:
    return FIXED_NOW + timedelta(minutes=minutes)


class InMemoryObjectStore:
    """Object store fake: objects by key, the reports written and the moves made."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.reports: dict[str, str] = {}
        self.moves: list[tuple[str, str]] = []

    def get(self, key: str, max_bytes: int) -> bytes:
        data = self.objects[key]
        if len(data) > max_bytes:
            raise ObjectTooLargeError()
        return data

    def put_report(self, source_key: str, report: str) -> None:
        self.reports[source_key] = report

    def move(self, key: str, outcome: str) -> None:
        self.moves.append((key, outcome))
        del self.objects[key]
