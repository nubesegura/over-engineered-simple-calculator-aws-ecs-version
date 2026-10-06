import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from calculator_core.adapters.outbound.migrations import apply_migrations
from calculator_core.adapters.outbound.postgres_repository import PostgresCalculationRepository
from calculator_core.application.errors import InfrastructureError
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import HistoryCursor
from calculator_core.domain.operation import Operation
from tests.adapters.postgres_support import MIGRATIONS_DIR, reset_database
from tests.fakes import FIXED_NOW

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def _calculation(seconds: int, number: int, result: str = "3") -> Calculation:
    return Calculation(
        id=UUID(int=number),
        operation=Operation.DIV,
        operand_a=Decimal("1.5"),
        operand_b=Decimal("0.5"),
        result=Decimal(result),
        occurred_at=FIXED_NOW + timedelta(seconds=seconds),
        correlation_id="corr",
    )


class RecordingConnection:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> RecordingConnection:
        if self.fail:
            raise psycopg.OperationalError("connection lost")
        self.statements.append((query, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class RecordingPool:
    def __init__(self, fail: bool = False) -> None:
        self.connection_in_use = RecordingConnection(fail)

    @contextmanager
    def connection(self) -> Iterator[RecordingConnection]:
        yield self.connection_in_use


def _repository(pool: RecordingPool) -> PostgresCalculationRepository:
    return PostgresCalculationRepository(cast("ConnectionPool[Any]", pool))


def test_statements_are_constants_and_values_travel_only_as_parameters() -> None:
    pool = RecordingPool()
    repository = _repository(pool)
    hostile = Calculation(
        **{**_calculation(0, 1).__dict__, "correlation_id": "x'); DROP TABLE calculations;--"}
    )

    repository.save(hostile)
    repository.list_after(10, HistoryCursor(FIXED_NOW, UUID(int=7)))

    for query, params in pool.connection_in_use.statements:
        assert query.count("%s") == len(params)
        assert "DROP TABLE" not in query
        assert str(UUID(int=1)) not in query
    assert "ON CONFLICT (calculation_id) DO NOTHING" in pool.connection_in_use.statements[0][0]
    assert "(occurred_at, calculation_id) < (%s, %s)" in pool.connection_in_use.statements[1][0]


def test_driver_failures_become_infrastructure_errors() -> None:
    repository = _repository(RecordingPool(fail=True))

    with pytest.raises(InfrastructureError):
        repository.save(_calculation(0, 1))
    with pytest.raises(InfrastructureError):
        repository.list_after(5, None)


@pytest.fixture
def repository() -> Iterator[PostgresCalculationRepository]:
    assert DATABASE_URL
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        reset_database(admin)
        apply_migrations(admin, MIGRATIONS_DIR)
    with ConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=True) as pool:
        yield PostgresCalculationRepository(pool)


@needs_postgres
def test_saved_calculation_is_read_back_unchanged(
    repository: PostgresCalculationRepository,
) -> None:
    saved = _calculation(0, 1, "3.000000000000000000000000000001")

    repository.save(saved)

    assert repository.list_after(10, None) == [saved]


@needs_postgres
def test_saving_the_same_id_twice_keeps_the_first_row(
    repository: PostgresCalculationRepository,
) -> None:
    repository.save(_calculation(0, 1, "3"))
    repository.save(_calculation(5, 1, "99"))

    rows = repository.list_after(10, None)
    assert [row.result for row in rows] == [Decimal(3)]


@needs_postgres
def test_order_ties_and_keyset_paging_without_repeat_or_skip(
    repository: PostgresCalculationRepository,
) -> None:
    rows = [_calculation(0, 1), _calculation(10, 2), _calculation(10, 3), _calculation(20, 4)]
    for row in rows:
        repository.save(row)
    expected = [4, 3, 2, 1]

    first = repository.list_after(2, None)
    second = repository.list_after(2, HistoryCursor(first[-1].occurred_at, first[-1].id))
    third = repository.list_after(2, HistoryCursor(second[-1].occurred_at, second[-1].id))

    assert [row.id.int for row in first + second] == expected
    assert third == []
