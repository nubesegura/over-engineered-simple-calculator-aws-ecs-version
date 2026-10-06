import logging
from collections.abc import Callable
from typing import Any

import psycopg
import pytest

from calculator_core.adapters.outbound.postgres_repository import RotatingConnector
from calculator_core.application.errors import InfrastructureError
from calculator_core.application.ports.credentials_provider import DatabaseCredentials

OLD = DatabaseCredentials("calc_app", "old-secret-value")
NEW = DatabaseCredentials("calc_app", "new-secret-value")
AUTH_FAILURE = psycopg.OperationalError(
    'connection failed: FATAL:  password authentication failed for user "calc_app"'
)


class FakeProvider:
    """Serves OLD until `refresh` is called, then NEW (a rotation happened meanwhile)."""

    def __init__(self) -> None:
        self.current = OLD
        self.refreshes = 0

    def get(self) -> DatabaseCredentials:
        return self.current

    def refresh(self) -> DatabaseCredentials:
        self.refreshes += 1
        self.current = NEW
        return self.current


def _connector(
    opener: Callable[[DatabaseCredentials], Any],
    provider: FakeProvider,
    sleeps: list[float],
    attempts: int = 3,
) -> RotatingConnector:
    return RotatingConnector(
        provider=provider,
        open_connection=opener,
        max_attempts=attempts,
        backoff_seconds=0.1,
        sleep=sleeps.append,
    )


def _opener_accepting(valid: DatabaseCredentials, seen: list[str]) -> Callable[..., object]:
    def open_connection(credentials: DatabaseCredentials) -> object:
        seen.append(credentials.password)
        if credentials != valid:
            raise AUTH_FAILURE
        return object()

    return open_connection


def test_rejected_connection_is_retried_with_refreshed_credentials_after_a_backoff() -> None:
    provider = FakeProvider()
    sleeps: list[float] = []
    seen: list[str] = []

    connection = _connector(_opener_accepting(NEW, seen), provider, sleeps).connect()

    assert connection is not None
    assert seen == [OLD.password, NEW.password]
    assert provider.refreshes == 1
    assert sleeps == [0.1]


def test_repeated_authentication_failure_raises_infrastructure_error_after_max_attempts() -> None:
    provider = FakeProvider()
    sleeps: list[float] = []
    seen: list[str] = []
    connector = _connector(_opener_accepting(DatabaseCredentials("x", "y"), seen), provider, sleeps)

    with pytest.raises(InfrastructureError) as raised:
        connector.connect()

    assert len(seen) == 3
    assert provider.refreshes == 2
    assert sleeps == [0.1, 0.2]
    assert OLD.password not in str(raised.value)
    assert NEW.password not in str(raised.value)


def test_failure_other_than_authentication_is_not_retried_nor_refreshed() -> None:
    provider = FakeProvider()
    sleeps: list[float] = []
    calls: list[int] = []

    def unreachable(credentials: DatabaseCredentials) -> object:
        calls.append(1)
        raise psycopg.OperationalError("connection refused")

    with pytest.raises(psycopg.OperationalError):
        _connector(unreachable, provider, sleeps).connect()

    assert calls == [1]
    assert provider.refreshes == 0


def test_connection_that_opens_with_cached_credentials_does_not_refresh() -> None:
    provider = FakeProvider()
    sleeps: list[float] = []
    seen: list[str] = []

    _connector(_opener_accepting(OLD, seen), provider, sleeps).connect()

    assert provider.refreshes == 0
    assert sleeps == []


def test_logs_and_exception_text_never_contain_the_password(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = FakeProvider()
    sleeps: list[float] = []
    seen: list[str] = []
    connector = _connector(_opener_accepting(DatabaseCredentials("x", "y"), seen), provider, sleeps)

    with caplog.at_level(logging.DEBUG), pytest.raises(InfrastructureError) as raised:
        connector.connect()

    text = caplog.text + str(raised.value) + repr(raised.value.original_error)
    assert OLD.password not in text
    assert NEW.password not in text
    assert "password=" not in repr(OLD)
