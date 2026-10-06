"""PostgreSQL implementation of the calculation repository (psycopg 3 with a pool).

Every statement is a constant: values only travel as bound parameters. Credentials come from
a provider and are read each time a *new* connection is opened, so connections that are
already pooled keep working across a password rotation and new ones pick up the new secret.
"""

import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, LiteralString, Self
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.credentials_provider import (
    CredentialsProvider,
    DatabaseCredentials,
)
from calculator_core.domain.calculation import Calculation
from calculator_core.domain.history import HistoryCursor
from calculator_core.domain.operation import Operation

logger = logging.getLogger(__name__)

_INSERT: LiteralString = (
    "INSERT INTO calculations "
    "(calculation_id, operation, operand_a, operand_b, result, occurred_at, correlation_id) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (calculation_id) DO NOTHING"
)
_SELECT: LiteralString = (
    "SELECT calculation_id, operation, operand_a, operand_b, result, occurred_at, "
    "correlation_id FROM calculations"
)
_AFTER_CURSOR: LiteralString = " WHERE (occurred_at, calculation_id) < (%s, %s)"
_ORDER_LIMIT: LiteralString = " ORDER BY occurred_at DESC, calculation_id DESC LIMIT %s"

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
_AUTH_SQLSTATES = {"28000", "28P01"}


def _is_authentication_failure(error: psycopg.Error) -> bool:
    # Failures while connecting often carry no SQLSTATE, only the server message.
    return error.sqlstate in _AUTH_SQLSTATES or "authentication failed" in str(error)


class RotatingConnector:
    """Opens connections; on an authentication failure refreshes the credentials and retries.

    Attempts are bounded and spaced by a linear backoff. Other failures (network, DNS) are
    raised unchanged so the pool applies its own reconnect policy.
    """

    def __init__(
        self,
        provider: CredentialsProvider,
        open_connection: Callable[..., Any],
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._open_connection = open_connection
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def connect(self, **options: Any) -> Any:
        """Open a connection; `options` (such as `connect_timeout`) go to the opener."""
        credentials = self._provider.get()
        attempt = 1
        while True:
            try:
                return self._open_connection(credentials, **options)
            except psycopg.OperationalError as error:
                if not _is_authentication_failure(error):
                    raise
                if attempt >= self._max_attempts:
                    raise InfrastructureError(
                        "The database rejected the application credentials.", error
                    ) from error
                logger.warning(
                    "Database rejected the credentials; refreshing them (attempt %d of %d).",
                    attempt,
                    self._max_attempts,
                )
                self._sleep(self._backoff_seconds * attempt)
                credentials = self._provider.refresh()
                attempt += 1


def create_pool(
    host: str,
    port: int,
    dbname: str,
    provider: CredentialsProvider,
    min_size: int = 1,
    max_size: int = 5,
) -> ConnectionPool[Any]:
    """Build the pool and start opening it; new connections use the provider's credentials."""
    conninfo = make_conninfo(host=host, port=port, dbname=dbname, sslmode="require")

    def open_connection(
        credentials: DatabaseCredentials, **options: Any
    ) -> psycopg.Connection[TupleRow]:
        return psycopg.Connection.connect(
            conninfo, user=credentials.username, password=credentials.password, **options
        )

    connector = RotatingConnector(provider, open_connection)

    class RotatingConnection(psycopg.Connection[TupleRow]):
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any) -> Self:
            options = {"connect_timeout": DEFAULT_CONNECT_TIMEOUT_SECONDS, **kwargs}
            connection: Self = connector.connect(**options)
            return connection

    pool: ConnectionPool[Any] = ConnectionPool(
        conninfo,
        connection_class=RotatingConnection,
        min_size=min_size,
        max_size=max_size,
        timeout=10,
        open=False,
        check=ConnectionPool.check_connection,
    )
    pool.open(wait=False)
    return pool


class PostgresCalculationRepository:
    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self._pool = pool

    def save(self, calculation: Calculation) -> None:
        params = (
            calculation.id,
            calculation.operation.value,
            calculation.operand_a,
            calculation.operand_b,
            calculation.result,
            calculation.occurred_at,
            calculation.correlation_id,
        )
        try:
            with self._pool.connection() as connection:
                connection.execute(_INSERT, params)
        except psycopg.Error as error:
            raise InfrastructureError("The calculation could not be saved.", error) from error

    def list_after(self, limit: int, after: HistoryCursor | None) -> list[Calculation]:
        query = _SELECT + _ORDER_LIMIT
        params: tuple[Any, ...] = (limit,)
        if after is not None:
            query = _SELECT + _AFTER_CURSOR + _ORDER_LIMIT
            params = (after.occurred_at, after.calculation_id, limit)
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(query, params).fetchall()
        except psycopg.Error as error:
            raise InfrastructureError("The history could not be read.", error) from error
        return [_to_calculation(row) for row in rows]


def _to_calculation(row: tuple[Any, ...]) -> Calculation:
    calculation_id, operation, operand_a, operand_b, result, occurred_at, correlation_id = row
    return Calculation(
        id=UUID(str(calculation_id)),
        operation=Operation(operation),
        operand_a=Decimal(operand_a),
        operand_b=Decimal(operand_b),
        result=Decimal(result),
        occurred_at=occurred_at,
        correlation_id=correlation_id,
    )
