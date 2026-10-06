from typing import Protocol

from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import HistoryCursor


class CalculationRepository(Protocol):
    def save(self, calculation: Calculation) -> None:
        """Persist a calculation. Idempotent by `calculation.id`.

        Saving an id that already exists keeps the stored row and does not fail.
        A row is visible to the next `list_after` call as soon as `save` returns.
        Must raise `InfrastructureError` if the row was not persisted.
        """

    def list_after(self, limit: int, after: HistoryCursor | None) -> list[Calculation]:
        """Return at most `limit` calculations in history order (keyset pagination).

        Order: `occurred_at` descending, then `id` descending. With `after` set, only
        rows strictly later than that key in this order are returned.
        Must raise `InfrastructureError` if the read failed.
        """
