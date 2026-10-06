from typing import Any

from calculator_core.adapters.outbound.migrations import set_role_password

CLEAR_PASSWORD = "clear-test-password"  # noqa: S105 - throwaway value
VERIFIER = "SCRAM-SHA-256$4096:salt$stored:server"


class FakePgConnection:
    def __init__(self) -> None:
        self.encrypted: list[tuple[bytes, bytes, bytes | None]] = []

    def encrypt_password(
        self, password: bytes, user: bytes, algorithm: bytes | None = None
    ) -> bytes:
        self.encrypted.append((password, user, algorithm))
        return VERIFIER.encode()


class FakeTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.pgconn = FakePgConnection()
        self.statements: list[str] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement: Any) -> None:
        self.statements.append(statement.as_string())


def test_the_statement_carries_a_scram_verifier_never_the_clear_password() -> None:
    connection = FakeConnection()

    set_role_password(connection, "calc_app", CLEAR_PASSWORD)  # type: ignore[arg-type]

    assert connection.pgconn.encrypted == [(CLEAR_PASSWORD.encode(), b"calc_app", b"scram-sha-256")]
    assert connection.statements == [f"ALTER ROLE \"calc_app\" PASSWORD '{VERIFIER}'"]
    assert CLEAR_PASSWORD not in connection.statements[0]
