import base64
import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.application.errors import InvalidQueryError
from calculator_core.application.use_cases.read_history import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ReadHistory,
    ReadHistoryQuery,
)
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.errors import InvalidCursorError, InvalidInputError
from calculator_core.domain.history import HistoryCursor, decode_cursor, encode_cursor
from calculator_core.domain.operation import Operation
from tests.fakes import FIXED_NOW


def _calculation(seconds: int, number: int) -> Calculation:
    return Calculation(
        id=UUID(int=number),
        operation=Operation.ADD,
        operand_a=Decimal(1),
        operand_b=Decimal(2),
        result=Decimal(3),
        occurred_at=FIXED_NOW + timedelta(seconds=seconds),
    )


def _repository(*rows: Calculation) -> InMemoryCalculationRepository:
    repo = InMemoryCalculationRepository()
    for row in rows:
        repo.save(row)
    return repo


def _numbers(items: list[Calculation]) -> list[int]:
    return [item.id.int for item in items]


def test_default_limit_is_20_and_next_cursor_is_null_at_the_end() -> None:
    repo = _repository(*(_calculation(n, n) for n in range(25)))

    first = ReadHistory(repo).execute(ReadHistoryQuery())
    second = ReadHistory(repo).execute(ReadHistoryQuery(cursor=first.next_cursor))

    assert DEFAULT_LIMIT == 20
    assert len(first.items) == 20
    assert first.next_cursor is not None
    assert len(second.items) == 5
    assert second.next_cursor is None


def test_limit_above_the_cap_is_reduced_to_100() -> None:
    repo = _repository(*(_calculation(n, n) for n in range(MAX_LIMIT + 5)))

    page = ReadHistory(repo).execute(ReadHistoryQuery(limit=10_000))

    assert len(page.items) == MAX_LIMIT
    assert page.next_cursor is not None


@pytest.mark.parametrize("limit", [0, -1])
def test_limit_below_one_is_an_input_error(limit: int) -> None:
    with pytest.raises(InvalidQueryError) as caught:
        ReadHistory(_repository()).execute(ReadHistoryQuery(limit=limit))

    assert isinstance(caught.value, InvalidInputError)


@pytest.mark.parametrize("cursor", ["not base64!", "e30", "bm90LWpzb24", "WzEsMl0"])
def test_invalid_cursor_is_an_input_error(cursor: str) -> None:
    with pytest.raises(InvalidInputError):
        ReadHistory(_repository()).execute(ReadHistoryQuery(cursor=cursor))


def _forge_raw(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"t":"2026-10-05T12:00:00+00:00","i":5}',
        b'{"t":5,"i":"00000000-0000-0000-0000-000000000001"}',
        b'{"t":null,"i":null}',
        b"[" * 20_000 + b"]" * 20_000,
    ],
    ids=["id-not-text", "time-not-text", "nulls", "deeply-nested"],
)
def test_cursor_with_wrong_types_or_extreme_nesting_is_an_input_error(raw: bytes) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(_forge_raw(raw))


def test_cursor_round_trips_with_timezone() -> None:
    cursor = HistoryCursor(occurred_at=FIXED_NOW, calculation_id=UUID(int=7))

    assert decode_cursor(encode_cursor(cursor)) == cursor


def test_cursor_without_timezone_is_rejected() -> None:
    forged = _forge({"t": "2026-10-05T12:00:00", "i": str(UUID(int=1))})

    with pytest.raises(InvalidInputError):
        decode_cursor(forged)


def _forge(payload: dict[str, str]) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_most_recent_first_with_stable_tie_break_on_calculation_id() -> None:
    repo = _repository(
        _calculation(10, 1),
        _calculation(20, 2),
        _calculation(20, 3),
        _calculation(5, 4),
    )

    page = ReadHistory(repo).execute(ReadHistoryQuery())

    assert _numbers(page.items) == [3, 2, 1, 4]


def test_paging_never_repeats_or_skips_rows_even_with_equal_timestamps() -> None:
    repo = _repository(*(_calculation(n // 3, n) for n in range(10)))
    use_case = ReadHistory(repo)

    seen: list[int] = []
    cursor: str | None = None
    while True:
        page = use_case.execute(ReadHistoryQuery(limit=3, cursor=cursor))
        seen.extend(_numbers(page.items))
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sorted(seen) == list(range(10))
    assert len(seen) == len(set(seen))
    assert seen == _numbers(use_case.execute(ReadHistoryQuery(limit=100)).items)


def test_saving_the_same_calculation_id_twice_keeps_one_row() -> None:
    repo = _repository(_calculation(1, 1), _calculation(1, 1), _calculation(9, 1))

    page = ReadHistory(repo).execute(ReadHistoryQuery())

    assert _numbers(page.items) == [1]
    assert page.items[0].occurred_at == FIXED_NOW + timedelta(seconds=1)
