from dataclasses import dataclass

from calculator_core.application.errors import InvalidQueryError
from calculator_core.application.ports.calculation_repository import CalculationRepository
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import cursor_for, decode_cursor, encode_cursor

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class ReadHistoryQuery:
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    items: list[Calculation]
    next_cursor: str | None


class ReadHistory:
    def __init__(self, repository: CalculationRepository) -> None:
        self.repository = repository

    def execute(self, query: ReadHistoryQuery) -> HistoryPage:
        if query.limit < 1:
            raise InvalidQueryError("limit must be greater than 0.")
        limit = min(query.limit, MAX_LIMIT)
        after = decode_cursor(query.cursor) if query.cursor else None
        # One extra row tells whether another page exists without a second query.
        rows = self.repository.list_after(limit + 1, after)
        items = rows[:limit]
        next_cursor = encode_cursor(cursor_for(items[-1])) if len(rows) > limit else None
        return HistoryPage(items=items, next_cursor=next_cursor)
