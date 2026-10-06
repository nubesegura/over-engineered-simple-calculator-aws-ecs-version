import json
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from starlette.testclient import TestClient

from calculator_core.config import http_service, migrate_job
from calculator_core.config.container import build_container
from calculator_core.config.settings import load_settings

LOCAL_ENV = {"ENVIRONMENT": "local", "SERVICE_NAME": "calc-test-local"}
MASTER_PASSWORD = "master-pw-for-tests"  # noqa: S105 - throwaway value
APP_PASSWORD = "app-pw-for-tests"  # noqa: S105 - throwaway value


@pytest.mark.parametrize(
    ("service", "post_path"),
    [
        ("calc-add", "/api/v1/add"),
        ("calc-sub", "/api/ecs/v1/sub"),
        ("calc-mul", "/api/v1/mul"),
        ("calc-div", "/api/ecs/v1/div"),
    ],
)
def test_calculation_services_serve_only_their_operation(service: str, post_path: str) -> None:
    container = build_container(load_settings(LOCAL_ENV))
    client = TestClient(http_service.build_service_app(service, container))

    assert client.post(post_path, json={"a": 6, "b": 3}).status_code == 200
    assert client.get("/api/v1/history").status_code == 404
    other = "/api/v1/sub" if service == "calc-add" else "/api/v1/add"
    assert client.post(other, json={"a": 1, "b": 1}).status_code == 404
    assert client.get("/health").status_code == 200


def test_history_service_serves_only_history() -> None:
    container = build_container(load_settings(LOCAL_ENV))
    client = TestClient(http_service.build_service_app("history", container))

    assert client.get("/api/ecs/v1/history").json() == {"items": [], "next_cursor": None}
    assert client.post("/api/v1/add", json={"a": 1, "b": 1}).status_code == 404


def test_unknown_service_is_rejected() -> None:
    container = build_container(load_settings(LOCAL_ENV))

    with pytest.raises(ValueError, match="Unknown service"):
        http_service.build_service_app("ingest", container)


def test_run_starts_uvicorn_with_tls_on_8443_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in {**LOCAL_ENV, "TLS_DIRECTORY": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    seen: dict[str, Any] = {}

    def fake_run(app: object, **options: Any) -> None:
        seen.update(options)
        seen["files_existed"] = Path(options["ssl_keyfile"]).exists()

    monkeypatch.setattr("uvicorn.run", fake_run)

    http_service.run_http_service("calc-add")

    assert seen["port"] == 8443
    assert seen["timeout_keep_alive"] == 65
    assert seen["files_existed"]
    assert not Path(seen["ssl_keyfile"]).exists()
    assert list(tmp_path.iterdir()) == []


def migrate_env() -> dict[str, str]:
    return {
        "MASTER_SECRET_ARN": "master-secret",
        "APP_SECRET_ARN": "app-secret",
        "DB_HOST": "db.example.test",
        "DB_NAME": "calc",
    }


def test_migrate_settings_name_missing_and_invalid_variables() -> None:
    with pytest.raises(migrate_job.MigrateConfigError, match="MASTER_SECRET_ARN, DB_NAME"):
        migrate_job.load_migrate_settings({"APP_SECRET_ARN": "a", "DB_HOST": "h"})
    with pytest.raises(migrate_job.MigrateConfigError, match="DB_PORT"):
        migrate_job.load_migrate_settings({**migrate_env(), "DB_PORT": "70000"})

    settings = migrate_job.load_migrate_settings(migrate_env())

    assert settings.db_port == 5432


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@mock_aws
def test_migrate_applies_files_with_the_master_credential_then_sets_the_app_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = boto3.client("secretsmanager", region_name="us-east-2")
    master = client.create_secret(
        Name="master",
        SecretString=json.dumps({"username": "postgres", "password": MASTER_PASSWORD}),
    )["ARN"]
    application = client.create_secret(
        Name="app", SecretString=json.dumps({"username": "calc_app", "password": APP_PASSWORD})
    )["ARN"]
    calls: list[tuple[str, tuple[Any, ...]]] = []
    connection = FakeConnection()

    def fake_connect(**kwargs: Any) -> FakeConnection:
        calls.append(("connect", (kwargs["user"], kwargs["password"], kwargs["sslmode"])))
        return connection

    monkeypatch.setattr("psycopg.connect", fake_connect)

    def fake_apply(conn: object, directory: Path) -> list[str]:
        calls.append(("apply", (conn, directory)))
        return ["0001_x"]

    monkeypatch.setattr(migrate_job, "apply_migrations", fake_apply)
    monkeypatch.setattr(
        migrate_job,
        "set_role_password",
        lambda conn, role, password: calls.append(("password", (conn, role, password))),
    )
    settings = migrate_job.load_migrate_settings(
        {**migrate_env(), "MASTER_SECRET_ARN": master, "APP_SECRET_ARN": application}
    )

    applied = migrate_job.run_migrations(settings, client)

    assert applied == ["0001_x"]
    assert [name for name, _ in calls] == ["connect", "apply", "password"]
    assert calls[0][1] == ("postgres", MASTER_PASSWORD, "require")
    assert calls[2][1] == (connection, "calc_app", APP_PASSWORD)


def test_migrate_main_exits_non_zero_and_never_logs_a_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in migrate_env().items():
        monkeypatch.setenv(name, value)

    def explode(settings: object, client: object) -> list[str]:
        raise RuntimeError(f"password {MASTER_PASSWORD} rejected")

    monkeypatch.setattr(migrate_job, "run_migrations", explode)
    monkeypatch.setattr("boto3.client", lambda name: object())

    assert migrate_job.main() == 1

    output = capsys.readouterr()
    assert MASTER_PASSWORD not in output.out + output.err
    assert "RuntimeError" in output.out


def test_migrate_main_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in migrate_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(migrate_job, "run_migrations", lambda settings, client: [])
    monkeypatch.setattr("boto3.client", lambda name: object())

    assert migrate_job.main() == 0
