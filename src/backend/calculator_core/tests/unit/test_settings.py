from decimal import Decimal

import pytest

from calculator_core.adapters.outbound.in_memory_repository import InMemoryCalculationRepository
from calculator_core.adapters.outbound.postgres_repository import PostgresCalculationRepository
from calculator_core.application.use_cases.calculate import CalculateCommand
from calculator_core.application.use_cases.read_history import ReadHistoryQuery
from calculator_core.config import container as container_module
from calculator_core.config.container import build_container
from calculator_core.config.settings import SettingsError, load_settings
from calculator_core.domain.operation import Operation

FULL = {
    "ENVIRONMENT": "dev",
    "SERVICE_NAME": "calc-add",
    "APP_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:000000000000:secret:app",
    "DB_HOST": "db.internal",
    "DB_PORT": "5432",
    "DB_NAME": "calculator",
    "ALERT_TOPIC_ARN": "arn:aws:sns:us-east-2:000000000000:alerts",
    "CORS_ALLOWED_ORIGIN": "https://app.example.test",
}
REQUIRED = [name for name in FULL if name != "DB_PORT"]


def test_complete_environment_is_loaded() -> None:
    settings = load_settings(FULL)

    assert settings.environment == "dev"
    assert settings.db_port == 5432
    assert settings.cors_allowed_origin == "https://app.example.test"


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_required_value_outside_local_stops_the_start(missing: str) -> None:
    env = {name: value for name, value in FULL.items() if name != missing}

    with pytest.raises(SettingsError, match=missing):
        load_settings(env)


def test_blank_value_counts_as_missing() -> None:
    with pytest.raises(SettingsError, match="DB_HOST"):
        load_settings({**FULL, "DB_HOST": "  "})


def test_error_lists_every_problem_and_never_echoes_values() -> None:
    env = {**FULL, "DB_PORT": "not-a-number", "CORS_ALLOWED_ORIGIN": "*"}
    del env["DB_NAME"]

    with pytest.raises(SettingsError) as raised:
        load_settings(env)

    message = str(raised.value)
    assert "DB_NAME" in message
    assert "DB_PORT" in message
    assert "CORS_ALLOWED_ORIGIN" in message
    assert "not-a-number" not in message


def test_unknown_or_absent_environment_is_rejected() -> None:
    with pytest.raises(SettingsError, match="ENVIRONMENT"):
        load_settings({})
    with pytest.raises(SettingsError, match="ENVIRONMENT"):
        load_settings({**FULL, "ENVIRONMENT": "staging"})


def test_local_mode_needs_nothing_and_wires_in_memory_adapters() -> None:
    settings = load_settings({"ENVIRONMENT": "local"})
    container = build_container(settings)

    created = container.calculate.execute(CalculateCommand(Operation.ADD, Decimal(1), Decimal(2)))
    assert container.read_history.execute(ReadHistoryQuery()).items == [created]
    assert isinstance(container.repository, InMemoryCalculationRepository)


def test_outside_local_the_container_uses_postgres_and_never_the_in_memory_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setattr(container_module, "create_pool", lambda *args, **kwargs: object())

    container = build_container(load_settings(FULL))

    assert isinstance(container.repository, PostgresCalculationRepository)
