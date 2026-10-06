from typing import Any

import psycopg
import pytest

from calculator_core.adapters.outbound import postgres_repository
from calculator_core.application.ports.credentials_provider import DatabaseCredentials


class FixedProvider:
    def get(self) -> DatabaseCredentials:
        return DatabaseCredentials("calc_app", "test-only-password")

    def refresh(self) -> DatabaseCredentials:
        return self.get()


class FakePool:
    check_connection = staticmethod(lambda connection: None)

    def __init__(self, conninfo: str, **options: Any) -> None:
        self.connection_class = options["connection_class"]

    def open(self, wait: bool = True) -> None:
        return None


def test_the_pool_connect_timeout_reaches_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_connect(conninfo: str, **options: Any) -> str:
        seen.update(options)
        return "connection"

    monkeypatch.setattr(postgres_repository, "ConnectionPool", FakePool)
    monkeypatch.setattr(psycopg.Connection, "connect", fake_connect)
    pool: Any = postgres_repository.create_pool("db.example.test", 5432, "calc", FixedProvider())

    opened = pool.connection_class.connect("host=db.example.test", connect_timeout=7)

    assert opened == "connection"
    assert seen["connect_timeout"] == 7
    assert seen["user"] == "calc_app"
