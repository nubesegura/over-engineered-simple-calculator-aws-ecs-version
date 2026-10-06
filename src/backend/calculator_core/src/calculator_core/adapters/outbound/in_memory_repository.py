"""In-memory repository for `ENVIRONMENT=local` only (never wired outside local)."""

from uuid import UUID

from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import HistoryCursor, is_after_cursor, sort_key


class InMemoryCalculationRepository:
    """Keeps one row per calculation id and serves the keyset order of the port."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Calculation] = {}

    def save(self, calculation: Calculation) -> None:
        self.rows.setdefault(calculation.id, calculation)

    def list_after(self, limit: int, after: HistoryCursor | None) -> list[Calculation]:
        ordered = sorted(self.rows.values(), key=sort_key, reverse=True)
        if after is not None:
            ordered = [row for row in ordered if is_after_cursor(row, after)]
        return ordered[:limit]
